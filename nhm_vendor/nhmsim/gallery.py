"""Static HTML for eyeballing: a gallery per run, a comparison page across conventions, and a
review form (verdict + edited narrative + notes per puzzle) that downloads review.json for
``nhmsim review --apply``. Grids are inline SVG; no images, no server."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Dict, List, Optional

from .state import COLOR_RGB

CELL = 13
GAP = 1

CSS = """
body{font-family:system-ui,-apple-system,sans-serif;max-width:1180px;margin:auto;padding:1em;color:#222;background:#fafafa}
h1{font-size:1.4em}h2{font-size:1.1em;margin:.2em 0}
.pz{border:1px solid #ccc;border-radius:6px;padding:.8em 1em;margin:1em 0;background:#fff}
.meta{font-size:12px;color:#555;margin:.3em 0}.tag{display:inline-block;background:#eef;border-radius:3px;padding:0 .4em;margin-right:.3em;font-size:11px}
.strip{display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap;margin:.4em 0}.frame{text-align:center;font-size:11px;color:#666}
.nar{white-space:pre-wrap;font-size:14px;background:#f5f5f5;padding:.5em;border-radius:4px;margin:.4em 0}
details{margin:.3em 0}summary{cursor:pointer;font-size:13px;color:#336}
.chain{font-family:ui-monospace,monospace;font-size:12px}.chain li{margin:.1em 0}
.review{border-top:1px dashed #bbb;margin-top:.6em;padding-top:.5em;font-size:13px}
.review textarea{width:100%;min-height:3em;font-size:13px}.review select,.review input{font-size:13px}
.bar{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:.4em 0;z-index:2;font-size:13px}
table.cmp{border-collapse:collapse}table.cmp td,table.cmp th{border:1px solid #ddd;padding:.3em .5em;vertical-align:top;font-size:12px}
.ok{color:#171}.bad{color:#a11}
"""

JS = """
function collect(){const out={};document.querySelectorAll('.pz').forEach(p=>{const id=p.dataset.id;
 const v=p.querySelector('.verdict').value;const n=p.querySelector('.narr').value;const notes=p.querySelector('.notes').value;
 const orig=p.querySelector('.narr').defaultValue;if(v||notes||n!==orig){out[id]={verdict:v,narrative:n,notes:notes};}});return out;}
function download(){const data=JSON.stringify(collect(),null,1);const a=document.createElement('a');
 a.href='data:application/json;charset=utf-8,'+encodeURIComponent(data);a.download='review.json';a.click();}
function count(){document.getElementById('cnt').textContent=Object.keys(collect()).length+' reviewed';}
document.addEventListener('input',count);
"""


def svg_grid(grid: List[List[int]], masked: bool = False, cell: int = CELL) -> str:
    rows, cols = len(grid), len(grid[0])
    w = cols * cell + (cols + 1) * GAP
    h = rows * cell + (rows + 1) * GAP
    out = [f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' style='background:#888'>"]
    for r in range(rows):
        for c in range(cols):
            if masked:
                rgb = (150, 150, 150) if (r + c) % 2 == 0 else (30, 30, 30)
            else:
                rgb = COLOR_RGB[grid[r][c]]
            x = GAP + c * (cell + GAP)
            y = GAP + r * (cell + GAP)
            out.append(f"<rect x='{x}' y='{y}' width='{cell}' height='{cell}' fill='rgb({rgb[0]},{rgb[1]},{rgb[2]})'/>")
    out.append("</svg>")
    return "".join(out)


def strip(grids: List[Optional[List[List[int]]]], masked: set = frozenset(), labels: Optional[List[str]] = None,
          answer: Optional[Dict[int, List[List[int]]]] = None) -> str:
    parts = ["<div class='strip'>"]
    for i, g in enumerate(grids):
        lab = labels[i] if labels and i < len(labels) else str(i)
        if i in masked:
            g = g or (answer or {}).get(i)
            parts.append(f"<div class='frame'>{svg_grid(g, masked=True)}<br>{html.escape(lab)} (masked)</div>")
        else:
            parts.append(f"<div class='frame'>{svg_grid(g)}<br>{html.escape(lab)}</div>")
    parts.append("</div>")
    return "".join(parts)


def _grids_of(pz: dict):
    k = pz["masked_positions"][0]
    return [it["grid"] if it["grid"] is not None else pz["answer_grids"][str(k)] for it in pz["sequence"]], k


def card(pz: dict, with_review: bool = True) -> str:
    m = pz["metadata"]
    grids, k = _grids_of(pz)
    ma = m["mask_analysis"]
    labels = ["setup"] + [c["label"] if c["atomic"] else "…" + c["label"] for c in m["event_chain"]]
    tags = "".join(f"<span class='tag'>{html.escape(t)}</span>" for t in m.get("tags", []))
    chain = "".join(
        f"<li>{c['position']}: <b>{html.escape(c['label'])}</b>" + ("" if c["atomic"] else " <i>(spans: " + ", ".join(html.escape(e['label']) for e in c['spanned_events']) + ")</i>")
        + (f" &lt;{html.escape(c.get('text') or '')}&gt;" if c.get("text") else "") + "</li>" for c in m["event_chain"])
    tree = " → ".join(f"{n['episode']}[{n['choice'] + 1}/{n['of']}]" for n in m.get("tree", []))
    alts = m.get("alternatives", {}).get(str(k), [])
    alt_html = "".join(f"<div class='frame'>{svg_grid(a['grid'])}<br>{html.escape(a['event'])}</div>" for a in alts)
    variants = "".join(
        f"<details><summary>{html.escape(v['variant'])} text</summary><div class='nar'>{html.escape(v['narrative'])}</div></details>"
        for v in pz.get("narrative_variants", []))
    taught = "<span class='ok'>self-taught</span>" if ma["self_taught"] else f"<span class='bad'>untaught: {html.escape(', '.join(ma['missing']))}</span>"
    other = ", ".join(f"k={a['position']} ({a['depth']}, {a['alternatives']} alts{'' if a['self_taught'] else ', untaught'})" for a in m.get("all_mask_analyses", []))
    rv = m.get("review", {})
    review = ""
    if with_review:
        review = (f"<div class='review'>verdict <select class='verdict'><option value=''></option>"
                  + "".join(f"<option value='{v}'{' selected' if rv.get('verdict') == v else ''}>{v}</option>" for v in ("keep", "fix", "drop"))
                  + "</select> &nbsp; notes <input class='notes' size='60' value='" + html.escape(rv.get("notes", ""), quote=True) + "'>"
                  + "<div>edit the story (saved to review.json; the grids come from the event chain, edit that in the JSON):</div>"
                  + f"<textarea class='narr'>{html.escape(pz['narrative'])}</textarea></div>")
    return (f"<div class='pz' data-id='{html.escape(pz['puzzle_id'])}'>"
            f"<h2>{html.escape(pz['puzzle_id'])} — {html.escape(pz['title'])}</h2>"
            f"<div class='meta'>{tags}</div>"
            f"<div class='meta'>story type <b>{html.escape(str(m.get('story_type')))}</b> · features {html.escape(str(m.get('story_features')))} · "
            f"tree {html.escape(tree)} · m={m.get('m')}</div>"
            f"<div class='meta'>mask k={k}/{len(grids) - 1} ({ma['depth']}) · masked event <b>{html.escape(ma['event'])}</b> · "
            f"<b>{ma['alternatives']}</b> alternatives · {taught} · other valid masks: {html.escape(other)}</div>"
            f"<div class='meta'>convention <b>{html.escape(m['convention']['name'])}</b> ({html.escape(', '.join(f'{c}={m['convention'][c]}' for c in ('mood', 'location', 'held', 'want', 'time')))}) · "
            f"symmetry <b>{html.escape(m['symmetry']['name'])}</b> ({html.escape(m.get('symmetry_text', ''))})</div>"
            f"{strip(grids, masked={k}, labels=labels)}"
            f"<div class='nar'>{html.escape(pz['narrative'])}</div>"
            f"<details><summary>answer</summary>{strip(grids, labels=labels)}</details>"
            f"<details><summary>{len(alts)} of {ma['alternatives']} alternatives from the grids alone (one-step completions)</summary><div class='strip'>{alt_html}</div></details>"
            f"<details><summary>event chain</summary><ol class='chain' start='1'>{chain}</ol></details>"
            f"{variants}"
            f"<details><summary>explicit conventions (the code, stated)</summary><div class='nar'>{html.escape(m.get('conventions_text', ''))}</div></details>"
            f"{review}</div>")


def write_gallery(puzzles: List[dict], out: Path, title: str, config: Optional[dict] = None, summary: Optional[dict] = None) -> None:
    head = f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
    bar = ("<div class='bar'><b>review:</b> set a verdict / edit a story / add notes, then "
           "<button onclick='download()'>download review.json</button> <span id='cnt'>0 reviewed</span> · apply with "
           "<code>python -m nhmsim review RUN --apply review.json</code></div>")
    parts = [head, f"<h1>{html.escape(title)}</h1>", bar]
    if config:
        parts.append(f"<div class='meta'>config: {html.escape(json.dumps(config))}</div>")
    if summary:
        parts.append(f"<div class='meta'>summary: {html.escape(json.dumps(summary))}</div>")
    parts.extend(card(p) for p in puzzles)
    parts.append(f"<script>{JS}</script></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts))


def write_compare(groups: List[dict], out: Path, title: str) -> None:
    """groups: [{"key": sample id, "story": text, "rows": [{"conv": name, "puzzle": dict|None, "reason": str}]}]"""
    head = f"<html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
    parts = [head, f"<h1>{html.escape(title)}</h1>",
             "<div class='meta'>Each block is one sampled story rendered under several conventions (same symmetry element). "
             "A row without grids failed the certificate under that convention; the reason says why.</div>"]
    for g in groups:
        parts.append(f"<div class='pz'><h2>{html.escape(g['key'])}</h2><div class='nar'>{html.escape(g['story'])}</div><table class='cmp'>")
        parts.append("<tr><th>convention</th><th>frames (masked hatched)</th><th>mask</th><th>alts</th><th>taught</th><th>reason</th></tr>")
        for r in g["rows"]:
            pz = r.get("puzzle")
            if pz:
                grids, k = _grids_of(pz)
                ma = pz["metadata"]["mask_analysis"]
                parts.append(f"<tr><td><b>{html.escape(r['conv'])}</b></td><td>{strip(grids, masked={k})}</td>"
                             f"<td>k={k} ({ma['depth']})<br>{html.escape(ma['event'])}</td><td>{ma['alternatives']}</td>"
                             f"<td>{'yes' if ma['self_taught'] else 'no: ' + html.escape(', '.join(ma['missing']))}</td><td>ok</td></tr>")
            else:
                parts.append(f"<tr><td><b>{html.escape(r['conv'])}</b></td><td></td><td></td><td></td><td></td><td class='bad'>{html.escape(r.get('reason', ''))}</td></tr>")
        parts.append("</table></div>")
    parts.append("</body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts))
