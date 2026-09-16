#!/usr/bin/env python3
"""Assertions for the grading key rule (grids.normalize_prediction_keys, Sep 16, 2026).

    python test_grading.py

No test framework; exits non-zero on the first failure.
"""
import grids
import prompts
from collect import grade_prediction

EXACT = [[5, 0, 7], [5, 0, 7], [5, 0, 7]]
WRONG = [[5, 0, 7], [5, 0, 7], [5, 0, 0]]
SINGLE = {"answer_grids": {"3": EXACT}, "masked_positions": [3]}
MULTI = {"answer_grids": {"2": EXACT, "3": WRONG}, "masked_positions": [2, 3]}


def check(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"
    print("ok  ", name)


# --- single mask ---
check("right key exact", grade_prediction(SINGLE, {"3": EXACT})[1], 1)
check("lone grid keyed 0 -> correct", grade_prediction(SINGLE, {"0": EXACT})[1], 1)
check("lone grid keyed pos+1 -> correct", grade_prediction(SINGLE, {"4": EXACT})[1], 1)
check("lone grid re-keyed in mapped dict", grade_prediction(SINGLE, {"0": EXACT})[0], {"3": EXACT})
check("_single unchanged", grade_prediction(SINGLE, {"_single": EXACT}), ({"3": EXACT}, 1, 1.0))
check("lone wrong grid keyed 0 -> wrong, partial accuracy",
      grade_prediction(SINGLE, {"0": WRONG})[1:], (0, 8 / 9))
check("two grids, none at key -> wrong", grade_prediction(SINGLE, {"0": EXACT, "1": EXACT})[1], 0)
check("two grids, one at key -> graded at key", grade_prediction(SINGLE, {"0": WRONG, "3": EXACT})[1], 1)

# --- multi mask ---
check("multi right keys", grade_prediction(MULTI, {"2": EXACT, "3": WRONG})[1], 1)
check("multi swapped keys -> still wrong", grade_prediction(MULTI, {"3": EXACT, "2": WRONG})[1], 0)
check("multi uniform +1 shift -> shifted and correct",
      grade_prediction(MULTI, {"3": EXACT, "4": WRONG}), ({"2": EXACT, "3": WRONG}, 1, 1.0))
check("multi partial overlap -> strict", grade_prediction(MULTI, {"3": EXACT, "5": WRONG})[1], 0)
check("multi fewer grids -> wrong", grade_prediction(MULTI, {"3": EXACT})[1], 0)
check("multi keys 0..n-1 -> strict (not remapped)",
      grids.normalize_prediction_keys({"0": EXACT, "1": WRONG}, [2, 3]), {"0": EXACT, "1": WRONG})
check("multi _single -> strict", grade_prediction(MULTI, {"_single": EXACT})[1], 0)

# --- extraction prompt ---
old = prompts.build_extraction("text")[0]["content"]
assert "<position>" in old and "\"Grid " not in old, old
new = prompts.build_extraction("text", masked_positions=[3], dimensions=[(3, 3)])[0]["content"]
for needle in ('"3": [[int, ...], ...]', 'Use exactly these position key(s): "3"', '3 rows x 3 cols', '"Grid 4"'):
    assert needle in new, (needle, new)
print("ok   extraction prompt carries positions, dimensions, Grid N label")
multi = prompts.build_extraction("text", masked_positions=[2, 3], dimensions=[(3, 3), (2, 2)])[0]["content"]
assert '"2": [[int, ...], ...], "3": [[int, ...], ...]' in multi and '"Grid 3"' in multi and '"Grid 4"' in multi, multi
print("ok   multi-mask extraction prompt")
print("all grading assertions passed")
