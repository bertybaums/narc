"""Analyze focalization experiment results.

Produces an HTML report showing whether narrative perspective (active/observer/absent)
affects model solve rate — the core focalization hypothesis.

Usage:
    python analyze_focal.py --model gpt-oss-120b
"""

import json
from pathlib import Path

import click


@click.command()
@click.option("--model", default="gpt-oss-120b")
@click.option("--output", default=None)
def main(model, output):
    results_file = f"focal_results_{model}.json"
    if not Path(results_file).exists():
        click.echo(f"No results file: {results_file}")
        return

    results = json.loads(open(results_file).read())
    click.echo(f"Loaded {len(results)} focal variant results")

    # Group by predicted ease
    by_ease = {"easy": [], "medium": [], "hard": []}
    for r in results:
        ease = r.get("predicted_ease", "?")
        if ease in by_ease:
            by_ease[ease].append(r)

    # Group by puzzle to see within-puzzle effects
    by_puzzle = {}
    for r in results:
        pid = r["puzzle_id"]
        by_puzzle.setdefault(pid, []).append(r)

    # Calculate stats
    stats = {}
    for ease, trials in by_ease.items():
        n = len(trials)
        correct = sum(1 for t in trials if t.get("correct"))
        avg_acc = sum(t.get("cell_accuracy", 0) for t in trials) / n if n else 0
        stats[ease] = {"n": n, "correct": correct, "rate": correct / n if n else 0,
                       "avg_accuracy": avg_acc}

    # Within-puzzle focalization effect
    focal_effects = []
    for pid, trials in by_puzzle.items():
        ease_map = {}
        for t in trials:
            ease_map[t["predicted_ease"]] = t
        if "easy" in ease_map and "hard" in ease_map:
            easy_acc = ease_map["easy"].get("cell_accuracy", 0)
            hard_acc = ease_map["hard"].get("cell_accuracy", 0)
            gap = easy_acc - hard_acc
            focal_effects.append({
                "puzzle_id": pid,
                "easy_correct": ease_map["easy"].get("correct", False),
                "hard_correct": ease_map["hard"].get("correct", False),
                "easy_acc": easy_acc, "hard_acc": hard_acc, "gap": gap,
                "easy_actor": ease_map["easy"].get("actor", "?"),
                "hard_actor": ease_map["hard"].get("actor", "?"),
            })

    avg_gap = sum(e["gap"] for e in focal_effects) / len(focal_effects) if focal_effects else 0
    positive_gap = sum(1 for e in focal_effects if e["gap"] > 0)

    # Build HTML report
    html = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>Focalization Analysis — {model}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
        .stat-card {{ background: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 20px; text-align: center; }}
        .stat-value {{ font-size: 2.5em; font-weight: bold; }}
        .stat-label {{ color: #9ca3af; }}
        .gap-positive {{ color: #2ECC40; }}
        .gap-negative {{ color: #FF4136; }}
        .gap-zero {{ color: #AAAAAA; }}
        table {{ font-size: 0.9em; }}
    </style>
</head>
<body>
<div class="container-fluid">
    <h1>Focalization Experiment: {model}</h1>
    <p class="text-muted">Does narrative perspective affect puzzle solvability?</p>

    <h3>Headline Results</h3>
    <div class="row g-3 mb-4">
        <div class="col-md-3">
            <div class="stat-card">
                <div class="stat-value gap-positive">{stats.get('easy', {}).get('rate', 0):.0%}</div>
                <div class="stat-label">Active Actor (Easy)<br>{stats.get('easy', {}).get('correct', 0)}/{stats.get('easy', {}).get('n', 0)}</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="stat-card">
                <div class="stat-value" style="color: #FFDC00;">{stats.get('medium', {}).get('rate', 0):.0%}</div>
                <div class="stat-label">Observer (Medium)<br>{stats.get('medium', {}).get('correct', 0)}/{stats.get('medium', {}).get('n', 0)}</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="stat-card">
                <div class="stat-value gap-negative">{stats.get('hard', {}).get('rate', 0):.0%}</div>
                <div class="stat-label">Absent Actor (Hard)<br>{stats.get('hard', {}).get('correct', 0)}/{stats.get('hard', {}).get('n', 0)}</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="stat-card">
                <div class="stat-value {'gap-positive' if avg_gap > 0.05 else 'gap-zero'}">{avg_gap:+.1%}</div>
                <div class="stat-label">Avg Easy-Hard Gap<br>{positive_gap}/{len(focal_effects)} puzzles positive</div>
            </div>
        </div>
    </div>

    <h3>Cell Accuracy by Perspective</h3>
    <div class="row g-3 mb-4">
        <div class="col-md-4">
            <div class="stat-card">
                <div class="stat-value">{stats.get('easy', {}).get('avg_accuracy', 0):.1%}</div>
                <div class="stat-label">Active Actor — Avg Cell Accuracy</div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="stat-card">
                <div class="stat-value">{stats.get('medium', {}).get('avg_accuracy', 0):.1%}</div>
                <div class="stat-label">Observer — Avg Cell Accuracy</div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="stat-card">
                <div class="stat-value">{stats.get('hard', {}).get('avg_accuracy', 0):.1%}</div>
                <div class="stat-label">Absent Actor — Avg Cell Accuracy</div>
            </div>
        </div>
    </div>

    <h3>Per-Puzzle Focalization Effects</h3>
    <p class="text-muted">Sorted by gap size (easy accuracy - hard accuracy). Positive = focalization hypothesis supported.</p>
    <table class="table table-striped table-sm">
        <thead>
            <tr><th>Puzzle</th><th>Easy Actor</th><th>Easy Acc</th><th>Hard Actor</th><th>Hard Acc</th><th>Gap</th></tr>
        </thead>
        <tbody>
"""

    for e in sorted(focal_effects, key=lambda x: -x["gap"]):
        gap_cls = "gap-positive" if e["gap"] > 0.05 else ("gap-negative" if e["gap"] < -0.05 else "gap-zero")
        easy_mark = "&#10003;" if e["easy_correct"] else "&#10007;"
        hard_mark = "&#10003;" if e["hard_correct"] else "&#10007;"
        html += f"""<tr>
            <td>{e['puzzle_id']}</td>
            <td>{e['easy_actor']}</td>
            <td>{easy_mark} {e['easy_acc']:.0%}</td>
            <td>{e['hard_actor']}</td>
            <td>{hard_mark} {e['hard_acc']:.0%}</td>
            <td class="{gap_cls}">{e['gap']:+.0%}</td>
        </tr>\n"""

    html += """
        </tbody>
    </table>
</div>
</body>
</html>"""

    outfile = output or f"focal_analysis_{model}.html"
    with open(outfile, "w") as f:
        f.write(html)
    click.echo(f"\nReport: {outfile}")
    click.echo(f"\nKey finding: Active actor {stats.get('easy', {}).get('rate', 0):.0%} vs "
               f"Absent actor {stats.get('hard', {}).get('rate', 0):.0%} "
               f"(gap: {avg_gap:+.1%})")


if __name__ == "__main__":
    main()
