"""Order-sensitivity (weak/strong NARC) collection.

For every NARC cell of a model, re-run the winning `both` condition over K
shuffled grid orders (same grids, same narrative). classify then labels each NARC
cell strong / partial / weak. Resumable (skips completed trials); --dry-run is
read-only.

Usage:
    python collect_sensitivity.py [--model MODEL | --all-models] [--puzzle PID]
                                  [--shuffles K] [--concurrency N] [--dry-run]

--all-models backfills every model that currently has NARC cells, then runs
classify.py for each so the strength verdict is stored.
"""

import click

import db
from classify import run_classify_job
from collect import run_sensitivity_job


def _narc_models():
    conn = db.init_db()
    try:
        return [r["model_name"] for r in conn.execute(
            "SELECT DISTINCT model_name FROM classifications WHERE has_narc=1 "
            "ORDER BY model_name"
        ).fetchall()]
    finally:
        conn.close()


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Subject model name")
@click.option("--all-models", is_flag=True,
              help="Backfill every model with NARC cells (overrides --model)")
@click.option("--puzzle", default=None, help="Single puzzle ID (default: all NARC cells)")
@click.option("--shuffles", default=None, type=int,
              help="Shuffles per cell (default: experiment.shuffles in config.yaml)")
@click.option("--concurrency", default=8, type=int, help="Max parallel requests")
@click.option("--dry-run", is_flag=True,
              help="List planned trials without calling the API or writing rows")
@click.option("--no-classify", is_flag=True,
              help="Skip the classify pass after collection")
def main(model, all_models, puzzle, shuffles, concurrency, dry_run, no_classify):
    targets = _narc_models() if all_models else [model]
    click.echo(f"Sensitivity targets: {targets}")
    for m in targets:
        click.echo(f"\n===== {m} =====")
        # Pre-classify (unless skipped): recompute the NARC set with current logic
        # so sensitivity only shuffles cells that are genuinely NARC now, not stale
        # orphan rows. classify is authoritative and cheap (no API calls).
        if not dry_run and not no_classify:
            click.echo(f"----- pre-classify {m} -----")
            run_classify_job(model=m, puzzle=puzzle, log_fn=click.echo)
        res = run_sensitivity_job(model=m, puzzle=puzzle, shuffles=shuffles,
                                  concurrency=concurrency, dry_run=dry_run,
                                  log_fn=click.echo)
        click.echo(f"  sensitivity: {res}")
        if not dry_run and not no_classify:
            click.echo(f"----- classify {m} (write strength) -----")
            cres = run_classify_job(model=m, puzzle=puzzle, log_fn=click.echo)
            click.echo(f"  classify: {cres}")


if __name__ == "__main__":
    main()
