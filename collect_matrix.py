"""Run the (narrative variant x mask variant) test matrix on a subject model.

Only cells the creator enabled in variant_pairs are run. Each cell uses the mask
variant's positions and the narrative variant's clue; trials are tagged with both
variant_id and mask_variant_id.

Usage:
    python collect_matrix.py [--model MODEL] [--puzzle PUZZLE_ID] [--concurrency N] [--dry-run]
"""

import click

from collect import run_matrix_job


@click.command()
@click.option("--model", default="gpt-oss-120b", help="Subject model name")
@click.option("--puzzle", default=None, help="Single puzzle ID (default: all with enabled pairs)")
@click.option("--concurrency", default=8, type=int, help="Max parallel requests")
@click.option("--dry-run", is_flag=True, help="List planned trials without calling the API or writing rows")
def main(model, puzzle, concurrency, dry_run):
    run_matrix_job(model=model, puzzle=puzzle, concurrency=concurrency,
                   dry_run=dry_run, log_fn=click.echo)


if __name__ == "__main__":
    main()
