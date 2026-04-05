"""Flask server for NARC puzzle creation and solving."""

import json
import os
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import click
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db
from db import get_variants, get_trials

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data" / "puzzles"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Secret key for sessions
_secret = os.environ.get("NARC_SECRET_KEY", "dev-secret-change-in-prod")
if _secret == "dev-secret-change-in-prod":
    print("WARNING: Using default secret key. Set NARC_SECRET_KEY in production.")
app.secret_key = _secret

MODELS = ["gpt-oss-120b", "gpt-oss-20b", "qwen3.5-122b", "nemotron-3-super"]


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


def _build_draft_map(conn):
    """Batch-compute is_draft for all puzzles. Returns {puzzle_id: bool}."""
    # Get all trials with a correct result, grouped by puzzle+model+condition
    rows = conn.execute(
        """SELECT puzzle_id, model_name, condition, MAX(correct) as correct
           FROM trials
           WHERE correct IS NOT NULL
           GROUP BY puzzle_id, model_name, condition"""
    ).fetchall()
    # Build: puzzle_id -> model -> {condition: correct}
    trial_map = {}
    for r in rows:
        trial_map.setdefault(r["puzzle_id"], {}).setdefault(
            r["model_name"], {}
        )[r["condition"]] = r["correct"]
    return trial_map


def _is_draft(puzzle_id, tags, trial_map):
    """Determine if a puzzle is a draft based on trial results and tags."""
    has_draft_tag = any(
        t.strip().startswith("draft:")
        for t in (tags or "").split(",")
    )
    if has_draft_tag:
        return True
    model_data = trial_map.get(puzzle_id, {})
    if not model_data:
        return True  # No trials at all
    # Unsolvable = no model can solve it via grids_only or both
    for model_name in MODELS:
        results = model_data.get(model_name, {})
        if results.get("grids_only") or results.get("both"):
            return False
    return True


def enrich_puzzle(conn, pdata, trial_map=None):
    """Add is_draft, variants, and variant_count to a puzzle dict."""
    pid = pdata["puzzle_id"]
    # Variants
    variants = []
    for v in get_variants(conn, pid):
        variants.append({
            "variant": v["variant"],
            "source_domain": v["source_domain"],
            "narrative": v["narrative"],
        })
    pdata["variants"] = variants
    pdata["variant_count"] = len(variants)
    # Draft status
    if trial_map is not None:
        pdata["is_draft"] = _is_draft(pid, pdata.get("tags"), trial_map)
    else:
        tm = _build_draft_map(conn)
        pdata["is_draft"] = _is_draft(pid, pdata.get("tags"), tm)
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


# --- Pages ---

@app.route("/")
def index():
    return redirect(url_for("about"))


@app.route("/about")
def about():
    conn = get_conn()
    puzzles = db.get_all_puzzles(conn)
    variant_count = conn.execute("SELECT COUNT(*) as c FROM narrative_variants").fetchone()["c"]
    trial_map = _build_draft_map(conn)
    # Count unique grid sizes and active/draft
    grid_sizes = set()
    active_count = 0
    draft_count = 0
    for p in puzzles:
        pdata = db.puzzle_to_json(p)
        for item in pdata["sequence"]:
            grid_sizes.add((item["rows"], item["cols"]))
        if _is_draft(pdata["puzzle_id"], pdata.get("tags"), trial_map):
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
    inspect_path = Path(__file__).parent / "inspect.html"
    if not inspect_path.exists():
        return "Run python inspector.py first", 404
    html = inspect_path.read_text()
    # Inject nav bar after <body> tag
    admin_link = ''
    user = current_user()
    if user:
        admin_link = '<a href="/admin" style="color:#ccc;text-decoration:none;">Admin</a>'
    nav = f"""<nav style="background:#16213e;padding:8px 20px;margin:-16px -16px 16px;display:flex;align-items:center;gap:20px;font-family:-apple-system,sans-serif;">
    <a href="/" style="color:#f59e0b;font-weight:bold;text-decoration:none;font-size:1.1em;">NARC</a>
    <a href="/browse" style="color:#ccc;text-decoration:none;">Browse</a>
    <a href="/create" style="color:#ccc;text-decoration:none;">Create</a>
    <a href="/inspect" style="color:#fff;text-decoration:none;">Inspect</a>
    {admin_link}
    </nav>"""
    html = html.replace("<body>", "<body>" + nav, 1)
    return html


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
    trial_map = _build_draft_map(conn)
    puzzles = [enrich_puzzle(conn, db.puzzle_to_json(r), trial_map) for r in rows]
    conn.close()

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

    return render_template("browse.html", puzzles=puzzles, tag_counts=tag_counts,
                           audience_tags=tags_by_prefix("audience"),
                           arc_tags=tags_by_prefix("arc"),
                           clue_tags=tags_by_prefix("clue"),
                           domain_tags=tags_by_prefix("domain"),
                           grid_tags=tags_by_prefix("grids"),
                           spectrum_tags=spectrum_tags)


@app.route("/create")
def create():
    edit_id = request.args.get("edit")
    revise_id = request.args.get("revise")
    puzzle = None
    puzzle_json = "null"
    revise_mode = False
    if edit_id or revise_id:
        conn = get_conn()
        row = db.get_puzzle(conn, edit_id or revise_id)
        if row:
            puzzle = enrich_puzzle(conn, db.puzzle_to_json(row))
            if revise_id:
                revise_mode = True
                puzzle["puzzle_id"] = puzzle["puzzle_id"] + "_rev"
            puzzle_json = json.dumps(puzzle)
        conn.close()
    return render_template("create.html", puzzle=puzzle, puzzle_json=puzzle_json,
                           revise_mode=revise_mode)


@app.route("/solve/<puzzle_id>")
def solve(puzzle_id):
    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    if not row:
        conn.close()
        return "Puzzle not found", 404
    puzzle = enrich_puzzle(conn, db.puzzle_to_json(row))
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

    user = current_user()

    if user and user["role"] in ("owner", "reviewer"):
        # Admin: save directly
        conn = get_conn()
        _save_puzzle_from_data(conn, data)
        db.log_activity(conn, user["user_id"], "save_puzzle", "puzzle",
                        puzzle_id, f"Saved puzzle '{title}'")
        conn.close()
        return jsonify({"status": "ok", "puzzle_id": puzzle_id})
    else:
        # Visitor: create submission
        sub_type = "revision" if data.get("is_revision") else "new_puzzle"
        conn = get_conn()
        sid = db.create_submission(
            conn, sub_type, json.dumps(data),
            target_puzzle_id=data.get("original_puzzle_id"),
            submitter_name=data.get("submitter_name"),
            submitter_email=data.get("submitter_email"),
        )
        db.log_activity(conn, None, "submit_puzzle", "submission",
                        str(sid), f"Visitor submitted '{title}'")
        conn.close()
        return jsonify({"status": "submitted", "submission_id": sid})


@app.route("/api/puzzles/<puzzle_id>/variants", methods=["POST"])
def api_add_variant(puzzle_id):
    data = request.get_json()
    if not data or not data.get("variant") or not data.get("narrative"):
        return jsonify({"error": "Missing variant name or narrative"}), 400

    user = current_user()

    if user and user["role"] in ("owner", "reviewer"):
        conn = get_conn()
        db.upsert_variant(conn, puzzle_id, data["variant"], data["narrative"],
                          source_domain=data.get("source_domain") or data.get("variant"))
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
    conn.close()
    return render_template("admin.html", pending=pending, history=history,
                           activity=activity, users=users)


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
        _save_puzzle_from_data(conn, payload)
        detail = f"Approved puzzle '{payload.get('title', payload.get('puzzle_id'))}'"
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
    conn = get_conn()
    if db.get_user_by_username(conn, data["username"]):
        conn.close()
        return jsonify({"error": "Username already exists"}), 409
    db.create_user(conn, data["username"],
                   generate_password_hash(data["password"]), "reviewer")
    user = current_user()
    db.log_activity(conn, user["user_id"], "create_user", "user",
                    data["username"], f"Created reviewer account '{data['username']}'")
    conn.close()
    return jsonify({"status": "ok"})


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
    )
    conn.close()
    return jsonify({"status": "ok"})


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
