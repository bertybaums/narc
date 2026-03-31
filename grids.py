"""Grid utilities: text <-> grid conversion, PNG rendering, response parsing.

Ported from MARC2 grids.py with additions for NARC grid sequences.
"""

import base64
import io
import json
import re
from typing import List

from PIL import Image, ImageDraw

# ARC color index -> single-character code
COLOR_CODES = {
    0: ".",  # black
    1: "B",  # blue
    2: "R",  # red
    3: "G",  # green
    4: "Y",  # yellow
    5: "X",  # grey
    6: "M",  # magenta
    7: "O",  # orange
    8: "A",  # azure
    9: "W",  # maroon
}

CODE_TO_INDEX = {v: k for k, v in COLOR_CODES.items()}

COLOR_KEY = "Color key: .=black B=blue R=red G=green Y=yellow X=grey M=magenta O=orange A=azure W=maroon"

# ARC color index -> RGB for PNG rendering
COLOR_RGB = {
    0: (0, 0, 0),
    1: (0, 116, 217),
    2: (255, 65, 54),
    3: (46, 204, 64),
    4: (255, 220, 0),
    5: (170, 170, 170),
    6: (240, 18, 190),
    7: (255, 133, 27),
    8: (127, 219, 255),
    9: (128, 0, 0),
}

CELL_SIZE = 20
BORDER_WIDTH = 1
BORDER_COLOR = (128, 128, 128)


def grid_to_text(grid: List[List[int]]) -> str:
    return "\n".join(" ".join(COLOR_CODES[cell] for cell in row) for row in grid)


def text_to_grid(text: str) -> List[List[int]]:
    grid = []
    for line in text.strip().split("\n"):
        row = [CODE_TO_INDEX[c] for c in line.split()]
        grid.append(row)
    return grid


def grid_to_png_bytes(grid: List[List[int]]) -> bytes:
    rows = len(grid)
    cols = len(grid[0]) if rows > 0 else 0
    width = cols * CELL_SIZE + (cols + 1) * BORDER_WIDTH
    height = rows * CELL_SIZE + (rows + 1) * BORDER_WIDTH
    img = Image.new("RGB", (width, height), BORDER_COLOR)
    draw = ImageDraw.Draw(img)
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            x0 = BORDER_WIDTH + c * (CELL_SIZE + BORDER_WIDTH)
            y0 = BORDER_WIDTH + r * (CELL_SIZE + BORDER_WIDTH)
            x1 = x0 + CELL_SIZE - 1
            y1 = y0 + CELL_SIZE - 1
            draw.rectangle([x0, y0, x1, y1], fill=COLOR_RGB[cell])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def grid_to_base64_png(grid: List[List[int]]) -> str:
    return base64.b64encode(grid_to_png_bytes(grid)).decode("ascii")


def compare_grids(predicted: List[List[int]], expected: List[List[int]]):
    """Compare two grids. Returns (correct: bool, cell_accuracy: float)."""
    if predicted == expected:
        return True, 1.0
    total = 0
    matching = 0
    max_rows = max(len(predicted), len(expected))
    for r in range(max_rows):
        pred_row = predicted[r] if r < len(predicted) else []
        exp_row = expected[r] if r < len(expected) else []
        max_cols = max(len(pred_row), len(exp_row))
        for c in range(max_cols):
            total += 1
            pred_val = pred_row[c] if c < len(pred_row) else -1
            exp_val = exp_row[c] if c < len(exp_row) else -2
            if pred_val == exp_val:
                matching += 1
    return False, matching / total if total > 0 else 0.0


def sequence_to_text(sequence: list, masked_positions) -> str:
    """Format a NARC grid sequence as text for LLM prompts.

    Each grid is formatted with its position, dimensions, and optional label.
    Masked grids show [MASKED] with their dimensions.
    masked_positions can be a single int or a list of ints.
    """
    if isinstance(masked_positions, int):
        masked_positions = [masked_positions]
    masked_set = set(masked_positions)

    parts = []
    for item in sequence:
        pos = item["position"]
        rows = item["rows"]
        cols = item["cols"]
        label = item.get("label", "")
        label_str = f" — {label}" if label else ""

        if pos in masked_set:
            parts.append(
                f"Grid {pos + 1} ({rows}x{cols}){label_str}: "
                f"[MASKED — reconstruct this {rows}x{cols} grid]"
            )
        else:
            grid_text = grid_to_text(item["grid"])
            parts.append(f"Grid {pos + 1} ({rows}x{cols}){label_str}:\n{grid_text}")
    return "\n\n".join(parts)


def parse_response_grids(response_text: str):
    """Parse model response to extract predicted grids and reasoning.

    Returns (grids_dict: dict|None, reasoning: str|None, error: str|None).
    grids_dict is keyed by position string, e.g. {"2": [[...]], "3": [[...]]}.
    """
    if not response_text:
        return None, None, "Empty response"

    text = response_text.strip()

    # Strategy 1: JSON parsing
    json_text = text
    if "```json" in json_text:
        try:
            start = json_text.index("```json") + 7
            end = json_text.index("```", start)
            json_text = json_text[start:end].strip()
        except ValueError:
            pass
    elif "```" in json_text:
        try:
            start = json_text.index("```") + 3
            end = json_text.index("```", start)
            json_text = json_text[start:end].strip()
        except ValueError:
            pass

    data = _try_parse_json(json_text)
    if data is None:
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            data = _try_parse_json(text[brace_start:brace_end + 1])

    if data is not None:
        reasoning = data.get("reasoning", "")

        # Try output_grids (dict) first
        output_grids = data.get("output_grids")
        if isinstance(output_grids, dict) and output_grids:
            result = {}
            for pos_key, grid_val in output_grids.items():
                grid, err = _convert_grid(grid_val)
                if grid is None:
                    return None, reasoning, f"Bad grid at position {pos_key}: {err}"
                result[str(pos_key)] = grid
            return result, reasoning, None

        # Fall back to output_grid (single) — wrap in dict with key "0"
        output_grid = data.get("output_grid")
        if output_grid is not None:
            grid, err = _convert_grid(output_grid)
            if grid is not None:
                return {"_single": grid}, reasoning, None
            return None, reasoning, err

        return None, reasoning, "No output_grids or output_grid in response JSON"

    # Strategy 2: ANSWER block (single grid only)
    grid, err = _extract_answer_block(text)
    if grid is not None:
        return {"_single": grid}, text, None

    return None, text, "Could not parse grids from response"


def _try_parse_json(text):
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _convert_grid(output_grid):
    if not output_grid or not output_grid[0]:
        return None, "Empty output_grid"
    if isinstance(output_grid[0][0], str):
        try:
            return [[CODE_TO_INDEX[c] for c in row] for row in output_grid], None
        except KeyError as e:
            return None, f"Unknown color code in grid: {e}"
    return output_grid, None


def _extract_answer_block(text):
    answer_match = re.search(r'ANSWER\s*:\s*\n((?:[.\w ]+\n?)+)', text, re.IGNORECASE)
    if not answer_match:
        answer_match = re.search(
            r'(?:Output grid|Output|Final grid|Final answer)\s*:\s*\n((?:[.\w ]+\n?)+)',
            text, re.IGNORECASE
        )
    if answer_match:
        grid_text = answer_match.group(1).strip()
        try:
            grid = text_to_grid(grid_text)
            if grid and len(grid) > 0 and len(grid[0]) > 0:
                return grid, None
        except (KeyError, ValueError, IndexError):
            pass

    # Try last block of grid-like lines
    grid_line_pattern = re.compile(r'^[.BRGXYMAOW](?:\s+[.BRGXYMAOW])+$')
    lines = text.split('\n')
    i = len(lines) - 1
    while i >= 0:
        if grid_line_pattern.match(lines[i].strip()):
            end = i
            while i >= 0 and grid_line_pattern.match(lines[i].strip()):
                i -= 1
            start = i + 1
            if end - start >= 1:
                grid_text = "\n".join(lines[start:end + 1])
                try:
                    grid = text_to_grid(grid_text)
                    if grid and len(grid) > 0 and len(grid[0]) > 0:
                        return grid, None
                except (KeyError, ValueError, IndexError):
                    pass
                break
        i -= 1

    return None, "No grid found in response"
