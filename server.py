"""Flask server for NARC puzzle creation and solving."""

import csv
import io
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import click
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db
import grids
from db import get_variants, get_trials
from collect import run_collect_job, run_matrix_job, run_sensitivity_job
from classify import run_classify_job
from ratelimit import mindrouter_bucket

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data" / "puzzles"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Secret key for sessions
_secret = os.environ.get("NARC_SECRET_KEY", "dev-secret-change-in-prod")
if _secret == "dev-secret-change-in-prod":
    print("WARNING: Using default secret key. Set NARC_SECRET_KEY in production.")
app.secret_key = _secret

MODELS = ["gpt-oss-120b", "gpt-oss-20b", "qwen3.5-122b", "qwen3.6-27b",
          "nemotron-3-super", "gemma-4-26b", "gemma-4-31b"]
REVIEW_MODELS = MODELS


def get_conn():
    return db.init_db()


# --- Auth helpers ---

def current_user():
    """Get the logged-in user, cached in flask.g. Returns dict or None."""
    if "user" not in g:
        uid = session.get("user_id")
        if uid:
            conn = get_conn()
            row = db.get_user_by_id(conn, uid)
            conn.close()
            g.user = dict(row) if row else None
        else:
            g.user = None
    return g.user


def require_role(*roles):
    """Decorator: require logged-in user with one of the specified roles."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or user["role"] not in roles:
                if request.is_json:
                    return jsonify({"error": "Unauthorized"}), 403
                flash("You don't have permission to access this page.", "danger")
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


def is_admin():
    """Check if current user is owner or reviewer."""
    user = current_user()
    return user is not None and user["role"] in ("owner", "reviewer")


@app.context_processor
def inject_user():
    """Make current_user available in all templates."""
    return {"user": current_user()}


def enrich_puzzle(conn, pdata, trial_map=None):
    """Add is_draft, is_featured, variants, and variant_count to a puzzle dict.
    The trial_map argument is unused (kept for call-site compatibility) since
    lifecycle now lives on puzzles.status.
    """
    pid = pdata["puzzle_id"]
    variants = []
    for v in get_variants(conn, pid):
        variants.append({
            "variant": v["variant"],
            "source_domain": v["source_domain"],
            "narrative": v["narrative"],
        })
    pdata["variants"] = variants
    pdata["variant_count"] = len(variants)
    status = pdata.get("status") or "draft"
    pdata["is_draft"] = (status == "draft")
    pdata["is_featured"] = (status == "featured")
    return pdata


def normalize_puzzle_input(data):
    """Accept both old format (masked_position/answer_grid) and new format
    (masked_positions/answer_grids). Returns (masked_positions, answer_grids)
    as JSON strings."""
    # New format
    if "masked_positions" in data and "answer_grids" in data:
        return json.dumps(data["masked_positions"]), json.dumps(data["answer_grids"])

    # Old format — convert
    mp = data.get("masked_position")
    ag = data.get("answer_grid")
    if mp is not None and ag is not None:
        return json.dumps([mp]), json.dumps({str(mp): ag})

    return None, None


def validate_puzzle_geometry(sequence, masked_positions, answer_grids):
    """Return an error string if masked_positions / answer_grids are inconsistent
    with the sequence, else None. Guards against stale out-of-range masked
    positions (e.g. from shrinking the sequence after masking a later grid),
    which otherwise crash collect with a cryptic 'list index out of range'."""
    if not isinstance(sequence, list) or not sequence:
        return "Sequence must be a non-empty list of grids"
    if not isinstance(masked_positions, list) or not masked_positions:
        return "masked_positions must be a non-empty list"
    n = len(sequence)
    for p in masked_positions:
        if not isinstance(p, int) or p < 0 or p >= n:
            return (f"Masked position {p} out of range for {n}-grid sequence "
                    f"(valid 0-{n - 1})")
    if isinstance(answer_grids, dict):
        missing = [p for p in masked_positions if str(p) not in answer_grids]
        if missing:
            return f"answer_grids missing entries for masked positions {missing}"
    return None


# --- Pages ---

@app.route("/")
def index():
    return redirect(url_for("about"))


@app.route("/about")
def about():
    conn = get_conn()
    puzzles = db.get_all_puzzles(conn)
    variant_count = conn.execute("SELECT COUNT(*) as c FROM narrative_variants").fetchone()["c"]
    grid_sizes = set()
    active_count = 0
    draft_count = 0
    for p in puzzles:
        pdata = db.puzzle_to_json(p)
        for item in pdata["sequence"]:
            grid_sizes.add((item["rows"], item["cols"]))
        if pdata.get("status") == "draft":
            draft_count += 1
        else:
            active_count += 1
    conn.close()
    stats = {
        "total_puzzles": len(puzzles),
        "active_puzzles": active_count,
        "draft_puzzles": draft_count,
        "total_variants": variant_count,
        "grid_sizes": len(grid_sizes),
    }
    return render_template("about.html", stats=stats)


@app.route("/inspect")
def inspect():
    tab = request.args.get("tab", "masking")
    conn = get_conn()

    # Get all models that have been tested
    models = [r[0] for r in conn.execute(
        "SELECT DISTINCT model_name FROM trials ORDER BY model_name"
    ).fetchall()]

    if tab == "masking":
        data = _inspect_masking(conn, models)
    elif tab == "ordering":
        data = _inspect_ordering(conn)
    elif tab == "stances":
        data = _inspect_stances(conn)
    elif tab == "oddoneout":
        data = _inspect_oddoneout(conn)
    else:
        data = {}

    conn.close()
    return render_template("inspect.html", tab=tab, models=models, **data)


def _inspect_masking(conn, models):
    """Build masking tab data: per-puzzle classification results."""
    rows = conn.execute(
        """SELECT c.puzzle_id, c.model_name, c.grids_only, c.narrative_only,
                  c.both, c.has_narc, c.narc_strength, c.shuffle_solved,
                  c.shuffle_total, c.variant_id, c.mask_variant_id,
                  nv.variant AS variant_label, mv.label AS mask_label
           FROM classifications c
           LEFT JOIN narrative_variants nv ON nv.variant_id = c.variant_id
           LEFT JOIN mask_variants mv ON mv.mask_variant_id = c.mask_variant_id
           ORDER BY c.puzzle_id, c.model_name"""
    ).fetchall()
    # Three views over the same rows:
    #   cls_map  — puzzle -> model -> best cell (drives dots, filters, summary)
    #   orig_map — puzzle -> model -> the original x original cell only
    #   cell_map — puzzle -> (narrative label, mask label) -> model -> result
    # A NARC cell also folds in the order-sensitivity verdict when tested.
    cls_map = {}
    orig_map = {}
    cell_map = {}
    # Prefer a NARC row over a non-NARC one; among NARC rows prefer the one
    # with a strength verdict (strong > partial > weak > untested). Applied to
    # all three maps so the header dots, the original table, and the variant
    # drill-down never contradict each other (duplicate original x original
    # rows exist where a legacy matrix run repeated the base protocol).
    _rank = {"strong": 3, "partial": 2, "weak": 1, None: 0}

    def _put_best(slot, key, cand):
        cur = slot.get(key)
        better = (cand["has_narc"] or 0, _rank.get(cand["narc_strength"], 0))
        current = (cur["has_narc"] or 0,
                   _rank.get(cur["narc_strength"], 0)) if cur else (-1, -1)
        if better > current:
            slot[key] = cand

    for r in rows:
        # Base-protocol rows (collect.py) carry variant_id NULL; matrix rows
        # for the same cell carry the 'original' narrative variant's id.
        vlabel = r["variant_label"] or "original"
        mlabel = r["mask_label"] or "original"
        cand = {
            "grids_only": r["grids_only"],
            "narrative_only": r["narrative_only"],
            "both": r["both"],
            "has_narc": r["has_narc"],
            "narc_strength": r["narc_strength"],
            "shuffle_solved": r["shuffle_solved"],
            "shuffle_total": r["shuffle_total"],
        }
        # Collapse per-(variant,mask) rows to one per (puzzle, model): a puzzle
        # is NARC for a model if ANY cell is; keep the strongest verdict seen.
        _put_best(cls_map.setdefault(r["puzzle_id"], {}), r["model_name"], cand)
        if vlabel == "original" and mlabel == "original":
            _put_best(orig_map.setdefault(r["puzzle_id"], {}),
                      r["model_name"], cand)
        _put_best(cell_map.setdefault(r["puzzle_id"], {}).setdefault(
            (vlabel, mlabel), {}), r["model_name"], cand)

    # Get puzzle info
    puzzles = []
    for p in conn.execute(
        "SELECT * FROM puzzles ORDER BY puzzle_id"
    ).fetchall():
        pid = p["puzzle_id"]
        if pid not in cls_map:
            continue
        pdata = db.puzzle_to_json(p)
        pdata["results"] = cls_map[pid]
        pdata["original_results"] = orig_map.get(pid, {})
        pdata["variant_cells"] = [
            {"variant": v, "mask": m, "results": cell_map[pid][(v, m)]}
            for (v, m) in sorted(cell_map.get(pid, {}),
                                 key=lambda k: (k != ("original", "original"), k))
        ]
        # Compute status per model
        statuses = set()
        narc_count = 0
        for model, res in cls_map[pid].items():
            if res["has_narc"]:
                statuses.add("narc")
                if res.get("narc_strength"):
                    statuses.add("narc_" + res["narc_strength"])
                narc_count += 1
            elif res["grids_only"]:
                statuses.add("grids_sufficient")
            elif res["narrative_only"]:
                statuses.add("narrative_sufficient")
            else:
                statuses.add("unsolvable")
        pdata["statuses"] = list(statuses)
        pdata["narc_count"] = narc_count
        puzzles.append(pdata)

    # Summary stats
    summary = {}
    for m in models:
        summary[m] = sum(1 for p in puzzles
                         if p["results"].get(m, {}).get("has_narc"))

    # Pick 3 highlights: highest NARC count, skip drafts and stance puzzles
    highlights = []
    seen_creators = set()
    for p in sorted(puzzles, key=lambda x: -x["narc_count"]):
        if len(highlights) >= 3:
            break
        if p.get("stance"):
            continue
        if p.get("status") == "draft":
            continue
        creator = p.get("creator", "claude")
        if creator not in seen_creators or len(highlights) < 3:
            highlights.append(p)
            seen_creators.add(creator)
    return {"puzzles": puzzles, "summary": summary, "highlights": highlights}


def _inspect_ordering(conn):
    """Build ordering tab data: per-puzzle tau scores."""
    models = [r[0] for r in conn.execute(
        "SELECT DISTINCT model_name FROM ordering_trials ORDER BY model_name"
    ).fetchall()]

    rows = conn.execute(
        """SELECT puzzle_id, model_name, condition,
                  AVG(kendall_tau) as avg_tau, COUNT(*) as n
           FROM ordering_trials
           WHERE kendall_tau IS NOT NULL
           GROUP BY puzzle_id, model_name, condition"""
    ).fetchall()

    # Build puzzle -> model -> condition -> tau
    tau_map = {}
    for r in rows:
        tau_map.setdefault(r["puzzle_id"], {}).setdefault(
            r["model_name"], {}
        )[r["condition"]] = round(r["avg_tau"], 3)

    puzzles = []
    for p in conn.execute(
        "SELECT * FROM puzzles ORDER BY puzzle_id"
    ).fetchall():
        pid = p["puzzle_id"]
        if pid not in tau_map:
            continue
        pdata = db.puzzle_to_json(p)
        pdata["tau_results"] = tau_map[pid]
        # Compute narrative lift per model
        lifts = {}
        for model, conds in tau_map[pid].items():
            go = conds.get("grids_only", 0)
            gn = conds.get("grids_and_narrative", 0)
            lifts[model] = round(gn - go, 3)
        pdata["lifts"] = lifts
        pdata["avg_lift"] = round(
            sum(lifts.values()) / len(lifts), 3
        ) if lifts else 0
        puzzles.append(pdata)

    # Sort by avg lift descending
    puzzles.sort(key=lambda p: -p["avg_lift"])

    summary = {}
    for m in models:
        taus = [p["tau_results"].get(m, {}).get("grids_and_narrative", 0)
                for p in puzzles if m in p["tau_results"]]
        summary[m] = round(sum(taus) / len(taus), 3) if taus else 0

    # Top 3 highlights by lift
    highlights = puzzles[:3] if len(puzzles) >= 3 else puzzles

    return {"puzzles": puzzles, "ordering_models": models,
            "ordering_summary": summary, "highlights": highlights}


def _inspect_stances(conn):
    """Build stances tab data: grouped by stance_group, using narrative variants."""
    rows = conn.execute(
        """SELECT p.puzzle_id, p.stance_group, p.title, p.sequence_json,
                  p.masked_positions, p.answer_grids, p.creator, p.difficulty,
                  p.human_difficulty, p.ai_difficulty, p.tags, p.created_at,
                  nv.variant, nv.source_domain, nv.narrative as variant_narrative,
                  nv.variant_id,
                  c.model_name, c.grids_only, c.narrative_only,
                  c.both, c.has_narc
           FROM puzzles p
           JOIN narrative_variants nv ON p.puzzle_id = nv.puzzle_id
           LEFT JOIN classifications c ON p.puzzle_id = c.puzzle_id
               AND c.variant_id = nv.variant_id
           WHERE p.stance_group IS NOT NULL
             AND nv.source_domain LIKE 'stance:%'
           ORDER BY p.stance_group, nv.source_domain"""
    ).fetchall()

    models = sorted(set(r["model_name"] for r in rows if r["model_name"]))

    # Group by stance_group, then by stance (from source_domain)
    groups = {}
    for r in rows:
        group = r["stance_group"]
        # Extract stance name from source_domain (e.g., "stance:intentional" -> "intentional")
        stance = r["source_domain"].replace("stance:", "") if r["source_domain"] else r["variant"]
        if group not in groups:
            groups[group] = {"name": group, "stances": {}}
        if stance not in groups[group]["stances"]:
            pdata = {
                "puzzle_id": r["puzzle_id"],
                "title": r["title"],
                "narrative": r["variant_narrative"],
                "sequence": json.loads(r["sequence_json"]),
                "masked_positions": json.loads(r["masked_positions"]),
                "answer_grids": json.loads(r["answer_grids"]),
                "creator": r["creator"],
                "difficulty": r["difficulty"],
                "human_difficulty": r["human_difficulty"],
                "ai_difficulty": r["ai_difficulty"],
                "tags": r["tags"],
                "created_at": r["created_at"],
                "stance": stance,
                "results": {},
            }
            groups[group]["stances"][stance] = pdata
        if r["model_name"]:
            groups[group]["stances"][stance]["results"][r["model_name"]] = {
                "has_narc": r["has_narc"],
                "grids_only": r["grids_only"],
                "narrative_only": r["narrative_only"],
                "both": r["both"],
            }

    # Compute NARC counts per stance per group
    for g in groups.values():
        for stance, pdata in g["stances"].items():
            pdata["narc_count"] = sum(
                1 for r in pdata["results"].values() if r.get("has_narc")
            )

    # Sort groups by name
    group_list = sorted(groups.values(), key=lambda g: g["name"])

    # Top 3 highlights: biggest stance-spread, filtered to groups where at
    # least intentional or moral produced at least one NARC success; ensure
    # at least one highlight includes the moral stance.
    def stance_spread(g):
        counts = [s.get("narc_count", 0) for s in g["stances"].values()]
        return max(counts) - min(counts) if counts else 0

    def has_intentional_or_moral_success(g):
        for s in ("intentional", "moral"):
            if g["stances"].get(s, {}).get("narc_count", 0) >= 1:
                return True
        return False

    eligible = [g for g in group_list if has_intentional_or_moral_success(g)]
    ranked = sorted(eligible, key=stance_spread, reverse=True)
    moral_groups = [g for g in ranked if "moral" in g["stances"]]

    stance_highlights = ranked[:3]
    if moral_groups and not any("moral" in h["stances"] for h in stance_highlights):
        stance_highlights = stance_highlights[:2] + [moral_groups[0]]

    return {"stance_groups": group_list, "stance_models": models,
            "highlights": stance_highlights}


def _reconstruct_ooo_grids(conn, puzzle_data, distractor_id):
    """Reconstruct the 4-grid arrangement used in an odd-one-out trial."""
    import hashlib
    import random as rng_mod

    pid = puzzle_data["puzzle_id"]
    seed = hashlib.md5(pid.encode()).hexdigest()
    rng = rng_mod.Random(seed)

    # Select 3 puzzle grids (same logic as collect_oddoneout.py)
    seq = puzzle_data["sequence"]
    masked = set(puzzle_data["masked_positions"])
    visible = [item for item in seq if item["position"] not in masked
               and item.get("grid")]
    if len(visible) < 3:
        answer_grids = puzzle_data.get("answer_grids", {})
        for pos in puzzle_data["masked_positions"]:
            ag = answer_grids.get(str(pos))
            if ag:
                visible.append({"position": pos, "grid": ag,
                                "rows": len(ag), "cols": len(ag[0])})
    if len(visible) < 3:
        return None, None

    selected = rng.sample(visible, min(3, len(visible)))
    while len(selected) < 3:
        selected.append(rng.choice(visible))
    puzzle_grids = [item["grid"] for item in selected]

    # Pick distractor (same logic — re-seed rng, find matching grid)
    rng2 = rng_mod.Random(seed)
    all_pids = [r["puzzle_id"] for r in db.get_all_puzzles(conn)]
    candidates = [p for p in all_pids if p != pid]
    rng2.shuffle(candidates)

    ref = visible[0]
    distractor_grid = None
    for cand_pid in candidates[:50]:
        if cand_pid == distractor_id:
            row = db.get_puzzle(conn, cand_pid)
            if row:
                cand = db.puzzle_to_json(row)
                cand_masked = set(cand["masked_positions"])
                for item in cand["sequence"]:
                    if item["position"] not in cand_masked and item.get("grid"):
                        if (item["rows"] == ref["rows"]
                                and item["cols"] == ref["cols"]):
                            distractor_grid = item["grid"]
                            break
                if not distractor_grid:
                    for item in cand["sequence"]:
                        if item["position"] not in cand_masked and item.get("grid"):
                            distractor_grid = item["grid"]
                            break
            break

    if not distractor_grid:
        return None, None

    # Deterministic distractor position
    distractor_pos = rng_mod.Random(seed).randint(0, 3)

    all_grids = list(puzzle_grids)
    all_grids.insert(distractor_pos, distractor_grid)
    return all_grids, distractor_pos


def _inspect_oddoneout(conn):
    """Build odd-one-out tab data: per-puzzle accuracy."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='oddoneout_trials'"
    ).fetchone()
    if not table_exists:
        return {"ooo_puzzles": [], "ooo_models": [], "ooo_summary": {}}

    ooo_models = [r[0] for r in conn.execute(
        "SELECT DISTINCT model_name FROM oddoneout_trials ORDER BY model_name"
    ).fetchall()]

    rows = conn.execute(
        """SELECT puzzle_id, model_name, condition,
                  COUNT(*) as n, SUM(correct) as correct_count
           FROM oddoneout_trials
           WHERE correct IS NOT NULL
           GROUP BY puzzle_id, model_name, condition"""
    ).fetchall()

    if not rows:
        return {"ooo_puzzles": [], "ooo_models": ooo_models, "ooo_summary": {}}

    acc_map = {}
    for r in rows:
        acc_map.setdefault(r["puzzle_id"], {}).setdefault(
            r["model_name"], {}
        )[r["condition"]] = round(r["correct_count"] / r["n"] * 100, 1) if r["n"] else 0

    # Get distractor IDs per puzzle
    distractor_map = {}
    for r in conn.execute(
        "SELECT DISTINCT puzzle_id, distractor_id FROM oddoneout_trials"
    ).fetchall():
        distractor_map[r["puzzle_id"]] = r["distractor_id"]

    puzzles = []
    for p in conn.execute("SELECT * FROM puzzles ORDER BY puzzle_id").fetchall():
        pid = p["puzzle_id"]
        if pid not in acc_map:
            continue
        pdata = db.puzzle_to_json(p)
        pdata["ooo_results"] = acc_map[pid]
        lifts = {}
        for model, conds in acc_map[pid].items():
            go = conds.get("grids_only", 0)
            gn = conds.get("grids_and_narrative", 0)
            lifts[model] = round(gn - go, 1)
        pdata["ooo_lifts"] = lifts
        pdata["avg_ooo_lift"] = round(
            sum(lifts.values()) / len(lifts), 1
        ) if lifts else 0

        # Reconstruct the 4-grid arrangement
        dist_id = distractor_map.get(pid)
        if dist_id:
            ooo_grids, dist_pos = _reconstruct_ooo_grids(conn, pdata, dist_id)
            pdata["ooo_grids"] = ooo_grids
            pdata["ooo_distractor_pos"] = dist_pos
            pdata["ooo_distractor_id"] = dist_id
        puzzles.append(pdata)

    puzzles.sort(key=lambda p: -p["avg_ooo_lift"])

    summary = {}
    for m in ooo_models:
        accs_go = [p["ooo_results"].get(m, {}).get("grids_only", 0)
                   for p in puzzles if m in p["ooo_results"]]
        accs_gn = [p["ooo_results"].get(m, {}).get("grids_and_narrative", 0)
                   for p in puzzles if m in p["ooo_results"]]
        summary[m] = {
            "grids_only": round(sum(accs_go) / len(accs_go), 1) if accs_go else 0,
            "with_narrative": round(sum(accs_gn) / len(accs_gn), 1) if accs_gn else 0,
        }

    # Top 3 highlights by lift (diverse puzzle IDs)
    ooo_highlights = puzzles[:3] if len(puzzles) >= 3 else puzzles

    return {"ooo_puzzles": puzzles, "ooo_models": ooo_models,
            "ooo_summary": summary, "highlights": ooo_highlights}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_conn()
        user = db.get_user_by_username(conn, username)
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["user_id"]
            flash(f"Welcome, {username}.", "success")
            if user["role"] == "collaborator":
                return redirect(url_for("inspect"))
            return redirect(url_for("admin_dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out.", "info")
    return redirect(url_for("about"))


@app.route("/browse")
def browse():
    conn = get_conn()
    rows = db.get_all_puzzles(conn)
    puzzles = [enrich_puzzle(conn, db.puzzle_to_json(r)) for r in rows]

    # Fetch vote counts and solve stats
    vote_counts = db.get_vote_counts(conn)
    solve_stats = db.get_puzzle_solve_stats(conn)
    voter_id = request.cookies.get('narc_voter_id')
    voter_votes = db.get_voter_votes(conn, voter_id) if voter_id else {}
    conn.close()

    # Enrich each puzzle with vote and solve data
    for p in puzzles:
        pid = p["puzzle_id"]
        vc = vote_counts.get(pid, {"up": 0, "down": 0, "net": 0})
        p["vote_net"] = vc["net"]
        p["vote_up"] = vc["up"]
        p["vote_down"] = vc["down"]
        p["my_vote"] = voter_votes.get(pid, 0)
        ss = solve_stats.get(pid, {})
        p["attempt_count"] = ss.get("attempts", 0)
        p["solve_rate"] = ss.get("solve_rate")
        p["narrative_lift"] = ss.get("narrative_lift")

    # Compute grids: tag from actual sequence length (not stored tags)
    for p in puzzles:
        grid_tag = f"grids:{len(p['sequence'])}"
        existing = (p.get("tags") or "")
        # Strip any stored grids: tags, add computed one
        tag_parts = [t.strip() for t in existing.split(",") if t.strip() and not t.strip().startswith("grids:")]
        tag_parts.append(grid_tag)
        p["tags"] = ",".join(tag_parts)

    # Collect tag counts by prefix for filter buttons
    tag_counts = {}
    for p in puzzles:
        tags = (p.get("tags") or "").split(",") if isinstance(p.get("tags"), str) else []
        for t in tags:
            t = t.strip()
            if t:
                tag_counts[t] = tag_counts.get(t, 0) + 1

    def tags_by_prefix(prefix):
        return sorted([t for t in tag_counts if t.startswith(prefix + ":")],
                       key=lambda t: -tag_counts[t])

    # Spectrum tags in logical order: human-forte → ... → ai-forte → domain-dependent
    spectrum_order = [
        "spectrum:human-forte", "spectrum:human-edge", "spectrum:balanced",
        "spectrum:ai-edge", "spectrum:ai-forte", "spectrum:domain-dependent"
    ]
    spectrum_tags = [t for t in spectrum_order if t in tag_counts]

    # Suppress grid variants from browse (parent_puzzle_id set)
    browsable = [p for p in puzzles if not p.get("parent_puzzle_id")]

    # Filter starter puzzles
    starter_puzzles = [p for p in browsable
                       if "collection:starter" in (p.get("tags") or "")]

    return render_template("browse.html", puzzles=browsable,
                           starter_puzzles=starter_puzzles,
                           tag_counts=tag_counts,
                           audience_tags=tags_by_prefix("audience"),
                           arc_tags=tags_by_prefix("arc"),
                           clue_tags=tags_by_prefix("clue"),
                           domain_tags=tags_by_prefix("domain"),
                           grid_tags=sorted([t for t in tag_counts if t.startswith("grids:")],
                                           key=lambda t: int(t.split(":")[1])),
                           spectrum_tags=spectrum_tags)


@app.route("/create")
def create():
    edit_id = request.args.get("edit")
    revise_id = request.args.get("revise")
    puzzle = None
    puzzle_json = "null"
    revise_mode = False
    original_creator = None
    if edit_id or revise_id:
        conn = get_conn()
        row = db.get_puzzle(conn, edit_id or revise_id)
        if row:
            puzzle = enrich_puzzle(conn, db.puzzle_to_json(row))
            original_creator = puzzle.get("creator", "human")
            if revise_id:
                revise_mode = True
                puzzle["puzzle_id"] = puzzle["puzzle_id"] + "_rev"
            puzzle_json = json.dumps(puzzle)
        conn.close()
    return render_template("create.html", puzzle=puzzle, puzzle_json=puzzle_json,
                           revise_mode=revise_mode,
                           original_creator=original_creator)


@app.route("/solve/<puzzle_id>")
def solve(puzzle_id):
    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    if not row:
        conn.close()
        return "Puzzle not found", 404
    puzzle = enrich_puzzle(conn, db.puzzle_to_json(row))

    # Optional ?mask=<mask_variant_id>: solve a different mask over the same grids.
    mask_variants = db.get_mask_variants(conn, puzzle_id)
    puzzle["mask_variants"] = [
        {"mask_variant_id": m["mask_variant_id"], "label": m["label"],
         "masked_positions": json.loads(m["masked_positions"])}
        for m in mask_variants
    ]
    active_mask_id = request.args.get("mask", type=int)
    puzzle["active_mask_id"] = None
    for m in mask_variants:
        if m["mask_variant_id"] == active_mask_id and m["label"] != "original":
            puzzle = grids.remask(puzzle, json.loads(m["masked_positions"]))
            puzzle["active_mask_id"] = active_mask_id
            break
    conn.close()
    return render_template("solve.html", puzzle=puzzle,
                           puzzle_json=json.dumps(puzzle))


# --- API ---

@app.route("/api/puzzles", methods=["GET"])
def api_list_puzzles():
    conn = get_conn()
    rows = db.get_all_puzzles(conn)
    puzzles = [db.puzzle_to_json(r) for r in rows]
    conn.close()
    return jsonify(puzzles)


@app.route("/api/puzzle-ids/next", methods=["GET"])
def api_next_puzzle_id():
    """Suggest the next available puzzle ID for a given prefix (default 'sub')."""
    prefix = request.args.get("prefix", "sub").strip()
    if not prefix.replace("_", "").replace("-", "").isalnum():
        return jsonify({"error": "Invalid prefix"}), 400
    conn = get_conn()
    pid = db.next_available_puzzle_id(conn, prefix=prefix)
    conn.close()
    return jsonify({"puzzle_id": pid, "prefix": prefix})


@app.route("/api/puzzle-ids/check/<puzzle_id>", methods=["GET"])
def api_check_puzzle_id(puzzle_id):
    """Check whether a puzzle ID is already taken (in puzzles or pending submissions)."""
    conn = get_conn()
    exists = db.puzzle_exists(conn, puzzle_id)
    conn.close()
    return jsonify({"puzzle_id": puzzle_id, "exists": exists})


@app.route("/api/puzzles/<puzzle_id>", methods=["GET"])
def api_get_puzzle(puzzle_id):
    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(db.puzzle_to_json(row))


def _save_puzzle_from_data(conn, data):
    """Save a puzzle directly to the database. Used by admin saves and approvals."""
    puzzle_id = data.get("puzzle_id")
    title = data.get("title")
    narrative = data.get("narrative")
    sequence = data.get("sequence")

    masked_positions_json, answer_grids_json = normalize_puzzle_input(data)

    geom_err = validate_puzzle_geometry(
        sequence,
        json.loads(masked_positions_json) if masked_positions_json else None,
        json.loads(answer_grids_json) if answer_grids_json else None,
    )
    if geom_err:
        raise ValueError(geom_err)

    tags = data.get("metadata", {}).get("tags")
    if isinstance(tags, list):
        tags = ",".join(tags)

    meta = data.get("metadata", {})
    db.upsert_puzzle(
        conn, puzzle_id, title, narrative,
        json.dumps(sequence), masked_positions_json, answer_grids_json,
        creator=meta.get("creator", "human"),
        difficulty=meta.get("difficulty"),
        tags=tags,
        human_difficulty=meta.get("human_difficulty"),
        ai_difficulty=meta.get("ai_difficulty"),
    )

    db.upsert_variant(conn, puzzle_id, "original", narrative)

    for v in data.get("variants", []):
        if v.get("variant") and v.get("narrative"):
            db.upsert_variant(conn, puzzle_id, v["variant"], v["narrative"],
                              source_domain=v.get("source_domain") or v.get("variant"))

    # Seed the 'original' mask variant and enable each narrative variant against
    # it, so the test matrix is populated for new/edited puzzles (idempotent).
    orig_mask_id = db.upsert_mask_variant(
        conn, puzzle_id, "original", json.loads(masked_positions_json))
    for v in db.get_variants(conn, puzzle_id):
        db.set_variant_pair(conn, puzzle_id, v["variant_id"], orig_mask_id, enabled=1)

    # User-defined mask variants from the Create page: persist each and enable
    # every narrative variant against it by default (fine-tune matrix in Edit).
    seq_len = len(sequence) if isinstance(sequence, list) else 0
    for mv in data.get("mask_variants", []):
        label = (mv.get("label") or "").strip()
        mpos = mv.get("masked_positions")
        if not label or label == "original" or not isinstance(mpos, list) or not mpos:
            continue
        mpos = sorted({p for p in mpos if isinstance(p, int) and 0 <= p < seq_len})
        if not mpos:
            continue
        mvid = db.upsert_mask_variant(conn, puzzle_id, label, mpos)
        for v in db.get_variants(conn, puzzle_id):
            db.set_variant_pair(conn, puzzle_id, v["variant_id"], mvid, enabled=1)

    # Export JSON
    export_path = DATA_DIR / f"{puzzle_id}.json"
    export_path.write_text(json.dumps(data, indent=2))

    return puzzle_id


@app.route("/api/puzzles", methods=["POST"])
def api_create_puzzle():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    puzzle_id = data.get("puzzle_id")
    title = data.get("title")
    narrative = data.get("narrative")
    sequence = data.get("sequence")

    masked_positions_json, answer_grids_json = normalize_puzzle_input(data)

    if not all([puzzle_id, title, narrative, sequence,
                masked_positions_json, answer_grids_json]):
        return jsonify({"error": "Missing required fields"}), 400

    geom_err = validate_puzzle_geometry(
        sequence, json.loads(masked_positions_json), json.loads(answer_grids_json)
    )
    if geom_err:
        return jsonify({"error": geom_err}), 400

    user = current_user()

    # is_new defaults to true (safer fail-closed). When set explicitly false the
    # caller is editing an existing puzzle and an overwrite is intentional.
    is_new = data.get("is_new", True)

    conn = get_conn()
    if is_new and db.puzzle_exists(conn, puzzle_id):
        conn.close()
        return jsonify({
            "error": f"Puzzle ID '{puzzle_id}' is already taken. Pick a different ID.",
            "code": "duplicate_id"
        }), 409

    if user and user["role"] in ("owner", "reviewer"):
        # Admin: save directly
        _save_puzzle_from_data(conn, data)
        db.log_activity(conn, user["user_id"], "save_puzzle", "puzzle",
                        puzzle_id, f"Saved puzzle '{title}'")
        conn.close()
        return jsonify({"status": "ok", "puzzle_id": puzzle_id})
    else:
        # Visitor: create submission
        sub_type = "revision" if data.get("is_revision") else "new_puzzle"
        sid = db.create_submission(
            conn, sub_type, json.dumps(data),
            target_puzzle_id=data.get("original_puzzle_id"),
            submitter_name=data.get("submitter_name"),
            submitter_email=data.get("submitter_email"),
        )
        db.log_activity(conn, None, "submit_puzzle", "submission",
                        str(sid), f"Visitor submitted '{title}'")
        conn.close()
        return jsonify({"status": "submitted", "submission_id": sid,
                        "puzzle_id": puzzle_id})


@app.route("/api/puzzles/<puzzle_id>/variants", methods=["POST"])
def api_add_variant(puzzle_id):
    data = request.get_json()
    if not data or not data.get("variant") or not data.get("narrative"):
        return jsonify({"error": "Missing variant name or narrative"}), 400

    user = current_user()

    if user and user["role"] in ("owner", "reviewer"):
        conn = get_conn()
        vid = db.upsert_variant(conn, puzzle_id, data["variant"], data["narrative"],
                                source_domain=data.get("source_domain") or data.get("variant"))
        # Enable the new narrative variant against the original mask by default.
        orig_mask_id = db.get_original_mask_variant_id(conn, puzzle_id)
        if vid and orig_mask_id:
            db.set_variant_pair(conn, puzzle_id, vid, orig_mask_id, enabled=1)
        db.log_activity(conn, user["user_id"], "add_variant", "variant",
                        puzzle_id, f"Added variant '{data['variant']}' to {puzzle_id}")
        conn.close()
        return jsonify({"status": "ok"})
    else:
        conn = get_conn()
        payload = {"puzzle_id": puzzle_id, **data}
        sid = db.create_submission(
            conn, "variant", json.dumps(payload),
            target_puzzle_id=puzzle_id,
            submitter_name=data.get("submitter_name"),
            submitter_email=data.get("submitter_email"),
        )
        db.log_activity(conn, None, "submit_variant", "submission",
                        str(sid), f"Visitor submitted variant for {puzzle_id}")
        conn.close()
        return jsonify({"status": "submitted", "submission_id": sid})


# --- mask variants & test matrix ---

@app.route("/api/puzzles/<puzzle_id>/masks", methods=["GET"])
def api_list_masks(puzzle_id):
    conn = get_conn()
    masks = [
        {"mask_variant_id": m["mask_variant_id"], "label": m["label"],
         "masked_positions": json.loads(m["masked_positions"])}
        for m in db.get_mask_variants(conn, puzzle_id)
    ]
    conn.close()
    return jsonify({"masks": masks})


@app.route("/api/puzzles/<puzzle_id>/masks", methods=["POST"])
@require_role("owner", "reviewer")
def api_add_mask(puzzle_id):
    data = request.get_json() or {}
    label = (data.get("label") or "").strip()
    positions = data.get("masked_positions")
    if not label:
        return jsonify({"error": "Missing mask label"}), 400
    if not isinstance(positions, list) or not positions:
        return jsonify({"error": "masked_positions must be a non-empty list"}), 400

    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    if not row:
        conn.close()
        return jsonify({"error": "Puzzle not found"}), 404
    n = len(json.loads(row["sequence_json"]))
    if any(not isinstance(p, int) or p < 0 or p >= n for p in positions):
        conn.close()
        return jsonify({"error": f"masked_positions out of range for {n}-grid sequence"}), 400

    mask_variant_id = db.upsert_mask_variant(conn, puzzle_id, label, sorted(set(positions)))
    user = current_user()
    db.log_activity(conn, user["user_id"], "add_mask_variant", "puzzle", puzzle_id,
                    f"Added mask variant '{label}' ({sorted(set(positions))}) to {puzzle_id}")
    conn.close()
    return jsonify({"status": "ok", "mask_variant_id": mask_variant_id})


@app.route("/api/puzzles/<puzzle_id>/masks/<int:mask_variant_id>", methods=["DELETE"])
@require_role("owner", "reviewer")
def api_delete_mask(puzzle_id, mask_variant_id):
    conn = get_conn()
    m = db.get_mask_variant(conn, mask_variant_id)
    if not m or m["puzzle_id"] != puzzle_id:
        conn.close()
        return jsonify({"error": "Mask variant not found"}), 404
    if m["label"] == "original":
        conn.close()
        return jsonify({"error": "Cannot delete the original mask variant"}), 400
    db.delete_mask_variant(conn, mask_variant_id)
    user = current_user()
    db.log_activity(conn, user["user_id"], "delete_mask_variant", "puzzle", puzzle_id,
                    f"Deleted mask variant '{m['label']}' from {puzzle_id}")
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/puzzles/<puzzle_id>/matrix", methods=["GET"])
def api_get_matrix(puzzle_id):
    """Narrative variants x mask variants, plus which cells are enabled."""
    conn = get_conn()
    narratives = [
        {"variant_id": v["variant_id"], "variant": v["variant"],
         "source_domain": v["source_domain"]}
        for v in db.get_variants(conn, puzzle_id)
    ]
    masks = [
        {"mask_variant_id": m["mask_variant_id"], "label": m["label"],
         "masked_positions": json.loads(m["masked_positions"])}
        for m in db.get_mask_variants(conn, puzzle_id)
    ]
    enabled = {f"{p['variant_id']}:{p['mask_variant_id']}": bool(p["enabled"])
               for p in db.get_variant_pairs(conn, puzzle_id)}
    conn.close()
    return jsonify({"narratives": narratives, "masks": masks, "enabled": enabled})


@app.route("/api/puzzles/<puzzle_id>/matrix", methods=["POST"])
@require_role("owner", "reviewer")
def api_toggle_matrix(puzzle_id):
    data = request.get_json() or {}
    variant_id = data.get("variant_id")
    mask_variant_id = data.get("mask_variant_id")
    enabled = 1 if data.get("enabled") else 0
    if variant_id is None or mask_variant_id is None:
        return jsonify({"error": "variant_id and mask_variant_id required"}), 400
    conn = get_conn()
    db.set_variant_pair(conn, puzzle_id, variant_id, mask_variant_id, enabled)
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/puzzles/<puzzle_id>/creator", methods=["PUT"])
@require_role("owner", "reviewer")
def api_update_creator(puzzle_id):
    data = request.get_json()
    creator = data.get("creator") if data else None
    if creator not in ("human", "claude", "colab"):
        return jsonify({"error": "Invalid creator value"}), 400
    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    if not row:
        conn.close()
        return jsonify({"error": "Puzzle not found"}), 404
    old_creator = row["creator"]
    db.update_puzzle_creator(conn, puzzle_id, creator)
    user = current_user()
    db.log_activity(conn, user["user_id"], "update_creator", "puzzle",
                    puzzle_id, f"Changed creator: {old_creator} → {creator}")
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/puzzles/<puzzle_id>/status", methods=["PUT"])
@require_role("owner", "reviewer")
def api_update_status(puzzle_id):
    data = request.get_json() or {}
    status = data.get("status")
    if status not in ("draft", "active", "featured"):
        return jsonify({"error": "Invalid status"}), 400
    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    if not row:
        conn.close()
        return jsonify({"error": "Puzzle not found"}), 404
    old_status = row["status"] if "status" in row.keys() else None
    db.set_puzzle_status(conn, puzzle_id, status)
    user = current_user()
    db.log_activity(conn, user["user_id"], "update_status", "puzzle",
                    puzzle_id, f"Status: {old_status} → {status}")
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/puzzles/<puzzle_id>/tags", methods=["PUT"])
@require_role("owner", "reviewer")
def api_update_tags(puzzle_id):
    data = request.get_json() or {}
    tags = data.get("tags")
    if tags is not None and not isinstance(tags, str):
        return jsonify({"error": "tags must be a comma-separated string or null"}), 400
    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    if not row:
        conn.close()
        return jsonify({"error": "Puzzle not found"}), 404
    old_tags = row["tags"]
    new_tags = db.set_puzzle_tags(conn, puzzle_id, tags)
    user = current_user()
    db.log_activity(conn, user["user_id"], "update_tags", "puzzle",
                    puzzle_id, f"Tags: {old_tags!r} → {new_tags!r}")
    conn.close()
    return jsonify({"status": "ok", "tags": new_tags})


@app.route("/api/puzzles/<puzzle_id>", methods=["DELETE"])
@require_role("owner")
def api_delete_puzzle(puzzle_id):
    conn = get_conn()
    # Snapshot for reversal
    row = db.get_puzzle(conn, puzzle_id)
    snapshot = json.dumps(db.puzzle_to_json(row)) if row else None
    db.delete_puzzle(conn, puzzle_id)
    user = current_user()
    db.log_activity(conn, user["user_id"], "delete_puzzle", "puzzle",
                    puzzle_id, f"Deleted puzzle '{puzzle_id}'", snapshot_json=snapshot)
    conn.close()
    export_path = DATA_DIR / f"{puzzle_id}.json"
    if export_path.exists():
        export_path.unlink()
    return jsonify({"status": "ok"})


@app.route("/admin")
@require_role("owner", "reviewer")
def admin_dashboard():
    conn = get_conn()
    pending = db.get_submissions(conn, status="pending")
    all_subs = db.get_submissions(conn)
    history = []
    now = datetime.utcnow()
    for s in all_subs:
        if s["status"] in ("approved", "rejected", "reversed"):
            d = dict(s)
            # Reversible if reviewed within 30 days
            if s["reviewed_at"]:
                reviewed = datetime.fromisoformat(s["reviewed_at"])
                d["reversible"] = (now - reviewed).days <= 30
            else:
                d["reversible"] = False
            history.append(d)
    activity = db.get_recent_activity(conn)
    users = db.get_all_users(conn) if current_user()["role"] == "owner" else []
    # Solve attempt stats
    row = conn.execute("""SELECT COUNT(*) as total,
        COUNT(DISTINCT session_id) as sessions,
        COUNT(DISTINCT puzzle_id) as puzzles_attempted,
        AVG(CASE WHEN correct IS NOT NULL THEN correct ELSE NULL END) as accuracy
        FROM solve_attempts""").fetchone()
    solve_stats = {
        "total": row["total"],
        "sessions": row["sessions"],
        "puzzles_attempted": row["puzzles_attempted"],
        "accuracy": (row["accuracy"] or 0) * 100,
    }
    # Puzzle list for admin origin management
    all_puzzles = [db.puzzle_to_json(r) for r in db.get_all_puzzles(conn)]
    conn.close()
    return render_template("admin.html", pending=pending, history=history,
                           activity=activity, users=users, solve_stats=solve_stats,
                           all_puzzles=all_puzzles)


# --- Submission review API ---

@app.route("/api/submissions/<int:sid>", methods=["PUT"])
@require_role("owner", "reviewer")
def api_update_submission(sid):
    data = request.get_json()
    if not data or "payload_json" not in data:
        return jsonify({"error": "Missing payload_json"}), 400
    conn = get_conn()
    db.update_submission_payload(conn, sid, data["payload_json"])
    user = current_user()
    db.log_activity(conn, user["user_id"], "edit_submission", "submission",
                    str(sid), "Edited submission payload")
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/submissions/<int:sid>/approve", methods=["POST"])
@require_role("owner", "reviewer")
def api_approve_submission(sid):
    conn = get_conn()
    sub = db.get_submission(conn, sid)
    if not sub or sub["status"] != "pending":
        conn.close()
        return jsonify({"error": "Submission not found or already reviewed"}), 404

    user = current_user()
    payload = json.loads(sub["payload_json"])

    if sub["submission_type"] in ("new_puzzle", "revision"):
        # Re-check uniqueness at approval time — another puzzle with the same ID
        # may have been added since this submission was queued.
        proposed_id = payload.get("puzzle_id")
        if sub["submission_type"] == "new_puzzle" and proposed_id and db.get_puzzle(conn, proposed_id):
            new_id = db.next_available_puzzle_id(conn, prefix="sub")
            payload["puzzle_id"] = new_id
            detail_prefix = f"Reassigned ID {proposed_id} -> {new_id}. "
        else:
            detail_prefix = ""
        try:
            _save_puzzle_from_data(conn, payload)
        except ValueError as e:
            conn.close()
            return jsonify({"error": f"Cannot approve: {e}"}), 400
        detail = detail_prefix + f"Approved puzzle '{payload.get('title', payload.get('puzzle_id'))}'"
    elif sub["submission_type"] == "variant":
        pid = payload.get("puzzle_id") or sub["target_puzzle_id"]
        db.upsert_variant(conn, pid, payload["variant"], payload["narrative"],
                          source_domain=payload.get("source_domain") or payload.get("variant"))
        detail = f"Approved variant '{payload['variant']}' for {pid}"

    note = (request.get_json() or {}).get("review_note")
    db.review_submission(conn, sid, "approved", user["user_id"], note)
    db.log_activity(conn, user["user_id"], "approve_submission", "submission",
                    str(sid), detail)
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/submissions/<int:sid>/reject", methods=["POST"])
@require_role("owner", "reviewer")
def api_reject_submission(sid):
    conn = get_conn()
    sub = db.get_submission(conn, sid)
    if not sub or sub["status"] != "pending":
        conn.close()
        return jsonify({"error": "Submission not found or already reviewed"}), 404

    user = current_user()
    note = (request.get_json() or {}).get("review_note")
    payload = json.loads(sub["payload_json"])
    detail = f"Rejected submission '{payload.get('title', payload.get('puzzle_id', sid))}'"

    db.review_submission(conn, sid, "rejected", user["user_id"], note)
    db.log_activity(conn, user["user_id"], "reject_submission", "submission",
                    str(sid), detail)
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/submissions/<int:sid>/reverse", methods=["POST"])
@require_role("owner")
def api_reverse_submission(sid):
    conn = get_conn()
    sub = db.get_submission(conn, sid)
    if not sub or sub["status"] not in ("approved", "rejected"):
        conn.close()
        return jsonify({"error": "Nothing to reverse"}), 400

    # Check 30-day window
    if sub["reviewed_at"]:
        reviewed = datetime.fromisoformat(sub["reviewed_at"])
        if (datetime.utcnow() - reviewed).days > 30:
            conn.close()
            return jsonify({"error": "Reversal window (30 days) has expired"}), 400

    user = current_user()
    payload = json.loads(sub["payload_json"])

    if sub["status"] == "approved":
        # Undo the approval
        if sub["submission_type"] in ("new_puzzle", "revision"):
            puzzle_id = payload.get("puzzle_id")
            if puzzle_id:
                # Snapshot before deleting
                row = db.get_puzzle(conn, puzzle_id)
                snapshot = json.dumps(db.puzzle_to_json(row)) if row else None
                db.delete_puzzle(conn, puzzle_id)
                export_path = DATA_DIR / f"{puzzle_id}.json"
                if export_path.exists():
                    export_path.unlink()
        elif sub["submission_type"] == "variant":
            pid = payload.get("puzzle_id") or sub["target_puzzle_id"]
            variant_name = payload.get("variant")
            if pid and variant_name:
                db.delete_variant(conn, pid, variant_name)

    # For rejected submissions, just re-open them as pending
    new_status = "reversed" if sub["status"] == "approved" else "pending"
    db.review_submission(conn, sid, new_status, user["user_id"])
    db.log_activity(conn, user["user_id"], "reverse_submission", "submission",
                    str(sid), f"Reversed {sub['status']} submission #{sid}")
    conn.close()
    return jsonify({"status": "ok"})


# --- User management API (owner only) ---

@app.route("/api/admin/users", methods=["POST"])
@require_role("owner")
def api_create_user():
    data = request.get_json()
    if not data or not data.get("username") or not data.get("password"):
        return jsonify({"error": "Username and password required"}), 400
    role = data.get("role", "reviewer")
    if role not in ("reviewer", "collaborator"):
        return jsonify({"error": "Role must be 'reviewer' or 'collaborator'"}), 400
    conn = get_conn()
    if db.get_user_by_username(conn, data["username"]):
        conn.close()
        return jsonify({"error": "Username already exists"}), 409
    db.create_user(conn, data["username"],
                   generate_password_hash(data["password"]), role)
    user = current_user()
    db.log_activity(conn, user["user_id"], "create_user", "user",
                    data["username"], f"Created {role} account '{data['username']}'")
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/admin/users/<int:uid>/role", methods=["PUT"])
@require_role("owner")
def api_update_user_role(uid):
    data = request.get_json() or {}
    role = data.get("role")
    if role not in ("owner", "reviewer", "collaborator"):
        return jsonify({"error": "Role must be owner, reviewer, or collaborator"}), 400
    conn = get_conn()
    target = db.get_user_by_id(conn, uid)
    if not target:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    if target["role"] == role:
        conn.close()
        return jsonify({"status": "ok", "role": role})
    # Never leave the system without an owner.
    if target["role"] == "owner" and role != "owner" and db.count_owners(conn) <= 1:
        conn.close()
        return jsonify({"error": "Cannot demote the last owner"}), 403
    old_role = target["role"]
    db.update_user_role(conn, uid, role)
    user = current_user()
    db.log_activity(conn, user["user_id"], "update_user_role", "user",
                    target["username"],
                    f"Changed {target['username']} role: {old_role} → {role}")
    conn.close()
    return jsonify({"status": "ok", "role": role})


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@require_role("owner")
def api_delete_user(uid):
    conn = get_conn()
    target = db.get_user_by_id(conn, uid)
    if not target:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    if target["role"] == "owner":
        conn.close()
        return jsonify({"error": "Cannot delete owner accounts"}), 403
    db.delete_user(conn, uid)
    user = current_user()
    db.log_activity(conn, user["user_id"], "delete_user", "user",
                    target["username"], f"Deleted user '{target['username']}'")
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/admin/change-password", methods=["POST"])
@require_role("owner", "reviewer")
def api_change_password():
    data = request.get_json()
    if not data or not data.get("current_password") or not data.get("new_password"):
        return jsonify({"error": "Current and new password required"}), 400
    user = current_user()
    conn = get_conn()
    row = db.get_user_by_id(conn, user["user_id"])
    if not check_password_hash(row["password_hash"], data["current_password"]):
        conn.close()
        return jsonify({"error": "Current password is incorrect"}), 403
    db.update_user_password(conn, user["user_id"],
                            generate_password_hash(data["new_password"]))
    db.log_activity(conn, user["user_id"], "change_password", "user",
                    user["username"], "Changed own password")
    conn.close()
    return jsonify({"status": "ok"})


# --- AI review jobs ---

REVIEW_LOG_DIR = Path(__file__).parent / "logs" / "review"
REVIEW_LOG_DIR.mkdir(parents=True, exist_ok=True)


_REVIEW_WORKER_COUNT = int(os.environ.get("NARC_REVIEW_WORKERS", "3"))
_REVIEW_EXECUTOR = ThreadPoolExecutor(
    max_workers=_REVIEW_WORKER_COUNT, thread_name_prefix="review"
)


def _recover_review_jobs():
    """On startup, requeue any jobs that were queued or interrupted mid-run.
    Partial trial work is already preserved in the trials table — collect.py's
    get_pending_trials returns only rows that haven't been answered yet, so a
    requeued job naturally picks up where it left off.
    """
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT job_id, puzzle_id, model_name, log_path FROM review_jobs
               WHERE status IN ('queued', 'running')"""
        ).fetchall()
        if not rows:
            conn.close()
            return
        conn.execute(
            """UPDATE review_jobs
               SET status='queued', started_at=NULL, finished_at=NULL,
                   error='requeued after restart'
               WHERE status IN ('queued', 'running')"""
        )
        conn.commit()
        conn.close()
        for r in rows:
            _REVIEW_EXECUTOR.submit(
                _run_review_job, r["job_id"], r["puzzle_id"],
                r["model_name"], r["log_path"]
            )
        print(f"Recovered {len(rows)} review job(s) after restart.")
    except Exception as e:
        print(f"WARNING: review job recovery failed: {e}")


def _run_review_job(job_id, puzzle_id, model_name, log_path):
    """Worker: run collect + classify in-process for one (puzzle, model).
    All HTTP calls flow through the shared MindRouter token bucket, so the
    100 req/min ceiling is honored regardless of how many jobs run in parallel.
    """
    if not os.environ.get("MINDROUTER_API_KEY"):
        conn = get_conn()
        db.set_review_job_status(conn, job_id, "failed",
                                 error="MINDROUTER_API_KEY not set")
        conn.close()
        return

    conn_status = get_conn()
    db.set_review_job_status(conn_status, job_id, "running")
    conn_status.close()

    try:
        with open(log_path, "w") as log:
            def log_write(msg):
                log.write(str(msg) + "\n")
                log.flush()

            log_write(f"=== collect: model={model_name} puzzle={puzzle_id} ===")
            collect_result = run_collect_job(
                model=model_name, puzzle=puzzle_id, log_fn=log_write
            )
            log_write(f"=== collect done: {collect_result} ===")

            # Variant matrix: every enabled (narrative variant x mask variant)
            # cell beyond the original x original one collect just covered.
            log_write(f"=== matrix: model={model_name} puzzle={puzzle_id} ===")
            matrix_result = run_matrix_job(
                model=model_name, puzzle=puzzle_id, log_fn=log_write
            )
            log_write(f"=== matrix done: {matrix_result} ===")

            log_write(f"=== classify: model={model_name} puzzle={puzzle_id} ===")
            classify_result = run_classify_job(
                model=model_name, puzzle=puzzle_id, log_fn=log_write
            )
            log_write(f"=== classify done: {classify_result} ===")

            # Order-sensitivity: shuffle-test the NARC cells just found, then
            # re-classify so the weak/strong verdict is stored. No-op when the
            # puzzle produced no NARC cells for this model.
            log_write(f"=== sensitivity: model={model_name} puzzle={puzzle_id} ===")
            sens_result = run_sensitivity_job(
                model=model_name, puzzle=puzzle_id, log_fn=log_write
            )
            log_write(f"=== sensitivity done: {sens_result} ===")
            if sens_result.get("completed"):
                run_classify_job(model=model_name, puzzle=puzzle_id, log_fn=log_write)
                log_write("=== reclassify (strength) done ===")

        conn = get_conn()
        db.set_review_job_status(conn, job_id, "done")
        conn.close()
    except Exception as e:
        try:
            with open(log_path, "a") as log:
                log.write(f"\n=== ERROR ===\n{e}\n")
        except Exception:
            pass
        conn = get_conn()
        db.set_review_job_status(conn, job_id, "failed", error=str(e))
        conn.close()


def _queue_review_job(conn, puzzle_id, model_name, user_id, rerun=False):
    """Create a review job and submit it to the worker pool.
    Returns job_id, or None if an active job already exists for this (puzzle, model).
    """
    existing = conn.execute(
        """SELECT job_id FROM review_jobs
           WHERE puzzle_id=? AND model_name=? AND status IN ('queued', 'running')
           LIMIT 1""",
        (puzzle_id, model_name),
    ).fetchone()
    if existing:
        return None
    if rerun:
        db.delete_trials_for_puzzle_model(conn, puzzle_id, model_name)
    ts = int(datetime.utcnow().timestamp())
    log_path = str(REVIEW_LOG_DIR / f"job_{puzzle_id}_{model_name}_{ts}.log")
    job_id = db.create_review_job(conn, puzzle_id, model_name, user_id, log_path)
    _REVIEW_EXECUTOR.submit(_run_review_job, job_id, puzzle_id, model_name, log_path)
    return job_id


# Recover any jobs left in queued/running when this process died.
_recover_review_jobs()


@app.route("/api/admin/review-jobs", methods=["GET"])
@require_role("owner", "reviewer")
def api_list_review_jobs():
    conn = get_conn()
    jobs = [dict(r) for r in db.get_review_jobs(conn)]
    # Attach classification + trial summary + puzzle title per job
    for j in jobs:
        p = db.get_puzzle(conn, j["puzzle_id"])
        j["title"] = p["title"] if p else ""
        cls = conn.execute(
            """SELECT grids_only, narrative_only, both, has_narc
               FROM classifications
               WHERE puzzle_id=? AND model_name=? AND variant_id IS NULL""",
            (j["puzzle_id"], j["model_name"]),
        ).fetchone()
        if not cls:
            cls = conn.execute(
                """SELECT grids_only, narrative_only, both, has_narc
                   FROM classifications
                   WHERE puzzle_id=? AND model_name=?
                   ORDER BY variant_id LIMIT 1""",
                (j["puzzle_id"], j["model_name"]),
            ).fetchone()
        j["classification"] = dict(cls) if cls else None
        trial_rows = conn.execute(
            """SELECT condition,
                      MAX(correct) as correct,
                      MAX(CASE WHEN correct IS NULL THEN error END) as error
               FROM trials WHERE puzzle_id=? AND model_name=?
               GROUP BY condition""",
            (j["puzzle_id"], j["model_name"]),
        ).fetchall()
        j["trials"] = {r["condition"]: r["correct"] for r in trial_rows}
        j["trial_errors"] = {r["condition"]: r["error"] for r in trial_rows
                             if r["correct"] is None and r["error"]}

    # Active (queued/running) jobs indexed by (puzzle_id, model_name)
    active = {}
    for j in jobs:
        if j["status"] in ("queued", "running"):
            active[(j["puzzle_id"], j["model_name"])] = j

    # Per-(puzzle, model) trial presence matrix
    trial_presence = conn.execute(
        """SELECT puzzle_id, model_name,
                  COUNT(*) as total,
                  SUM(CASE WHEN response_text IS NOT NULL OR error IS NOT NULL THEN 1 ELSE 0 END) as completed
           FROM trials GROUP BY puzzle_id, model_name"""
    ).fetchall()
    presence = {}
    for r in trial_presence:
        presence.setdefault(r["puzzle_id"], {})[r["model_name"]] = {
            "total": r["total"], "completed": r["completed"]
        }

    # Build coverage: every puzzle × REVIEW_MODELS
    all_puzzles = db.get_all_puzzles(conn)
    coverage = []
    untested_info = []
    for p in all_puzzles:
        pid = p["puzzle_id"]
        p_presence = presence.get(pid, {})
        models_status = []
        tested_count = 0
        for m in REVIEW_MODELS:
            info = p_presence.get(m)
            act = active.get((pid, m))
            if act:
                state = "running" if act["status"] == "running" else "queued"
            elif info and info["completed"] > 0:
                state = "tested"
                tested_count += 1
            else:
                state = "missing"
            models_status.append({"model": m, "state": state,
                                  "job_id": act["job_id"] if act else None})
        row = {
            "puzzle_id": pid,
            "title": p["title"],
            "created_at": p["created_at"],
            "models": models_status,
            "tested_count": tested_count,
        }
        if tested_count == 0:
            untested_info.append(row)
        elif tested_count < len(REVIEW_MODELS):
            coverage.append(row)
    conn.close()
    return jsonify({
        "jobs": jobs,
        "untested": untested_info,
        "coverage": coverage,
        "models": REVIEW_MODELS,
    })


@app.route("/api/admin/puzzles/<puzzle_id>/run-review", methods=["POST"])
@require_role("owner", "reviewer")
def api_run_review(puzzle_id):
    data = request.get_json(silent=True) or {}
    model = data.get("model")
    rerun = bool(data.get("rerun"))
    models = [model] if model else list(REVIEW_MODELS)
    invalid = [m for m in models if m not in REVIEW_MODELS]
    if invalid:
        return jsonify({"error": f"Unknown model(s): {invalid}"}), 400

    conn = get_conn()
    puzzle = db.get_puzzle(conn, puzzle_id)
    if not puzzle:
        conn.close()
        return jsonify({"error": "Puzzle not found"}), 404

    user = current_user()
    queued = []
    skipped = []
    for m in models:
        jid = _queue_review_job(conn, puzzle_id, m, user["user_id"], rerun=rerun)
        if jid:
            queued.append({"model": m, "job_id": jid})
        else:
            skipped.append(m)
    db.log_activity(conn, user["user_id"], "run_review", "puzzle", puzzle_id,
                    f"Queued AI review on {[q['model'] for q in queued]}"
                    + (f" (rerun)" if rerun else ""))
    conn.close()
    return jsonify({"status": "queued", "queued": queued, "skipped": skipped})


@app.route("/api/admin/review-jobs/<int:job_id>/log")
@require_role("owner", "reviewer")
def api_review_job_log(job_id):
    conn = get_conn()
    job = db.get_review_job(conn, job_id)
    conn.close()
    if not job or not job["log_path"]:
        return jsonify({"error": "Not found"}), 404
    try:
        with open(job["log_path"]) as f:
            return jsonify({"log": f.read()[-20000:]})
    except FileNotFoundError:
        return jsonify({"log": ""})


@app.route("/api/admin/export/solve-attempts")
@require_role("owner", "reviewer")
def api_export_solve_attempts():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM solve_attempts ORDER BY created_at"
    ).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    if rows:
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow(tuple(r))
    resp = app.make_response(output.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=solve_attempts.csv"
    return resp


@app.route("/api/admin/export/submissions")
@require_role("owner", "reviewer")
def api_export_submissions():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM submissions ORDER BY created_at"
    ).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    if rows:
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow(tuple(r))
    resp = app.make_response(output.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=submissions.csv"
    return resp


# --- Inspect data exports (collaborators + admins) ---

DATA_ROLES = ("owner", "reviewer", "collaborator")


def _json_download(name, payload):
    body = {
        "schema": "narc-export-v1",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        **payload,
    }
    today = datetime.utcnow().strftime("%Y-%m-%d")
    resp = app.make_response(json.dumps(body, indent=2))
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = f"attachment; filename={name}_{today}.json"
    return resp


@app.route("/api/inspect/export/masking.json")
@require_role(*DATA_ROLES)
def api_export_masking():
    conn = get_conn()
    models = [r[0] for r in conn.execute(
        "SELECT DISTINCT model_name FROM trials ORDER BY model_name"
    ).fetchall()]
    data = _inspect_masking(conn, models)
    conn.close()
    data.pop("highlights", None)
    return _json_download("narc_masking", {"experiment": "masking",
                                           "models": models, **data})


@app.route("/api/inspect/export/ordering.json")
@require_role(*DATA_ROLES)
def api_export_ordering():
    conn = get_conn()
    data = _inspect_ordering(conn)
    conn.close()
    data.pop("highlights", None)
    return _json_download("narc_ordering", {"experiment": "ordering", **data})


@app.route("/api/inspect/export/stances.json")
@require_role(*DATA_ROLES)
def api_export_stances():
    conn = get_conn()
    data = _inspect_stances(conn)
    conn.close()
    data.pop("highlights", None)
    return _json_download("narc_stances", {"experiment": "stances", **data})


@app.route("/api/inspect/export/oddoneout.json")
@require_role(*DATA_ROLES)
def api_export_oddoneout():
    conn = get_conn()
    data = _inspect_oddoneout(conn)
    conn.close()
    data.pop("highlights", None)
    return _json_download("narc_oddoneout", {"experiment": "oddoneout", **data})


@app.route("/api/inspect/export/puzzles.json")
@require_role(*DATA_ROLES)
def api_export_puzzles():
    conn = get_conn()
    puzzles = []
    for row in db.get_all_puzzles(conn):
        p = db.puzzle_to_json(row)
        variants = []
        for v in db.get_variants(conn, p["puzzle_id"]):
            variants.append({
                "variant_id": v["variant_id"],
                "variant": v["variant"],
                "source_domain": v["source_domain"],
                "narrative": v["narrative"],
                "generator": v["generator"],
                "created_at": v["created_at"],
            })
        p["variants"] = variants
        puzzles.append(p)
    conn.close()
    return _json_download("narc_puzzles", {"puzzles": puzzles,
                                            "count": len(puzzles)})


def _build_puzzle_bundle(conn, pid):
    row = db.get_puzzle(conn, pid)
    if not row:
        return None
    bundle = db.puzzle_to_json(row)
    bundle["variants"] = [
        {"variant_id": v["variant_id"], "variant": v["variant"],
         "source_domain": v["source_domain"], "narrative": v["narrative"],
         "generator": v["generator"], "created_at": v["created_at"]}
        for v in db.get_variants(conn, pid)
    ]
    bundle["classifications"] = [dict(r) for r in conn.execute(
        """SELECT model_name, variant_id, grids_only, narrative_only, both, has_narc
           FROM classifications WHERE puzzle_id=?
           ORDER BY model_name, variant_id""", (pid,)).fetchall()]
    bundle["ordering"] = [
        {"model_name": r["model_name"], "condition": r["condition"],
         "avg_kendall_tau": round(r["avg_tau"], 4) if r["avg_tau"] is not None else None,
         "exact_match_rate": round(r["exact_rate"], 4) if r["exact_rate"] is not None else None,
         "n_trials": r["n"]}
        for r in conn.execute(
            """SELECT model_name, condition, AVG(kendall_tau) as avg_tau,
                      AVG(exact_match) as exact_rate, COUNT(*) as n
               FROM ordering_trials
               WHERE puzzle_id=? AND kendall_tau IS NOT NULL
               GROUP BY model_name, condition""", (pid,)).fetchall()]
    ooo_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='oddoneout_trials'"
    ).fetchone()
    if ooo_table:
        bundle["oddoneout"] = [
            {"model_name": r["model_name"], "condition": r["condition"],
             "accuracy": round(r["correct_count"] / r["n"], 4) if r["n"] else None,
             "n_trials": r["n"]}
            for r in conn.execute(
                """SELECT model_name, condition, COUNT(*) as n,
                          SUM(correct) as correct_count
                   FROM oddoneout_trials
                   WHERE puzzle_id=? AND correct IS NOT NULL
                   GROUP BY model_name, condition""", (pid,)).fetchall()]
    else:
        bundle["oddoneout"] = []
    return bundle


@app.route("/api/inspect/export/puzzle/<pid>.json")
@require_role(*DATA_ROLES)
def api_export_one_puzzle(pid):
    conn = get_conn()
    bundle = _build_puzzle_bundle(conn, pid)
    conn.close()
    if not bundle:
        return jsonify({"error": "Not found"}), 404
    return _json_download(f"narc_puzzle_{pid}", bundle)


@app.route("/api/inspect/export/selected.json", methods=["POST"])
@require_role(*DATA_ROLES)
def api_export_selected():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids must be a non-empty list"}), 400
    if len(ids) > 500:
        return jsonify({"error": "Too many ids (max 500)"}), 400
    conn = get_conn()
    bundles = []
    missing = []
    for pid in ids:
        b = _build_puzzle_bundle(conn, pid)
        if b:
            bundles.append(b)
        else:
            missing.append(pid)
    conn.close()
    return _json_download("narc_selected", {"puzzles": bundles,
                                             "count": len(bundles),
                                             "missing": missing})


@app.route("/api/vote", methods=["POST"])
def api_vote():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    puzzle_id = data.get("puzzle_id")
    voter_id = data.get("voter_id")
    value = data.get("value")

    if not puzzle_id or not voter_id or value not in (1, -1, 0):
        return jsonify({"error": "Invalid data"}), 400

    ip = request.remote_addr
    conn = get_conn()

    # Rate limit: max 50 votes per IP per hour
    if db.count_recent_votes_by_ip(conn, ip) > 50:
        conn.close()
        return jsonify({"error": "Rate limited"}), 429

    if value == 0:
        db.delete_vote(conn, puzzle_id, voter_id)
    else:
        db.upsert_vote(conn, puzzle_id, voter_id, value, ip)

    counts = db.get_puzzle_vote_counts(conn, puzzle_id)
    conn.close()

    resp = jsonify({"status": "ok", "up": counts["up"], "down": counts["down"]})
    resp.set_cookie('narc_voter_id', voter_id, max_age=365*24*3600, samesite='Lax')
    return resp


@app.route("/api/variant-view", methods=["POST"])
def api_variant_view():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    conn = get_conn()
    db.insert_variant_view(conn, data["session_id"], data["puzzle_id"], data["variant"])
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/solve", methods=["POST"])
def api_solve_attempt():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    submitted = data.get("submitted_grids")
    conn = get_conn()
    db.insert_solve_attempt(
        conn,
        puzzle_id=data["puzzle_id"],
        session_id=data["session_id"],
        phase=data["phase"],
        saw_narrative=data.get("saw_narrative", 0),
        submitted_grids=json.dumps(submitted) if submitted else None,
        correct=data.get("correct"),
        cell_accuracy=data.get("cell_accuracy"),
        time_spent_ms=data.get("time_spent_ms"),
        skipped_phase1=data.get("skipped_phase1", 0),
        active_variant=data.get("active_variant"),
    )
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/solve-events", methods=["POST"])
def api_solve_events():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400
    session_id = data.get("session_id")
    puzzle_id = data.get("puzzle_id")
    events = data.get("events") or []
    if not session_id or not puzzle_id:
        return jsonify({"error": "Missing session_id or puzzle_id"}), 400
    if not isinstance(events, list):
        return jsonify({"error": "events must be a list"}), 400
    conn = get_conn()
    n = db.insert_solve_events(conn, session_id, puzzle_id, events)
    conn.close()
    return jsonify({"status": "ok", "inserted": n})


@app.cli.command("seed-owner")
@click.argument("username")
@click.password_option()
def seed_owner(username, password):
    """Create an owner account."""
    conn = get_conn()
    existing = db.get_user_by_username(conn, username)
    if existing:
        click.echo(f"User '{username}' already exists.")
        conn.close()
        return
    db.create_user(conn, username, generate_password_hash(password), "owner")
    conn.close()
    click.echo(f"Owner account '{username}' created.")


if __name__ == "__main__":
    conn = get_conn()
    conn.close()
    print("NARC server starting on http://localhost:8000")
    app.run(debug=True, port=8000)
