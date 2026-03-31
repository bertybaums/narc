"""Phase 3: Generate HTML analysis report.

Usage:
    python analyze.py [--model MODEL] [--output FILE]
"""

import json

import click
import db
import grids


def generate_report(conn, model_name):
    puzzles = db.get_all_puzzles(conn)

    # Gather data
    puzzle_results = []
    counts = {"narc": 0, "grids_sufficient": 0, "narrative_sufficient": 0, "unsolvable": 0}

    for p in puzzles:
        pid = p["puzzle_id"]
        pdata = db.puzzle_to_json(p)
        trials = db.get_trials(conn, pid, model_name=model_name)

        result = {
            "puzzle_id": pid,
            "title": p["title"],
            "num_grids": len(pdata["sequence"]),
            "masked_positions": pdata["masked_positions"],
            "trials": {},
        }

        for t in trials:
            cond = t["condition"]
            result["trials"][cond] = {
                "correct": t["correct"],
                "cell_accuracy": t["cell_accuracy"],
                "predicted_grids": json.loads(t["predicted_grids"]) if t["predicted_grids"] else None,
            }

        g = result["trials"].get("grids_only", {}).get("correct", 0)
        n = result["trials"].get("narrative_only", {}).get("correct", 0)
        b = result["trials"].get("both", {}).get("correct", 0)

        if g:
            result["status"] = "grids_sufficient"
        elif n:
            result["status"] = "narrative_sufficient"
        elif b:
            result["status"] = "narc"
        else:
            result["status"] = "unsolvable"

        counts[result["status"]] = counts.get(result["status"], 0) + 1
        puzzle_results.append(result)

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>NARC Analysis — {model_name}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
        .status-narc {{ color: #2ECC40; font-weight: bold; }}
        .status-grids_sufficient {{ color: #0074D9; }}
        .status-narrative_sufficient {{ color: #FF851B; }}
        .status-unsolvable {{ color: #FF4136; }}
        .grid-img {{ border: 1px solid #555; }}
        table {{ font-size: 0.9em; }}
        .cell-acc {{ font-family: monospace; }}
    </style>
</head>
<body>
<div class="container-fluid">
    <h1>NARC Analysis: {model_name}</h1>

    <h3>Summary</h3>
    <div class="row mb-4">
        <div class="col-md-6">
            <table class="table table-sm">
                <tr><td>Total puzzles</td><td><strong>{len(puzzle_results)}</strong></td></tr>
                <tr><td class="status-narc">NARC-verified</td><td><strong>{counts.get("narc", 0)}</strong></td></tr>
                <tr><td class="status-grids_sufficient">Grids sufficient</td><td>{counts.get("grids_sufficient", 0)}</td></tr>
                <tr><td class="status-narrative_sufficient">Narrative sufficient</td><td>{counts.get("narrative_sufficient", 0)}</td></tr>
                <tr><td class="status-unsolvable">Unsolvable</td><td>{counts.get("unsolvable", 0)}</td></tr>
            </table>
        </div>
    </div>

    <h3>Per-Puzzle Results</h3>
    <table class="table table-striped table-sm">
        <thead>
            <tr>
                <th>Puzzle</th>
                <th>Title</th>
                <th>Grids</th>
                <th>Grids Only</th>
                <th>Narrative Only</th>
                <th>Both</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
"""
    for r in puzzle_results:
        def fmt_trial(cond):
            t = r["trials"].get(cond, {})
            if t.get("correct") is None:
                return '<td class="text-muted">—</td>'
            c = t["correct"]
            acc = t.get("cell_accuracy", 0) or 0
            cls = "text-success" if c else "text-danger"
            icon = "&#10003;" if c else "&#10007;"
            return f'<td class="{cls}">{icon} <span class="cell-acc">{acc:.0%}</span></td>'

        html += f"""
            <tr>
                <td>{r['puzzle_id']}</td>
                <td>{r['title']}</td>
                <td>{r['num_grids']}</td>
                {fmt_trial('grids_only')}
                {fmt_trial('narrative_only')}
                {fmt_trial('both')}
                <td class="status-{r['status']}">{r['status']}</td>
            </tr>
"""

    html += """
        </tbody>
    </table>
</div>
</body>
</html>
"""
    return html


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Model name")
@click.option("--output", default=None, help="Output file (default: analysis_{model}.html)")
def main(model, output):
    conn = db.init_db()
    html = generate_report(conn, model)
    conn.close()

    outfile = output or f"analysis_{model}.html"
    with open(outfile, "w") as f:
        f.write(html)
    click.echo(f"Report written to {outfile}")


if __name__ == "__main__":
    main()
