"""Prompt templates for the odd-one-out experiment.

Design: 3 grids from the same puzzle + 1 distractor grid from a different puzzle,
shuffled. The model must identify which grid doesn't belong.

Two conditions:
  - grids_only: just the four grids, no narrative
  - grids_and_narrative: four grids + the puzzle's narrative
"""

import random
import string

from grids import COLOR_KEY, grid_to_text


def _format_grid_with_label(label, grid):
    """Format a single grid with a letter label."""
    text = grid_to_text(grid)
    rows = len(grid)
    cols = len(grid[0])
    return f"Grid {label} ({rows}x{cols}):\n{text}"


def build_oddoneout_grids_only(puzzle_grids, distractor_grid, distractor_pos):
    """Build prompt for grids-only condition.

    Args:
        puzzle_grids: list of 3 grids from the puzzle
        distractor_grid: 1 grid from a different puzzle
        distractor_pos: 0-3 index where distractor is placed

    Returns:
        (messages, correct_answer) where correct_answer is the letter (A-D)
    """
    # Assemble 4 grids with distractor at specified position
    all_grids = list(puzzle_grids)
    all_grids.insert(distractor_pos, distractor_grid)
    labels = list(string.ascii_uppercase[:4])
    correct_label = labels[distractor_pos]

    grid_texts = []
    for i, grid in enumerate(all_grids):
        grid_texts.append(_format_grid_with_label(labels[i], grid))

    system = f"""\
You are solving an odd-one-out puzzle.

You are shown four grids. Three of them belong to the same sequence (they are \
part of the same story). One grid is a distractor — it comes from a different \
sequence entirely.

Your task: identify which grid is the odd one out.

{COLOR_KEY}

Provide your answer as a JSON object:
{{"reasoning": "your step-by-step reasoning", "odd_one_out": "X"}}

where X is the letter (A, B, C, or D) of the grid that doesn't belong.

IMPORTANT: You MUST end your response with your final answer as a JSON object."""

    user = "\n\n".join(grid_texts)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return messages, correct_label


def build_oddoneout_grids_and_narrative(puzzle_grids, distractor_grid,
                                        distractor_pos, narrative):
    """Build prompt for grids+narrative condition."""
    all_grids = list(puzzle_grids)
    all_grids.insert(distractor_pos, distractor_grid)
    labels = list(string.ascii_uppercase[:4])
    correct_label = labels[distractor_pos]

    grid_texts = []
    for i, grid in enumerate(all_grids):
        grid_texts.append(_format_grid_with_label(labels[i], grid))

    system = f"""\
You are solving an odd-one-out puzzle.

You are shown four grids and a narrative clue. Three of the grids belong to the \
same sequence — they are part of the story described by the narrative. One grid \
is a distractor from a different sequence entirely.

Your task: identify which grid is the odd one out.

{COLOR_KEY}

Provide your answer as a JSON object:
{{"reasoning": "your step-by-step reasoning", "odd_one_out": "X"}}

where X is the letter (A, B, C, or D) of the grid that doesn't belong.

IMPORTANT: You MUST end your response with your final answer as a JSON object."""

    user = "\n\n".join(grid_texts)
    user += f"\n\nNarrative clue: {narrative}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return messages, correct_label


def build_oddoneout_extraction(reasoning_text):
    """Build extraction prompt for pass 2."""
    return [
        {"role": "system", "content": (
            "Extract the odd-one-out answer from the reasoning below. "
            "Return ONLY a JSON object: {\"odd_one_out\": \"X\"} "
            "where X is A, B, C, or D."
        )},
        {"role": "user", "content": reasoning_text},
    ]
