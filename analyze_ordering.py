"""Analyze ordering experiment results.

Computes narrative ordering lift (tau_narrative - tau_grids_only) and
generates a summary report.

Usage:
    python analyze_ordering.py
    python analyze_ordering.py --model gpt-oss-120b
    python analyze_ordering.py --html ordering_report.html
"""

import json
import sqlite3

import click

import db


def get_ordering_results(conn, model_name=None):
    """Fetch ordering trial results, paired by puzzle."""
    sql = """
        SELECT puzzle_id, model_name, condition, kendall_tau, exact_match,
               predicted_order, correct_order
        FROM ordering_trials
        WHERE kendall_tau IS NOT NULL
    """
    params = []
    if model_name:
        sql += " AND model_name=?"
        params.append(model_name)
    sql += " ORDER BY puzzle_id, condition"
    return conn.execute(sql, tuple(params)).fetchall()


def compute_narrative_lift(conn, model_name=None):
    """Compute per-puzzle narrative ordering lift.

    Returns list of dicts with puzzle_id, tau_grids, tau_narrative, lift, n_grids.
    """
    results = get_ordering_results(conn, model_name)

    # Group by (puzzle_id, model)
    by_puzzle = {}
    for r in results:
        key = (r["puzzle_id"], r["model_name"])
        by_puzzle.setdefault(key, {})[r["condition"]] = {
            "tau": r["kendall_tau"],
            "exact": r["exact_match"],
        }

    lifts = []
    for (pid, model), conds in sorted(by_puzzle.items()):
        if "grids_only" not in conds or "grids_and_narrative" not in conds:
            continue

        # Get grid count
        puzzle_row = db.get_puzzle(conn, pid)
        pdata = db.puzzle_to_json(puzzle_row)
        n_grids = len(pdata["sequence"])

        tau_g = conds["grids_only"]["tau"]
        tau_n = conds["grids_and_narrative"]["tau"]
        lifts.append({
            "puzzle_id": pid,
            "model": model,
            "n_grids": n_grids,
            "tau_grids": tau_g,
            "tau_narrative": tau_n,
            "lift": tau_n - tau_g,
            "exact_grids": conds["grids_only"]["exact"],
            "exact_narrative": conds["grids_and_narrative"]["exact"],
        })

    return lifts


def print_summary(lifts, model_name=None):
    """Print a text summary of ordering results."""
    if not lifts:
        click.echo("No paired results found.")
        return

    header = f"Ordering Experiment Results"
    if model_name:
        header += f" — {model_name}"
    click.echo(f"\n{header}")
    click.echo("=" * len(header))

    # Aggregate stats
    n = len(lifts)
    avg_tau_g = sum(l["tau_grids"] for l in lifts) / n
    avg_tau_n = sum(l["tau_narrative"] for l in lifts) / n
    avg_lift = sum(l["lift"] for l in lifts) / n
    exact_g = sum(l["exact_grids"] for l in lifts)
    exact_n = sum(l["exact_narrative"] for l in lifts)

    positive_lift = sum(1 for l in lifts if l["lift"] > 0)
    negative_lift = sum(1 for l in lifts if l["lift"] < 0)
    zero_lift = sum(1 for l in lifts if l["lift"] == 0)

    click.echo(f"\nPuzzles with paired results: {n}")
    click.echo(f"\n{'Condition':<25} {'Avg tau':>10} {'Exact match':>15}")
    click.echo("-" * 52)
    click.echo(f"{'Grids only':<25} {avg_tau_g:>+10.3f} {exact_g:>8}/{n}")
    click.echo(f"{'Grids + narrative':<25} {avg_tau_n:>+10.3f} {exact_n:>8}/{n}")
    click.echo(f"{'Narrative lift':<25} {avg_lift:>+10.3f}")

    click.echo(f"\nLift direction: {positive_lift} positive, "
               f"{negative_lift} negative, {zero_lift} zero")

    # By grid count
    by_n = {}
    for l in lifts:
        by_n.setdefault(l["n_grids"], []).append(l)

    click.echo(f"\n{'Grids':>5} {'N':>5} {'Avg tau_g':>10} {'Avg tau_n':>10} "
               f"{'Avg lift':>10} {'Exact_g':>8} {'Exact_n':>8}")
    click.echo("-" * 62)
    for ng in sorted(by_n):
        group = by_n[ng]
        gn = len(group)
        click.echo(
            f"{ng:>5} {gn:>5} "
            f"{sum(l['tau_grids'] for l in group)/gn:>+10.3f} "
            f"{sum(l['tau_narrative'] for l in group)/gn:>+10.3f} "
            f"{sum(l['lift'] for l in group)/gn:>+10.3f} "
            f"{sum(l['exact_grids'] for l in group):>5}/{gn} "
            f"{sum(l['exact_narrative'] for l in group):>5}/{gn}"
        )

    # Top 10 biggest narrative lifts
    sorted_lifts = sorted(lifts, key=lambda l: l["lift"], reverse=True)
    click.echo(f"\nTop 10 narrative lift:")
    click.echo(f"{'Puzzle':<15} {'Grids':>5} {'tau_g':>8} {'tau_n':>8} {'Lift':>8}")
    click.echo("-" * 48)
    for l in sorted_lifts[:10]:
        click.echo(f"{l['puzzle_id']:<15} {l['n_grids']:>5} "
                   f"{l['tau_grids']:>+8.3f} {l['tau_narrative']:>+8.3f} "
                   f"{l['lift']:>+8.3f}")

    # Bottom 10 (narrative hurts)
    click.echo(f"\nBottom 10 (narrative hurts):")
    click.echo(f"{'Puzzle':<15} {'Grids':>5} {'tau_g':>8} {'tau_n':>8} {'Lift':>8}")
    click.echo("-" * 48)
    for l in sorted_lifts[-10:]:
        click.echo(f"{l['puzzle_id']:<15} {l['n_grids']:>5} "
                   f"{l['tau_grids']:>+8.3f} {l['tau_narrative']:>+8.3f} "
                   f"{l['lift']:>+8.3f}")


def generate_html_report(lifts, output_path, model_name=None):
    """Generate an HTML report of ordering results."""
    if not lifts:
        click.echo("No results to report.")
        return

    n = len(lifts)
    avg_tau_g = sum(l["tau_grids"] for l in lifts) / n
    avg_tau_n = sum(l["tau_narrative"] for l in lifts) / n
    avg_lift = sum(l["lift"] for l in lifts) / n
    exact_g = sum(l["exact_grids"] for l in lifts)
    exact_n = sum(l["exact_narrative"] for l in lifts)
    positive = sum(1 for l in lifts if l["lift"] > 0)
    negative = sum(1 for l in lifts if l["lift"] < 0)

    # By grid count
    by_n = {}
    for l in lifts:
        by_n.setdefault(l["n_grids"], []).append(l)

    title = "NARC Ordering Experiment"
    if model_name:
        title += f" — {model_name}"

    grid_rows_html = ""
    for ng in sorted(by_n):
        group = by_n[ng]
        gn = len(group)
        grid_rows_html += f"""<tr>
            <td>{ng}</td><td>{gn}</td>
            <td>{sum(l['tau_grids'] for l in group)/gn:+.3f}</td>
            <td>{sum(l['tau_narrative'] for l in group)/gn:+.3f}</td>
            <td>{sum(l['lift'] for l in group)/gn:+.3f}</td>
            <td>{sum(l['exact_grids'] for l in group)}/{gn}</td>
            <td>{sum(l['exact_narrative'] for l in group)}/{gn}</td>
        </tr>"""

    sorted_lifts = sorted(lifts, key=lambda l: l["lift"], reverse=True)
    puzzle_rows_html = ""
    for l in sorted_lifts:
        lift_class = "text-success" if l["lift"] > 0.05 else (
            "text-danger" if l["lift"] < -0.05 else ""
        )
        puzzle_rows_html += f"""<tr>
            <td>{l['puzzle_id']}</td><td>{l['n_grids']}</td>
            <td>{l['tau_grids']:+.3f}</td>
            <td>{l['tau_narrative']:+.3f}</td>
            <td class="{lift_class}"><strong>{l['lift']:+.3f}</strong></td>
            <td>{'Y' if l['exact_grids'] else ''}</td>
            <td>{'Y' if l['exact_narrative'] else ''}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
  body {{ font-family: system-ui; max-width: 1000px; margin: 2em auto; padding: 0 1em; }}
  .metric {{ font-size: 2em; font-weight: bold; }}
  .metric-label {{ font-size: 0.8em; color: #666; }}
  table {{ font-size: 0.9em; }}
</style>
</head><body>
<h1>{title}</h1>
<p class="text-muted">Task: given all grids in shuffled order, recover the correct chronological sequence.
Measures whether narratives help models understand temporal/causal ordering.</p>

<div class="row text-center my-4">
  <div class="col"><div class="metric">{n}</div><div class="metric-label">Puzzles</div></div>
  <div class="col"><div class="metric">{avg_tau_g:+.3f}</div><div class="metric-label">Avg tau (grids only)</div></div>
  <div class="col"><div class="metric">{avg_tau_n:+.3f}</div><div class="metric-label">Avg tau (+ narrative)</div></div>
  <div class="col"><div class="metric">{avg_lift:+.3f}</div><div class="metric-label">Avg narrative lift</div></div>
</div>

<div class="row text-center mb-4">
  <div class="col"><div class="metric">{exact_g}/{n}</div><div class="metric-label">Exact match (grids only)</div></div>
  <div class="col"><div class="metric">{exact_n}/{n}</div><div class="metric-label">Exact match (+ narrative)</div></div>
  <div class="col"><div class="metric">{positive}</div><div class="metric-label">Narrative helps</div></div>
  <div class="col"><div class="metric">{negative}</div><div class="metric-label">Narrative hurts</div></div>
</div>

<h2>By Grid Count</h2>
<table class="table table-sm table-striped">
<thead><tr><th>Grids</th><th>N</th><th>Avg tau (grids)</th><th>Avg tau (narr)</th>
<th>Avg lift</th><th>Exact (grids)</th><th>Exact (narr)</th></tr></thead>
<tbody>{grid_rows_html}</tbody>
</table>

<h2>All Puzzles (sorted by lift)</h2>
<table class="table table-sm table-hover">
<thead><tr><th>Puzzle</th><th>Grids</th><th>tau (grids)</th><th>tau (narr)</th>
<th>Lift</th><th>Exact (g)</th><th>Exact (n)</th></tr></thead>
<tbody>{puzzle_rows_html}</tbody>
</table>

</body></html>"""

    with open(output_path, "w") as f:
        f.write(html)
    click.echo(f"Report written to {output_path}")


@click.command()
@click.option("--model", default=None, help="Filter to a specific model")
@click.option("--html", default=None, help="Output HTML report path")
def main(model, html):
    conn = db.init_db()

    # Check table exists
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if "ordering_trials" not in tables:
        click.echo("No ordering_trials table. Run collect_ordering.py first.")
        return

    lifts = compute_narrative_lift(conn, model_name=model)
    print_summary(lifts, model_name=model)

    if html:
        generate_html_report(lifts, html, model_name=model)


if __name__ == "__main__":
    main()
