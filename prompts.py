"""Prompt templates for the NARC 3-condition testing pipeline."""

import re

from grids import COLOR_KEY, sequence_to_text

# Function words stripped by extract_keywords. Spatial words (up, down, above,
# below, between, through...) are deliberately NOT stopwords — in a grid puzzle
# they carry content. Temporal/causal connectives (before, then, because...)
# ARE stripped: they carry the narrative structure this ablation removes.
STOPWORDS = frozenset("""
a about after again all am an and any are as at be because been before being
both but by can could did do does doing done during each few for from further
had has have having he her here hers herself him himself his how i if in into
is it its itself just me might more most must my myself no nor not now of on
once only or other our ours ourselves own shall she should so some such than
that the their theirs them themselves then there these they this those to too
until upon very was we were what when where which while who whom whose why
will with would you your yours yourself yourselves
""".split())


def _mask_info(puzzle):
    """Build text describing which grids are masked and their dimensions."""
    masked_positions = puzzle["masked_positions"]
    sequence = puzzle["sequence"]
    answer_grids = puzzle["answer_grids"]

    if len(masked_positions) == 1:
        pos = masked_positions[0]
        item = sequence[pos]
        return (f"Grid {pos + 1} is masked ({item['rows']}x{item['cols']}).\n"
                f"Reconstruct it as a 2D array of integers.")
    else:
        lines = [f"{len(masked_positions)} grids are masked. Reconstruct each one:"]
        for pos in masked_positions:
            item = sequence[pos]
            lines.append(f"  - Grid {pos + 1}: {item['rows']}x{item['cols']}")
        return "\n".join(lines)


def _system_prompt(puzzle):
    n = len(puzzle["masked_positions"])
    if n == 1:
        pos = puzzle["masked_positions"][0]
        return f"""\
You are solving a NARC puzzle (Narrative Augmented Reasoning Challenge).

A NARC puzzle consists of a sequence of grids that tell an abstract story. \
One grid in the sequence is masked. Your task is to reconstruct the masked \
grid pixel-perfectly.

Provide your answer as a JSON object:
{{"reasoning": "your step-by-step reasoning", "output_grids": {{"{pos}": [[int, ...], ...]}}}}

The grid must be a 2D array of integers (0-9) representing colors.
The key is the grid position (0-indexed).

IMPORTANT: You MUST end your response with your final answer as a JSON object. \
Even if uncertain, commit to your best guess. Do not end mid-reasoning."""
    else:
        keys = ", ".join(f'"{p}"' for p in puzzle["masked_positions"])
        return f"""\
You are solving a NARC puzzle (Narrative Augmented Reasoning Challenge).

A NARC puzzle consists of a sequence of grids that tell an abstract story. \
{n} grids in the sequence are masked. Your task is to reconstruct ALL masked \
grids pixel-perfectly.

Provide your answer as a JSON object:
{{"reasoning": "your step-by-step reasoning", "output_grids": {{{keys}: [[int, ...], ...]}}}}

Each grid must be a 2D array of integers (0-9) representing colors.
Keys are grid positions (0-indexed).

IMPORTANT: You MUST end your response with your final answer as a JSON object. \
Even if uncertain, commit to your best guess. Do not end mid-reasoning."""


def build_grids_only(puzzle):
    """Build prompt for grids_only condition: grid sequence, no narrative."""
    seq_text = sequence_to_text(puzzle["sequence"], puzzle["masked_positions"])
    mask_info = _mask_info(puzzle)

    user_msg = f"""{COLOR_KEY}

{seq_text}

{mask_info}"""

    return [
        {"role": "system", "content": _system_prompt(puzzle)},
        {"role": "user", "content": user_msg},
    ]


def build_narrative_only(puzzle, narrative=None):
    """Build prompt for narrative_only condition: narrative, no grids."""
    narrative = narrative or puzzle["narrative"]
    num_grids = len(puzzle["sequence"])
    mask_info = _mask_info(puzzle)
    masked_list = ", ".join(str(p + 1) for p in puzzle["masked_positions"])

    user_msg = f"""{COLOR_KEY}

You are given a narrative describing a sequence of grids that tell an abstract story. \
Reconstruct the masked grid(s) based on the narrative alone.

Narrative: "{narrative}"

The puzzle has {num_grids} grids in sequence. Grid(s) {masked_list} masked.
{mask_info}"""

    return [
        {"role": "system", "content": _system_prompt(puzzle)},
        {"role": "user", "content": user_msg},
    ]


def build_both(puzzle, narrative=None):
    """Build prompt for both condition: grid sequence + narrative."""
    narrative = narrative or puzzle["narrative"]
    seq_text = sequence_to_text(puzzle["sequence"], puzzle["masked_positions"])
    mask_info = _mask_info(puzzle)

    user_msg = f"""{COLOR_KEY}

Narrative: "{narrative}"

{seq_text}

{mask_info}
Use the narrative and visible grids together to reconstruct the masked grid(s)."""

    return [
        {"role": "system", "content": _system_prompt(puzzle)},
        {"role": "user", "content": user_msg},
    ]


def extract_keywords(narrative):
    """Deterministic key-term extraction for the narrative-sensitivity test.

    Lowercase -> tokenize (letters, digits, internal apostrophes/hyphens) ->
    drop stopwords -> dedupe -> alphabetize. Alphabetizing matters: a list in
    clue order would leak the narrative sequence this ablation removes.
    """
    tokens = re.findall(r"[a-z0-9]+(?:['-][a-z0-9]+)*", narrative.lower())
    return sorted({t for t in tokens if t not in STOPWORDS})


def build_both_keywords(puzzle, narrative=None):
    """Build prompt for both_keywords condition: grids + alphabetized key terms.

    Identical to build_both except the narrative clue is replaced by the bag of
    its content words, so the contrast between the two conditions isolates
    narrative form from lexical content. Never presented as a story or clue.
    """
    narrative = narrative or puzzle["narrative"]
    keywords = extract_keywords(narrative)
    seq_text = sequence_to_text(puzzle["sequence"], puzzle["masked_positions"])
    mask_info = _mask_info(puzzle)

    user_msg = f"""{COLOR_KEY}

Key terms (unordered): {", ".join(keywords)}

{seq_text}

{mask_info}
Use the key terms and visible grids together to reconstruct the masked grid(s)."""

    return [
        {"role": "system", "content": _system_prompt(puzzle)},
        {"role": "user", "content": user_msg},
    ]


def build_extraction(reasoning):
    """Build pass-2 extraction prompt from model reasoning."""
    return [
        {"role": "system", "content": (
            "You are a JSON formatter. Do NOT reason, explain, or think. "
            "Read the text below and output ONLY the final answer grid as JSON.\n\n"
            "Format: {\"output_grids\": {\"<position>\": [[int, ...], ...]}}\n\n"
            "Rules:\n"
            "- Output raw JSON only. No markdown, no commentary.\n"
            "- Grid values are integers 0-9.\n"
            "- Position keys are 0-indexed integers as strings.\n"
            "- If the text contains multiple grid attempts, use the LAST one."
        )},
        {"role": "user", "content": reasoning},
    ]


def build_extraction_strict(reasoning, masked_positions, dimensions):
    """Build a strict pass-3 extraction prompt with explicit dimensions.

    Used as a retry when the standard extraction fails to parse.
    """
    grid_specs = []
    for pos, (rows, cols) in zip(masked_positions, dimensions):
        grid_specs.append(f'  Position {pos}: {rows} rows x {cols} cols')
    spec_text = '\n'.join(grid_specs)

    example_keys = ", ".join(f'"{p}": [[int, ...], ...]' for p in masked_positions)
    return [
        {"role": "system", "content": (
            "Output ONLY a JSON object. No text before or after.\n"
            '{"output_grids": {' + example_keys + '}}'
        )},
        {"role": "user", "content": (
            f"Extract the final answer grid from this reasoning. "
            f"The grid must have these exact dimensions:\n{spec_text}\n\n"
            f"If no clear grid is stated, make your best guess from the reasoning.\n\n"
            f"---\n{reasoning[-3000:]}"
        )},
    ]
