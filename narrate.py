"""Narrate: invited participants write narratives that help a model solve a puzzle.

The participant sees the complete grid sequence (ground truth), including the grid
the model will not see. They write a narrative; we run it through the benchmark's
own `both` condition (collect.run_trial: same prompt, two-pass protocol, parser,
grader) against one model, and show whether the model reconstructed the hidden
grid. Every submission, every reveal of the reference narrative, and the model's
full response are recorded in narrate.db (see narrate_db.py).

Access is invite-code gated. Logged-in owners/reviewers can join without a code;
their participant rows carry staff_username so they can be filtered out.

Settings live under `narrate:` in config.yaml. A puzzle is offered to participants
only after its reference narrative has solved it at least once in a reference
check (`flask --app server narrate verify`, or the button on /narrate/admin), so
the page's promise that a working narrative exists is backed by a recorded run.
The check uses the puzzle's `reference_model` if set (e.g. a puzzle whose reference
narrative is known to work on gpt-oss-120b but not on the participant model), and
the page names that model.
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import click
from flask import (Blueprint, Response, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

import collect
import db
import grids
import narrate_db
import prompts

bp = Blueprint("narrate", __name__, url_prefix="/narrate")

COOKIE_NAME = "narc_narrate"
COOKIE_MAX_AGE = 90 * 24 * 3600
# An attempt still queued/running after this long is treated as lost.
STALE_MINUTES = 20
# Global brute-force brake on invite codes (not per IP: the app sits behind a proxy).
MAX_JOIN_FAILURES_PER_10_MIN = 30

_DEFAULTS = {
    "model": "qwen3.8-27b",
    "extraction_model": "gpt-oss-120b-extract",
    "max_narrative_chars": 1500,
    "submissions_per_hour": 12,
    "submissions_per_day": 40,
    "max_queue": 12,
    "workers": 2,
    "reference_runs": 3,
    "puzzles": [],
}


def settings():
    cfg = collect.load_config().get("narrate") or {}
    out = dict(_DEFAULTS)
    out.update(cfg)
    return out


# --- background execution ---
# One pool per gunicorn worker process. Every model call goes through the shared
# SQLite token bucket in ratelimit.py, so the MindRouter per-minute ceiling holds
# across this pool, the review-job pool, and all processes.

_executor = None
_executor_lock = threading.Lock()


def _pool():
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=int(settings()["workers"]),
                                           thread_name_prefix="narrate")
    return _executor


# --- helpers ---

def _staff_username():
    """Username of a logged-in owner/reviewer, else None."""
    if "narrate_staff" not in g:
        g.narrate_staff = None
        uid = session.get("user_id")
        if uid:
            conn = db.init_db()
            row = db.get_user_by_id(conn, uid)
            conn.close()
            if row and row["role"] in ("owner", "reviewer"):
                g.narrate_staff = row["username"]
    return g.narrate_staff


def _participant(conn):
    return narrate_db.get_participant_by_token(conn, request.cookies.get(COOKIE_NAME))


def _display_title(title):
    return (title or "").replace("\U0001F9E9", "").strip()


def _load_puzzle(puzzle_id):
    """(view, reference_text, reference_model) for a configured puzzle, or None.

    view is the puzzle re-masked at its original positions over a complete
    sequence: every item carries its true grid, `masked_positions` says which
    ones the model is denied, and `answer_grids` holds what it must produce.
    """
    entry = next((p for p in settings()["puzzles"] if p.get("puzzle_id") == puzzle_id), None)
    if not entry:
        return None
    conn = db.init_db()
    try:
        row = db.get_puzzle(conn, puzzle_id)
    finally:
        conn.close()
    if not row:
        return None
    pj = db.puzzle_to_json(row)
    view = grids.remask(pj, pj["masked_positions"])
    reference = (entry.get("reference_narrative") or pj["narrative"] or "").strip()
    return view, reference, entry.get("reference_model") or settings()["model"]


def _reference_state(conn, puzzle_id, reference_text, reference_model):
    solved, total = narrate_db.reference_status(conn, puzzle_id, reference_text,
                                                reference_model)
    return {"verified": solved > 0, "solved": solved, "total": total,
            "model": reference_model}


def _utc_iso(ts):
    """SQLite CURRENT_TIMESTAMP (UTC, naive) -> ISO string with Z for the browser."""
    return f"{ts.replace(' ', 'T')}Z" if ts else None


def _attempt_public(row):
    """What a participant may see about their own attempt (no raw model output)."""
    predicted = json.loads(row["predicted_grids"]) if row["predicted_grids"] else None
    return {
        "attempt_id": row["attempt_id"],
        "puzzle_id": row["puzzle_id"],
        "seq_num": row["seq_num"],
        "narrative": row["narrative"],
        "saw_reference": bool(row["saw_reference"]),
        "status": row["status"],
        "correct": row["correct"],
        "cell_accuracy": row["cell_accuracy"],
        "unreadable": bool(row["parse_error"]) and predicted is None,
        "failed": row["status"] == "failed",
        "predicted_grids": predicted,
        "created_at": _utc_iso(row["created_at"]),
        "started_at": _utc_iso(row["started_at"]),
        "finished_at": _utc_iso(row["finished_at"]),
    }


def _reap_if_lost(conn, row):
    """Fail an attempt whose runner is gone, so the participant isn't left waiting.

    The runner pid check assumes every web worker shares one PID namespace (true
    for the single narc container). Age is the backstop for everything else.
    """
    if row["status"] not in ("queued", "running"):
        return row
    lost = False
    if row["runner_pid"] and row["runner_pid"] != os.getpid():
        try:
            os.kill(row["runner_pid"], 0)
        except ProcessLookupError:
            lost = True
        except PermissionError:
            pass
    if not lost:
        lost = conn.execute(
            "SELECT ? < datetime('now', ?)", (row["created_at"], f"-{STALE_MINUTES} minutes")
        ).fetchone()[0] == 1
    if lost:
        narrate_db.fail_attempt(conn, row["attempt_id"],
                                "lost: server restarted or run timed out")
        return narrate_db.get_attempt(conn, row["attempt_id"])
    return row


def _solve_once(view, narrative, model=None):
    """Run the benchmark's `both` condition with `narrative` against `model`
    (default: the Narrate model). Returns kwargs for narrate_db.finish_attempt /
    insert_reference_check."""
    s = settings()
    config = collect.load_config()
    model_config = collect.get_model_config(config, model or s["model"])
    extraction_config = collect.get_model_config(config, s["extraction_model"])
    result = collect.run_trial(model_config, extraction_config,
                               {"trial_id": None, "condition": "both"},
                               view, variant_narrative=narrative)
    if len(result) < 7:
        # run_trial's transport-failure return: (id, None, None, None, error, 0)
        return {"error": result[4] or "model call failed"}
    _, raw, extraction_text, reasoning, parse_error, latency, predicted = result
    out = {"raw_response": raw, "extraction_text": extraction_text,
           "reasoning": reasoning, "latency_ms": latency}
    if predicted is None:
        out.update(parse_error=parse_error or "no grid found in the answer",
                   correct=0, cell_accuracy=0.0)
    else:
        # The extraction pass isn't told which position is masked and often keys a
        # lone grid "0" even when the model wrote the right key. With one hidden
        # grid and one predicted grid there is nothing to disambiguate, so grade
        # it at the hidden position (the raw key survives in extraction_text).
        masked = view["masked_positions"]
        if len(masked) == 1 and len(predicted) == 1 and str(masked[0]) not in predicted:
            predicted = {str(masked[0]): next(iter(predicted.values()))}
        mapped, correct, accuracy = collect.grade_prediction(view, predicted)
        out.update(predicted_grids=json.dumps(mapped), correct=correct,
                   cell_accuracy=accuracy)
    return out


def _run_attempt(attempt_id):
    conn = narrate_db.connect()
    try:
        row = narrate_db.get_attempt(conn, attempt_id)
        if not row or row["status"] != "queued":
            return
        narrate_db.mark_attempt_running(conn, attempt_id, os.getpid())
        loaded = _load_puzzle(row["puzzle_id"])
        if not loaded:
            narrate_db.fail_attempt(conn, attempt_id, "puzzle no longer available")
            return
        view = grids.remask(loaded[0], json.loads(row["masked_positions"]))
        narrate_db.finish_attempt(conn, attempt_id, **_solve_once(view, row["narrative"]))
    except Exception as e:
        narrate_db.fail_attempt(conn, attempt_id, f"internal error: {e}")
    finally:
        conn.close()


_reference_running = set()


def run_reference_checks(puzzle_id, runs, log_fn=print):
    """Run the puzzle's reference narrative `runs` times through the participant
    pipeline and record each result. Returns (solved, completed)."""
    loaded = _load_puzzle(puzzle_id)
    if not loaded:
        log_fn(f"{puzzle_id}: not configured under narrate.puzzles or missing from narc.db")
        return 0, 0
    view, reference, model = loaded
    solved = completed = 0
    conn = narrate_db.connect()
    try:
        for i in range(runs):
            out = _solve_once(view, reference, model)
            narrate_db.insert_reference_check(
                conn, puzzle_id, reference, model, out.get("correct"),
                out.get("cell_accuracy"), parse_error=out.get("parse_error"),
                error=out.get("error"), latency_ms=out.get("latency_ms"))
            if out.get("error"):
                log_fn(f"{puzzle_id} on {model} run {i + 1}/{runs}: ERROR {out['error']}")
                continue
            completed += 1
            solved += out["correct"]
            verdict = "solved" if out["correct"] else f"not solved ({out['cell_accuracy']:.0%} cells)"
            log_fn(f"{puzzle_id} on {model} run {i + 1}/{runs}: {verdict}")
    finally:
        conn.close()
    return solved, completed


def _run_reference_checks_bg(puzzle_id, runs):
    try:
        run_reference_checks(puzzle_id, runs, log_fn=lambda m: None)
    finally:
        _reference_running.discard(puzzle_id)


# --- participant pages ---

@bp.route("")
def index():
    conn = narrate_db.connect()
    try:
        p = _participant(conn)
        staff = _staff_username()
        if not p:
            return render_template("narrate_gate.html", step="invite", staff=staff)
        if not p["acknowledged_at"]:
            return render_template("narrate_gate.html", step="acknowledge", staff=staff,
                                   s=settings())
        cards = []
        for entry in settings()["puzzles"]:
            pid = entry.get("puzzle_id")
            loaded = _load_puzzle(pid)
            if not loaded:
                if staff:
                    cards.append({"puzzle_id": pid, "missing": True})
                continue
            view, reference, ref_model = loaded
            ref = _reference_state(conn, pid, reference, ref_model)
            if not ref["verified"] and not staff:
                continue
            attempts = narrate_db.get_attempts(conn, p["participant_id"], pid)
            cards.append({
                "puzzle_id": pid,
                "title": _display_title(view["title"]),
                "n_grids": len(view["sequence"]),
                "attempts": len(attempts),
                "solved": any(a["correct"] for a in attempts),
                "revealed": narrate_db.has_revealed(conn, p["participant_id"], pid),
                "reference": ref,
                "missing": False,
            })
        return render_template("narrate_list.html", cards=cards, staff=staff, s=settings())
    finally:
        conn.close()


@bp.route("/join", methods=["POST"])
def join():
    staff = _staff_username()
    conn = narrate_db.connect()
    try:
        if request.form.get("as_staff") and staff:
            _, token = narrate_db.create_participant(conn, staff_username=staff)
        else:
            if narrate_db.recent_join_failures(conn) >= MAX_JOIN_FAILURES_PER_10_MIN:
                flash("Too many invite-code attempts right now. Please try again in a few minutes.",
                      "warning")
                return redirect(url_for("narrate.index"))
            invite, reason = narrate_db.check_invite(conn, request.form.get("code"))
            if not invite:
                narrate_db.record_join_failure(conn)
                flash(reason, "danger")
                return redirect(url_for("narrate.index"))
            _, token = narrate_db.create_participant(conn, invite_code=invite["code"],
                                                     staff_username=staff)
    finally:
        conn.close()
    resp = redirect(url_for("narrate.index"))
    resp.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True,
                    samesite="Lax", secure=request.is_secure)
    return resp


@bp.route("/acknowledge", methods=["POST"])
def acknowledge():
    conn = narrate_db.connect()
    try:
        p = _participant(conn)
        if not p:
            return redirect(url_for("narrate.index"))
        if request.form.get("ack") != "yes":
            flash("Please tick the box to confirm you understand that every submission is recorded.",
                  "warning")
            return redirect(url_for("narrate.index"))
        narrate_db.acknowledge(conn, p["participant_id"])
    finally:
        conn.close()
    return redirect(url_for("narrate.index"))


@bp.route("/leave", methods=["POST"])
def leave():
    """Forget this browser's participant cookie (records are kept)."""
    resp = redirect(url_for("narrate.index"))
    resp.delete_cookie(COOKIE_NAME)
    return resp


@bp.route("/<puzzle_id>")
def puzzle(puzzle_id):
    conn = narrate_db.connect()
    try:
        p = _participant(conn)
        if not p or not p["acknowledged_at"]:
            return redirect(url_for("narrate.index"))
        loaded = _load_puzzle(puzzle_id)
        if not loaded:
            flash("That puzzle isn't part of this study.", "warning")
            return redirect(url_for("narrate.index"))
        view, reference, ref_model = loaded
        staff = _staff_username()
        ref = _reference_state(conn, puzzle_id, reference, ref_model)
        if not ref["verified"] and not staff:
            flash("That puzzle isn't available yet.", "warning")
            return redirect(url_for("narrate.index"))
        attempts = [_reap_if_lost(conn, a)
                    for a in narrate_db.get_attempts(conn, p["participant_id"], puzzle_id)]
        revealed = narrate_db.has_revealed(conn, p["participant_id"], puzzle_id)
        s = settings()
        data = {
            "puzzle_id": puzzle_id,
            "title": _display_title(view["title"]),
            "sequence": [{"position": it["position"], "rows": it["rows"], "cols": it["cols"],
                          "grid": it["grid"]} for it in view["sequence"]],
            "masked_positions": view["masked_positions"],
            "answer_grids": view["answer_grids"],
            "model": s["model"],
            "max_chars": int(s["max_narrative_chars"]),
            "revealed": revealed,
            "reference": reference if revealed else None,
            "attempts": [_attempt_public(a) for a in attempts],
        }
        return render_template("narrate_puzzle.html", data=data, staff=staff, ref=ref)
    finally:
        conn.close()


# --- participant API ---

def _api_participant(conn):
    p = _participant(conn)
    if not p or not p["acknowledged_at"]:
        return None, (jsonify({"error": "Your session has ended. Reload the page to rejoin."}), 401)
    return p, None


@bp.route("/api/<puzzle_id>/reveal", methods=["POST"])
def api_reveal(puzzle_id):
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in ("opened", "cancelled", "confirmed"):
        return jsonify({"error": "Invalid action"}), 400
    conn = narrate_db.connect()
    try:
        p, err = _api_participant(conn)
        if err:
            return err
        loaded = _load_puzzle(puzzle_id)
        if not loaded:
            return jsonify({"error": "Not found"}), 404
        reference = loaded[1]
        narrate_db.record_reveal(conn, p["participant_id"], puzzle_id, action,
                                 reference if action == "confirmed" else None)
        if action == "confirmed":
            return jsonify({"reference": reference})
        return jsonify({"status": "ok"})
    finally:
        conn.close()


@bp.route("/api/<puzzle_id>/attempts", methods=["POST"])
def api_submit(puzzle_id):
    body = request.get_json(silent=True) or {}
    narrative = (body.get("narrative") or "").strip()
    s = settings()
    max_chars = int(s["max_narrative_chars"])
    # An empty narrative must never reach prompts.build_both: it falls back to the
    # puzzle's own narrative, which would silently test the reference instead.
    if not narrative:
        return jsonify({"error": "Write a narrative first."}), 400
    if len(narrative) > max_chars:
        return jsonify({"error": f"Please keep it under {max_chars} characters."}), 400
    elapsed = body.get("client_elapsed_ms")
    elapsed = int(elapsed) if isinstance(elapsed, (int, float)) and elapsed >= 0 else None

    conn = narrate_db.connect()
    try:
        p, err = _api_participant(conn)
        if err:
            return err
        loaded = _load_puzzle(puzzle_id)
        if not loaded:
            return jsonify({"error": "Not found"}), 404
        view, reference, ref_model = loaded
        if not _reference_state(conn, puzzle_id, reference, ref_model)["verified"] and not _staff_username():
            return jsonify({"error": "This puzzle isn't available yet."}), 403

        pid = p["participant_id"]
        for a in narrate_db.get_attempts(conn, pid):
            if a["status"] in ("queued", "running"):
                _reap_if_lost(conn, a)
        if narrate_db.count_active_attempts(conn, pid):
            return jsonify({"error": "Your previous narrative is still being tested. "
                                     "Wait for its result, then submit again."}), 409
        if narrate_db.count_recent_attempts(conn, pid, 1) >= int(s["submissions_per_hour"]):
            return jsonify({"error": f"You've reached the limit of {s['submissions_per_hour']} "
                                     "narratives per hour. Please come back a little later."}), 429
        if narrate_db.count_recent_attempts(conn, pid, 24) >= int(s["submissions_per_day"]):
            return jsonify({"error": f"You've reached the limit of {s['submissions_per_day']} "
                                     "narratives per day. Please come back tomorrow."}), 429
        if narrate_db.count_active_attempts(conn) >= int(s["max_queue"]):
            return jsonify({"error": "The model is busy with other people's narratives. "
                                     "Please try again in a minute."}), 503

        prompt_json = json.dumps(prompts.build_both(view, narrative=narrative))
        attempt_id = narrate_db.create_attempt(
            conn, pid, puzzle_id, narrative, s["model"],
            json.dumps(view["masked_positions"]), prompt_json,
            client_elapsed_ms=elapsed, runner_pid=os.getpid())
        _pool().submit(_run_attempt, attempt_id)
        return jsonify(_attempt_public(narrate_db.get_attempt(conn, attempt_id))), 201
    finally:
        conn.close()


@bp.route("/api/attempts/<int:attempt_id>")
def api_attempt(attempt_id):
    conn = narrate_db.connect()
    try:
        p, err = _api_participant(conn)
        if err:
            return err
        row = narrate_db.get_attempt(conn, attempt_id)
        if not row or row["participant_id"] != p["participant_id"]:
            return jsonify({"error": "Not found"}), 404
        return jsonify(_attempt_public(_reap_if_lost(conn, row)))
    finally:
        conn.close()


# --- staff ---

def _require_staff():
    if not _staff_username():
        flash("You don't have permission to access this page.", "danger")
        return redirect(url_for("login"))
    return None


@bp.route("/admin")
def admin():
    denied = _require_staff()
    if denied:
        return denied
    conn = narrate_db.connect()
    try:
        s = settings()
        puzzles = []
        for entry in s["puzzles"]:
            pid = entry.get("puzzle_id")
            loaded = _load_puzzle(pid)
            if not loaded:
                puzzles.append({"puzzle_id": pid, "missing": True})
                continue
            view, reference, ref_model = loaded
            puzzles.append({
                "puzzle_id": pid, "missing": False,
                "title": _display_title(view["title"]),
                "reference": reference,
                "overridden": bool(entry.get("reference_narrative")),
                "state": _reference_state(conn, pid, reference, ref_model),
                "checking": pid in _reference_running,
            })
        attempts = conn.execute(
            """SELECT a.attempt_id, a.participant_id, a.puzzle_id, a.seq_num, a.narrative,
                      a.saw_reference, a.status, a.correct, a.cell_accuracy, a.parse_error,
                      a.error, a.latency_ms, a.created_at, p.invite_code, p.staff_username
               FROM attempts a JOIN participants p USING (participant_id)
               ORDER BY a.attempt_id DESC LIMIT 200"""
        ).fetchall()
        return render_template("narrate_admin.html", s=s, puzzles=puzzles,
                               invites=narrate_db.get_invites(conn), attempts=attempts,
                               staff=_staff_username())
    finally:
        conn.close()


@bp.route("/admin/invites", methods=["POST"])
def admin_create_invite():
    denied = _require_staff()
    if denied:
        return denied
    f = request.form
    max_p = f.get("max_participants", "").strip()
    expires = f.get("expires", "").strip()
    conn = narrate_db.connect()
    try:
        code = narrate_db.create_invite(
            conn, label=f.get("label", "").strip() or None,
            max_participants=int(max_p) if max_p.isdigit() else None,
            expires_at=f"{expires} 23:59:59" if expires else None,
            code=f.get("code", "").strip() or None, created_by=_staff_username())
        flash(f"Invite code created: {code}", "success")
    except Exception as e:
        flash(f"Couldn't create that code: {e}", "danger")
    finally:
        conn.close()
    return redirect(url_for("narrate.admin"))


@bp.route("/admin/invites/<code>/active", methods=["POST"])
def admin_toggle_invite(code):
    denied = _require_staff()
    if denied:
        return denied
    conn = narrate_db.connect()
    try:
        narrate_db.set_invite_active(conn, code, request.form.get("active") == "1")
    finally:
        conn.close()
    return redirect(url_for("narrate.admin"))


@bp.route("/admin/verify/<puzzle_id>", methods=["POST"])
def admin_verify(puzzle_id):
    denied = _require_staff()
    if denied:
        return denied
    if puzzle_id in _reference_running:
        flash(f"Reference check for {puzzle_id} is already running in this worker.", "info")
    elif not _load_puzzle(puzzle_id):
        flash(f"{puzzle_id} isn't configured or doesn't exist.", "danger")
    else:
        runs = int(settings()["reference_runs"])
        _reference_running.add(puzzle_id)
        _pool().submit(_run_reference_checks_bg, puzzle_id, runs)
        flash(f"Started {runs} reference run(s) for {puzzle_id}. Reload in a minute or two.", "info")
    return redirect(url_for("narrate.admin"))


def export_payload(conn):
    def rows(sql):
        return [dict(r) for r in conn.execute(sql).fetchall()]
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings(),
        "invite_codes": rows("SELECT * FROM invite_codes"),
        "participants": rows("""SELECT participant_id, invite_code, staff_username,
                                       acknowledged_at, created_at FROM participants"""),
        "reveal_events": rows("SELECT * FROM reveal_events ORDER BY event_id"),
        "attempts": rows("SELECT * FROM attempts ORDER BY attempt_id"),
        "reference_checks": rows("SELECT * FROM reference_checks ORDER BY check_id"),
    }


@bp.route("/admin/export.json")
def admin_export():
    denied = _require_staff()
    if denied:
        return denied
    conn = narrate_db.connect()
    try:
        payload = export_payload(conn)
    finally:
        conn.close()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Response(json.dumps(payload, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=narrate_{stamp}.json"})


# --- CLI: flask --app server narrate <command> ---

@bp.cli.command("invite")
@click.option("--label", default=None, help="Cohort label, e.g. 'lab pilot'")
@click.option("--max-participants", type=int, default=None, help="Cap on people joining with this code")
@click.option("--expires", default=None, help="Last valid day, YYYY-MM-DD (UTC)")
@click.option("--code", default=None, help="Custom code instead of a random one")
def cli_invite(label, max_participants, expires, code):
    """Create an invite code."""
    conn = narrate_db.connect()
    try:
        display = narrate_db.create_invite(
            conn, label=label, max_participants=max_participants,
            expires_at=f"{expires} 23:59:59" if expires else None, code=code, created_by="cli")
    finally:
        conn.close()
    click.echo(display)


@bp.cli.command("invites")
def cli_invites():
    """List invite codes with usage."""
    conn = narrate_db.connect()
    try:
        for r in narrate_db.get_invites(conn):
            state = "active" if r["active"] else "inactive"
            cap = r["max_participants"] if r["max_participants"] is not None else "∞"
            click.echo(f"{r['display_code']:16} {state:8} {r['participants']}/{cap} people  "
                       f"{r['attempts']} attempts  expires={r['expires_at'] or '-'}  {r['label'] or ''}")
    finally:
        conn.close()


@bp.cli.command("deactivate")
@click.argument("code")
def cli_deactivate(code):
    """Deactivate an invite code (existing participants keep access)."""
    conn = narrate_db.connect()
    try:
        n = narrate_db.set_invite_active(conn, code, False)
    finally:
        conn.close()
    click.echo("deactivated" if n else "no such code")


@bp.cli.command("verify")
@click.option("--puzzle", default=None, help="One puzzle id (default: all configured)")
@click.option("--runs", type=int, default=None, help="Runs per puzzle (default: narrate.reference_runs)")
def cli_verify(puzzle, runs):
    """Run reference narratives through the participant pipeline and record results."""
    runs = runs or int(settings()["reference_runs"])
    ids = [puzzle] if puzzle else [p["puzzle_id"] for p in settings()["puzzles"]]
    for pid in ids:
        solved, completed = run_reference_checks(pid, runs, log_fn=click.echo)
        click.echo(f"{pid}: {solved}/{completed} solved"
                   + ("" if solved else "  -> hidden from participants until a run solves it"))


@bp.cli.command("export")
@click.argument("path")
def cli_export(path):
    """Write every Narrate table to a JSON file."""
    conn = narrate_db.connect()
    try:
        payload = export_payload(conn)
    finally:
        conn.close()
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    click.echo(f"wrote {len(payload['attempts'])} attempts to {path}")
