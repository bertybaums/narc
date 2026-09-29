"""Symmetries G of the grid surface: transformations that commute with the semantics.

A symmetry is sampled once per puzzle and applied to every grid of that puzzle (visible,
answer and alternatives alike), so the visible grids pin down the element and the solver's
job is invariance, not a new code. m_grid = |G| when the group is sampled.

    identity
    pal      permute identity colours within their classes (characters among characters,
             objects among objects). This acts on the SCENE, not on colour values: the
             ground row, the marker, the broken grey and the day yellow keep their colours
             even though green, azure, maroon and yellow are also object colours.
    mirror   reverse the column order (a held object then sits left of its holder)
    vflip    reverse the row order (the ground becomes a ceiling; bars hang)
    shift    prepend 1 or 2 black columns (translation; the sparse RHM's insensitivity)

Compose with '+', e.g. 'pal+mirror'. The 'full' alias is pal+mirror. ``render`` applies a
symmetry end to end: recolour the scene, render under the convention, transform the grid.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List

from .state import CHAR_COLORS, OBJ_COLORS, Scene

Grid = List[List[int]]
NAMES = ("identity", "pal", "mirror", "vflip", "shift")


@dataclass
class Symmetry:
    name: str = "identity"
    params: Dict = field(default_factory=dict)

    def spec(self):
        return {"name": self.name, "params": self.params}

    @classmethod
    def from_spec(cls, d):
        return cls(d.get("name", "identity"), d.get("params", {}))

    def parts(self) -> List[str]:
        return [] if self.name == "identity" else self.name.split("+")


def parse(spec: str) -> List[str]:
    spec = (spec or "identity").strip()
    if spec == "full":
        spec = "pal+mirror"
    parts = [p.strip() for p in spec.split("+") if p.strip()]
    for p in parts:
        if p not in NAMES:
            raise KeyError(f"unknown symmetry {p!r}; known: {', '.join(NAMES)}")
    return [p for p in parts if p != "identity"]


def sample(spec: str, rng: random.Random) -> Symmetry:
    parts = parse(spec)
    if not parts:
        return Symmetry()
    params: Dict = {}
    if "pal" in parts:
        cp, op = list(CHAR_COLORS), list(OBJ_COLORS)
        while True:                       # a non-identity permutation on at least one class
            rng.shuffle(cp)
            rng.shuffle(op)
            if cp != CHAR_COLORS or op != OBJ_COLORS:
                break
        perm = {a: b for a, b in zip(CHAR_COLORS, cp)}
        perm.update({a: b for a, b in zip(OBJ_COLORS, op)})
        params["perm"] = {str(k): v for k, v in perm.items()}
    if "shift" in parts:
        params["k"] = rng.choice([1, 2])
    return Symmetry("+".join(parts), params)


def recolor(scene: Scene, sym: Symmetry) -> Scene:
    """The scene with identity colours permuted (the 'pal' part); other parts are geometric."""
    if "pal" not in sym.parts():
        return scene
    perm = {int(k): v for k, v in sym.params["perm"].items()}
    s = scene.clone()
    for c in s.chars.values():
        c.color = perm.get(c.color, c.color)
    for o in s.objs.values():
        o.color = perm.get(o.color, o.color)
    return s


def transform(grid: Grid, sym: Symmetry) -> Grid:
    """The geometric parts (mirror, vflip, shift). 'pal' is applied by ``recolor`` before rendering."""
    g = [row[:] for row in grid]
    for p in sym.parts():
        if p == "mirror":
            g = [row[::-1] for row in g]
        elif p == "vflip":
            g = g[::-1]
        elif p == "shift":
            k = int(sym.params.get("k", 1))
            g = [[0] * k + row for row in g]
    return g


def render(conv, scene: Scene, width: int, sym: Symmetry) -> Grid:
    return transform(conv.render(recolor(scene, sym), width), sym)


def describe(sym: Symmetry) -> str:
    if not sym.parts():
        return "canonical rendering"
    bits = []
    for p in sym.parts():
        if p == "pal":
            bits.append("identity colours relabelled")
        elif p == "mirror":
            bits.append("left and right swapped")
        elif p == "vflip":
            bits.append("top and bottom swapped")
        elif p == "shift":
            bits.append(f"shifted right by {sym.params.get('k', 1)}")
    return "; ".join(bits)
