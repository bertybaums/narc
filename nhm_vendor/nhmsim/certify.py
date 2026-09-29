"""Event chain -> scene chain -> grids -> NARC puzzle, with the property certified by construction.

The certificate C of the ICLR paper, generalised to any convention and to any mask position:

  (A) ambiguity     >= ``min_alternatives`` grammar-consistent alternative grids exist for the
                    masked slot given the visible grids alone. For a final or middle mask these
                    are one-primitive successors of the previous frame (and, for a middle mask,
                    one further primitive must reach the next frame). For a FIRST mask they are
                    one-primitive predecessors of frame 1 that differ from the true setup.
  (B) determinacy   the event chain yields the answer (by construction).
  (C) self-teaching every convention the answer relies on is exhibited in a visible frame of
                    the same puzzle, under the same convention. For a first mask, everything
                    the setup shows that frame 1 does not show must be taught elsewhere.

``simulate`` adds a frame only when the *rendered* grid changes, so an event with no
denotation under the chosen convention (WANT under the baseline, WITHDRAW under the table)
changes the state silently and is attached to the next visible transition as a spanned event.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

from . import symmetries as S
from .conventions import BaseConvention
from .conventions import get as get_convention
from .grammar import Sample
from .state import EXTRA_STATES, GROUND_COLOR, MOODS, OBJ_STATES, TIMES, Event, Scene, apply

GENERATOR_VERSION = "0.1.0"
MAX_GRIDS = 8
MIN_GRIDS = 3
PROVISIONAL_SLOTS = 6
Grid = List[List[int]]


@dataclass
class Step:
    event: Event                    # the visible event that produced the frame
    scene: Scene
    spanned: List[Event]            # invisible events folded into this transition (before ``event``)


# ------------------------------------------------------------------------------------------
def simulate(events: List[Event], conv: BaseConvention, initial: Optional[Scene] = None) -> Tuple[Scene, List[Step]]:
    """Apply the events in order; return the setup scene and one Step per rendered change.

    As in the ICLR generator, characters and objects that act before they were ever shown are
    introduced retroactively into every snapshot since the last change of location (a friend
    who "ran away" without having arrived was there all along)."""
    cur = initial.clone() if initial is not None else Scene()
    W = conv.width_for(PROVISIONAL_SLOTS)
    i = 0
    if initial is None:
        # leading APPEARs without a location define the setup silently ("once upon a time there was")
        # ... plus one state-setting TRANSFORM per object introduced there ("the fire was blazing")
        state_set = set()
        while i < len(events) and ((events[i].prim == "APPEAR" and not events[i].location)
                                   or (i > 0 and events[i].prim == "TRANSFORM" and events[i].theme in cur.objs
                                       and cur.objs[events[i].theme].present and events[i].theme not in state_set)):
            if events[i].prim == "TRANSFORM":
                state_set.add(events[i].theme)
            new = apply(cur, events[i])
            if new is not None:
                cur = new
            i += 1
    snaps: List[Scene] = [cur]
    steps: List[Step] = []
    pending: List[Event] = []
    last_loc = 0

    def introduce_char(name):
        nonlocal cur
        if name in cur.chars and (cur.chars[name].seen or cur.chars[name].present):
            return                       # known (present, or gone: only APPEAR brings them back)
        c = cur.get_char(name)           # new, or registered from a legend but never drawn
        if c is None:
            return
        c.present = c.seen = True
        for s in snaps[last_loc:]:
            if name not in s.chars:
                s.chars[name] = type(c)(**vars(c))
                s.n_slots = max(s.n_slots, cur.n_slots)

    materialised = {o.name for o in cur.objs.values() if o.present or o.seen}
    for c in cur.chars.values():
        if c.present:
            c.seen = True

    def introduce_obj(name, holder=None):
        nonlocal cur
        if name in cur.objs and name in materialised:
            return                       # has been on scene (present, or gone: only APPEAR/ACQUIRE bring it back)
        o = cur.get_obj(name)            # a WANT may have registered the colour without showing the object
        if o is None:
            return
        o.present = o.seen = True
        if holder and holder in cur.chars and cur.chars[holder].present:
            o.holder = holder
        else:
            o.ground_slot = cur.free_ground_slot()
        for s in snaps[last_loc:]:
            if name not in s.objs or not s.objs[name].present:
                s.objs[name] = type(o)(**vars(o))
                s.n_slots = max(s.n_slots, cur.n_slots)

    for ev in events[i:]:
        if ev.prim not in ("APPEAR", "TIME"):
            for a in ev.agents:
                introduce_char(a)
        if ev.prim in ("TRANSFER", "MOVE", "WITHDRAW", "SPLIT") and ev.recipient:
            introduce_char(ev.recipient)
        if ev.prim in ("DROP", "LOSE", "TRANSFER", "SPLIT") and ev.theme:
            introduce_obj(ev.theme, holder=ev.agents[0] if ev.agents else None)
        if ev.prim == "TRANSFORM" and ev.theme:
            introduce_obj(ev.theme, holder=None)
        if ev.prim == "EMOTE" and not any(a in cur.chars and cur.chars[a].present for a in ev.agents):
            continue
        new = apply(cur, ev)
        if new is None:
            continue
        materialised |= {o.name for o in new.objs.values() if o.present}
        if conv.render(new, W) == conv.render(cur, W):
            # a state change with no denotation under this convention: fold into the next frame
            if ev.prim == "MOVE" and new.location != cur.location:
                last_loc = len(snaps)
            cur = new
            snaps[-1] = cur
            pending.append(ev)
            if steps:
                steps[-1].scene = cur
            continue
        if ev.prim == "MOVE" and new.location != cur.location:
            last_loc = len(snaps)
        cur = new
        snaps.append(cur)
        steps.append(Step(ev, cur, pending))
        pending = []
    return snaps[0], steps


def keyframes(all_scenes: List[Scene], all_steps: List[Step]):
    """Long chains are reduced to the setup, evenly spaced middle states and the two final
    states, so the ending transition stays atomic. Returns (frame_idx, scenes, steps) where
    steps[j] is the atomic Step into frame j+1 or None when the transition spans several."""
    n = len(all_scenes) - 1
    if len(all_scenes) > MAX_GRIDS:
        n_mid = MAX_GRIDS - 3
        mids = list(range(1, n - 1))
        picks = sorted({mids[round(i * (len(mids) - 1) / max(1, n_mid - 1))] for i in range(n_mid)}) if mids else []
        frame_idx = [0] + picks + [n - 1, n]
    else:
        frame_idx = list(range(n + 1))
    scenes = [all_scenes[i] for i in frame_idx]
    steps: List[Optional[Step]] = []
    spanned: List[List[Event]] = []
    for a, b in zip(frame_idx, frame_idx[1:]):
        evs: List[Event] = []
        for i in range(a, b):
            evs.extend(all_steps[i].spanned)
            evs.append(all_steps[i].event)
        spanned.append(evs)
        steps.append(all_steps[a] if b - a == 1 else None)
    return frame_idx, scenes, steps, spanned


# ------------------------------------------------------------------------------------------
ALL_PRIMS = frozenset(("APPEAR", "VANISH", "MOVE", "ASCEND", "DESCEND", "ACQUIRE", "DROP", "LOSE", "TRANSFER",
                       "EMOTE", "TRANSFORM", "WANT", "UNWANT", "WITHDRAW", "SPLIT", "TIME"))


def candidate_events(scene: Scene, known_locs: List[str], conv: BaseConvention, prims=None, states=None) -> List[Event]:
    """Every single-primitive event applicable in ``scene``: the grammar's one-step neighbourhood.
    ``prims`` restricts the neighbourhood to the grammar's own primitives (an alternative must be
    something the grammar could have generated)."""
    prims = frozenset(prims) if prims else ALL_PRIMS
    obj_states = tuple(OBJ_STATES) + tuple(st for st in EXTRA_STATES if states and st in states)
    C: List[Event] = []
    present = scene.present_chars()
    absent = [c for c in scene.chars.values() if not c.present]
    names = [c.name for c in present]
    groups = [[n] for n in names] + ([names] if len(names) > 1 else [])

    def mk(prim, agents=(), **kw):
        if prim in prims:
            C.append(Event(prim, list(agents), **kw))

    for g in groups:
        for mood in MOODS:
            mk("EMOTE", g, mood=mood)
        for lc in known_locs:
            if lc != scene.location:
                mk("MOVE", g, location=lc)
        mk("ASCEND", g)
        mk("DESCEND", g)
        if len(g) == 1:
            mk("VANISH", g)
    for c in absent:
        mk("APPEAR", [c.name])
    objs = [o for o in scene.objs.values()
            if o.seen or (conv.renders_want() and any(c.wants == o.name for c in present))]
    for c in present:
        for o in objs:
            if not (o.present and o.holder == c.name):
                mk("ACQUIRE", [c.name], theme=o.name)
            if o.present and o.holder == c.name:
                mk("DROP", [c.name], theme=o.name)
                mk("LOSE", [c.name], theme=o.name)
                for d in present:
                    if d.name != c.name:
                        mk("TRANSFER", [c.name], theme=o.name, recipient=d.name)
                        if o.state == "big":
                            mk("SPLIT", [c.name], theme=o.name, recipient=d.name)
            if conv.renders_want():
                mk("WANT", [c.name], theme=o.name)
        if conv.renders_want() and c.wants:
            mk("UNWANT", [c.name])
        if conv.renders_adjacency():
            for d in present:
                if d.name != c.name:
                    mk("MOVE", [c.name], recipient=d.name)
                    mk("WITHDRAW", [c.name], recipient=d.name)
    for o in objs:
        for st in obj_states:
            mk("TRANSFORM", [], theme=o.name, state=st)
    if conv.renders_time():
        for t in TIMES:
            if t != scene.time:
                mk("TIME", [], state=t)
    return C


def _successors(scene: Scene, known_locs, conv, width, prims=None, states=None) -> Dict[str, Tuple[Grid, Scene, Event]]:
    out: Dict[str, Tuple[Grid, Scene, Event]] = {}
    for c in candidate_events(scene, known_locs, conv, prims, states):
        s2 = apply(scene, c)
        if s2 is None:
            continue
        g2 = conv.render(s2, width)
        key = json.dumps(g2)
        if key not in out:
            out[key] = (g2, s2, c)
    return out


def analyse_mask(scenes: List[Scene], steps: List[Optional[Step]], grids: List[Grid], k: int,
                 conv: BaseConvention, width: int, known_locs: List[str], store: int = 8, prims=None, states=None) -> dict:
    """Alternatives count and self-teaching verdict for masking frame ``k`` (0 = the setup)."""
    answer = grids[k]
    alts: Dict[str, Tuple[Scene, str]] = {}
    if k == 0:
        nxt = scenes[1]
        for key, (g0, s0, c) in _successors(nxt, known_locs, conv, width, prims, states).items():
            if g0 == answer or g0 == grids[1]:
                continue
            # a predecessor must reach frame 1 in one primitive
            if any(g == grids[1] for g, _, _ in _successors(s0, known_locs, conv, width, prims, states).values()):
                alts[key] = (s0, f"before: {c.label()}")
        vis: Set = set()
        for i, s in enumerate(scenes):
            if i != 0:
                vis |= conv.facts(s)
        vis |= _taught_reslots(scenes, steps, skip=0)
        ev = steps[0].event if steps[0] is not None else None
        needs = (conv.facts(scenes[0]) - conv.facts(scenes[1]))
        if ev is not None:
            needs |= conv.needs(scenes[0], scenes[1], ev)
        event_label = ev.label() if ev else "span"
    else:
        prev = scenes[k - 1]
        nxt_grid = grids[k + 1] if k + 1 < len(grids) else None
        for key, (g2, s2, c) in _successors(prev, known_locs, conv, width, prims, states).items():
            if g2 == answer or g2 == grids[k - 1]:
                continue
            if nxt_grid is not None:
                if g2 == nxt_grid:
                    continue
                if not any(g == nxt_grid for g, _, _ in _successors(s2, known_locs, conv, width, prims, states).values()):
                    continue
            alts[key] = (s2, c.label())
        vis = set()
        for i, s in enumerate(scenes):
            if i != k:
                vis |= conv.facts(s)
        vis |= _taught_reslots(scenes, steps, skip=k)
        ev = steps[k - 1].event if steps[k - 1] is not None else None
        needs = conv.needs(prev, scenes[k], ev) if ev is not None else set()
        event_label = ev.label() if ev else "span"
    missing = sorted(str(x) for x in needs - vis)
    stored = [{"scene": sc, "event": lab} for sc, lab in list(alts.values())[:store]]
    return {"position": k, "alternatives": len(alts), "self_taught": not missing, "missing": missing,
            "event": event_label, "depth": "first" if k == 0 else ("final" if k == len(grids) - 1 else "middle"),
            "stored": stored}


def _taught_reslots(scenes, steps, skip) -> Set:
    out: Set = set()
    for i in range(1, len(scenes)):
        st = steps[i - 1]
        if i != skip and st is not None and st.event.prim in ("MOVE", "WITHDRAW") and scenes[i].location == scenes[i - 1].location:
            out.add(("reslot",))
    return out


# ------------------------------------------------------------------------------------------
def build_puzzles(sample: Sample, conv: BaseConvention, sym_spec: str = "identity", rng: Optional[random.Random] = None,
                  mask: str = "last", min_alternatives: int = 2, require_self_taught: bool = True, max_chars: int = 3,
                  store_alts: int = 8, puzzle_id: str = "nhm", sym: Optional[S.Symmetry] = None) -> Tuple[List[dict], str]:
    """Return (puzzles, reason). ``mask`` is last | middle | first | any | all."""
    rng = rng or random.Random(0)
    sym = sym or S.sample(sym_spec, rng)
    return _build(sample.events, None, conv, sym, mask, min_alternatives, require_self_taught, max_chars, store_alts,
                  puzzle_id, meta_from_sample(sample))


def meta_from_sample(sample: Sample) -> dict:
    return {"grammar": sample.grammar, "story_type": sample.story_type, "features": sample.features,
            "roles": sample.roles, "m": sample.m, "tree": sample.tree, "texts": sample.texts,
            "primitives": sample.primitives, "states": sample.states}


def _build(events: List[Event], initial: Optional[Scene], conv: BaseConvention, sym: S.Symmetry, mask: str,
           min_alternatives: int, require_self_taught: bool, max_chars: int, store_alts: int, puzzle_id: str,
           meta: dict) -> Tuple[List[dict], str]:
    setup, all_steps = simulate(events, conv, initial)
    all_scenes = [setup] + [s.scene for s in all_steps]
    if len(all_scenes[-1].chars) > max_chars:
        return [], "too_many_characters"
    if len(all_scenes) < MIN_GRIDS:
        return [], "too_few_visible_events"
    frame_idx, scenes, steps, spanned = keyframes(all_scenes, all_steps)
    width = conv.width_for(max(s.n_slots for s in all_scenes))
    if width > conv.max_width:
        return [], "too_wide"
    if any(not sc.present_chars() for sc in scenes):
        return [], "empty_scene"
    grids = [conv.render(s, width) for s in scenes]
    for i in range(1, len(grids)):
        if grids[i] == grids[i - 1]:
            return [], "duplicate_adjacent_grid"
    known_locs = sorted({s.location for s in scenes if s.location != "none"})
    n = len(grids)
    positions = [k for k in range(1, n) if steps[k - 1] is not None and (k == n - 1 or steps[k] is not None)]
    if steps[0] is not None:
        positions = [0] + positions
    prims = meta.get('primitives') or None
    states = meta.get('states') or None
    analyses = [analyse_mask(scenes, steps, grids, k, conv, width, known_locs, store=store_alts, prims=prims, states=states) for k in positions]
    if not analyses:
        return [], "no_atomic_mask_position"
    valid = [a for a in analyses if (a["self_taught"] or not require_self_taught) and a["alternatives"] >= min_alternatives]
    if not valid:
        reasons = Counter("untaught" if not a["self_taught"] else "unambiguous" for a in analyses)
        return [], "no_valid_mask:" + ",".join(f"{k}={v}" for k, v in reasons.items())
    chosen = _choose(valid, mask, n)
    if not chosen:
        return [], f"no_{mask}_mask"
    puzzles = []
    for a in chosen:
        k = a["position"]
        pid = puzzle_id if len(chosen) == 1 and mask != "all" else f"{puzzle_id}_k{k}"
        puzzles.append(_puzzle(pid, events, setup, conv, sym, scenes, steps, spanned, grids, frame_idx, len(all_scenes),
                               a, analyses, valid, known_locs, all_scenes, all_steps, meta))
    return puzzles, "ok"


def _choose(valid: List[dict], mask: str, n: int) -> List[dict]:
    by_depth = {"first": [a for a in valid if a["position"] == 0],
                "final": [a for a in valid if a["position"] == n - 1],
                "middle": [a for a in valid if 0 < a["position"] < n - 1]}
    if mask == "all":
        return valid
    if mask == "last":
        return by_depth["final"][:1] or []
    if mask == "first":
        return by_depth["first"][:1] or []
    if mask == "middle":
        mids = by_depth["middle"]
        return [max(mids, key=lambda a: (a["alternatives"], a["position"]))] if mids else []
    if mask == "any":                       # the ICLR generator's rule: last if valid, else most ambiguous non-first
        if by_depth["final"]:
            return by_depth["final"][:1]
        rest = [a for a in valid if a["position"] > 0]
        return [max(rest, key=lambda a: (a["alternatives"], a["position"]))] if rest else []
    raise ValueError(f"unknown mask policy {mask}")


def _ev_dict(e: Event) -> dict:
    return e.to_dict()


def _puzzle(pid, events, setup, conv, sym, scenes, steps, spanned, grids, frame_idx, n_states_total, chosen, analyses,
            valid, known_locs, all_scenes, all_steps, meta) -> dict:
    k = chosen["position"]
    width = len(grids[0][0])
    tgrids = [S.render(conv, sc, width, sym) for sc in scenes]
    sequence = []
    for i, g in enumerate(tgrids):
        item = {"position": i, "grid": None if i == k else g, "rows": len(g), "cols": len(g[0]), "label": ""}
        if i == k:
            item["masked"] = True
        sequence.append(item)
    chain_meta = []
    for i, sp in enumerate(spanned):
        d = {"position": i + 1, "atomic": len(sp) == 1, **_ev_dict(sp[-1]), "label": sp[-1].label()}
        if len(sp) > 1:
            d["spanned_events"] = [{**_ev_dict(e), "label": e.label()} for e in sp]
        chain_meta.append(d)
    def rc(sc):                                   # the physical text describes the rendered grid
        return S.recolor(sc, sym)
    physical = [conv_describe_scene(conv, rc(setup), sym)]
    for prev, st in zip(all_scenes, all_steps):
        physical.append(conv.describe(rc(prev), rc(st.scene), st.event, sym=sym))
    texts = meta.get("texts", {})
    protagonist = next((c.name for c in sorted(setup.chars.values(), key=lambda c: c.slot)), "story")
    ev_label = chosen["event"]
    last = rc(scenes[-1])
    legend = {"ground": {lc: GROUND_COLOR[lc] for lc in known_locs},
              "chars": {c.name: c.color for c in last.chars.values()},
              "objs": {o.name: o.color for o in last.objs.values()}}
    variants = [{"variant": "grammar", "narrative": texts.get("grammar", ""), "generator": f"nhm-sim-{GENERATOR_VERSION}"},
                {"variant": "physical", "narrative": " ".join(physical), "generator": f"nhm-sim-{GENERATOR_VERSION}:{conv.name}"}]
    for sname, t in texts.items():
        if sname not in ("story", "grammar"):
            variants.append({"variant": sname, "narrative": t, "generator": f"nhm-sim-{GENERATOR_VERSION}"})
    alternatives = {str(a["position"]): [{"grid": S.render(conv, x["scene"], width, sym), "event": x["event"]} for x in a["stored"]] for a in analyses}
    slim = [{kk: v for kk, v in a.items() if kk != "stored"} for a in analyses]
    return {
        "puzzle_id": pid,
        "title": f"{protagonist.title()}: {ev_label}",
        "narrative": texts.get("story", ""),
        "sequence": sequence,
        "masked_positions": [k],
        "answer_grids": {str(k): tgrids[k]},
        "narrative_variants": variants,
        "metadata": {
            "creator": "nhm-sim", "generator_version": GENERATOR_VERSION, "created_at": date.today().isoformat(),
            "grammar": meta.get("grammar"), "story_type": meta.get("story_type"), "story_features": meta.get("features", []),
            "roles": meta.get("roles", {}), "m": meta.get("m"), "tree": meta.get("tree", []),
            "primitives": sorted(meta.get("primitives") or []), "states": sorted(meta.get("states") or []),
            "convention": conv.spec(), "symmetry": sym.spec(), "symmetry_text": S.describe(sym),
            "initial_scene": setup.to_dict(), "events": [_ev_dict(e) for e in events],
            "n_grids": len(grids), "n_states_total": n_states_total, "frame_indices": frame_idx,
            "event_chain": chain_meta,
            "mask_analysis": {kk: v for kk, v in chosen.items() if kk != "stored"},
            "all_mask_analyses": slim,
            "mask_variants": [a["position"] for a in valid],
            "alternatives": alternatives,
            "legend": legend,
            "conventions_text": conv.legend(known_locs, sym),
            "review": {"verdict": "", "notes": ""},
            "tags": ["audience:general", f"domain:{meta.get('grammar')}", f"grids:{len(grids)}", "size:uniform", "mask:single",
                     f"depth:{chosen['depth']}", f"event:{ev_label.split('(')[0].lower()}", f"phi:{conv.name}", f"sym:{sym.name}"],
        },
    }


def conv_describe_scene(conv: BaseConvention, scene: Scene, sym=None) -> str:
    fn = getattr(conv, "describe_scene", None)
    return fn(scene, sym=sym) if fn else ""


# ------------------------------------------------------------------------------------------
def rebuild(puzzle: dict, conv: Optional[BaseConvention] = None, sym: Optional[S.Symmetry] = None, mask: Optional[str] = None,
            min_alternatives: int = 2, require_self_taught: bool = True, store_alts: int = 8) -> Tuple[List[dict], str]:
    """Regenerate a puzzle from its metadata (initial scene + full event list), optionally under
    another convention, symmetry or mask policy. This is what a human edit round-trips through:
    edit ``metadata.events`` (or ``narrative``), then rebuild."""
    m = puzzle["metadata"]
    conv = conv or get_convention(_conv_name(m["convention"]))
    sym = sym or S.Symmetry.from_spec(m["symmetry"])
    events = [Event.from_dict(e) for e in m["events"]]
    initial = Scene.from_dict(m["initial_scene"]) if m.get("initial_scene") else None
    k = puzzle["masked_positions"][0]
    if mask is None:
        mask = "first" if k == 0 else ("last" if k == m["n_grids"] - 1 else "middle")
    meta = {"grammar": m.get("grammar"), "story_type": m.get("story_type"), "features": m.get("story_features", []),
            "roles": m.get("roles", {}), "m": m.get("m"), "tree": m.get("tree", []), "primitives": m.get("primitives") or None, "states": m.get("states") or None,
            "texts": {"story": puzzle["narrative"], **{v["variant"]: v["narrative"] for v in puzzle.get("narrative_variants", []) if v["variant"] != "physical"}}}
    pzs, why = _build(events, initial, conv, sym, mask, min_alternatives, require_self_taught, 3, store_alts,
                      puzzle["puzzle_id"], meta)
    for p in pzs:
        p["metadata"]["review"] = m.get("review", {"verdict": "", "notes": ""})
    return pzs, why


def _conv_name(spec: dict) -> str:
    if spec["name"] in ("table",) or spec.get("mood") == "table":
        return "table"
    if spec["name"] and "=" not in spec["name"]:
        try:
            get_convention(spec["name"])
            return spec["name"]
        except KeyError:
            pass
    return ",".join(f"{k}={spec[k]}" for k in ("mood", "location", "held", "want", "time"))

# ------------------------------------------------------------------------------------------
def event_from_spec(spec: str, roles: Dict[str, str]) -> Event:
    """'EMOTE.sad(g)' / 'TRANSFORM.hidden(e)' / 'LOSE(p, o3)' over role letters -> an Event over
    the puzzle's fillers (used to name a trend's continuation independently of the sample)."""
    m = re.match(r"^([A-Z]+)(?:\.([a-z]+))?\((.*)\)$", spec.strip())
    if not m:
        raise ValueError(f"bad event spec {spec!r}")
    prim, arg, inner = m.group(1), m.group(2), m.group(3)
    parts = [x.strip() for x in inner.split(",") if x.strip()]
    fill = {k.lower(): v for k, v in roles.items()}

    def f(letter):
        return fill.get(letter, letter).lower() if letter in fill else letter

    if prim == "EMOTE":
        return Event("EMOTE", [f(x) for x in parts], mood=arg)
    if prim == "TRANSFORM":
        return Event("TRANSFORM", [], theme=fill.get(parts[0], parts[0]), state=arg)
    if prim in ("ACQUIRE", "DROP", "LOSE", "WANT"):
        return Event(prim, [f(parts[0])], theme=fill.get(parts[1], parts[1]) if len(parts) > 1 else None)
    if prim in ("ASCEND", "DESCEND", "VANISH", "APPEAR", "UNWANT"):
        return Event(prim, [f(x) for x in parts])
    if prim in ("TRANSFER", "SPLIT"):
        return Event(prim, [f(parts[0])], theme=fill.get(parts[1], parts[1]), recipient=f(parts[2]))
    if prim in ("MOVE", "WITHDRAW"):
        return Event(prim, [f(parts[0])], recipient=f(parts[1]))
    raise ValueError(f"unsupported spec {spec!r}")


def continuation_grid(pz: dict, event: Event) -> Optional[Grid]:
    """The grid the masked frame would show if ``event`` (instead of the true event) had happened
    after the frame before the mask, rendered under the puzzle's own code and symmetry. None if the
    event does not apply there. Two different events can give the same grid (a held object hides
    another in the one held cell), which is why trend checks compare grids rather than labels."""
    m = pz["metadata"]
    conv = get_convention(_conv_name(m["convention"]))
    sym = S.Symmetry.from_spec(m["symmetry"])
    events = [Event.from_dict(e) for e in m["events"]]
    initial = Scene.from_dict(m["initial_scene"]) if m.get("initial_scene") else None
    setup, steps = simulate(events, conv, initial)
    all_scenes = [setup] + [st.scene for st in steps]
    frame_idx = m.get("frame_indices") or list(range(len(all_scenes)))
    k = pz["masked_positions"][0]
    if k == 0:
        return None
    prev = all_scenes[frame_idx[k - 1]]
    nxt = apply(prev, event)
    if nxt is None:
        return None
    width = len(pz["sequence"][0]["grid"] or pz["answer_grids"][str(k)][0]) if pz["sequence"][0]["grid"] is not None else len(pz["answer_grids"][str(k)][0])
    width = len(next(it["grid"] for it in pz["sequence"] if it["grid"] is not None)[0])
    if sym.parts() and "shift" in sym.parts():
        width -= int(sym.params.get("k", 1))
    return S.render(conv, nxt, width, sym)
