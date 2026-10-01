"""LLM API abstraction -- OpenAI-compatible (MindRouter) for subject model testing.

Ported from MARC2. Supports two-pass approach:
  Pass 1: Let the model reason freely (unstructured)
  Pass 2: Feed reasoning back and extract structured JSON output
"""

import json
import os
import time

import httpx

from ratelimit import mindrouter_bucket


# Streaming (September 29, 2026, protocol v3). MindRouter aborts a non-streaming backend
# attempt after 180 s ("Backend attempt exceeded 180s; use streaming or reduce
# max_tokens"), retries up to 3x and returns 504 at 300 s — a wall-clock cap in place of
# the token cap v3 removed. A streamed request is exempt: measured 269 s and 323 s
# generations to completion. The SSE deltas are reassembled into the same response
# shape the non-streaming path returned (choices[0].message.{content,reasoning_content},
# choices[0].finish_reason, usage), so everything downstream — grading, db.STANDS_SQL,
# audits over raw_response — is unchanged. STREAM_IDLE_TIMEOUT bounds the silence
# between chunks, not the whole generation.
STREAM = True
STREAM_IDLE_TIMEOUT = 180.0

# A model whose backend is down gets HTTP 503 ("temporarily unavailable — no healthy
# backend is currently serving it") on every request, instantly. Without a pause a
# backfill lane sprints through all its phases on errors and logs a false DONE
# (glm-5.3-flash, September 30, 2026: 942 errors in 20 minutes). So a 503 waits and
# retries — one probe a minute, for up to BACKEND_WAIT_MAX seconds — before it counts
# as a transport failure. Set to 0 to fail fast.
BACKEND_WAIT_MAX = 2 * 3600
BACKEND_WAIT_STEP = 60


def _wait_for_backend(resp_status, waited, model_id):
    """Return the new total wait after sleeping, or None when the wait is exhausted
    (or the status is not one we wait on). 503 = this model's backend is down;
    None = the gateway itself could not be reached (httpx connect error), e.g.
    MindRouter restarting (September 30, 2026, 23:50 UTC)."""
    if resp_status not in (503, None) or waited >= BACKEND_WAIT_MAX:
        return None
    time.sleep(BACKEND_WAIT_STEP)
    return waited + BACKEND_WAIT_STEP


class StreamEndedEarly(RuntimeError):
    """The SSE stream stopped before finish_reason/[DONE]: MindRouter documents that a
    backend failure after the first chunk ends the stream with no error event."""


def call_llm(model_config, messages, stream=None):
    """Call an LLM. Returns (raw_response_json, response_text, latency_ms)."""
    api_key = _get_api_key(model_config)
    endpoint = model_config["endpoint"].rstrip("/")
    url = f"{endpoint}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model_config["model_id"],
        "messages": messages,
        "temperature": model_config.get("temperature", 0.0),
    }
    if "max_tokens" in model_config:
        body["max_tokens"] = model_config["max_tokens"]
    if "reasoning_effort" in model_config:
        body["reasoning_effort"] = model_config["reasoning_effort"]
    if stream is None:
        stream = model_config.get("stream", STREAM)

    waited = 0
    while True:  # loops only to wait out a 503 (backend down); see BACKEND_WAIT_MAX
        mindrouter_bucket.acquire()
        start = time.monotonic()
        if not stream:
            with httpx.Client(timeout=model_config.get("timeout", 300.0)) as client:
                try:
                    resp = client.post(url, json=body, headers=headers)
                except httpx.ConnectError:
                    w = _wait_for_backend(None, waited, model_config["model_id"])
                    if w is not None:
                        waited = w
                        continue
                    raise
                if resp.status_code == 503:
                    w = _wait_for_backend(503, waited, model_config["model_id"])
                    if w is not None:
                        waited = w
                        continue
                resp.raise_for_status()
            latency_ms = int((time.monotonic() - start) * 1000)
            raw = resp.text
            data = resp.json()
            msg = data["choices"][0]["message"]
            text = msg.get("content") or msg.get("reasoning_content") or ""
            return raw, text, latency_ms
        break

    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    content, reasoning = [], []
    finish = None
    usage = None
    meta = {}
    done = False
    timeout = httpx.Timeout(connect=30.0, read=model_config.get("stream_idle_timeout",
                                                               STREAM_IDLE_TIMEOUT),
                            write=30.0, pool=30.0)
    with httpx.Client(timeout=timeout) as client:
        while True:  # one attempt per pass; loops only to wait out a 503 / gateway down
            try:
                with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code == 503:
                        resp.read()
                        w = _wait_for_backend(503, waited, model_config["model_id"])
                        if w is not None:
                            waited = w
                            mindrouter_bucket.acquire()
                            start = time.monotonic()
                            continue
                    if resp.status_code >= 400:
                        resp.read()  # so the error body is available in the exception message
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            done = True
                            break
                        try:
                            ev = json.loads(payload)
                        except ValueError:
                            continue
                        if not meta and ev.get("id"):
                            meta = {"id": ev.get("id"), "model": ev.get("model"),
                                    "created": ev.get("created")}
                        if ev.get("usage"):
                            usage = ev["usage"]
                        for ch in ev.get("choices") or []:
                            delta = ch.get("delta") or {}
                            if delta.get("content"):
                                content.append(delta["content"])
                            r = delta.get("reasoning_content") or delta.get("reasoning")
                            if r:
                                reasoning.append(r)
                            if ch.get("finish_reason"):
                                finish = ch["finish_reason"]
                break
            except httpx.ConnectError:
                w = _wait_for_backend(None, waited, model_config["model_id"])
                if w is not None:
                    waited = w
                    mindrouter_bucket.acquire()
                    start = time.monotonic()
                    continue
                raise
    latency_ms = int((time.monotonic() - start) * 1000)
    if not done and finish is None:
        raise StreamEndedEarly(
            f"stream ended early after {latency_ms} ms without finish_reason "
            f"({len(reasoning)} reasoning / {len(content)} content chunks)")

    message = {"role": "assistant", "content": "".join(content) or None}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    data = {**meta, "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": usage, "streamed": True}
    text = message.get("content") or message.get("reasoning_content") or ""
    return json.dumps(data), text, latency_ms


def call_llm_two_pass(model_config, messages, extraction_prompt_fn,
                      extraction_model_config=None):
    """Two-pass LLM call: reasoning then extraction."""
    raw1, text1, latency1 = call_llm(model_config, messages)

    raw1_data = json.loads(raw1) if isinstance(raw1, str) else raw1
    msg = raw1_data.get("choices", [{}])[0].get("message", {})
    reasoning = msg.get("reasoning_content") or text1 or ""
    # Models that split their turn into reasoning_content + content (gpt-oss, Nemotron,
    # GLM) put the final answer in content. Until September 29, 2026 only
    # reasoning_content reached the extractor; gpt-oss repeats its grid there, so it
    # rarely mattered (audit: 2 of 400 trials), but GLM's reasoning_content is a
    # one-line summary, so the extractor never saw its answer and guessed. Append
    # the final message so the tail (below) ends with the answer. Content-only models
    # are unchanged (content is already `reasoning`).
    content = msg.get("content") or ""
    if content and content != reasoning:
        reasoning = reasoning + "\n\n" + content

    pass2_config = extraction_model_config or model_config
    # Truncate reasoning to last 4000 chars for extraction — conclusions are at the end
    reasoning_tail = reasoning[-4000:] if len(reasoning) > 4000 else reasoning
    extraction_messages = extraction_prompt_fn(reasoning_tail)
    raw2, text2, latency2 = call_llm(pass2_config, extraction_messages)

    return raw1, reasoning, raw2, text2, latency1 + latency2


def _get_api_key(config):
    env_var = config.get("api_key_env")
    if not env_var:
        return None
    key = os.environ.get(env_var)
    if not key:
        raise RuntimeError(f"Environment variable {env_var} not set")
    return key
