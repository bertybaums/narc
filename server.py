"""Flask server for NARC puzzle creation and solving."""

import json
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

import db

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data" / "puzzles"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_conn():
    return db.init_db()


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
    # Count unique grid sizes
    grid_sizes = set()
    for p in puzzles:
        pdata = db.puzzle_to_json(p)
        for item in pdata["sequence"]:
            grid_sizes.add((item["rows"], item["cols"]))
    conn.close()
    stats = {
        "total_puzzles": len(puzzles),
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
    nav = """<nav style="background:#16213e;padding:8px 20px;margin:-16px -16px 16px;display:flex;align-items:center;gap:20px;font-family:-apple-system,sans-serif;">
    <a href="/" style="color:#f59e0b;font-weight:bold;text-decoration:none;font-size:1.1em;">NARC</a>
    <a href="/browse" style="color:#ccc;text-decoration:none;">Browse</a>
    <a href="/create" style="color:#ccc;text-decoration:none;">Create</a>
    <a href="/inspect" style="color:#fff;text-decoration:none;">Inspect</a>
    </nav>"""
    html = html.replace("<body>", "<body>" + nav, 1)
    return html


@app.route("/browse")
def browse():
    conn = get_conn()
    rows = db.get_all_puzzles(conn)
    puzzles = [db.puzzle_to_json(r) for r in rows]
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
    puzzle = None
    puzzle_json = "null"
    if edit_id:
        conn = get_conn()
        row = db.get_puzzle(conn, edit_id)
        conn.close()
        if row:
            puzzle = db.puzzle_to_json(row)
            puzzle_json = json.dumps(puzzle)
    return render_template("create.html", puzzle=puzzle, puzzle_json=puzzle_json)


@app.route("/solve/<puzzle_id>")
def solve(puzzle_id):
    conn = get_conn()
    row = db.get_puzzle(conn, puzzle_id)
    conn.close()
    if not row:
        return "Puzzle not found", 404
    puzzle = db.puzzle_to_json(row)
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

    tags = data.get("metadata", {}).get("tags")
    if isinstance(tags, list):
        tags = ",".join(tags)

    conn = get_conn()
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

    # Also insert original narrative as a variant
    db.upsert_variant(conn, puzzle_id, "original", narrative)

    conn.close()

    # Export JSON
    export_path = DATA_DIR / f"{puzzle_id}.json"
    export_path.write_text(json.dumps(data, indent=2))

    return jsonify({"status": "ok", "puzzle_id": puzzle_id})


@app.route("/api/puzzles/<puzzle_id>", methods=["DELETE"])
def api_delete_puzzle(puzzle_id):
    conn = get_conn()
    db.delete_puzzle(conn, puzzle_id)
    conn.close()
    export_path = DATA_DIR / f"{puzzle_id}.json"
    if export_path.exists():
        export_path.unlink()
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


if __name__ == "__main__":
    conn = get_conn()
    conn.close()
    print("NARC server starting on http://localhost:8000")
    app.run(debug=True, port=8000)
