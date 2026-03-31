"""Generate NARC puzzles that maximize the gap between
"abstract visual grids" and "intuitive with narrative."

Three strategies:

Strategy 1 — VISUAL-FIRST (Maximum Ambiguity)
  Start with grid sequences that look random/meaningless.
  The grids follow a hidden rule that only becomes obvious with narrative.
  Goal: grids that look like noise until the story clicks.

Strategy 2 — NARRATIVE-FIRST (Maximum Aha)
  Start with a compelling, well-known story or concept.
  Design grids that abstractly encode it — but so abstractly that
  without the story, you'd never guess what they represent.
  Goal: the "oh, of COURSE!" moment when narrative is revealed.

Strategy 3 — REMIX (Alternative grids for existing narratives,
             or alternative narratives for existing grids)
  Take existing puzzles and either:
  (a) Keep the grid sequence, write a completely different narrative
      that ALSO makes the masked grid uniquely determined (but via
      different reasoning)
  (b) Keep the narrative, redesign the grids to be more abstract/less
      guessable without the narrative
  Goal: test whether the NARC property transfers across representations.

Usage:
    python generate_gap_puzzles.py --strategy visual-first --count 5
    python generate_gap_puzzles.py --strategy narrative-first --count 5
    python generate_gap_puzzles.py --strategy remix --count 5 --source narc_001
"""

import json
import subprocess
from pathlib import Path

import click

PUZZLES_DIR = Path(__file__).parent / "data" / "puzzles"


def call_claude(prompt):
    """Use Claude subagent to generate content."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--tools", "", "--model", "opus",
             "--output-format", "text", "--no-session-persistence"],
            capture_output=True, text=True, timeout=180
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        click.echo(f"  Subagent error: {e}")
        return None


# ---------------------------------------------------------------------------
# Strategy 1: VISUAL-FIRST
# ---------------------------------------------------------------------------

VISUAL_FIRST_PROMPT = """You are designing a NARC puzzle (Narrative Augmented Reasoning Challenge).

STRATEGY: Visual-First — Maximum Ambiguity.

Design a grid sequence where:
1. The visible grids follow a hidden rule that is NOT obvious from visual inspection.
2. There are at least 3 plausible completions for the masked grid.
3. The narrative clue reveals the hidden rule, making exactly one completion correct.
4. The "aha moment" should be strong — grids that looked random suddenly make sense.

Good techniques for maximum ambiguity:
- Use modular arithmetic patterns (values mod N follow a rule humans can't see)
- Use relationships BETWEEN non-adjacent grids (not just sequential differences)
- Use spatial transformations that aren't simple rotations/translations
- Use rules that operate on rows/columns independently (hard to see as a gestalt)
- Use "exceptions" to apparent patterns (the pattern has one deliberate break)

The puzzle should have {num_grids} grids, each {rows}x{cols}.

Return ONLY valid JSON in this format:
{{
  "puzzle_id": "{puzzle_id}",
  "title": "...",
  "narrative": "...",
  "sequence": [...],
  "masked_positions": [...],
  "answer_grids": {{...}},
  "metadata": {{"creator": "claude", "created_at": "2026-03-30", "difficulty": "hard",
               "tags": ["visual-first", "gap-maximizing"]}}
}}

The narrative should make the masked grid feel INEVITABLE once you read it,
even though it was completely unpredictable before."""


# ---------------------------------------------------------------------------
# Strategy 2: NARRATIVE-FIRST
# ---------------------------------------------------------------------------

NARRATIVE_FIRST_PROMPT = """You are designing a NARC puzzle (Narrative Augmented Reasoning Challenge).

STRATEGY: Narrative-First — Maximum Aha Moment.

Start with this story/concept: "{concept}"

Design a grid sequence that abstractly encodes this story, where:
1. The grids use colors/shapes to represent story elements, but so abstractly
   that without knowing the story, you'd never guess what they represent.
2. One grid is masked at a critical story moment.
3. The narrative clue connects the abstract patterns to the story, making the
   masked grid's content suddenly obvious.
4. A reader who knows the story should have an "of COURSE!" reaction.

The puzzle should have {num_grids} grids. Grid sizes can vary if it serves the story.

Good techniques for maximum aha:
- Map story characters to colors, but don't make the mapping obvious from the grids alone
- Use spatial position to represent abstract concepts (status, time, relationship)
- Make the "twist" in the story correspond to a visual surprise in the masked grid
- The grids should look like abstract art without the narrative, but like a storyboard with it

Return ONLY valid JSON in the standard NARC format.

The key: someone looking at just the grids should think "this could be anything."
Someone reading the narrative should think "this could only be one thing." """


# ---------------------------------------------------------------------------
# Strategy 3: REMIX
# ---------------------------------------------------------------------------

REMIX_GRID_PROMPT = """You are redesigning the grids for an existing NARC puzzle.

The original puzzle "{puzzle_id}" has this narrative:
"{narrative}"

And this answer for the masked grid(s): {answer_grids}

The original grids were too easy to guess without the narrative.
Redesign the grid sequence to be MORE ABSTRACT — harder to guess the pattern
from the grids alone, while the narrative still uniquely determines the answer.

Techniques:
- Add visual noise (extra colors that don't affect the rule)
- Use less obvious spatial arrangements
- Make the "default" pattern more ambiguous (more plausible completions)
- Change grid sizes if it helps obscure the pattern

Keep the same masked positions and answer grids. Return ONLY valid JSON."""


REMIX_NARRATIVE_PROMPT = """You have an existing NARC puzzle with this grid sequence:

{grid_description}

Masked position(s): {masked_positions}
Answer grid(s): {answer_grids}

The original narrative was: "{original_narrative}"

Write a COMPLETELY DIFFERENT narrative that ALSO makes the masked grid uniquely
determined, but through different reasoning. The new narrative should:
1. Come from a different domain/metaphor than the original
2. Encode the same disambiguating information via a different logical path
3. Be evocative and puzzle-like, not a direct instruction

Return ONLY the new narrative text (not JSON)."""


@click.command()
@click.option("--strategy", type=click.Choice(["visual-first", "narrative-first", "remix"]),
              required=True)
@click.option("--count", default=5, type=int)
@click.option("--source", default=None, help="Source puzzle ID for remix strategy")
@click.option("--start-id", default=100, type=int, help="Starting puzzle number")
def main(strategy, count, source, start_id):
    click.echo(f"Strategy: {strategy}, generating {count} puzzles")

    if strategy == "visual-first":
        configs = [
            (4, 4, 4), (5, 5, 3), (3, 6, 6), (6, 4, 4), (5, 3, 5),
            (4, 5, 5), (7, 3, 3), (3, 4, 4), (8, 3, 3), (5, 4, 5),
        ]
        for i in range(count):
            pid = f"narc_gap_{start_id + i:03d}"
            num_grids, rows, cols = configs[i % len(configs)]
            click.echo(f"\n{pid}: {num_grids} grids, {rows}x{cols}")

            prompt = VISUAL_FIRST_PROMPT.format(
                num_grids=num_grids, rows=rows, cols=cols, puzzle_id=pid
            )
            result = call_claude(prompt)
            if result:
                _save_puzzle(pid, result)
            else:
                click.echo("  FAILED")

    elif strategy == "narrative-first":
        concepts = [
            "The Trolley Problem — but the trolley is already past the switch, and the question is whether to confess",
            "A murmuration of starlings — thousands of individuals following simple rules create emergent beauty",
            "The heat death of the universe — all energy differences gradually equalizing",
            "A rumor spreading through a social network — true information corrupted at each retelling",
            "The Ship of Theseus — but told from the perspective of the replaced planks",
            "Gödel's incompleteness — a system that contains a statement about itself it cannot prove",
            "A tide pool ecosystem over one day — the cast of characters changes with each wave",
            "The stages of grief — denial, anger, bargaining, depression, acceptance",
            "A jazz improvisation — theme stated, variations explored, original theme transformed",
            "The prisoner's last meal — abundance surrounded by constraint",
        ]
        for i in range(count):
            pid = f"narc_gap_{start_id + i:03d}"
            concept = concepts[i % len(concepts)]
            num_grids = [5, 6, 7, 4, 5, 3, 8, 5, 6, 4][i % 10]
            click.echo(f"\n{pid}: '{concept[:50]}...' ({num_grids} grids)")

            prompt = NARRATIVE_FIRST_PROMPT.format(
                concept=concept, num_grids=num_grids
            )
            result = call_claude(prompt)
            if result:
                _save_puzzle(pid, result)
            else:
                click.echo("  FAILED")

    elif strategy == "remix":
        if not source:
            # Remix all existing puzzles
            sources = sorted(f.stem for f in PUZZLES_DIR.glob("narc_0*.json"))[:count]
        else:
            sources = [source]

        for src_id in sources:
            src_path = PUZZLES_DIR / f"{src_id}.json"
            if not src_path.exists():
                click.echo(f"Source {src_id} not found, skipping")
                continue

            src = json.loads(src_path.read_text())
            click.echo(f"\nRemixing {src_id}: {src['title']}")

            # Generate alternative narrative
            from grids import sequence_to_text
            grid_desc = sequence_to_text(src["sequence"], src["masked_positions"])

            prompt = REMIX_NARRATIVE_PROMPT.format(
                grid_description=grid_desc,
                masked_positions=src["masked_positions"],
                answer_grids=json.dumps(src["answer_grids"]),
                original_narrative=src["narrative"]
            )
            result = call_claude(prompt)
            if result:
                click.echo(f"  New narrative: {result[:80]}...")
                # Store as variant
                import db
                conn = db.init_db()
                db.upsert_variant(conn, src_id, "remix", result,
                                  source_domain="remix", generator="claude")
                conn.close()
            else:
                click.echo("  FAILED")


def _save_puzzle(puzzle_id, raw_json):
    """Parse and save a puzzle from Claude's response."""
    # Extract JSON from response
    text = raw_json.strip()
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try finding JSON object
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                data = json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                click.echo("  Could not parse JSON")
                return
        else:
            click.echo("  No JSON found")
            return

    data["puzzle_id"] = puzzle_id
    out_path = PUZZLES_DIR / f"{puzzle_id}.json"
    out_path.write_text(json.dumps(data, indent=2))
    click.echo(f"  Saved: {data.get('title', '?')} ({len(data.get('sequence', []))} grids)")


if __name__ == "__main__":
    main()
