"""Prototype: generate NARC puzzles from the Narrative Hierarchy Model simulator, review and
edit them, rebuild the certificate, and test them on one model, all on a separate database
(proto_data/proto.db, see proto_db.py). Owner/reviewer only.

The simulator is `nhmsim` from ../nhm (the sibling repository). It is imported from
NHM_PATH, else from ../nhm when present (local development), else from ./nhm_vendor
(the copy committed to this repository for production; refresh it with ./sync_nhm.sh).

The loop (nhm/DESIGN.md §2): direct the knobs → generate a run → read the run page (story,
masked strip, answer, one-step alternatives, event chain, every text surface) → set a verdict,
edit the story, add notes → for grid changes edit the event chain on the puzzle page and
rebuild → run the three-condition battery on a model per text surface → export the kept
puzzles as narc JSON.
"""

import json
import os
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import (Blueprint, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

import collect
import db
import proto_db

# --- locate nhmsim -------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent


def _nhm_root():
    cands = [os.environ.get("NHM_PATH"), str(_HERE.parent / "nhm"), str(_HERE / "nhm_vendor")]
    for c in cands:
        if c and (Path(c) / "nhmsim" / "__init__.py").exists():
            return Path(c)
    return None


NHM_ROOT = _nhm_root()
if NHM_ROOT is not None and str(NHM_ROOT) not in sys.path:
    sys.path.insert(0, str(NHM_ROOT))

try:
    import nhmsim  # noqa: E402
    from nhmsim import certify, gallery, grammar  # noqa: E402
    from nhmsim import symmetries as S  # noqa: E402
    from nhmsim.conventions import PRESETS  # noqa: E402
    from nhmsim.conventions import get as get_convention  # noqa: E402
    NHM_AVAILABLE = True
    NHM_IMPORT_ERROR = None
except Exception as e:  # pragma: no cover
    NHM_AVAILABLE = False
    NHM_IMPORT_ERROR = repr(e)

bp = Blueprint("proto", __name__, url_prefix="/proto")

MAX_N = 60
MASKS = ["any", "last", "middle", "first", "all"]
SYMS = ["identity", "pal", "mirror", "vflip", "shift", "pal+mirror", "pal+mirror+vflip"]
CONDITIONS = ["grids_only", "narrative_only", "both"]


# --- auth (same rule as the Admin tab; no import from server.py to avoid a cycle) ---------

def _staff():
    if "proto_staff" not in g:
        g.proto_staff = None
        uid = session.get("user_id")
        if uid:
            conn = db.init_db()
            try:
                row = db.get_user_by_id(conn, uid)
            finally:
                conn.close()
            if row and row["role"] in ("owner", "reviewer"):
                g.proto_staff = dict(row)
    return g.proto_staff


@bp.before_request
def _gate():
    if _staff() is None:
        if request.is_json or request.path.endswith(".json"):
            return jsonify({"error": "Unauthorized"}), 403
        flash("The Prototype tab is for owners and reviewers.", "warning")
        return redirect(url_for("login"))
    if not NHM_AVAILABLE:
        return Response(f"nhmsim is not importable ({NHM_IMPORT_ERROR}); set NHM_PATH or run sync_nhm.sh", 500)


# --- helpers ---------------------------------------------------------------------------------

def _conn():
    return proto_db.init_db()


def _knobs():
    return {
        "grammars": [(n, grammar.load(n)) for n in grammar.available()],
        "conventions": PRESETS,
        "syms": SYMS,
        "masks": MASKS,
        "models": [m["name"] for m in collect.load_config()["models"] if m.get("role") == "subject"],
    }


def _grids_of(pz):
    k = pz["masked_positions"][0]
    return [it["grid"] if it["grid"] is not None else pz["answer_grids"][str(k)] for it in pz["sequence"]], k


def prepare(pz, row=None):
    """Everything a template needs to draw one puzzle: SVG strips, chain, alternatives, texts."""
    m = pz["metadata"]
    grids, k = _grids_of(pz)
    labels = ["setup"] + [c["label"] if c["atomic"] else "…" + c["label"] for c in m["event_chain"]]
    frames = [{"svg": gallery.svg_grid(g, masked=(i == k)), "label": labels[i] if i < len(labels) else str(i), "masked": i == k}
              for i, g in enumerate(grids)]
    answer = {"svg": gallery.svg_grid(grids[k]), "label": labels[k] if k < len(labels) else str(k)}
    alts = [{"svg": gallery.svg_grid(a["grid"]), "event": a["event"]} for a in m.get("alternatives", {}).get(str(k), [])]
    texts = [{"variant": "story", "text": pz["narrative"]}] + \
            [{"variant": v["variant"], "text": v["narrative"]} for v in pz.get("narrative_variants", [])]
    ma = m["mask_analysis"]
    conv = m["convention"]
    return {
        "id": pz["puzzle_id"], "title": pz["title"], "story": pz["narrative"], "k": k, "n": len(grids),
        "frames": frames, "answer": answer, "alts": alts, "texts": texts,
        "chain": m["event_chain"], "tree": m.get("tree", []), "story_type": m.get("story_type"),
        "features": m.get("story_features", []), "m": m.get("m"),
        "mask": ma, "other_masks": m.get("all_mask_analyses", []),
        "conv": conv, "conv_text": ", ".join(f"{c}={conv[c]}" for c in ("mood", "location", "held", "want", "time")),
        "sym": m["symmetry"]["name"], "sym_text": m.get("symmetry_text", ""),
        "conventions_text": m.get("conventions_text", ""), "tags": m.get("tags", []),
        "events_json": json.dumps(m.get("events", []), indent=1),
        "verdict": (row or {}).get("verdict", ""), "notes": (row or {}).get("notes", ""),
        "updated_at": (row or {}).get("updated_at"),
    }


def _generate(cfg, created_by):
    g_ = grammar.load(cfg["grammar"])
    convs = [get_convention(p) for p in cfg["phi"]]
    rng = random.Random(cfg["seed"])
    puzzles, reasons, produced, i = [], {c.name: {} for c in convs}, 0, 0
    while produced < cfg["n"] and i < cfg["n"] * 20:
        i += 1
        smp = grammar.sample(g_, rng, m=cfg["m"], story_type=cfg.get("story_type") or None)
        sym = S.sample(cfg["sym"], rng)
        any_ok = False
        for conv in convs:
            pid = f"p{cfg['seed']}-{i:04d}" + (f"-{conv.name}" if len(convs) > 1 else "")
            pzs, why = certify.build_puzzles(smp, conv, cfg["sym"], rng, mask=cfg["mask"],
                                             min_alternatives=cfg["min_alternatives"],
                                             require_self_taught=cfg["self_teaching"], store_alts=8,
                                             puzzle_id=pid, sym=sym)
            reasons[conv.name][why] = reasons[conv.name].get(why, 0) + 1
            if pzs:
                any_ok = True
                puzzles.extend(pzs)
        if any_ok:
            produced += 1
    summary = {"samples_tried": i, "puzzles": len(puzzles), "reasons": reasons,
               "depth": _count(p["metadata"]["mask_analysis"]["depth"] for p in puzzles),
               "masked_event": _count(p["metadata"]["mask_analysis"]["event"].split("(")[0] for p in puzzles),
               "story_types": _count(p["metadata"]["story_type"] for p in puzzles)}
    return puzzles, summary


def _count(it):
    out = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# --- background trials ------------------------------------------------------------------------

_executor = None
_executor_lock = threading.Lock()


def _pool():
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="proto")
    return _executor


def _run_trial_bg(trial_id, model_name, condition, puzzle_view, narrative):
    conn = _conn()
    try:
        proto_db.set_trial_status(conn, trial_id, "running")
        cfg = collect.load_config()
        model_cfg = collect.get_model_config(cfg, model_name)
        try:
            extraction_cfg = collect.get_model_config(cfg, "gpt-oss-120b-extract")
        except ValueError:
            extraction_cfg = model_cfg
        res = collect.run_trial(model_cfg, extraction_cfg, {"trial_id": trial_id, "condition": condition},
                                puzzle_view, variant_narrative=narrative)
        if len(res) == 6:                      # call failed
            _, _, _, _, err, latency = res
            proto_db.finish_trial(conn, trial_id, None, None, None, None, None, None, err, latency)
            return
        _, raw1, text2, reasoning, parse_error, latency, predicted = res
        if predicted is None:
            proto_db.finish_trial(conn, trial_id, raw1, text2, reasoning, None, 0, 0.0,
                                  parse_error or "no grid parsed", latency)
            return
        pred_mapped, correct, acc = collect.grade_prediction(puzzle_view, predicted)
        proto_db.finish_trial(conn, trial_id, raw1, text2, reasoning, pred_mapped, correct, acc, None, latency)
    except Exception as e:  # pragma: no cover
        proto_db.finish_trial(conn, trial_id, None, None, None, None, None, None, repr(e), 0)
    finally:
        conn.close()


# --- pages ------------------------------------------------------------------------------------

@bp.route("/")
def index():
    conn = _conn()
    try:
        runs = proto_db.get_runs(conn)
    finally:
        conn.close()
    for r in runs:
        r["config"] = json.loads(r["config_json"])
    return render_template("proto_index.html", runs=runs, knobs=_knobs(), nhm_root=str(NHM_ROOT),
                           nhm_version=getattr(nhmsim, "__version__", "?"), max_n=MAX_N)


@bp.route("/generate", methods=["POST"])
def generate():
    f = request.form
    try:
        cfg = {
            "grammar": f["grammar"],
            "phi": [p.strip() for p in f.get("phi", "baseline").split(",") if p.strip()],
            "sym": f.get("sym", "identity"),
            "mask": f.get("mask", "any"),
            "n": max(1, min(MAX_N, int(f.get("n", 20)))),
            "seed": int(f.get("seed", 1)),
            "m": max(1, min(3, int(f.get("m", 3)))),
            "story_type": f.get("story_type", "").strip(),
            "min_alternatives": max(1, int(f.get("min_alternatives", 2))),
            "self_teaching": f.get("self_teaching", "on") == "on",
        }
        for p in cfg["phi"]:
            get_convention(p)
        S.parse(cfg["sym"])
        if cfg["mask"] not in MASKS:
            raise ValueError("mask")
    except (KeyError, ValueError) as e:
        flash(f"Bad knob: {e}", "danger")
        return redirect(url_for("proto.index"))
    name = f.get("name", "").strip() or f"{cfg['grammar']} · {','.join(cfg['phi'])} · {cfg['sym']} · {cfg['mask']} · seed {cfg['seed']}"
    puzzles, summary = _generate(cfg, _staff()["username"])
    conn = _conn()
    try:
        run_id = proto_db.insert_run(conn, name, cfg, summary, getattr(nhmsim, "__version__", "?"), _staff()["username"])
        for p in puzzles:
            p["puzzle_id"] = f"r{run_id}-{p['puzzle_id']}"
            proto_db.insert_puzzle(conn, run_id, p)
        conn.commit()
    finally:
        conn.close()
    flash(f"Generated {len(puzzles)} puzzles from {summary['samples_tried']} samples.", "success")
    return redirect(url_for("proto.run", run_id=run_id))


@bp.route("/run/<int:run_id>")
def run(run_id):
    verdict = request.args.get("verdict")
    conn = _conn()
    try:
        r = proto_db.get_run(conn, run_id)
        if not r:
            abort(404)
        rows = proto_db.get_puzzles(conn, run_id, verdict=verdict if verdict in ("", "keep", "fix", "drop") else None)
    finally:
        conn.close()
    r["config"] = json.loads(r["config_json"])
    r["summary"] = json.loads(r["summary_json"] or "{}")
    items = [prepare(json.loads(row["puzzle_json"]), row) for row in rows]
    return render_template("proto_run.html", run=r, items=items, verdict=verdict, knobs=_knobs())


@bp.route("/run/<int:run_id>/delete", methods=["POST"])
def run_delete(run_id):
    conn = _conn()
    try:
        proto_db.delete_run(conn, run_id)
    finally:
        conn.close()
    flash(f"Run {run_id} deleted.", "info")
    return redirect(url_for("proto.index"))


@bp.route("/run/<int:run_id>/export.json")
def run_export(run_id):
    only = request.args.get("verdict")
    conn = _conn()
    try:
        rows = proto_db.get_puzzles(conn, run_id, verdict=only if only in ("keep", "fix", "drop", "") else None)
    finally:
        conn.close()
    out = []
    for row in rows:
        pz = json.loads(row["puzzle_json"])
        pz["metadata"]["review"] = {"verdict": row["verdict"], "notes": row["notes"]}
        out.append(pz)
    return Response(json.dumps(out, indent=1), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=proto_run{run_id}.json"})


@bp.route("/puzzle/<puzzle_id>")
def puzzle(puzzle_id):
    conn = _conn()
    try:
        row = proto_db.get_puzzle(conn, puzzle_id)
        if not row:
            abort(404)
        trials = proto_db.get_trials(conn, puzzle_id)
        r = proto_db.get_run(conn, row["run_id"])
    finally:
        conn.close()
    pz = json.loads(row["puzzle_json"])
    item = prepare(pz, row)
    cells = proto_db.verdicts_by_cell(trials)
    cell_rows = [{"model": k[0], "surface": k[1], **v} for k, v in sorted(cells.items())]
    return render_template("proto_puzzle.html", item=item, run=r, trials=trials, cells=cell_rows,
                           knobs=_knobs(), surfaces=[t["variant"] for t in item["texts"]])


@bp.route("/puzzle/<puzzle_id>.json")
def puzzle_json(puzzle_id):
    conn = _conn()
    try:
        row = proto_db.get_puzzle(conn, puzzle_id)
    finally:
        conn.close()
    if not row:
        abort(404)
    pz = json.loads(row["puzzle_json"])
    pz["metadata"]["review"] = {"verdict": row["verdict"], "notes": row["notes"]}
    return jsonify(pz)


@bp.route("/puzzle/<puzzle_id>/review", methods=["POST"])
def review(puzzle_id):
    data = request.get_json(silent=True) or request.form
    verdict = data.get("verdict")
    notes = data.get("notes")
    narrative = data.get("narrative")
    if verdict is not None and verdict not in ("", "keep", "fix", "drop"):
        return jsonify({"error": "bad verdict"}), 400
    conn = _conn()
    try:
        row = proto_db.get_puzzle(conn, puzzle_id)
        if not row:
            abort(404)
        proto_db.update_review(conn, puzzle_id, verdict, notes)
        if narrative is not None:
            pz = json.loads(row["puzzle_json"])
            if narrative.strip() and narrative != pz["narrative"]:
                if not any(v["variant"] == "story_original" for v in pz.get("narrative_variants", [])):
                    pz.setdefault("narrative_variants", []).append(
                        {"variant": "story_original", "narrative": pz["narrative"], "generator": "nhm-sim"})
                pz["narrative"] = narrative
                proto_db.update_puzzle_json(conn, puzzle_id, pz)
    finally:
        conn.close()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Saved.", "success")
    return redirect(request.referrer or url_for("proto.puzzle", puzzle_id=puzzle_id))


@bp.route("/puzzle/<puzzle_id>/rebuild", methods=["POST"])
def rebuild(puzzle_id):
    """Rebuild from the (possibly edited) event chain; optionally under another code or mask."""
    f = request.form
    conn = _conn()
    try:
        row = proto_db.get_puzzle(conn, puzzle_id)
        if not row:
            abort(404)
        pz = json.loads(row["puzzle_json"])
        if f.get("events", "").strip():
            try:
                events = json.loads(f["events"])
                assert isinstance(events, list) and all(isinstance(e, dict) and "prim" in e for e in events)
            except Exception as e:
                flash(f"Events must be a JSON list of event objects: {e}", "danger")
                return redirect(url_for("proto.puzzle", puzzle_id=puzzle_id))
            pz["metadata"]["events"] = events
        conv = get_convention(f["phi"]) if f.get("phi") else None
        mask = f.get("mask") or None
        try:
            pzs, why = certify.rebuild(pz, conv=conv, mask=mask if mask in ("last", "middle", "first", "any") else None,
                                       require_self_taught=f.get("self_teaching", "on") == "on")
        except Exception as e:
            flash(f"Rebuild failed: {e}", "danger")
            return redirect(url_for("proto.puzzle", puzzle_id=puzzle_id))
        if not pzs:
            flash(f"The certificate rejected the rebuilt chain: {why}. Nothing saved.", "warning")
            return redirect(url_for("proto.puzzle", puzzle_id=puzzle_id))
        new = pzs[0]
        new["puzzle_id"] = puzzle_id
        new["narrative"] = pz["narrative"]                 # the story is the person's, keep it
        old = pz["metadata"]["mask_analysis"]
        nw = new["metadata"]["mask_analysis"]
        proto_db.update_puzzle_json(conn, puzzle_id, new)
    finally:
        conn.close()
    flash(f"Rebuilt: mask k={nw['position']} ({nw['depth']}), {nw['alternatives']} alternatives, "
          f"{'self-taught' if nw['self_taught'] else 'untaught: ' + ', '.join(nw['missing'])} "
          f"(was k={old['position']}, {old['alternatives']} alternatives).", "success")
    return redirect(url_for("proto.puzzle", puzzle_id=puzzle_id))


@bp.route("/puzzle/<puzzle_id>/test", methods=["POST"])
def test(puzzle_id):
    """Queue the three-condition battery on one model for one or all text surfaces."""
    f = request.form
    model_name = f.get("model")
    surface = f.get("surface", "story")
    if model_name not in _knobs()["models"]:
        flash("Unknown model.", "danger")
        return redirect(url_for("proto.puzzle", puzzle_id=puzzle_id))
    conn = _conn()
    try:
        row = proto_db.get_puzzle(conn, puzzle_id)
        if not row:
            abort(404)
        pz = json.loads(row["puzzle_json"])
        texts = {"story": pz["narrative"]}
        texts.update({v["variant"]: v["narrative"] for v in pz.get("narrative_variants", []) if v["variant"] != "story_original"})
        surfaces = list(texts) if surface == "all" else [surface]
        if any(s not in texts for s in surfaces):
            flash("Unknown surface.", "danger")
            return redirect(url_for("proto.puzzle", puzzle_id=puzzle_id))
        view = {"puzzle_id": pz["puzzle_id"], "narrative": pz["narrative"], "sequence": pz["sequence"],
                "masked_positions": pz["masked_positions"], "answer_grids": pz["answer_grids"]}
        queued = 0
        for s in surfaces:
            for cond in CONDITIONS:
                if cond == "grids_only" and s != surfaces[0]:
                    continue                        # grids alone does not depend on the surface
                tid = proto_db.insert_trial(conn, puzzle_id, model_name, s if cond != "grids_only" else "-", cond,
                                            None if cond == "grids_only" else texts[s])
                _pool().submit(_run_trial_bg, tid, model_name, cond, view, None if cond == "grids_only" else texts[s])
                queued += 1
    finally:
        conn.close()
    flash(f"Queued {queued} trials on {model_name}; this page refreshes itself.", "info")
    return redirect(url_for("proto.puzzle", puzzle_id=puzzle_id))


@bp.route("/puzzle/<puzzle_id>/trials.json")
def trials_json(puzzle_id):
    conn = _conn()
    try:
        trials = proto_db.get_trials(conn, puzzle_id)
    finally:
        conn.close()
    cells = proto_db.verdicts_by_cell(trials)
    return jsonify({"trials": [{k: v for k, v in t.items() if k not in ("raw_response",)} for t in trials],
                    "cells": [{"model": k[0], "surface": k[1], **v} for k, v in sorted(cells.items())],
                    "pending": sum(1 for t in trials if t["status"] in ("queued", "running"))})


@bp.route("/puzzle/<puzzle_id>/delete", methods=["POST"])
def puzzle_delete(puzzle_id):
    conn = _conn()
    try:
        row = proto_db.get_puzzle(conn, puzzle_id)
        if not row:
            abort(404)
        proto_db.delete_puzzle(conn, puzzle_id)
    finally:
        conn.close()
    return redirect(url_for("proto.run", run_id=row["run_id"]))
