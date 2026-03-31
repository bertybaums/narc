"""Generate alternative narratives for NARC-verified puzzles.

Uses Claude subagents to produce domain-diverse alternative narratives,
mirroring MARC2's Phase 8 approach with opacity guidance.

Usage:
    python generate_alternatives.py [--puzzle PUZZLE_ID] [--domains N]
"""

import json
import subprocess

import click
import db

SOURCE_DOMAINS = [
    "biology", "cooking", "music", "sports", "weather",
    "architecture", "warfare", "theater", "gardening", "astronomy",
    "ocean/sailing", "electronics", "mythology", "dance", "geology",
]


def generate_alternative(puzzle_data, original_narrative, source_domain):
    """Use Claude subagent to generate an alternative narrative."""
    prompt = f"""You are generating an alternative narrative for a NARC puzzle.

The original narrative is:
"{original_narrative}"

The puzzle has {len(puzzle_data['sequence'])} grids. Grid {puzzle_data['masked_position'] + 1} is masked.
The narrative must make the masked grid uniquely determinable when combined with the visible grids.

Generate a NEW narrative using imagery and metaphors from the domain of **{source_domain}**.

OPACITY GUIDANCE: The narrative should be:
- Ambiguous enough that someone reading ONLY the narrative cannot reconstruct the grid
- Evocative enough that someone seeing the grid sequence AND the narrative can determine the masked grid
- The narrative should NOT directly describe grid coordinates, colors, or pixel positions
- Instead, use the {source_domain} metaphor to convey the same structural/logical information

Return ONLY the alternative narrative text, nothing else."""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--tools", "", "--model", "opus",
             "--output-format", "text", "--no-session-persistence"],
            capture_output=True, text=True, timeout=120
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        click.echo(f"  Subagent error: {e}")
        return None


@click.command()
@click.option("--puzzle", default=None, help="Single puzzle ID")
@click.option("--domains", default=5, type=int, help="Number of domains to use (max 15)")
@click.option("--model", default="gpt-oss-120b", help="Model for NARC-verified filter")
def main(puzzle, domains, model):
    conn = db.init_db()
    domains_to_use = SOURCE_DOMAINS[:min(domains, len(SOURCE_DOMAINS))]

    if puzzle:
        puzzle_rows = [db.get_puzzle(conn, puzzle)]
    else:
        # Get NARC-verified puzzles
        rows = conn.execute(
            "SELECT puzzle_id FROM classifications WHERE model_name=? AND has_narc=1",
            (model,)
        ).fetchall()
        puzzle_rows = [db.get_puzzle(conn, r["puzzle_id"]) for r in rows]

        if not puzzle_rows:
            click.echo("No NARC-verified puzzles found. Running on all puzzles instead.")
            puzzle_rows = db.get_all_puzzles(conn)

    click.echo(f"Generating alternatives: {len(puzzle_rows)} puzzles x {len(domains_to_use)} domains")

    for puzzle_row in puzzle_rows:
        puzzle_data = db.puzzle_to_json(puzzle_row)
        pid = puzzle_data["puzzle_id"]
        original_narrative = puzzle_data["narrative"]

        click.echo(f"\n{pid}: {puzzle_data['title']}")

        for domain in domains_to_use:
            # Check if already exists
            existing = conn.execute(
                "SELECT variant_id FROM narrative_variants WHERE puzzle_id=? AND variant=?",
                (pid, domain)
            ).fetchone()
            if existing:
                click.echo(f"  {domain}: already exists, skipping")
                continue

            click.echo(f"  {domain}: generating...", nl=False)
            alt_narrative = generate_alternative(puzzle_data, original_narrative, domain)

            if alt_narrative:
                db.upsert_variant(conn, pid, domain, alt_narrative,
                                  source_domain=domain, generator="claude")
                click.echo(f" OK ({len(alt_narrative)} chars)")
            else:
                click.echo(" FAILED")

    conn.close()
    click.echo("\nDone.")


if __name__ == "__main__":
    main()
