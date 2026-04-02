# Four Narrative Strategies for Puzzle Design: Preliminary Report

**Date:** April 2, 2026
**Prepared by:** Bert Baumgaertner, University of Idaho
**Frameworks:** ABT (Randy Olson), Story Prism (Erin James, University of Idaho)

---

## Summary

We tested whether narrative *structure* (ABT vs. AAA) or narrative *facet choice* (Story Prism) matters more for producing strong NARC puzzles. Using 20 controlled paired variants — same grids, same answer, only the narrative changed — we found:

1. **ABT structure has a small positive effect.** Adding an explicit "But" turned 1 additional puzzle from fail to NARC; removing it broke 1. The effect is real but modest.
2. **Rich literary expression does not help AI solvers — and may hurt.** Stripping characters, sensory detail, and narrative voice from rich narratives *improved* performance on 1 puzzle and maintained it on the rest (4/5 → 5/5).
3. **What matters is what information is foregrounded, not how literarily it is expressed.** The clinical version "water rises one level per day; levee resets to day-2 level" works as well or better for AI than "The Patel family watched from their porch as the river swallowed the fields."

These preliminary results suggest that the Story Prism's value for AI puzzle-solving lies in its *selection* function (which aspects of the story to foreground) rather than its *expression* function (how richly to render those aspects).

---

## The Four Strategies

| Strategy | What it controls | Source |
|---|---|---|
| **ABT** (And, But, Therefore) | Narrative structure: setup → contradiction → consequence | Randy Olson (2012) |
| **AAA** (And, And, And) | Linear accumulation of facts, no contradiction | Olson's "non-narrative" baseline |
| **DHY** (Despite, However, Yet) | Multiple contradictions, overly complex | Olson's "over-narrative" category |
| **Story Prism** | Which narrative facet is foregrounded: Teller, World, Events, Actors, Feeling | Erin James |

ABT and Story Prism are orthogonal — a narrative can be ABT-structured *and* Teller-forward, or AAA-structured *and* Feeling-forward. This enables a 2×2 experimental design.

---

## Corpus Classification

All 252 NARC narratives were classified on the ABT spectrum using keyword heuristics with LLM-assisted borderline resolution:

| Classification | Count | % |
|---|---|---|
| AAA (linear) | 171 | 68% |
| ABT-explicit | 56 | 22% |
| ABT-implicit | 20 | 8% |
| DHY (over-narrative) | 5 | 2% |

The high AAA rate (68%) reflects the spectrum series (100 algorithmic puzzles with linear descriptions) and the AI-native series (procedural technical narratives).

---

## The Central Experiment

**Design:** 20 paired variants across 4 conditions, tested on gpt-oss-120b. Each variant shares the same grids and answer as its original — only the narrative changes.

### Group A: Hold Facets Constant, Vary Structure

**A1: AAA → ABT (added "But" to 5 puzzles)**

| Puzzle | Original (AAA) `both` | + ABT "But" `both` | Change |
|---|---|---|---|
| [The Mirror](https://bertybaums.github.io/narc/#narc_004) | OK | OK | = |
| [The Dial](https://bertybaums.github.io/narc/#narc_ai_003) | 50% | 0% | worse |
| [The Last Piece](https://bertybaums.github.io/narc/#narc_005) | OK | OK | = |
| [The Tide](https://bertybaums.github.io/narc/#narc_010) | 0% | **OK** | **+** |
| [The Merge](https://bertybaums.github.io/narc/#narc_ai_008) | OK | OK | = |

Adding ABT structure: **3/5 → 4/5 correct.** One puzzle gained (The Tide — the explicit "But" at the seawall moment helped), one lost (The Dial — the added contradiction may have confused).

Notable: The Tide went from unsolvable (0%) to correct with just a structural rewrite. The "But" made the seawall intervention salient. The Merge's narrative-only also improved (0% → OK), showing ABT can help the model extract clues from narrative alone.

**A2: ABT → AAA (removed "But" from 5 puzzles)**

| Puzzle | Original (ABT) `both` | Flattened (AAA) `both` | Change |
|---|---|---|---|
| [The Lighthouse](https://bertybaums.github.io/narc/#narc_001) | OK | OK | = |
| [The Flood](https://bertybaums.github.io/narc/#narc_003) | 83% | 83% | = |
| [The Confession](https://bertybaums.github.io/narc/#narc_prism_003) | OK | 0% | **worse** |
| [The Standing Ovation](https://bertybaums.github.io/narc/#narc_prism_009) | OK | OK | = |
| [The Lullaby](https://bertybaums.github.io/narc/#narc_prism_011) | OK | OK | = |

Removing ABT structure: **4/5 → 3/5 correct.** One puzzle broke (The Confession — the linear version lost the critical garden-panic pivot), three survived, one stayed broken.

**Group A verdict:** ABT structure has a small, puzzle-dependent effect. It helps when the contradiction *is* the disambiguation clue (The Tide, The Confession). It's irrelevant when the clue is encoded elsewhere.

### Group B: Hold Structure Constant, Vary Facets

**B1: Thin → Rich (added characters, sensory detail, voice to 5 puzzles)**

| Puzzle | Original (thin) `both` | + Rich facets `both` | Change |
|---|---|---|---|
| [The Quadrants](https://bertybaums.github.io/narc/#narc_002) | OK | OK | = |
| Baby Shoes (narc_013) | 0% | 78% | better but not OK |
| [The Journey](https://bertybaums.github.io/narc/#narc_009) | 0% | 80% | better but not OK |
| [The Heist](https://bertybaums.github.io/narc/#narc_011) | OK | OK | = |
| [The Lid](https://bertybaums.github.io/narc/#narc_020) | ERR | 60% | better but not OK |

Adding rich facets: **2/5 → 2/5 correct.** No new solves. Three puzzles improved in cell accuracy (0% → 60-80%) but didn't cross the threshold. Rich expression lifts partial accuracy without achieving correctness.

**B2: Rich → Thin (stripped to impersonal clinical description for 5 puzzles)**

| Puzzle | Original (rich) `both` | Stripped (thin) `both` | Change |
|---|---|---|---|
| [The Lighthouse](https://bertybaums.github.io/narc/#narc_001) | OK | OK | = |
| [The Flood](https://bertybaums.github.io/narc/#narc_003) | 83% | **OK** | **+** |
| [The Captain's Log](https://bertybaums.github.io/narc/#narc_prism_001) | OK | OK | = |
| [The Confession](https://bertybaums.github.io/narc/#narc_prism_003) | OK | OK | = |
| [The Lullaby](https://bertybaums.github.io/narc/#narc_prism_011) | OK | OK | = |

Stripping to clinical: **4/5 → 5/5 correct.** The Flood, which was stuck at 83% with the rich Patel-family narrative, reached 100% with the clinical "water rises one level; levee resets to day-2 level." No puzzle got worse.

**Group B verdict:** Rich literary expression does not help AI solvers. Clinical precision may actually be superior — the model doesn't benefit from characters, sensory language, or narrative voice. It benefits from *clear, unambiguous specification of the transformation rule.*

---

## Interpretation

### What the Story Prism results actually showed

In the [earlier Story Prism report](story_prism_results.md), we found that puzzles with Teller, Feeling, or Actors tags showed ~80% NARC rates vs. ~30% for untagged puzzles. We interpreted this as "facet richness produces better puzzles."

The ABT experiment complicates this. Stripping rich facets to clinical descriptions doesn't hurt — and sometimes helps. This suggests the Story Prism advantage was not about *literary richness* but about **information selection**:

- A Teller-forward narrative (first person) forces the author to commit to a specific perspective, which naturally foregrounds certain grid elements
- A Feeling-forward narrative forces attention to *change* (emotional arcs require before/after states), which encodes transformation rules
- An Actors-forward narrative forces identification of which elements are agents, which clarifies which cells to track

The facet acts as a **design constraint** that leads the puzzle author to include the right disambiguation information. The literary expression of that information (rich vs. clinical) is secondary.

### When ABT matters

ABT structure helps specifically when **the contradiction IS the clue.** Examples:
- The Tide: the seawall intervention (the "But") is literally the disambiguation point
- The Confession: the garden-panic reversal (the "But") is what distinguishes the masked grid from a simple continuation

When the clue is a *rule* (e.g., "clockwise, one position per hour") rather than a *twist*, ABT structure is irrelevant.

### The compression hypothesis

The B2 result (clinical descriptions outperforming rich ones) hints at something deeper: **narrative richness may be compression that AI doesn't know how to decompress.** When we write "The Patel family watched from their porch as the river swallowed the fields," we've compressed "water level rises one row per day" into a felt experience that a human unpacks intuitively. An LLM may struggle with the decompression step — it processes the characters, the porch, the emotion as additional tokens that dilute rather than clarify the grid-transformation signal.

This deserves further investigation. If true, it would mean:
- For **human solvers**, rich narrative is helpful (compression aids intuition)
- For **AI solvers**, clinical specification is sufficient (no decompression needed)
- The **NARC property** might differ between humans and AI not because of what they know, but because of how they process narrative compression

---

## Limitations

1. **Small sample sizes.** 5 puzzles per condition on 1 model. Results are suggestive, not statistically definitive.
2. **Single model.** All results are gpt-oss-120b. Other models may respond differently to structure and facets.
3. **Variant quality.** Generated by Claude, not human authors. The AAA versions may inadvertently encode more or less information than intended.
4. **Confounds.** The puzzles used across groups are not identical, so cross-group comparisons are weaker than within-group paired comparisons.

---

## Next Steps

1. **Cross-model testing** of all 20 variants on gpt-oss-20b, qwen3.5-122b, and nemotron-3-super
2. **Human solver comparison** — do humans show the opposite pattern (rich > clinical)?
3. **Deeper investigation of the compression hypothesis** — systematically vary explicitness while holding information constant
4. **Larger sample** — extend to 10+ puzzles per condition for statistical power

---

## Attribution

- **ABT Framework:** Randy Olson (2012). *Connection: Hollywood Storytelling Meets Critical Thinking.*
- **Story Prism:** Erin James, University of Idaho. Original narrative decomposition framework.
- **NARC Benchmark:** Bert Baumgaertner, University of Idaho.

---

*NARC is open source: [github.com/bertybaums/narc](https://github.com/bertybaums/narc)*
*Live demo: [bertybaums.github.io/narc](https://bertybaums.github.io/narc)*
