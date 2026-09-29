"""Import puzzles made by the ICLR 2027 generator (narc-tiny-stories, extracted TinyStories
chains) into the simulator, so they can be rebuilt under any code, symmetry or mask.

A narc-tiny-stories puzzle stores its grids, its legend (which name has which colour) and
its event chain per frame (with the events a compressed transition spans), but not the
setup scene. The setup is recovered by reading the first grid back under the baseline code
(the inverse of the renderer: ground row -> place, bars -> characters with mood and
elevation, cells -> objects held or on the ground, grey -> broken, two cells -> big). The
events then replay from that scene. Fidelity is checked by re-rendering under the baseline
code and comparing every frame, including the answer, with the original.

    python -m nhmsim import-nts ../narc-tiny-stories/data/puzzles_clean/all --limit 300 --phi baseline,marker --out runs/imported
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import symmetries as S
from .certify import _build
from .conventions import BaseConvention, get as get_convention
from .state import BROKEN_COLOR, CHAR_COLORS, GROUND_COLOR, H, OBJ_COLORS, Char, Event, Obj, Scene

STAND_ROW, ELEVATED_ROW, GROUND_ROW = 5, 2, 6
NTS_PRIMITIVES = ["ACQUIRE", "APPEAR", "ASCEND", "DESCEND", "DROP", "EMOTE", "LOSE", "MOVE", "TRANSFER", "TRANSFORM", "VANISH", "WANT"]
_GROUND_CLASS = {v: k for k, v in GROUND_COLOR.items() if v}
_MOOD_BY_HEIGHT = {1: "sad", 2: "neutral", 3: "happy"}


def scene_from_grid(grid: List[List[int]], legend: Dict, preregister: bool = True) -> Scene:
    """The setup scene that renders as ``grid`` under the baseline code. With ``preregister``,
    characters and objects the legend names but the grid lacks are registered absent (their
    colour and slot fixed as in the original); without it they are created when they first act,
    which is what the original did for entities that acted before they were shown."""
    width = len(grid[0])
    s = Scene()
    s.location = _GROUND_CLASS.get(grid[GROUND_ROW][0], "none") if any(grid[GROUND_ROW]) else "none"
    char_by_color = {int(c): n for n, c in legend.get("chars", {}).items()}
    obj_by_color = {int(c): n for n, c in legend.get("objs", {}).items()}
    n_slots = max(2, (width - 1) // 3)
    # characters: a vertical run of a character colour ending on the stand row or the elevated row
    for slot in range(n_slots):
        x = 1 + 3 * slot
        if x >= width:
            break
        for base in (STAND_ROW, ELEVATED_ROW):
            col = grid[base][x]
            if col in CHAR_COLORS:
                h = 0
                r = base
                while r >= 0 and grid[r][x] == col:
                    h += 1
                    r -= 1
                name = char_by_color.get(col, f"char{col}")
                s.chars[name] = Char(name, col, slot, present=True, mood=_MOOD_BY_HEIGHT.get(min(h, 3), "neutral"),
                                     elevated=(base == ELEVATED_ROW))
                break
    # characters in the legend but not on the first grid: registered absent at their legend-order
    # slot (the legend lists characters in registration order, which fixed their slots and
    # colours in the original), so later APPEARs put them where the original did
    for idx, (name, col) in enumerate(legend.get("chars", {}).items()):
        if name not in s.chars and preregister:
            s.chars[name] = Char(name, int(col), slot=idx, present=False)
    for c in s.chars.values():
        c.seen = c.present
    # objects: held (right of a bar at hand height) or on the ground (third column of a slot)
    used_names = set()

    def obj_name(col: int) -> str:
        if col in obj_by_color and obj_by_color[col] not in used_names:
            return obj_by_color[col]
        if col == BROKEN_COLOR:
            for n, c in legend.get("objs", {}).items():
                if n not in used_names:
                    return n
        return f"obj{col}_{len(used_names)}"

    for slot in range(n_slots):
        x_held, x_ground = 2 + 3 * slot, 3 + 3 * slot
        holder = next((c for c in s.chars.values() if c.present and c.slot == slot), None)
        if holder is not None and x_held < width:
            hand = (ELEVATED_ROW if holder.elevated else STAND_ROW) - 1
            col = grid[hand][x_held]
            if col and col not in CHAR_COLORS:
                name = obj_name(col)
                used_names.add(name)
                big = hand - 1 >= 0 and grid[hand - 1][x_held] == col
                real = int(legend.get("objs", {}).get(name, col)) if col == BROKEN_COLOR else col
                s.objs[name] = Obj(name, real, present=True, holder=holder.name, state="broken" if col == BROKEN_COLOR else ("big" if big else "ok"), seen=True)
        if x_ground < width:
            col = grid[STAND_ROW][x_ground]
            if col and col not in CHAR_COLORS:
                name = obj_name(col)
                used_names.add(name)
                big = grid[STAND_ROW - 1][x_ground] == col
                real = int(legend.get("objs", {}).get(name, col)) if col == BROKEN_COLOR else col
                s.objs[name] = Obj(name, real, present=True, ground_slot=slot, state="broken" if col == BROKEN_COLOR else ("big" if big else "ok"), seen=True)
    for name, col in legend.get("objs", {}).items():
        if name not in s.objs and preregister:
            s.objs[name] = Obj(name, int(col), present=False, seen=False)
    s.n_slots = max([c.slot + 1 for c in s.chars.values()] + [o.ground_slot + 1 for o in s.objs.values() if o.ground_slot is not None] + [1])
    return s


def events_from_nts(puzzle: dict) -> List[Event]:
    """The full post-setup chain: each frame's spanned events (or its one event), in order. A
    MOVE to a place takes its class from the frame's ground row, which is what the grid shows."""
    seq = puzzle["sequence"]
    k = puzzle["masked_positions"][0]
    grids = [it["grid"] if it["grid"] is not None else puzzle["answer_grids"][str(k)] for it in seq]
    out: List[Event] = []
    for c in puzzle["metadata"]["event_chain"]:
        pos = c["position"]
        frame_class = _GROUND_CLASS.get(grids[pos][GROUND_ROW][0]) if pos < len(grids) and any(grids[pos][GROUND_ROW]) else None
        for e in (c.get("spanned_events") or [c]):
            loc = e.get("location")
            if e["prim"] == "MOVE" and loc and frame_class:
                loc = frame_class
            out.append(Event(e["prim"], list(e.get("agents") or []), e.get("theme"), e.get("recipient"), loc,
                             e.get("mood"), e.get("state"), verb=e.get("verb") or "", conf=e.get("conf", 1.0)))
    return out


def import_puzzle(nts: dict, conv: Optional[BaseConvention] = None, sym: Optional[S.Symmetry] = None,
                  keep_mask: bool = True, store_alts: int = 8) -> Tuple[Optional[dict], str, dict]:
    """Rebuild one narc-tiny-stories puzzle. Returns (puzzle or None, reason, fidelity) where
    fidelity = {"frames_equal": bool, "n_frames": int, "n_equal": int, "mask_kept": bool} measured
    under the baseline code and the identity symmetry regardless of ``conv``/``sym``."""
    legend = nts["metadata"]["legend"]
    k = nts["masked_positions"][0]
    grids = [it["grid"] if it["grid"] is not None else nts["answer_grids"][str(k)] for it in nts["sequence"]]
    events = events_from_nts(nts)
    base = get_convention("baseline")
    texts = {"story": nts["narrative"]}
    for v in nts.get("narrative_variants", []):
        texts[v["variant"]] = v["narrative"]
    meta = {"grammar": "nts-import", "story_type": None, "features": nts["metadata"].get("story_features", []),
            "roles": {}, "m": None, "tree": [], "texts": texts, "primitives": NTS_PRIMITIVES,
            "source_puzzle_id": nts["puzzle_id"], "source_generator": nts["metadata"].get("generator_version")}

    def fidelity_of(initial):
        ref, why = _build(events, initial, base, S.Symmetry(), "all", 1, False, 4, 0, nts["puzzle_id"], dict(meta))
        fid = {"frames_equal": False, "n_frames": len(grids), "n_equal": 0, "mask_kept": False, "reason": why}
        if ref:
            rg = [it["grid"] if it["grid"] is not None else ref[0]["answer_grids"][str(ref[0]["masked_positions"][0])] for it in ref[0]["sequence"]]
            fid["n_equal"] = sum(1 for a, b in zip(rg, grids) if a == b)
            fid["frames_equal"] = len(rg) == len(grids) and fid["n_equal"] == len(grids)
            fid["mask_kept"] = any(p["masked_positions"][0] == k for p in ref)
        return fid

    # two ways to read the setup; keep the one that re-renders the original best
    candidates = []
    for pre in (True, False):
        init = scene_from_grid(grids[0], legend, preregister=pre)
        fid = fidelity_of(init)
        fid["preregister"] = pre
        candidates.append((fid["frames_equal"], fid["n_equal"], fid["mask_kept"], init, fid))
        if fid["frames_equal"]:
            break
    _, _, _, initial, fid = max(candidates, key=lambda c: (c[0], c[1], c[2]))
    conv = conv or base
    sym = sym or S.Symmetry()
    pzs, why2 = _build(events, initial, conv, sym, "all", 2, True, 4, store_alts, nts["puzzle_id"], meta)
    if not pzs:
        return None, why2, fid
    chosen = next((p for p in pzs if p["masked_positions"][0] == k), None) if keep_mask else None
    if chosen is None:
        finals = [p for p in pzs if p["masked_positions"][0] == len(grids) - 1]
        chosen = finals[0] if finals else max(pzs, key=lambda p: p["metadata"]["mask_analysis"]["alternatives"])
    chosen["puzzle_id"] = nts["puzzle_id"]
    chosen["title"] = nts.get("title", chosen["title"])
    chosen["metadata"]["tags"] = sorted(set(chosen["metadata"].get("tags", [])) | {"source:nts", f"nts:{nts['metadata'].get('generator_version')}"})
    chosen["metadata"]["import_fidelity"] = fid
    return chosen, "ok", fid


def load_sources(src: str, limit: Optional[int] = None) -> List[Path]:
    p = Path(src)
    files = sorted(p.glob("*.json")) if p.is_dir() else sorted(Path().glob(src)) if any(ch in src for ch in "*?[") else [p]
    return files[:limit] if limit else files
