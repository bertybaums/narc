"""Command line for the simulator.

    python -m nhmsim list
    python -m nhmsim gen --grammar tinystories_base --phi baseline --sym identity --mask last --n 30 --seed 1 --out runs/demo
    python -m nhmsim gen --grammar restraint --phi bubble,bubble_marker,table --mask all --n 20 --out runs/restraint  (+ compare.html)
    python -m nhmsim render runs/demo/puzzles/X.json --phi baseline,marker,width,altitude,sky,table --out runs/demo/x_compare.html
    python -m nhmsim recertify runs/demo/puzzles/X.json [--in-place] [--phi marker] [--mask middle]
    python -m nhmsim review runs/demo --apply runs/demo/review.json
    python -m nhmsim yields --grammar tinystories_base --n 200 --seed 1        (every channel combination; a table)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

from . import certify, gallery, grammar
from . import symmetries as S
from .conventions import PRESETS, all_channel_combinations
from .conventions import get as get_convention


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def cmd_list(a):
    print("grammars:")
    for name in grammar.available():
        g = grammar.load(name)
        print(f"  {name:22s} {len(g.story_types)} story types, {len(g.episodes)} episodes, primitives {', '.join(g.primitives())}"
              + (f"  requires {g.requires}" if g.requires else ""))
        print(f"  {'':22s} {g.description}")
    print("conventions (presets; or channel=value lists, see CONVENTIONS.md):")
    for name, c in PRESETS.items():
        print(f"  {name:14s} mood={c.mood:8s} location={c.location:6s} held={c.held:5s} want={c.want:6s} time={c.time:6s}  {c.description}")
    print(f"  ({len(all_channel_combinations())} valid channel combinations in all)")
    print("symmetries: identity, pal, mirror, vflip, shift; compose with '+'; 'full' = pal+mirror")
    print("mask policies: last, middle, first, any, all")


def _gen_one(g, conv, sym_spec, rng, a, i, sample=None, sym=None):
    sample = sample or grammar.sample(g, rng, m=a.m, story_type=a.story_type)
    pid = f"nhm_{g.name}_{a.seed}-{i:05d}" + (f"_{conv.name}" if a.phi and "," in a.phi else "")
    pzs, why = certify.build_puzzles(sample, conv, sym_spec, rng, mask=a.mask, min_alternatives=a.min_alternatives,
                                     require_self_taught=not a.no_self_teaching, store_alts=a.store_alts, puzzle_id=pid, sym=sym)
    return sample, pzs, why


def cmd_gen(a):
    g = grammar.load(a.grammar)
    convs = [get_convention(p) for p in a.phi.split(",")]
    for c in convs:
        for k, v in g.requires.items():
            if getattr(c, k, None) != v and c.name != "table":
                print(f"warning: grammar {g.name} requires {k}={v}; convention {c.name} has {getattr(c, k, None)} "
                      f"(events with no denotation are folded into the next visible transition)", file=sys.stderr)
    out = Path(a.out)
    rng = random.Random(a.seed)
    puzzles: Dict[str, List[dict]] = {c.name: [] for c in convs}
    reasons: Dict[str, Counter] = {c.name: Counter() for c in convs}
    groups = []
    produced = 0
    i = 0
    families = list(g.families.items()) if a.pairs and g.families else []
    if a.pairs and not families:
        print(f"warning: grammar {g.name} defines no families; --pairs ignored", file=sys.stderr)
    while produced < a.n and i < a.n * a.max_tries:
        i += 1
        seed_i = rng.randrange(1 << 30)
        if families:
            fam, types = families[rng.randrange(len(families))]
            samples = [grammar.sample(g, random.Random(seed_i), m=a.m, story_type=t) for t in types]   # same roles per seed
        else:
            samples = [grammar.sample(g, random.Random(seed_i), m=a.m, story_type=a.story_type)]
        sym = S.sample(a.sym, rng)
        rows = []
        any_ok = False
        for sample in samples:
            for conv in convs:
                pid_suffix = f"_{sample.story_type}" if len(samples) > 1 else ""
                pid = f"nhm_{g.name}_{a.seed}-{i:05d}{pid_suffix}" + (f"_{conv.name}" if "," in a.phi else "")
                pzs, why = certify.build_puzzles(sample, conv, a.sym, rng, mask=a.mask, min_alternatives=a.min_alternatives,
                                                 require_self_taught=not a.no_self_teaching, store_alts=a.store_alts, puzzle_id=pid, sym=sym)
                reasons[conv.name][why] += 1
                if pzs:
                    any_ok = True
                    puzzles[conv.name].extend(pzs)
                rows.append({"conv": conv.name + (f" · {sample.story_type}" if len(samples) > 1 else ""), "puzzle": pzs[0] if pzs else None, "reason": why})
        if any_ok:
            produced += 1
            sample = samples[0]
            groups.append({"key": f"sample {i}: {sample.story_type if len(samples) == 1 else fam + ' family'} ({', '.join(f'{k}={v}' for k, v in sample.roles.items())})",
                           "story": sample.texts["story"], "rows": rows})
    config = vars(a).copy()
    config.pop("func", None)
    summary = {}
    for conv in convs:
        pz = puzzles[conv.name]
        sub = out / "puzzles" if len(convs) == 1 else out / "puzzles" / conv.name
        for p in pz:
            _write_json(sub / f"{p['puzzle_id']}.json", p)
        depth = Counter(p["metadata"]["mask_analysis"]["depth"] for p in pz)
        alts = Counter(min(p["metadata"]["mask_analysis"]["alternatives"], 10) for p in pz)
        prims = Counter(p["metadata"]["mask_analysis"]["event"].split("(")[0] for p in pz)
        summary[conv.name] = {"puzzles": len(pz), "samples_tried": i, "reasons": dict(reasons[conv.name]),
                              "depth": dict(depth), "alternatives_capped10": dict(sorted(alts.items())),
                              "masked_event": dict(prims.most_common()),
                              "story_types": dict(Counter(p["metadata"]["story_type"] for p in pz))}
        gallery.write_gallery(pz, out / ("gallery.html" if len(convs) == 1 else f"gallery_{conv.name}.html"),
                              f"nhm {g.name} · {conv.name} · sym {a.sym} · mask {a.mask}", config, summary[conv.name])
    if len(convs) > 1 or a.compare or a.pairs:
        gallery.write_compare(groups, out / "compare.html", f"nhm {g.name}: {', '.join(c.name for c in convs)}")
    _write_json(out / "config.json", config)
    _write_json(out / "summary.json", summary)
    for conv in convs:
        s = summary[conv.name]
        print(f"{conv.name:14s} {s['puzzles']:4d} puzzles from {i} samples; depth {s['depth']}; reasons {s['reasons']}")
    print(f"wrote {out}/")


def cmd_render(a):
    convs = [get_convention(p) for p in a.phi.split(",")]
    groups = []
    for f in a.puzzle:
        pz = json.loads(Path(f).read_text())
        rows = []
        for conv in convs:
            pzs, why = certify.rebuild(pz, conv=conv, mask=a.mask, require_self_taught=not a.no_self_teaching)
            rows.append({"conv": conv.name, "puzzle": pzs[0] if pzs else None, "reason": why})
        groups.append({"key": pz["puzzle_id"], "story": pz["narrative"], "rows": rows})
    out = Path(a.out or (Path(a.puzzle[0]).with_suffix("") .as_posix() + "_compare.html"))
    gallery.write_compare(groups, out, f"nhm compare: {', '.join(c.name for c in convs)}")
    print("wrote", out)


def cmd_recertify(a):
    for f in a.puzzle:
        p = Path(f)
        pz = json.loads(p.read_text())
        conv = get_convention(a.phi) if a.phi else None
        pzs, why = certify.rebuild(pz, conv=conv, mask=a.mask, min_alternatives=a.min_alternatives,
                                   require_self_taught=not a.no_self_teaching)
        old = pz["metadata"]["mask_analysis"]
        if not pzs:
            print(f"{p.name}: REJECTED ({why}); was k={old['position']} alts={old['alternatives']}")
            continue
        new = pzs[0]["metadata"]["mask_analysis"]
        changed = new["position"] != old["position"] or new["alternatives"] != old["alternatives"] or new["self_taught"] != old["self_taught"]
        print(f"{p.name}: ok k={new['position']} ({new['depth']}) alts={new['alternatives']} taught={new['self_taught']}"
              + (f"  [changed from k={old['position']} alts={old['alternatives']} taught={old['self_taught']}]" if changed else ""))
        if a.in_place:
            pzs[0]["puzzle_id"] = pz["puzzle_id"]
            _write_json(p, pzs[0])
        elif a.out:
            _write_json(Path(a.out) / p.name, pzs[0])


def cmd_review(a):
    run = Path(a.run)
    review = json.loads(Path(a.apply).read_text())
    files = list(run.glob("puzzles/**/*.json"))
    by_id = {}
    for f in files:
        by_id[json.loads(f.read_text())["puzzle_id"]] = f
    n = Counter()
    for pid, r in review.items():
        f = by_id.get(pid)
        if not f:
            print("no such puzzle in run:", pid)
            continue
        pz = json.loads(f.read_text())
        pz["metadata"]["review"] = {"verdict": r.get("verdict", ""), "notes": r.get("notes", "")}
        if r.get("narrative") and r["narrative"] != pz["narrative"]:
            pz.setdefault("narrative_variants", []).append({"variant": "story_original", "narrative": pz["narrative"], "generator": "nhm-sim"})
            pz["narrative"] = r["narrative"]
            n["narrative edited"] += 1
        if r.get("verdict") == "drop":
            dst = run / "rejected" / f.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            _write_json(dst, pz)
            f.unlink()
            n["dropped"] += 1
        else:
            _write_json(f, pz)
            n[r.get("verdict") or "annotated"] += 1
    print(dict(n))
    remaining = [json.loads(f.read_text()) for f in sorted(run.glob("puzzles/**/*.json"))]
    if remaining and (run / "gallery.html").exists():
        gallery.write_gallery(remaining, run / "gallery.html", f"nhm {run.name} (reviewed)")
        print("gallery rewritten")


def cmd_yields(a):
    g = grammar.load(a.grammar)
    rng = random.Random(a.seed)
    samples = [grammar.sample(g, rng, m=a.m) for _ in range(a.n)]
    convs = list(PRESETS.values()) if a.presets else all_channel_combinations()
    print(f"{'convention':64s} {'ok':>4s} {'first':>5s} {'mid':>4s} {'final':>5s}  reasons")
    for conv in convs:
        conv.check()
        reasons = Counter()
        depth = Counter()
        for i, s in enumerate(samples):
            pzs, why = certify.build_puzzles(s, conv, "identity", random.Random(i), mask="all", store_alts=0)
            reasons[why] += 1
            for p in pzs:
                depth[p["metadata"]["mask_analysis"]["depth"]] += 1
        ok = reasons.get("ok", 0)
        bad = ", ".join(f"{k}={v}" for k, v in reasons.most_common() if k != "ok")
        print(f"{conv.name:64s} {ok:4d} {depth['first']:5d} {depth['middle']:4d} {depth['final']:5d}  {bad}")


def cmd_import_nts(a):
    from . import importer
    files = importer.load_sources(a.src, a.limit)
    convs = [get_convention(p) for p in a.phi.split(",")]
    out = Path(a.out)
    rng = random.Random(a.seed)
    stats = {c.name: Counter() for c in convs}
    fidelity = Counter()
    puzzles = {c.name: [] for c in convs}
    for f in files:
        nts = json.loads(f.read_text())
        sym = S.sample(a.sym, rng)
        for conv in convs:
            pz, why, fid = importer.import_puzzle(nts, conv=conv, sym=sym, keep_mask=not a.remask, store_alts=a.store_alts)
            stats[conv.name][why] += 1
            if conv is convs[0]:
                fidelity["exact" if fid["frames_equal"] else ("partial" if fid["n_equal"] else "none")] += 1
                fidelity["mask_kept"] += int(fid["mask_kept"])
            if pz:
                puzzles[conv.name].append(pz)
    for conv in convs:
        sub = out / "puzzles" if len(convs) == 1 else out / "puzzles" / conv.name
        for pz in puzzles[conv.name]:
            _write_json(sub / f"{pz['puzzle_id']}.json", pz)
        gallery.write_gallery(puzzles[conv.name], out / ("gallery.html" if len(convs) == 1 else f"gallery_{conv.name}.html"),
                              f"nhm import from {a.src} · {conv.name}", {"src": a.src, "phi": conv.name, "sym": a.sym},
                              {"n": len(puzzles[conv.name]), "reasons": dict(stats[conv.name]), "fidelity": dict(fidelity)})
        print(f"{conv.name:14s} {len(puzzles[conv.name]):5d} of {len(files)} imported; reasons {dict(stats[conv.name])}")
    print(f"fidelity under the baseline code (re-render vs original, all frames): {dict(fidelity)}")
    _write_json(out / "summary.json", {"src": a.src, "n_files": len(files), "phi": [c.name for c in convs], "sym": a.sym,
                                      "reasons": {k: dict(v) for k, v in stats.items()}, "fidelity": dict(fidelity)})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nhmsim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="grammars, conventions, symmetries, mask policies")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("gen", help="sample a grammar, certify under one or more conventions, write puzzles + gallery")
    p.add_argument("--grammar", default="tinystories_base")
    p.add_argument("--phi", default="baseline", help="preset or channel spec; comma list renders each sample under all")
    p.add_argument("--sym", default="identity")
    p.add_argument("--mask", default="last", choices=["last", "middle", "first", "any", "all"])
    p.add_argument("--n", type=int, default=30, help="samples that yield at least one puzzle")
    p.add_argument("--max-tries", type=int, default=20, help="give up after n*max_tries samples")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--m", type=int, default=3, help="synonymy cap at the episode and clause levels")
    p.add_argument("--story-type", default=None)
    p.add_argument("--min-alternatives", type=int, default=2)
    p.add_argument("--no-self-teaching", action="store_true")
    p.add_argument("--store-alts", type=int, default=8)
    p.add_argument("--compare", action="store_true", help="also write compare.html (automatic with several --phi)")
    p.add_argument("--pairs", action="store_true", help="per seed, generate every story type of one family (same roles): twist and twins side by side")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("render", help="re-render existing puzzles under other conventions into a comparison page")
    p.add_argument("puzzle", nargs="+")
    p.add_argument("--phi", default="baseline,marker,width,altitude,sky,band,above,table")
    p.add_argument("--mask", default=None, choices=[None, "last", "middle", "first", "any"])
    p.add_argument("--no-self-teaching", action="store_true")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("recertify", help="rebuild a puzzle from its (possibly edited) event chain and re-run the certificate")
    p.add_argument("puzzle", nargs="+")
    p.add_argument("--phi", default=None)
    p.add_argument("--mask", default=None, choices=[None, "last", "middle", "first", "any"])
    p.add_argument("--min-alternatives", type=int, default=2)
    p.add_argument("--no-self-teaching", action="store_true")
    p.add_argument("--in-place", action="store_true")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_recertify)

    p = sub.add_parser("review", help="apply a review.json downloaded from a gallery to a run")
    p.add_argument("run")
    p.add_argument("--apply", required=True)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("import-nts", help="import narc-tiny-stories puzzles (setup read from the first grid, events from the chain) and rebuild under codes")
    p.add_argument("src", help="a puzzle JSON, a directory of them, or a glob")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--phi", default="baseline")
    p.add_argument("--sym", default="identity")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--remask", action="store_true", help="choose the mask afresh instead of keeping the original position")
    p.add_argument("--store-alts", type=int, default=8)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_import_nts)

    p = sub.add_parser("yields", help="certificate yield per convention on one sample set (phase-0 gate of the metaphor brief)")
    p.add_argument("--grammar", default="tinystories_base")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--m", type=int, default=3)
    p.add_argument("--presets", action="store_true", help="only the named presets, not every channel combination")
    p.set_defaults(func=cmd_yields)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
