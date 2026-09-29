"""Grid conventions: the code phi that assigns latent variables to visual features.

A convention is composed channel by channel, so "which feature carries mood" can be changed
while "which feature carries location" stays put (the metaphor brief's requirement), and the
product of channel choices gives a family of codes rather than three hand-written renderers.

    mood      height   bar height 1/2/3 (the ICLR baseline; iconic: more = taller)
              marker   bar fixed at 2; one cell above the head: grey = sad, yellow = happy, none = neutral
              width    bar fixed at 2 tall; width 1/2/3 (slots become 5 columns wide)
              altitude bar fixed at 2 tall; it stands higher the happier it is (sad on row 5, happy on row 3)
    location  ground   the bottom row is coloured by place (baseline)
              sky      the top row is coloured by place, bottom row black
              band     both the top and the bottom row
    held      right    a held object sits right of its holder at hand height (baseline)
              above    a held object sits on its holder's head
    want      none     desire has no denotation (baseline)
              bubble   the wanted object's colour in the top row above the wanter (the "thought bubble")
    time      none     (baseline)
              skyrow   the top row is yellow by day and black by night

Every convention exposes the same five methods the certificate needs:
``render``, ``facts`` (which conventions a scene exhibits), ``needs`` (which conventions a
transition relies on), ``legend`` (the explicit statement of the code, for the
conventions-only control) and ``describe`` / ``describe_scene`` (physical-stance sentences,
the literal text surface that names grid features rather than events). The text methods take
the puzzle's symmetry so that colours and directions describe the grid as rendered: under a
palette permutation the scene is recoloured first (``symmetries.recolor``), under ``mirror``
"right" reads "left", under ``vflip`` "top" reads "bottom".

``TableConvention`` is the non-iconic pole (SPINE 4a, "the metaphor priced at zero"): one
row per entity, one column per attribute; adjacency has no denotation there.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Set, Tuple

from .state import (BROKEN_COLOR, COLOR_NAMES, GROUND_COLOR, H, MOODS, Char, Event, Obj, Scene)

STAND_ROW = 5
ELEVATED_ROW = 2
MOOD_HEIGHT = {"sad": 1, "neutral": 2, "happy": 3}
MOOD_WIDTH = {"sad": 1, "neutral": 2, "happy": 3}
MOOD_BASE = {"sad": 5, "neutral": 4, "happy": 3}      # altitude code
MARKER_COLOR = {"sad": 5, "happy": 4}                  # marker code
DAY_COLOR = 4
LOC_WORD = {"green": "park", "water": "pond", "indoor": "house", "town": "town", "sky": "sky", "none": "nowhere"}

Grid = List[List[int]]
Fact = Tuple


def cname(c: int) -> str:
    return COLOR_NAMES[c]


_CANON = {"right": "right", "left": "left", "up": "up", "down": "down", "top": "top", "bottom": "bottom",
          "above": "above", "below": "below", "on top of": "on top of", "underneath": "underneath",
          "higher": "higher", "lower": "lower"}


def words(sym=None) -> Dict[str, str]:
    """Direction words for the physical-stance text and the legend, under a symmetry: a mirrored
    grid puts the held object left of its holder, a flipped grid has its ground row at the top."""
    w = dict(_CANON)
    parts = sym.parts() if sym is not None and hasattr(sym, "parts") else []

    def swap(a, b):
        w[a], w[b] = w[b], w[a]

    if "mirror" in parts:
        swap("right", "left")
    if "vflip" in parts:
        swap("up", "down")
        swap("top", "bottom")
        swap("above", "below")
        swap("on top of", "underneath")
        swap("higher", "lower")
    return w


class BaseConvention:
    name: str = "base"
    max_width: int = 13
    slot_w: int = 3
    mood = location = held = want = time = "n/a"
    description: str = ""

    def check(self) -> None:
        pass

    def width_for(self, n_slots: int) -> int:
        raise NotImplementedError

    def render(self, scene: Scene, width: int) -> Grid:
        raise NotImplementedError

    def facts(self, scene: Scene) -> Set[Fact]:
        raise NotImplementedError

    def needs(self, prev: Scene, nxt: Scene, ev: Event) -> Set[Fact]:
        raise NotImplementedError

    def legend(self, known_locs: List[str], sym=None) -> str:
        raise NotImplementedError

    def describe(self, prev: Scene, nxt: Scene, ev: Event, sym=None) -> str:
        raise NotImplementedError

    def describe_scene(self, scene: Scene, sym=None) -> str:
        raise NotImplementedError

    def spec(self) -> Dict:
        return {"name": self.name, "mood": self.mood, "location": self.location, "held": self.held,
                "want": self.want, "time": self.time, "slot_w": self.slot_w, "max_width": self.max_width}

    def renders_want(self) -> bool:
        return self.want != "none"

    def renders_time(self) -> bool:
        return self.time != "none"

    def renders_adjacency(self) -> bool:
        return True


# ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Convention(BaseConvention):
    name: str = "baseline"
    mood: str = "height"
    location: str = "ground"
    held: str = "right"
    want: str = "none"
    time: str = "none"
    slot_w: int = 3
    max_width: int = 13
    description: str = ""

    # ---- validity -------------------------------------------------------------------------
    def check(self) -> None:
        if self.mood not in ("height", "marker", "width", "altitude"):
            raise ValueError(f"unknown mood code {self.mood}")
        if self.location not in ("ground", "sky", "band"):
            raise ValueError(f"unknown location code {self.location}")
        if self.held not in ("right", "above"):
            raise ValueError(f"unknown held code {self.held}")
        if self.want not in ("none", "bubble"):
            raise ValueError(f"unknown want code {self.want}")
        if self.time not in ("none", "skyrow"):
            raise ValueError(f"unknown time code {self.time}")
        if self.mood == "marker" and self.held == "above":
            raise ValueError("marker and above both use the cell over the head")
        if self.time == "skyrow" and self.location in ("sky", "band"):
            raise ValueError("skyrow and a top-row location code both use row 0")
        if self.mood == "width" and self.slot_w < 5:
            raise ValueError("width code needs slot_w = 5")

    # ---- layout ---------------------------------------------------------------------------
    def width_for(self, n_slots: int) -> int:
        return max(7, self.slot_w * max(n_slots, 2) + 1)

    def _x(self, slot: int) -> int:
        return 1 + self.slot_w * slot

    def _held_col(self, slot: int) -> int:
        return self._x(slot) + self.slot_w - 2

    def _ground_col(self, slot: int) -> int:
        return self._x(slot) + self.slot_w - 1

    def _top_row_used(self) -> bool:
        return self.location in ("sky", "band") or self.time == "skyrow"

    def _base(self, c: Char) -> int:
        if self.mood == "altitude":
            return 1 if c.elevated else MOOD_BASE[c.mood]
        return ELEVATED_ROW if c.elevated else STAND_ROW

    def _bar_cells(self, c: Char) -> List[Tuple[int, int]]:
        x, base = self._x(c.slot), self._base(c)
        if self.mood == "height":
            return [(r, x) for r in range(base, base - MOOD_HEIGHT[c.mood], -1)]
        if self.mood == "width":
            return [(r, x + dx) for r in (base, base - 1) for dx in range(MOOD_WIDTH[c.mood])]
        return [(r, x) for r in (base, base - 1)]

    def _top_of_bar(self, c: Char) -> int:
        return min(r for r, _ in self._bar_cells(c))

    def _row_phrase(self, w: Dict[str, str]) -> str:
        return {"ground": f"the {w['bottom']} row", "sky": f"the {w['top']} row",
                "band": f"the {w['top']} and {w['bottom']} rows"}[self.location]

    # ---- render ---------------------------------------------------------------------------
    def render(self, scene: Scene, width: int) -> Grid:
        g = [[0] * width for _ in range(H)]
        gc = GROUND_COLOR.get(scene.location, 0)
        if gc:
            rows = {"ground": [H - 1], "sky": [0], "band": [0, H - 1]}[self.location]
            for r in rows:
                for x in range(width):
                    g[r][x] = gc
        if self.time == "skyrow" and scene.time == "day":
            for x in range(width):
                g[0][x] = DAY_COLOR
        for c in scene.present_chars():
            for r, x in self._bar_cells(c):
                if 0 <= r < H and 0 <= x < width:
                    g[r][x] = c.color
            if self.mood == "marker" and c.mood in MARKER_COLOR:
                r, x = self._base(c) - 2, self._x(c.slot)
                if 0 <= r < H and x < width:
                    g[r][x] = MARKER_COLOR[c.mood]
        for o in scene.present_objs():
            col = BROKEN_COLOR if o.state == "broken" else o.color
            cells = 2 if o.state == "big" else 1
            holder = scene.chars.get(o.holder) if o.holder else None
            if holder is not None and holder.present:
                if self.held == "right":
                    x, bottom = self._held_col(holder.slot), self._base(holder) - 1
                else:
                    x, bottom = self._x(holder.slot), self._top_of_bar(holder) - 1
            else:
                slot = o.ground_slot if o.ground_slot is not None else 0
                x, bottom = self._ground_col(slot), STAND_ROW
            for r in range(bottom, bottom - cells, -1):
                if 0 <= r < H and 0 <= x < width:
                    g[r][x] = col
        if self.want == "bubble":
            r = 1 if self._top_row_used() else 0
            for c in scene.present_chars():
                if c.wants and c.wants in scene.objs:
                    x = self._x(c.slot)
                    if x < width:
                        g[r][x] = scene.objs[c.wants].color
        return g

    # ---- self-teaching --------------------------------------------------------------------
    def facts(self, scene: Scene) -> Set[Fact]:
        f: Set[Fact] = {("ground", scene.location)}
        if self.time != "none":
            f.add(("time", scene.time))
        for c in scene.present_chars():
            f.add(("char", c.name))
            f.add(("mood", c.mood))
            if c.elevated:
                f.add(("elevated", True))
            if c.wants and self.want != "none":
                f.add(("want",))
                if c.wants in scene.objs:
                    f.add(("obj", scene.objs[c.wants].color))
        for o in scene.present_objs():
            f.add(("obj", o.color))
            f.add(("state", o.state))
            f.add(("held",) if o.holder else ("ground_obj",))
        return f

    def needs(self, prev: Scene, nxt: Scene, ev: Event) -> Set[Fact]:
        n: Set[Fact] = set()
        p = ev.prim

        def ocol(name):
            o = nxt.objs.get(name) or prev.objs.get(name)
            return o.color if o else None

        if p == "EMOTE":
            for a in ev.agents:
                if a in nxt.chars and nxt.chars[a].present:
                    n.add(("mood", nxt.chars[a].mood))
                    n.add(("char", a))
        elif p == "MOVE":
            if nxt.location != prev.location:
                n.add(("ground", nxt.location))
            else:
                n.add(("reslot",))
            for a in ev.agents:
                n.add(("char", a))
        elif p == "WITHDRAW":
            n.add(("reslot",))
            for a in ev.agents:
                n.add(("char", a))
        elif p in ("ASCEND", "DESCEND"):
            n.add(("elevated", True))
            for a in ev.agents:
                n.add(("char", a))
        elif p == "APPEAR":
            for a in ev.agents:
                n.add(("char", a))
            if ev.theme:
                n.add(("obj", ocol(ev.theme)))
                n.add(("ground_obj",))
        elif p == "VANISH":
            for a in ev.agents:
                n.add(("char", a))
        elif p in ("ACQUIRE", "TRANSFER"):
            n.add(("held",))
            n.add(("obj", ocol(ev.theme)))
            for a in ev.agents:
                n.add(("char", a))
            if ev.recipient:
                n.add(("char", ev.recipient))
        elif p == "SPLIT":
            n.add(("held",))
            n.add(("obj", ocol(ev.theme)))
            for a in ev.agents:
                n.add(("char", a))
            if ev.recipient:
                n.add(("char", ev.recipient))
        elif p == "DROP":
            n.add(("ground_obj",))
            n.add(("obj", ocol(ev.theme)))
        elif p == "LOSE":
            n.add(("obj", ocol(ev.theme)))
        elif p == "TRANSFORM":
            n.add(("obj", ocol(ev.theme)))
            if ev.theme in nxt.objs and nxt.objs[ev.theme].present:
                n.add(("state", nxt.objs[ev.theme].state))
        elif p == "WANT":
            n.add(("want",))
            n.add(("obj", ocol(ev.theme)))
            for a in ev.agents:
                n.add(("char", a))
        elif p == "UNWANT":
            n.add(("want",))
            for a in ev.agents:
                n.add(("char", a))
        elif p == "TIME":
            n.add(("time", nxt.time))
        n.discard(("obj", None))
        return n

    # ---- the explicit statement of the code ------------------------------------------------
    def legend(self, known_locs: List[str], sym=None) -> str:
        w = words(sym)
        loc_words = ", ".join(f"{cname(GROUND_COLOR[l])} = {LOC_WORD.get(l, l)}" for l in known_locs if l in GROUND_COLOR and l != "none")
        loc_row = self._row_phrase(w)
        loc_row = loc_row[0].upper() + loc_row[1:]
        s = [f"Each grid has {H} rows.", f"{loc_row} is coloured by the place: {loc_words}." if loc_words else f"{loc_row} is coloured by the place."]
        s.append("A character is a vertical bar of one colour, and keeps its colour in every grid.")
        s.append({
            "height": "The bar's height is the character's mood: 1 cell sad, 2 neutral, 3 happy.",
            "marker": f"The bar is 2 cells tall. The cell {w['above']} its head shows the mood: grey sad, yellow happy, nothing neutral.",
            "width": "The bar is 2 cells tall. Its width is the mood: 1 cell sad, 2 neutral, 3 happy.",
            "altitude": f"The bar is 2 cells tall. It stands {w['higher']} the happier the character is: sad rests on the second row from the {w['bottom']}, neutral one row {w['up']}, happy two rows {w['up']}.",
        }[self.mood])
        s.append(f"A character who has climbed up stands near the {w['top']} of the grid.")
        s.append("An object is one cell of one colour. " + {
            "right": f"Held, it sits just {w['right']} of its holder at hand height; on the ground, it sits at the {w['bottom']} of the holder's area.",
            "above": f"Held, it sits {w['on top of']} its holder's head; on the ground, it sits at the {w['bottom']} of the holder's area.",
        }[self.held])
        s.append("A big object is two cells tall. A broken object is grey. An object that is gone is not drawn.")
        if self.want == "bubble":
            row = f"second row from the {w['top']}" if self._top_row_used() else f"{w['top']} row"
            s.append(f"When a character wants an object, that object's colour appears in the {row} {w['above']} the character.")
        if self.time == "skyrow":
            s.append(f"The {w['top']} row is yellow by day and black at night.")
        return " ".join(s)

    # ---- physical-stance surface ----------------------------------------------------------
    def describe(self, prev: Scene, nxt: Scene, ev: Event, sym=None) -> str:
        """One sentence naming what the grid does. Pass scenes already recoloured under the
        symmetry (symmetries.recolor); directions follow the symmetry."""
        p = ev.prim
        w = words(sym)

        def bar(name):
            c = nxt.chars.get(name) or prev.chars.get(name)
            return f"the {cname(c.color)} bar" if c else "a bar"

        def cell(name):
            o = nxt.objs.get(name) or prev.objs.get(name)
            return f"the {cname(o.color)} cell" if o else "a cell"

        if p == "EMOTE":
            out = []
            for a in ev.agents:
                c = nxt.chars.get(a)
                if not c or not c.present:
                    continue
                if self.mood == "height":
                    h = MOOD_HEIGHT[c.mood]
                    out.append(f"{bar(a)} became {h} cell{'s' if h > 1 else ''} tall")
                elif self.mood == "width":
                    wd = MOOD_WIDTH[c.mood]
                    out.append(f"{bar(a)} became {wd} cell{'s' if wd > 1 else ''} wide")
                elif self.mood == "marker":
                    if c.mood in MARKER_COLOR:
                        out.append(f"{_a(cname(MARKER_COLOR[c.mood]))} cell appeared {w['above']} {bar(a)}")
                    else:
                        out.append(f"the cell {w['above']} {bar(a)} went away")
                else:
                    was = prev.chars[a].mood if a in prev.chars else "neutral"
                    d = MOOD_BASE[was] - MOOD_BASE[c.mood]
                    out.append(f"{bar(a)} moved {w['up'] if d > 0 else w['down']} {abs(d)} row{'s' if abs(d) > 1 else ''}")
            return _sent("; ".join(out))
        if p == "MOVE" and nxt.location != prev.location:
            gone = [bar(c.name) for c in prev.present_chars() if not nxt.chars[c.name].present]
            s = f"{self._row_phrase(w)} turned {cname(GROUND_COLOR[nxt.location])}"
            if gone:
                s += ", and " + ", ".join(gone) + " went away"
            return _sent(s)
        if p == "MOVE":
            return _sent(f"{bar(ev.agents[0])} moved to stand next to {bar(ev.recipient)}")
        if p == "WITHDRAW":
            return _sent(f"{bar(ev.agents[0])} moved away from {bar(ev.recipient)}, leaving a gap")
        if p in ("ASCEND", "DESCEND"):
            where = f"near the {w['top']}" if p == "ASCEND" else f"the {w['bottom']}"
            return _sent(f"{bar(ev.agents[0])} moved {w['up'] if p == 'ASCEND' else w['down']} to {where} of the grid")
        if p == "APPEAR":
            parts = [f"{bar(a)} appeared" for a in ev.agents]
            if ev.theme:
                parts.append(f"{cell(ev.theme)} appeared on the ground")
            return _sent(" and ".join(parts))
        if p == "VANISH":
            return _sent(" and ".join(f"{bar(a)} disappeared" for a in ev.agents))
        if p == "ACQUIRE":
            where = f"just {w['right']} of" if self.held == "right" else w["on top of"]
            return _sent(f"{cell(ev.theme)} moved {where} {bar(ev.agents[0])}")
        if p == "DROP":
            return _sent(f"{cell(ev.theme)} moved {w['down']} to the ground beside {bar(ev.agents[0])}")
        if p == "LOSE":
            return _sent(f"{cell(ev.theme)} disappeared")
        if p == "TRANSFER":
            return _sent(f"{cell(ev.theme)} moved from {bar(ev.agents[0])} to {bar(ev.recipient)}")
        if p == "SPLIT":
            return _sent(f"{cell(ev.theme)} became one cell, and a second {cname(nxt.objs[ev.theme].color)} cell appeared at {bar(ev.recipient)}")
        if p == "TRANSFORM":
            o = nxt.objs.get(ev.theme)
            if o and not o.present:
                return _sent(f"{cell(ev.theme)} disappeared")
            if o and o.state == "broken":
                return _sent(f"{cell(ev.theme)} turned grey")
            if o and o.state == "big":
                return _sent(f"{cell(ev.theme)} became two cells tall")
            return _sent(f"{cell(ev.theme)} turned {cname(o.color)} and one cell tall" if o else "a cell changed")
        if p == "WANT":
            return _sent(f"{_a(cname(nxt.objs[ev.theme].color))} cell appeared in the {w['top']} row {w['above']} {bar(ev.agents[0])}")
        if p == "UNWANT":
            return _sent(f"the cell in the {w['top']} row {w['above']} {bar(ev.agents[0])} went away")
        if p == "TIME":
            return _sent(f"the {w['top']} row turned yellow" if nxt.time == "day" else f"the {w['top']} row turned black")
        return _sent("something changed")

    def describe_scene(self, scene: Scene, sym=None) -> str:
        """The setup, in the physical stance: what the first grid shows (scene already recoloured)."""
        w = words(sym)
        parts = []
        if scene.location != "none":
            parts.append(f"{self._row_phrase(w)} is {cname(GROUND_COLOR[scene.location])}")
        for c in scene.present_chars():
            if self.mood == "height":
                h = MOOD_HEIGHT[c.mood]
                m = f"{h} cell{'s' if h > 1 else ''} tall"
            elif self.mood == "width":
                wd = MOOD_WIDTH[c.mood]
                m = f"{wd} cell{'s' if wd > 1 else ''} wide"
            elif self.mood == "marker":
                m = f"with {_a(cname(MARKER_COLOR[c.mood]))} cell {w['above']} it" if c.mood in MARKER_COLOR else f"with nothing {w['above']} it"
            else:
                n = MOOD_BASE["sad"] - MOOD_BASE[c.mood]
                m = f"standing {n} row{'s' if n != 1 else ''} {w['up']}"
            parts.append(f"{_a(cname(c.color))} bar {m}" + (f" near the {w['top']}" if c.elevated else ""))
        for o in scene.present_objs():
            col = "grey" if o.state == "broken" else cname(o.color)
            size = "two cells tall" if o.state == "big" else "one cell"
            if o.holder and o.holder in scene.chars and scene.chars[o.holder].present:
                where = f"{'just ' + w['right'] + ' of' if self.held == 'right' else w['on top of']} the {cname(scene.chars[o.holder].color)} bar"
            else:
                where = "on the ground"
            parts.append(f"{_a(col)} cell ({size}) {where}")
        if self.want == "bubble":
            for c in scene.present_chars():
                if c.wants and c.wants in scene.objs:
                    parts.append(f"{_a(cname(scene.objs[c.wants].color))} cell in the {w['top']} row {w['above']} the {cname(c.color)} bar")
        if self.time == "skyrow":
            parts.append(f"the {w['top']} row is yellow" if scene.time == "day" else f"the {w['top']} row is black")
        return _sent("at first " + ", ".join(parts)) if parts else "At first the grid is empty."


def _sent(s: str) -> str:
    s = s.strip()
    return (s[0].upper() + s[1:] + ".") if s else ""


def _a(word: str) -> str:
    """Indefinite article: an orange bar, a blue bar."""
    return ("an " if word[:1] in "aeiou" else "a ") + word


# ------------------------------------------------------------------------------------------
class TableConvention(BaseConvention):
    """The ledger drawn as a grid: one row per entity, one column per attribute. Columns:
    0 identity colour, 1 mood (grey sad / yellow happy / black neutral) or object state
    (grey broken / identity colour big), 2 place colour, 3 holder's colour (objects) or
    yellow if elevated (characters), 4 wanted object's colour. Adjacency is not drawn."""
    name = "table"
    mood = location = held = "table"
    want = "bubble"
    time = "skyrow"
    slot_w = 1
    max_width = 5
    description = "one row per entity, one column per attribute; the non-iconic pole"
    WIDTH = 5

    def width_for(self, n_slots: int) -> int:
        return self.WIDTH

    def renders_adjacency(self) -> bool:
        return False

    def _rows(self, scene: Scene):
        chars = sorted(scene.chars.values(), key=lambda c: c.slot)
        objs = sorted(scene.objs.values(), key=lambda o: o.name)
        return (chars + objs)[:H]

    def render(self, scene: Scene, width: int) -> Grid:
        g = [[0] * self.WIDTH for _ in range(H)]
        gc = GROUND_COLOR.get(scene.location, 0)
        for r, e in enumerate(self._rows(scene)):
            if not e.present:
                continue
            g[r][0] = e.color
            if isinstance(e, Char):
                if e.mood in MARKER_COLOR:
                    g[r][1] = MARKER_COLOR[e.mood]
                g[r][2] = gc
                if e.elevated:
                    g[r][3] = DAY_COLOR
                if e.wants and e.wants in scene.objs:
                    g[r][4] = scene.objs[e.wants].color
            else:
                if e.state == "broken":
                    g[r][1] = BROKEN_COLOR
                elif e.state == "big":
                    g[r][1] = e.color
                if e.holder and e.holder in scene.chars and scene.chars[e.holder].present:
                    g[r][3] = scene.chars[e.holder].color
        return g

    def facts(self, scene: Scene) -> Set[Fact]:
        f: Set[Fact] = {("ground", scene.location)}
        for c in scene.present_chars():
            f.add(("char", c.name))
            f.add(("mood", c.mood))
            if c.elevated:
                f.add(("elevated", True))
            if c.wants:
                f.add(("want",))
                if c.wants in scene.objs:
                    f.add(("obj", scene.objs[c.wants].color))
        for o in scene.present_objs():
            f.add(("obj", o.color))
            f.add(("state", o.state))
            f.add(("held",) if o.holder else ("ground_obj",))
        return f

    def needs(self, prev, nxt, ev):
        n = Convention.needs(_PROXY, prev, nxt, ev)
        n.discard(("reslot",))
        n.discard(("time", "day"))
        n.discard(("time", "night"))
        return n

    def legend(self, known_locs, sym=None):
        w = words(sym)
        loc_words = ", ".join(f"{cname(GROUND_COLOR[l])} = {LOC_WORD.get(l, l)}" for l in known_locs if l in GROUND_COLOR and l != "none")
        return (f"Each grid is a table with one row per character or object and five columns, counting columns from the {w['left']} "
                f"and rows from the {w['top']}. Column 1 is the entity's colour, "
                "and stays the same in every grid; a black row means the entity is not there. For a character, column 2 is the mood "
                "(grey sad, yellow happy, black neutral), column 3 is the place (" + loc_words + "), column 4 is yellow if the character "
                "has climbed up, and column 5 shows the colour of an object the character wants. For an object, column 2 is grey if it "
                "is broken and repeats its colour if it is big, and column 4 shows the colour of the character holding it (black if it is on the ground).")

    def describe(self, prev, nxt, ev, sym=None):
        return Convention.describe(_PROXY_TABLE, prev, nxt, ev, sym=sym)

    def describe_scene(self, scene, sym=None):
        return Convention.describe_scene(_PROXY_TABLE, scene, sym=sym)


_PROXY = Convention(name="_proxy", want="bubble", time="skyrow")
_PROXY_TABLE = Convention(name="_proxy_table", mood="marker", want="bubble")


# ------------------------------------------------------------------------------------------
PRESETS: Dict[str, BaseConvention] = {
    "baseline": Convention("baseline", description="ICLR 2027 code: mood = height, ground row = place, held = right"),
    "marker": Convention("marker", mood="marker", description="mood = marker above the head (arbitrary code; metaphor brief phi_2)"),
    "width": Convention("width", mood="width", slot_w=5, max_width=16, description="mood = bar width (metaphor brief phi_3)"),
    "altitude": Convention("altitude", mood="altitude", description="mood = how high the bar stands (iconic: high spirits)"),
    "sky": Convention("sky", location="sky", description="place = top row instead of ground row"),
    "band": Convention("band", location="band", description="place = top and bottom rows"),
    "above": Convention("above", held="above", description="held object on the head instead of at the hand"),
    "bubble": Convention("bubble", want="bubble", description="baseline + thought bubble for WANT (ICML 4c)"),
    "bubble_marker": Convention("bubble_marker", mood="marker", want="bubble", description="marker mood + thought bubble"),
    "daylight": Convention("daylight", time="skyrow", description="baseline + sky row for day/night (ICML 4c): top row yellow by day"),
    "enriched": Convention("enriched", want="bubble", time="skyrow", description="baseline + bubble + sky row (yellow by day): the full enriched tree"),
    "table": TableConvention(),
}


def get(spec: str) -> BaseConvention:
    """A preset name, or a comma list of channel=value, e.g. 'mood=marker,location=sky,want=bubble'."""
    if spec in PRESETS:
        conv = PRESETS[spec]
        conv.check()
        return conv
    if "=" not in spec:
        raise KeyError(f"unknown convention {spec!r}; presets: {', '.join(PRESETS)}")
    kw = {}
    for part in spec.split(","):
        k, v = part.split("=", 1)
        kw[k.strip()] = v.strip()
    if kw.get("mood") == "width":
        kw.setdefault("slot_w", 5)
        kw.setdefault("max_width", 16)
    for k in ("slot_w", "max_width"):
        if k in kw:
            kw[k] = int(kw[k])
    conv = replace(Convention(name=spec), **kw)
    conv.check()
    return conv


def all_channel_combinations() -> List[Convention]:
    """Every valid product of channel choices (for the catalogue and for yield tables)."""
    out = []
    for mood in ("height", "marker", "width", "altitude"):
        for location in ("ground", "sky", "band"):
            for held in ("right", "above"):
                for want in ("none", "bubble"):
                    for time in ("none", "skyrow"):
                        c = Convention(name=f"mood={mood},location={location},held={held},want={want},time={time}",
                                       mood=mood, location=location, held=held, want=want, time=time,
                                       slot_w=5 if mood == "width" else 3, max_width=16 if mood == "width" else 13)
                        try:
                            c.check()
                        except ValueError:
                            continue
                        out.append(c)
    return out
