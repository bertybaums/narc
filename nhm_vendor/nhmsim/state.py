"""World state and event semantics for the Narrative Hierarchy Model simulator.

A ``Scene`` is the state between two events. ``apply(scene, event)`` is the denotation of an
event primitive as a Scene -> Scene function (``None`` when nothing in the *state* changes;
whether the *rendered* grid changes is the renderer's business, see ``certify.simulate``).

The eleven primitives of the ICLR 2027 instance (APPEAR, VANISH, MOVE, ASCEND, DESCEND,
ACQUIRE, DROP, LOSE, TRANSFER, EMOTE, TRANSFORM) follow ``narc-tiny-stories/nts/scene.py``
exactly, so the baseline convention here renders the same grids (see tests/test_parity_nts.py).
Five primitives are new (the enriched tree of the ICML plan and the auto-narc moral loops):

    WANT(x, o)        x wants o          (a thought bubble under conventions that render it)
    UNWANT(x)         x lets it go       (bubble clears; also cleared by getting o)
    WITHDRAW(x, y)    x steps away from y (re-slot leaving a gap; the "refrain" move)
    SPLIT(x, o, y)    x halves a big o with y (each holds one cell of o's colour)
    TIME(state)       day -> night or back (a sky row under conventions that render it)
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

H = 7
COLOR_RGB = {0: (0, 0, 0), 1: (0, 116, 217), 2: (255, 65, 54), 3: (46, 204, 64), 4: (255, 220, 0),
             5: (170, 170, 170), 6: (240, 18, 190), 7: (255, 133, 27), 8: (127, 219, 255), 9: (128, 0, 0)}
COLOR_NAMES = {0: "black", 1: "blue", 2: "red", 3: "green", 4: "yellow", 5: "grey", 6: "magenta",
               7: "orange", 8: "azure", 9: "maroon"}
COLOR_LETTER = {0: ".", 1: "B", 2: "R", 3: "G", 4: "Y", 5: "X", 6: "M", 7: "O", 8: "A", 9: "W"}

CHAR_COLORS = [1, 2, 6, 7]          # blue, red, magenta, orange (order of introduction)
OBJ_COLORS = [4, 8, 3, 9]           # yellow, azure, green, maroon
BROKEN_COLOR = 5
GROUND_COLOR = {"green": 3, "water": 8, "indoor": 5, "town": 9, "sky": 4, "none": 0}
MOODS = ("sad", "neutral", "happy")
OBJ_STATES = ("ok", "big", "broken", "hidden")
EXTRA_STATES = ("huge",)              # only in the neighbourhood of grammars that use them
OBJ_SIZE = {"ok": 1, "big": 2, "huge": 3, "broken": 1}
TIMES = ("day", "night")

PRIMITIVES = ("APPEAR", "VANISH", "MOVE", "ASCEND", "DESCEND", "ACQUIRE", "DROP", "LOSE", "TRANSFER",
              "EMOTE", "TRANSFORM", "WANT", "UNWANT", "WITHDRAW", "SPLIT", "TIME")

# location word -> location class; grammars extend this when loaded (grammar.load)
LOCATION_CLASS: Dict[str, str] = {}
for _cls, _words in {"green": ["park", "garden", "forest", "field", "hill", "yard", "woods", "meadow"],
                     "water": ["pond", "beach", "lake", "river", "sea", "ocean", "pool"],
                     "indoor": ["house", "room", "school", "store", "kitchen", "home", "shop", "bedroom"],
                     "town": ["town", "street", "village", "city", "road"],
                     "sky": ["sky"]}.items():
    for _w in _words:
        LOCATION_CLASS[_w] = _cls


def register_locations(classes: Dict[str, List[str]]) -> None:
    for cls, words in classes.items():
        for w in words:
            LOCATION_CLASS[w] = cls


def loc_class(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    if name in GROUND_COLOR:
        return name
    return LOCATION_CLASS.get(name)


# ------------------------------------------------------------------------------------------
@dataclass
class Char:
    name: str
    color: int
    slot: int
    present: bool = False
    mood: str = "neutral"
    elevated: bool = False
    wants: Optional[str] = None
    seen: bool = False         # has ever been drawn (a registered-but-unseen character can still be introduced retroactively)


@dataclass
class Obj:
    name: str
    color: int
    present: bool = False
    holder: Optional[str] = None
    ground_slot: Optional[int] = None
    state: str = "ok"          # ok | big | huge | broken | hidden
    seen: bool = False         # has ever been drawn (registered-but-unseen objects are not in the neighbourhood)


@dataclass
class Event:
    prim: str
    agents: List[str] = field(default_factory=list)
    theme: Optional[str] = None
    recipient: Optional[str] = None
    location: Optional[str] = None      # a location word (park) or class (green)
    mood: Optional[str] = None
    state: Optional[str] = None
    verb: str = ""
    text: str = ""                      # the clause that realised it (if any)
    sent_idx: int = -1
    conf: float = 1.0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        keep = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**keep)

    def label(self) -> str:
        """Short symbol, e.g. EMOTE.sad(tim) or TRANSFER(tim, ball -> lily)."""
        args = ", ".join(self.agents)
        if self.prim == "EMOTE":
            return f"EMOTE.{self.mood}({args})"
        if self.prim == "MOVE":
            return f"MOVE.loc({args} -> {self.location})" if self.location else f"MOVE.to({args} -> {self.recipient})"
        if self.prim in ("ACQUIRE", "DROP", "LOSE", "WANT"):
            return f"{self.prim}({args}, {self.theme})"
        if self.prim in ("TRANSFER", "SPLIT"):
            return f"{self.prim}({args}, {self.theme} -> {self.recipient})"
        if self.prim == "TRANSFORM":
            return f"TRANSFORM.{self.state}({self.theme})"
        if self.prim == "TIME":
            return f"TIME.{self.state}"
        if self.prim == "WITHDRAW":
            return f"WITHDRAW({args} <- {self.recipient})"
        if self.prim == "APPEAR" and self.theme and not self.agents:
            return f"APPEAR.obj({self.theme})"
        return f"{self.prim}({args})"


@dataclass
class Scene:
    location: str = "none"
    time: str = "day"
    chars: Dict[str, Char] = field(default_factory=dict)
    objs: Dict[str, Obj] = field(default_factory=dict)
    n_slots: int = 0

    def clone(self) -> "Scene":
        return copy.deepcopy(self)

    # ---- registry -------------------------------------------------------------------------
    def get_char(self, name):
        if name not in self.chars:
            if len(self.chars) >= len(CHAR_COLORS):
                return None
            self.chars[name] = Char(name, CHAR_COLORS[len(self.chars)], slot=self.n_slots)
            self.n_slots += 1
        return self.chars[name]

    def get_obj(self, name, color: Optional[int] = None):
        if name not in self.objs:
            if color is None:
                if len(self.objs) >= len(OBJ_COLORS):
                    return None
                color = OBJ_COLORS[len(self.objs) % len(OBJ_COLORS)]
            self.objs[name] = Obj(name, color)
        return self.objs[name]

    def present_chars(self) -> List[Char]:
        return sorted([c for c in self.chars.values() if c.present], key=lambda c: c.slot)

    def present_objs(self) -> List[Obj]:
        return [o for o in self.objs.values() if o.present]

    def free_ground_slot(self, prefer=None):
        used = {o.ground_slot for o in self.objs.values() if o.present and o.holder is None}
        order = ([prefer] if prefer is not None else []) + list(range(self.n_slots))
        for s in order:
            if s not in used:
                return s
        self.n_slots += 1
        return self.n_slots - 1

    def signature(self):
        cs = tuple(sorted((c.name, c.slot, c.mood, c.elevated, c.wants) for c in self.chars.values() if c.present))
        os_ = tuple(sorted((o.name, o.holder, o.ground_slot, o.state) for o in self.objs.values() if o.present))
        return (self.location, self.time, cs, os_)

    # ---- (de)serialisation, so a puzzle JSON carries its initial state ---------------------
    def to_dict(self):
        return {"location": self.location, "time": self.time, "n_slots": self.n_slots,
                "chars": {k: asdict(v) for k, v in self.chars.items()},
                "objs": {k: asdict(v) for k, v in self.objs.items()}}

    @classmethod
    def from_dict(cls, d):
        s = cls(location=d.get("location", "none"), time=d.get("time", "day"), n_slots=d.get("n_slots", 0))
        s.chars = {k: Char(**v) for k, v in d.get("chars", {}).items()}
        s.objs = {k: Obj(**v) for k, v in d.get("objs", {}).items()}
        return s


# ------------------------------------------------------------------------------------------
def apply(scene: Scene, ev: Event) -> Optional[Scene]:
    """Return a new Scene if the event changes the state, else None."""
    s = _apply(scene, ev)
    if s is not None:
        for o in s.objs.values():
            if o.present:
                o.seen = True
        for c in s.chars.values():
            if c.present:
                c.seen = True
    return s


def _apply(scene: Scene, ev: Event) -> Optional[Scene]:
    s = scene.clone()
    p = ev.prim
    changed = False

    if p == "APPEAR":
        for a in ev.agents:
            c = s.get_char(a)
            if c and not c.present:
                c.present, c.mood, c.elevated = True, "neutral", False
                changed = True
        if ev.theme:
            o = s.get_obj(ev.theme)
            if o and not o.present:
                o.present, o.holder, o.ground_slot = True, None, s.free_ground_slot(_slot_of_first_present(s))
                changed = True
        lc = loc_class(ev.location)
        if lc and s.location == "none":
            s.location = lc
            changed = True
        return s if changed else None

    if p == "VANISH":
        for a in ev.agents:
            c = s.chars.get(a)
            if c and c.present:
                c.present = False
                for o in s.objs.values():
                    if o.holder == a:
                        o.present = False
                changed = True
        return s if changed else None

    if p == "MOVE":
        movers = [s.chars.get(a) for a in ev.agents if s.chars.get(a)]
        movers = [m for m in movers if m.present] or [s.get_char(a) for a in ev.agents if s.get_char(a)]
        movers = [m for m in movers if m]
        lc = loc_class(ev.location)
        if lc and lc != s.location and movers:
            s.location = lc
            names = {m.name for m in movers}
            for c in s.chars.values():
                c.present = c.name in names
                if c.present:
                    c.elevated = False
            for o in s.objs.values():
                if o.holder is None or o.holder not in names:
                    o.present = False
            for m in movers:
                m.present = True
            return s
        if ev.recipient and ev.recipient in s.chars and movers:
            tgt = s.chars[ev.recipient]
            if not tgt.present:
                return None
            for m in movers:
                if m.name == tgt.name:
                    continue
                if not m.present:
                    m.present = True
                    changed = True
                if abs(m.slot - tgt.slot) != 1:
                    changed |= _move_adjacent(s, m, tgt)
            return s if changed else None
        return None

    if p == "WITHDRAW":
        mover = s.chars.get(ev.agents[0]) if ev.agents else None
        tgt = s.chars.get(ev.recipient) if ev.recipient else None
        if not mover or not mover.present or not tgt or not tgt.present or mover.name == tgt.name:
            return None
        if abs(mover.slot - tgt.slot) > 1:
            return None                      # already apart
        # step away from the target, past the end of the line on the mover's side, so one
        # empty slot separates them (a visible gap); slots are renumbered from 0
        pc = s.present_chars()
        if mover.slot > tgt.slot:
            mover.slot = max(c.slot for c in pc) + 1
        else:
            mover.slot = min(c.slot for c in pc) - 1
        _compact(s)
        return s

    if p in ("ASCEND", "DESCEND"):
        want = p == "ASCEND"
        for a in ev.agents:
            c = s.chars.get(a)
            if c and c.present and c.elevated != want:
                c.elevated = want
                changed = True
        return s if changed else None

    if p == "ACQUIRE":
        if not ev.theme or not ev.agents:
            return None
        c = s.get_char(ev.agents[0])
        o = s.get_obj(ev.theme)
        if not c or not o:
            return None
        if not c.present:
            c.present = True
        if o.present and o.holder == c.name:
            return None
        if o.state == "hidden":
            o.state = "ok"
        o.present, o.holder, o.ground_slot = True, c.name, None
        if c.wants == o.name:
            c.wants = None
        return s

    if p == "DROP":
        o = s.objs.get(ev.theme) if ev.theme else None
        if not o or not o.present or o.holder is None:
            return None
        holder = s.chars.get(o.holder)
        o.holder = None
        o.ground_slot = s.free_ground_slot(holder.slot if holder else None)
        return s

    if p == "LOSE":
        o = s.objs.get(ev.theme) if ev.theme else None
        if not o or not o.present:
            return None
        o.present, o.holder = False, None
        return s

    if p == "TRANSFER":
        if not ev.theme:
            return None
        giver = s.chars.get(ev.agents[0]) if ev.agents else None
        recv = s.chars.get(ev.recipient) if ev.recipient else None
        if recv is None or not recv.present:
            others = [c for c in s.present_chars() if not giver or c.name != giver.name]
            if len(others) != 1:
                return None
            recv = others[0]
        if giver is not None and giver.name == recv.name:
            return None
        o = s.get_obj(ev.theme)
        if not o:
            return None
        if o.present and o.holder == recv.name:
            return None
        o.present, o.holder, o.ground_slot = True, recv.name, None
        if o.state == "hidden":
            o.state = "ok"
        if recv.wants == o.name:
            recv.wants = None
        return s

    if p == "SPLIT":
        o = s.objs.get(ev.theme) if ev.theme else None
        giver = s.chars.get(ev.agents[0]) if ev.agents else None
        recv = s.chars.get(ev.recipient) if ev.recipient else None
        if not o or not o.present or o.state != "big" or not giver or o.holder != giver.name:
            return None
        if recv is None or not recv.present or recv.name == giver.name:
            return None
        half = s.get_obj(o.name + "_half", color=o.color)
        if half is None or half.present:
            return None
        o.state = "ok"
        half.present, half.holder, half.ground_slot, half.state = True, recv.name, None, "ok"
        if recv.wants == o.name:
            recv.wants = None
        return s

    if p == "EMOTE":
        for a in ev.agents:
            c = s.chars.get(a)
            if c and c.present and ev.mood and c.mood != ev.mood:
                c.mood = ev.mood
                changed = True
        return s if changed else None

    if p == "WANT":
        c = s.chars.get(ev.agents[0]) if ev.agents else None
        if not c or not c.present or not ev.theme:
            return None
        o = s.get_obj(ev.theme)           # register the colour even if the object is not yet on scene
        if o is None or c.wants == ev.theme:
            return None
        if o.present and o.holder == c.name:
            return None                   # already has it
        c.wants = ev.theme
        return s

    if p == "UNWANT":
        c = s.chars.get(ev.agents[0]) if ev.agents else None
        if not c or not c.present or c.wants is None:
            return None
        c.wants = None
        return s

    if p == "TIME":
        st = ev.state
        if st not in TIMES or s.time == st:
            return None
        s.time = st
        return s

    if p == "TRANSFORM":
        if not ev.theme:
            return None
        o = s.get_obj(ev.theme)
        if not o:
            return None
        st = ev.state or "broken"
        if st not in OBJ_STATES + EXTRA_STATES + ("open", "recolor", "closed", "small"):
            return None
        if st in ("open", "recolor", "closed"):
            st = {"open": "big", "recolor": "ok", "closed": "ok"}[st]
        if st == "small":
            st = "ok"
        if not o.present:
            if st == "hidden":
                return None
            o.present, o.holder, o.ground_slot = True, None, s.free_ground_slot(_slot_of_first_present(s))
            o.state = st
            return s
        if st == "hidden":
            o.present = False
            return s
        if o.state == st:
            return None
        o.state = st
        return s

    return None


def _slot_of_first_present(s: Scene):
    pc = s.present_chars()
    return pc[0].slot if pc else None


def _move_adjacent(s: Scene, mover: Char, target: Char) -> bool:
    """Re-slot ``mover`` to the slot right after ``target`` (shifting others up)."""
    order = sorted(s.chars.values(), key=lambda c: c.slot)
    order = [c for c in order if c.name != mover.name]
    idx = [c.name for c in order].index(target.name)
    order.insert(idx + 1, mover)
    old = {c.name: c.slot for c in s.chars.values()}
    for i, c in enumerate(order):
        c.slot = i
    s.n_slots = max(s.n_slots, len(order))
    return any(old[c.name] != c.slot for c in s.chars.values())


def _compact(s: Scene) -> None:
    """Shift slots so the leftmost present character is at slot 0; keep gaps; keep ground objects."""
    pc = s.present_chars()
    if not pc:
        return
    lo = min(c.slot for c in pc)
    if lo != 0:
        for c in s.chars.values():
            c.slot = max(0, c.slot - lo)
        for o in s.objs.values():
            if o.ground_slot is not None:
                o.ground_slot = max(0, o.ground_slot - lo)
    s.n_slots = max(max(c.slot for c in s.chars.values()) + 1,
                    max([o.ground_slot + 1 for o in s.objs.values() if o.ground_slot is not None] or [0]))
