"""Event-semantics grammars (the latent tree T and the text surface A) loaded from YAML.

A grammar file has the shape of ``narc-tiny-stories/nts/rhm.py`` made data:

    name: tinystories_base
    extends: <another grammar>            # optional; dicts merge, lists replace
    lexicon:  {names_m: [...], names_f: [...], animals: [...], objects: [...],
               locations: {green: [park, ...], water: [...], indoor: [...], town: [...]}}
    roles:    {P: person, F: person, A: animal, O: object, L1: location, L2: location}
    story_types:
      quest_success: {episodes: [SETUP, WANT, SEEK, GET, JOY], features: []}
    episodes:                              # m synonymous event sequences per episode
      SETUP:
        - [[APPEAR, P]]
        - [[APPEAR, P], [MOVE.loc, P, L1]]
    clauses:                               # m synonymous clause templates per event key
      "APPEAR:P": ["Once upon a time, there was a little kid named {P}.", ...]
    surfaces:                              # optional extra text surfaces (same keys)
      terse: {"APPEAR:P": ["{P} was there."]}

Event tuples (role letters are replaced by the sampled role fillers):

    [APPEAR, R]  [APPEAR.obj, O]  [VANISH, R]  [MOVE.loc, R, L]  [MOVE.to, R, R2]  [WITHDRAW, R, R2]
    [ASCEND, R]  [DESCEND, R]  [ACQUIRE, R, O]  [DROP, R, O]  [LOSE, R, O]  [TRANSFER, R, O, R2]
    [SPLIT, R, O, R2]  [EMOTE, R|ALL, mood]  [TRANSFORM, O, state]  [WANT, R, O]  [UNWANT, R]  [TIME, state]

Clause keys: the tuple kind, with a suffix for the discriminating argument:
``APPEAR:P`` (per role), ``APPEAR.obj``, ``EMOTE:happy``, ``EMOTE:ALL:sad``, ``TRANSFORM:broken``,
``TIME:night``; everything else is the bare kind (``MOVE.loc``, ``TRANSFER``, ``WANT``...).
Templates may use ``{P} {F} {A} {O} ...`` (all roles), ``{X}`` (the acting role's filler),
``{Y}`` (the second role's filler) and ``{L}`` (the location word).

``sample`` returns the story type, the tree (which of the m alternatives was drawn at each
node), the event chain, and one text per surface. ``m`` caps how many alternatives are used
at the episode and clause levels, as in the RHM. The ``grammar`` text (m = 1: template 0
everywhere) is also returned, as the minimum-synonymy control surface.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .state import Event, register_locations

GRAMMAR_DIR = Path(__file__).resolve().parent.parent / "grammars"


@dataclass
class Grammar:
    name: str
    description: str
    lexicon: Dict
    roles: Dict[str, str]
    story_types: Dict[str, Dict]
    episodes: Dict[str, List[List[list]]]
    clauses: Dict[str, List[str]]
    surfaces: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    requires: Dict[str, str] = field(default_factory=dict)     # e.g. {want: bubble}
    families: Dict[str, List[str]] = field(default_factory=dict)  # twist/twin groups sharing a prefix
    path: Optional[str] = None

    def location_classes(self) -> Dict[str, List[str]]:
        return self.lexicon.get("locations", {})

    def states(self):
        """Object states the grammar's TRANSFORM events use (extra sizes enter the neighbourhood only here)."""
        out = set()
        for alts in self.episodes.values():
            for seq in alts:
                for ev in seq:
                    ev0 = _split_tag(ev)[0]
                    if ev0[0] == "TRANSFORM":
                        out.add(ev0[2])
        return sorted(out)

    def primitives(self):
        prims = set()
        for alts in self.episodes.values():
            for seq in alts:
                for ev in seq:
                    prims.add(_split_tag(ev)[0][0].split(".")[0])
        return sorted(prims)


def _merge(base: Dict, over: Dict) -> Dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _find(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.exists():
        return p
    q = GRAMMAR_DIR / f"{name_or_path}.yaml"
    if q.exists():
        return q
    raise FileNotFoundError(f"no grammar {name_or_path!r} (looked in {GRAMMAR_DIR})")


def load(name_or_path: str) -> Grammar:
    path = _find(name_or_path)
    raw = yaml.safe_load(path.read_text())
    if raw.get("extends"):
        base = load(raw["extends"])
        raw = _merge(_as_raw(base), {k: v for k, v in raw.items() if k != "extends"})
    g = Grammar(name=raw["name"], description=raw.get("description", ""), lexicon=raw["lexicon"],
                roles=raw["roles"], story_types=raw["story_types"], episodes=raw["episodes"],
                clauses=raw["clauses"], surfaces=raw.get("surfaces", {}) or {},
                requires=raw.get("requires", {}) or {}, families=raw.get("families", {}) or {}, path=str(path))
    register_locations(g.location_classes())
    validate(g)
    return g


def _as_raw(g: Grammar) -> Dict:
    return {"name": g.name, "description": g.description, "lexicon": g.lexicon, "roles": g.roles,
            "story_types": g.story_types, "episodes": g.episodes, "clauses": g.clauses,
            "surfaces": g.surfaces, "requires": g.requires, "families": g.families}


def available() -> List[str]:
    return sorted(p.stem for p in GRAMMAR_DIR.glob("*.yaml"))


KINDS = {"APPEAR", "APPEAR.obj", "VANISH", "MOVE.loc", "MOVE.to", "WITHDRAW", "ASCEND", "DESCEND", "ACQUIRE",
         "DROP", "LOSE", "TRANSFER", "SPLIT", "EMOTE", "TRANSFORM", "WANT", "UNWANT", "TIME"}


def validate(g: Grammar) -> None:
    for fam, types in g.families.items():
        for t in types:
            if t not in g.story_types:
                raise ValueError(f"{g.name}: family {fam} names unknown story type {t}")
    for st, spec in g.story_types.items():
        for ep in spec["episodes"]:
            if ep not in g.episodes:
                raise ValueError(f"{g.name}: story type {st} uses undefined episode {ep}")
    for ep, alts in g.episodes.items():
        for seq in alts:
            for ev in seq:
                if _split_tag(ev)[0][0] not in KINDS:
                    raise ValueError(f"{g.name}: episode {ep} has unknown event kind {ev[0]}")
                key = clause_key(ev)
                if key not in g.clauses:
                    raise ValueError(f"{g.name}: no clause templates for {key} (episode {ep})")


def _split_tag(ev: list):
    """An optional last element '@sense' selects a clause-template set for an event whose
    grid denotation is the same as another's (a gift and a grab are both TRANSFER)."""
    if len(ev) > 1 and isinstance(ev[-1], str) and ev[-1].startswith("@"):
        return ev[:-1], ev[-1][1:]
    return ev, None


def clause_key(ev: list) -> str:
    ev, tag = _split_tag(ev)
    kind = ev[0]
    if kind == "APPEAR":
        key = f"APPEAR:{ev[1]}"
    elif kind == "EMOTE":
        key = f"EMOTE:ALL:{ev[2]}" if ev[1] == "ALL" else f"EMOTE:{ev[2]}"
    elif kind == "TRANSFORM":
        key = f"TRANSFORM:{ev[2]}"
    elif kind == "TIME":
        key = f"TIME:{ev[1]}"
    else:
        key = kind
    return f"{key}@{tag}" if tag else key


# ------------------------------------------------------------------------------------------
@dataclass
class Sample:
    grammar: str
    story_type: str
    features: List[str]
    roles: Dict[str, str]
    tree: List[dict]
    events: List[Event]
    texts: Dict[str, str]          # surface -> text; always has "story" and "grammar"
    m: int = 3
    primitives: List[str] = field(default_factory=list)
    states: List[str] = field(default_factory=list)


def _fill_roles(g: Grammar, rng: random.Random) -> Dict[str, str]:
    lex = g.lexicon
    used: Dict[str, set] = {}
    roles: Dict[str, str] = {}
    gender = rng.choice("mf")
    loc_classes = list(g.location_classes())
    rng.shuffle(loc_classes)
    li = 0
    for role, kind in g.roles.items():
        if kind == "person":
            pool = lex["names_m"] + lex["names_f"] if role != "P" else (lex["names_m"] if gender == "m" else lex["names_f"])
        elif kind == "animal":
            pool = lex["animals"]
        elif kind == "object":
            pool = lex["objects"]
        elif kind == "location":
            cls = loc_classes[li % len(loc_classes)]
            li += 1
            pool = lex["locations"][cls]
        else:
            pool = lex[kind]
        choices = [w for w in pool if w not in used.setdefault(kind, set())]
        w = rng.choice(choices or pool)
        used[kind].add(w)
        roles[role] = w
    return roles


def _event(ev: list, roles: Dict[str, str], si: int):
    """Build an Event from an event tuple. Returns (event, fill kwargs)."""
    ev, tag = _split_tag(ev)
    event, kw = _event_core(ev, roles, si)
    if tag:
        event.verb = tag
    return event, kw


def _event_core(ev: list, roles: Dict[str, str], si: int):
    kind = ev[0]
    R = lambda r: roles.get(r, r)          # noqa: E731
    ident = lambda r: "ALL" if r == "ALL" else R(r).lower()   # noqa: E731  (ALL expands to everyone present later)
    kw: Dict[str, str] = {}
    if kind == "APPEAR":
        return Event("APPEAR", [ident(ev[1])], sent_idx=si, verb="appear"), {"X": R(ev[1])}
    if kind == "APPEAR.obj":
        return Event("APPEAR", [], theme=R(ev[1]), sent_idx=si, verb="see"), {"O": R(ev[1]), "X": R("P")}
    if kind == "VANISH":
        return Event("VANISH", [ident(ev[1])], sent_idx=si, verb="leave"), {"X": R(ev[1])}
    if kind == "MOVE.loc":
        kw = {"X": R(ev[1]), "L": R(ev[2])}
        return Event("MOVE", [ident(ev[1])], location=R(ev[2]), sent_idx=si, verb="go"), kw
    if kind == "MOVE.to":
        kw = {"X": R(ev[1]), "Y": R(ev[2])}
        return Event("MOVE", [ident(ev[1])], recipient=ident(ev[2]), sent_idx=si, verb="run to"), kw
    if kind == "WITHDRAW":
        kw = {"X": R(ev[1]), "Y": R(ev[2])}
        return Event("WITHDRAW", [ident(ev[1])], recipient=ident(ev[2]), sent_idx=si, verb="step back"), kw
    if kind in ("ASCEND", "DESCEND"):
        return Event(kind, [ident(ev[1])], sent_idx=si, verb="climb" if kind == "ASCEND" else "fall"), {"X": R(ev[1])}
    if kind in ("ACQUIRE", "DROP", "LOSE"):
        kw = {"X": R(ev[1]), "O": R(ev[2])}
        return Event(kind, [ident(ev[1])], theme=R(ev[2]), sent_idx=si, verb=kind.lower()), kw
    if kind in ("TRANSFER", "SPLIT"):
        kw = {"X": R(ev[1]), "O": R(ev[2]), "Y": R(ev[3])}
        return Event(kind, [ident(ev[1])], theme=R(ev[2]), recipient=ident(ev[3]), sent_idx=si,
                     verb="give" if kind == "TRANSFER" else "share"), kw
    if kind == "EMOTE":
        who, mood = ev[1], ev[2]
        if who == "ALL":
            return Event("EMOTE", ["ALL"], mood=mood, sent_idx=si, verb="be " + mood), {}
        return Event("EMOTE", [ident(who)], mood=mood, sent_idx=si, verb="be " + mood), {"X": R(who)}
    if kind == "TRANSFORM":
        kw = {"O": R(ev[1]), "X": R("P")}
        return Event("TRANSFORM", [ident("P")], theme=R(ev[1]), state=ev[2], sent_idx=si,
                     verb="break" if ev[2] == "broken" else "fix"), kw
    if kind == "WANT":
        kw = {"X": R(ev[1]), "O": R(ev[2])}
        return Event("WANT", [ident(ev[1])], theme=R(ev[2]), sent_idx=si, verb="want"), kw
    if kind == "UNWANT":
        return Event("UNWANT", [ident(ev[1])], sent_idx=si, verb="let go"), {"X": R(ev[1])}
    if kind == "TIME":
        return Event("TIME", [], state=ev[1], sent_idx=si, verb=ev[1]), {}
    raise ValueError(f"unknown event kind {kind}")


def realise(template: str, roles: Dict[str, str], kw: Dict[str, str]) -> str:
    d = dict(roles)
    d.setdefault("X", roles.get("P", ""))        # an agentless event's clause may still name the protagonist
    d.setdefault("L", roles.get("L1", ""))
    d.update(kw)
    return template.format(**d)


def sample(g: Grammar, rng: random.Random, m: int = 3, story_type: Optional[str] = None) -> Sample:
    st = story_type or rng.choice(list(g.story_types))
    spec = g.story_types[st]
    roles = _fill_roles(g, rng)
    surfaces = {"story": g.clauses, **g.surfaces}
    texts: Dict[str, List[str]] = {s: [] for s in surfaces}
    texts["grammar"] = []
    events: List[Event] = []
    tree: List[dict] = []
    si = 0
    for ep in spec["episodes"]:
        alts = g.episodes[ep][:max(1, m)]
        ei = rng.randrange(len(alts))
        node = {"episode": ep, "choice": ei, "of": len(alts), "clauses": []}
        tree.append(node)
        for ev in alts[ei]:
            event, kw = _event(ev, roles, si)
            events.append(event)
            key = clause_key(ev)
            temps = g.clauses[key][:max(1, m)]
            ci = rng.randrange(len(temps))
            node["clauses"].append({"event": key, "choice": ci, "of": len(temps)})
            event.text = realise(temps[ci], roles, kw)
            for sname, table in surfaces.items():
                tl = (table.get(key) or g.clauses[key])[:max(1, m)]
                texts[sname].append(realise(tl[ci % len(tl)], roles, kw))
            texts["grammar"].append(realise(g.clauses[key][0], roles, kw))
            si += 1
    present: List[str] = []
    for e in events:
        if e.prim == "APPEAR":
            present.extend(a for a in e.agents if a not in present)
        for a in e.agents + ([e.recipient] if e.recipient else []):
            if a and a != "ALL" and a not in present:
                present.append(a)
        if e.agents == ["ALL"]:
            e.agents = list(present) or [roles["P"].lower()]
    return Sample(g.name, st, list(spec.get("features", [])), roles, tree, events,
                  {k: " ".join(x for x in v if x) for k, v in texts.items()}, m=m, primitives=g.primitives(), states=g.states())
