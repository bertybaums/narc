"""Reset transport-failed trials so the collect scripts re-run them.

run_trial stores an HTTP/network failure (MindRouter 5xx, timeout, DNS) in
trials.error, and get_pending_trials skips any row with error set — so a
transient outage permanently leaves that cell unanswered. This script NULLs
the error on such rows for one model, turning them back into pending trials;
then re-run collect.py / collect_matrix.py / collect_sensitivity.py /
collect_narrative_sensitivity.py (ideally --concurrency 1) to fill them.

Parse errors ("Could not parse grids…", "Bad grid at position…") are NOT
transport failures — the model answered, the answer just wasn't a grid. They
are left alone unless --parse-errors is passed.

Usage:
    python retry_errors.py --model qwen3.8-27b [--parse-errors] [--dry-run]
"""

import click

import db

# Substrings that mark a failure before/at the HTTP layer (see prod error
# survey Sep 2026: "Server error '500 …'", "[Errno 8] nodename…",
# "The read operation timed out", "Server error '502 Bad Gateway'").
TRANSPORT_PATTERNS = [
    "Server error '5",
    "Client error '429",
    "[Errno ",
    "timed out",
    "Timeout",
    "Connection",
    "Bad Gateway",
    "Service Unavailable",
    "Environment variable",  # MINDROUTER_API_KEY missing at run time
]

PARSE_PATTERNS = [
    "Could not parse grids",
    "No output_grids",
    "Bad grid at position",
    "Empty response",
]


def _where(patterns):
    return " OR ".join("error LIKE ?" for _ in patterns), [f"%{p}%" for p in patterns]


@click.command()
@click.option("--model", required=True, help="Model name (config.yaml `name`)")
@click.option("--parse-errors", is_flag=True,
              help="Also reset parse errors (model answered, no grid found)")
@click.option("--dry-run", is_flag=True, help="Report counts only")
def main(model, parse_errors, dry_run):
    patterns = list(TRANSPORT_PATTERNS) + (list(PARSE_PATTERNS) if parse_errors else [])
    clause, params = _where(patterns)
    conn = db.init_db()
    try:
        rows = conn.execute(
            f"SELECT substr(error,1,60) e, COUNT(*) n FROM trials "
            f"WHERE model_name=? AND error IS NOT NULL AND ({clause}) "
            f"GROUP BY e ORDER BY n DESC",
            [model] + params,
        ).fetchall()
        total = sum(r["n"] for r in rows)
        other = conn.execute(
            f"SELECT COUNT(*) FROM trials WHERE model_name=? AND error IS NOT NULL "
            f"AND NOT ({clause})", [model] + params,
        ).fetchone()[0]
        click.echo(f"{model}: {total} retryable errored trial(s), {other} left alone")
        for r in rows:
            click.echo(f"  {r['n']:5d}  {r['e']}")
        if dry_run or not total:
            return
        cur = conn.execute(
            f"UPDATE trials SET error=NULL, raw_response=NULL, response_text=NULL, "
            f"latency_ms=NULL WHERE model_name=? AND error IS NOT NULL AND ({clause})",
            [model] + params,
        )
        conn.commit()
        click.echo(f"Reset {cur.rowcount} trial(s) to pending. Re-run the collect "
                   f"scripts for {model} to fill them.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
