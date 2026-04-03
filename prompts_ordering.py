"""Prompt templates for the NARC ordering experiment.

Given all grids (including solution grids) in a shuffled order,
the model must recover the correct chronological sequence.

Two conditions:
  - grids_only: shuffled grids, no narrative
  - grids_and_narrative: shuffled grids + narrative clue
"""

import hashlib
import random
import string

from grids import COLOR_KEY, grid_to_text


def _deterministic_shuffle(items, puzzle_id):
    """Return a shuffled copy of items using a seed derived from puzzle_id."""
    seed = int(hashlib.sha256(puzzle_id.encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    return shuffled


def build_complete_sequence(puzzle):
    """Build the full grid sequence with answer grids inserted at masked positions."""
    sequence = puzzle["sequence"]
    answer_grids = puzzle["answer_grids"]
    masked_positions = set(puzzle["masked_positions"])

    complete = []
    for item in sequence:
        pos = item["position"]
        if pos in masked_positions:
            grid = answer_grids[str(pos)]
            complete.append({
                "position": pos,
                "rows": item["rows"],
                "cols": item["cols"],
                "grid": grid,
                "label": item.get("label", ""),
            })
        else:
            complete.append(item)
    return complete


def shuffle_sequence(puzzle):
    """Return (shuffled_grids, letter_labels, correct_order).

    shuffled_grids: list of grid dicts in shuffled order
    letter_labels: list of letter labels (A, B, C, ...) for the shuffled grids
    correct_order: list of letters in the correct chronological order
    """
    complete = build_complete_sequence(puzzle)
    n = len(complete)
    labels = list(string.ascii_uppercase[:n])

    # Create position indices and shuffle them
    indices = list(range(n))
    shuffled_indices = _deterministic_shuffle(indices, puzzle["puzzle_id"])

    # Map: shuffled_position -> (label, original_grid)
    shuffled_grids = []
    # correct_order[i] = the label assigned to the grid whose true position is i
    position_to_label = {}
    for label_idx, orig_idx in enumerate(shuffled_indices):
        label = labels[label_idx]
        position_to_label[orig_idx] = label
        shuffled_grids.append((label, complete[orig_idx]))

    correct_order = [position_to_label[i] for i in range(n)]
    return shuffled_grids, labels, correct_order


def _system_prompt(n):
    """System prompt for the ordering task."""
    return f"""\
You are solving a NARC ordering puzzle (Narrative Augmented Reasoning Challenge).

You are given {n} grids labeled with letters. These grids form a sequence that \
tells an abstract story, but they have been shuffled out of order. Your task is \
to determine the correct chronological order of the grids.

Provide your answer as a JSON object:
{{"reasoning": "your step-by-step reasoning", "order": ["X", "Y", "Z", ...]}}

The "order" array must contain all {n} letter labels in the correct sequence, \
from first to last.

IMPORTANT: You MUST end your response with your final answer as a JSON object. \
Even if uncertain, commit to your best guess. Do not end mid-reasoning."""


def _format_shuffled_grids(shuffled_grids):
    """Format shuffled grids as text for the prompt."""
    parts = []
    for label, item in shuffled_grids:
        rows = item["rows"]
        cols = item["cols"]
        grid_text = grid_to_text(item["grid"])
        parts.append(f"Grid {label} ({rows}x{cols}):\n{grid_text}")
    return "\n\n".join(parts)


def build_ordering_grids_only(puzzle):
    """Shuffled grids, no narrative."""
    shuffled_grids, labels, correct_order = shuffle_sequence(puzzle)
    n = len(shuffled_grids)
    grids_text = _format_shuffled_grids(shuffled_grids)

    user_msg = f"""{COLOR_KEY}

The following {n} grids have been shuffled. Determine their correct chronological order.

{grids_text}

Provide the correct order as a JSON array of letters."""

    return [
        {"role": "system", "content": _system_prompt(n)},
        {"role": "user", "content": user_msg},
    ], correct_order


def build_ordering_grids_and_narrative(puzzle, narrative=None):
    """Shuffled grids + narrative clue."""
    narrative = narrative or puzzle["narrative"]
    shuffled_grids, labels, correct_order = shuffle_sequence(puzzle)
    n = len(shuffled_grids)
    grids_text = _format_shuffled_grids(shuffled_grids)

    user_msg = f"""{COLOR_KEY}

Narrative: "{narrative}"

The following {n} grids have been shuffled. They tell the story described in the \
narrative above. Determine their correct chronological order.

{grids_text}

Use the narrative to help determine the correct order. \
Provide the correct order as a JSON array of letters."""

    return [
        {"role": "system", "content": _system_prompt(n)},
        {"role": "user", "content": user_msg},
    ], correct_order


def build_ordering_extraction(reasoning):
    """Pass-2 extraction prompt for ordering responses."""
    return [
        {"role": "system", "content": (
            "You are a JSON formatter. Do NOT reason, explain, or think. "
            "Read the text below and output ONLY the final ordering as JSON.\n\n"
            'Format: {"order": ["A", "B", "C", ...]}\n\n'
            "Rules:\n"
            "- Output raw JSON only. No markdown, no commentary.\n"
            "- The order array must contain every letter label exactly once.\n"
            "- If the text contains multiple ordering attempts, use the LAST one."
        )},
        {"role": "user", "content": reasoning},
    ]
