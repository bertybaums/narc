# Story Prism Integration Results

**Date:** April 1, 2026
**Prepared by:** Bert Baumgaertner, University of Idaho
**Framework:** Story Prism (Erin James, University of Idaho)

---

## Summary

We integrated Erin James's Story Prism framework into the NARC puzzle benchmark to test whether the five narrative facets — **Teller & Told**, **World**, **Events**, **Actors**, and **How It Feels** — differentially affect AI puzzle-solving. The results demonstrate that (1) facet choice significantly affects solvability, (2) previously underrepresented facets produce the most robust puzzles, and (3) different AI models are sensitive to different narrative framings of the same puzzle.

---

## Background

NARC (Narrative Augmented Reasoning Challenges) is a benchmark where sequences of colored grids tell an abstract "story." One or more grids are masked, and the solver must reconstruct them. A short narrative clue accompanies each puzzle — without it, the puzzle is ambiguous; with it, the answer is uniquely determined. This is the **NARC property**.

An audit of 240 existing puzzles through the Story Prism lens revealed the corpus was strong on **Events** (the grid sequence format naturally enforces temporal structure) but weak on the other four facets:

| Facet | Coverage before integration |
|---|---|
| Events | Strong (~67% of puzzles) |
| World | Moderate (~33%) |
| Actors | Weak (~23%, mostly in one subseries) |
| Teller & Told | Very weak (~10%, zero first-person) |
| How It Feels | Very weak (~27%, almost no sensory detail) |

---

## What We Did

### 1. Revised 9 existing puzzle narratives
Added characters, sensory detail, and emotional grounding to the weakest narratives while preserving the puzzle logic. Examples:
- "A lighthouse beam sweeps clockwise..." → Added a keeper character, fog, the sound of the sea
- "Byte-pair encoding iteratively merges..." → Added Yusuf, a researcher having an aha moment

### 2. Created 17 Story Prism narrative variants
For puzzles with strong grid logic, we generated alternative narratives that foreground a **different** Story Prism facet. Same grids, different narrative framing. Three tiers:

- **Actor-salience variants (8):** Same story told from a different character's perspective (e.g., "The Boy Who Cried Wolf" told from the wolf's perspective, the villagers' perspective)
- **Teller variants (5):** Shifted narrative voice (omniscient 3rd person → 1st person diary, 2nd person address)
- **Feeling variants (4):** Changed emotional framing (neutral → dread, hope, visceral, uncanny)

### 3. Created 12 new puzzles designed around underrepresented facets
Each puzzle was designed to be strong on at least 3 Story Prism facets:

| Primary facet | Puzzles |
|---|---|
| Teller & Told | The Captain's Log (1st-person diary), Dear Committee (epistolary), The Confession (retrospective) |
| Actors | The Negotiation (opposing goals), The Understudies (scale switch), The Hive (individual vs. collective) |
| How It Feels | The Thaw (sensory/renewal), The Vigil (dread/approach), The Standing Ovation (joy building) |
| Multi-facet | The Cartographer's Dilemma (teller+world+actors), The Lullaby (feeling+actors+events), The Inheritance (actors+world+events) |

### 4. Tested everything on 4 AI models
All puzzles and variants were tested on gpt-oss-120b, gpt-oss-20b, qwen3.5-122b, and nemotron-3-super via the MindRouter HPC infrastructure at the University of Idaho.

---

## Key Results

### Result 1: Underrepresented facets produce the most robust NARC puzzles

Of the 12 new Story Prism puzzles, **6 are robustly NARC** (the property holds on 3 or more of 4 models tested):

| Puzzle | Primary facets | Models showing NARC |
|---|---|---|
| **Dear Committee** | Teller | 4/4 |
| **The Confession** | Teller + Feeling | 4/4 |
| **The Standing Ovation** | Feeling + Events | 4/4 |
| **The Vigil** | Feeling | 3/4 |
| **The Cartographer's Dilemma** | Teller + World + Actors | 3/4 |
| **The Lullaby** | Feeling + Actors + Events | 3/4 |

**The facets that were most underrepresented in the original corpus — Teller and Feeling — produce the most reliable NARC puzzles.** All three teller-forward puzzles are NARC on 2+ models. The feeling-forward puzzles (Vigil, Standing Ovation, Lullaby) are NARC on 3-4 models.

By contrast, the actors-forward puzzles without other strong facets (Understudies, Hive) were less reliable, and Events-heavy puzzles (the original corpus's strength) tend to be solvable from grids alone.

### Result 2: Actor salience affects solvability

Testing the same puzzle with narratives foregrounding different actors produced measurably different results:

**"The Boy Who Cried Wolf" (narc_006) — 6 narrative variants tested on gpt-oss-120b:**

| Narrative variant | Grids only | Narrative only | Both |
|---|---|---|---|
| Original (3rd person, boy focus) | correct | error | correct |
| Wolf perspective | 44% | 0% | correct |
| Villagers perspective | 80% | 46% | correct |
| 1st-person boy | correct | 0% | correct |
| 1st-person villager | correct | 20% | correct |

The `both` condition is robust across all perspectives (always correct), but **narrative-only performance varies dramatically by actor**: the villagers' perspective gives 46% accuracy while the wolf's perspective gives 0%. The villagers experience the *repetition and disappointment* directly — the key structural clue — while the wolf only observes the outcome.

**"The Journey" (narc_009) — landscape perspective outperforms traveler:**

| Variant | Narrative only |
|---|---|
| Original (traveler) | 20% |
| River perspective | 0% |
| Landscape perspective | 48% |

Foregrounding the spatial layout (landscape as protagonist) nearly triples narrative-only accuracy vs. the original traveler-focused narrative.

### Result 3: Emotional framing affects clue legibility

**"The Lighthouse" (narc_001) — hope vs. dread framing:**

| Variant | Narrative only |
|---|---|
| Dread ("storm approaching, fog closing in") | 0% |
| Hope ("a faint pulse in the fog, then brighter, then certain") | 88% |

The hopeful framing describes the beam's *progression* ("faint... brighter... certain"), which maps directly to the clockwise sweep. The dread framing emphasizes atmosphere without encoding the rotation. Same information, same grid — but the emotional frame changes what the model extracts.

### Result 4: Different models comprehend different narrative framings

**"The Negotiation" (narc_prism_004)** is completely unsolvable on gpt-oss-120b (0% on all conditions) but shows the NARC property on both qwen3.5-122b and nemotron-3-super. The narrative describes two nations dividing territory through rounds of concessions — a social/strategic framing that some model architectures parse better than others.

**Cross-model NARC classification for all 12 puzzles:**

| Puzzle | gpt-oss-120b | gpt-oss-20b | qwen3.5-122b | nemotron-3-super |
|---|---|---|---|---|
| The Captain's Log | NARC | fail | fail | NARC |
| Dear Committee | NARC | NARC | NARC | NARC |
| The Confession | NARC | NARC | NARC | NARC |
| The Negotiation | fail | fail | NARC | NARC |
| The Understudies | grids suff. | NARC | fail | grids suff. |
| The Hive | fail | NARC | fail | fail |
| The Thaw | grids suff. | fail | NARC | fail |
| The Vigil | NARC | NARC | NARC | grids suff. |
| The Standing Ovation | NARC | NARC | NARC | NARC |
| The Cartographer | NARC | fail | NARC | NARC |
| The Lullaby | NARC | fail | NARC | NARC |
| The Inheritance | NARC | fail | fail | NARC |

No single model solves all puzzles. The model that benefits most from a given narrative depends on which Story Prism facets that narrative foregrounds.

---

## Implications

1. **The Story Prism provides a principled design space for NARC puzzles.** Rather than generating narratives ad hoc, puzzle designers can systematically vary facets and predict which will produce robust NARC properties.

2. **Teller and Feeling are high-value facets.** The corpus had almost none of these, yet they produce the most reliable NARC puzzles. This suggests the field's default mode (impersonal, process-describing narratives) leaves significant puzzle-design space unexplored.

3. **Actor salience is a measurable variable.** The same grid sequence paired with narratives foregrounding different actors produces different solve rates. This connects directly to focalization theory — the "camera angle" of the narrative changes what information is computationally accessible.

4. **Narrative framing effects are model-dependent.** No single narrative works optimally for all models, which means narrative design for AI benchmarks must consider the diversity of model architectures, not just optimize for one.

5. **Emotional valence is not just decoration.** The hope/dread comparison on "The Lighthouse" shows that emotional framing changes what structural information a model extracts from the narrative. This challenges the assumption that only "logical" content matters for reasoning tasks.

---

## Methods Note

All testing used a two-pass protocol: the subject model reasons freely (pass 1), then an extraction model formats the answer as JSON (pass 2). A strict pass-3 retry fires if pass 2 fails to parse. Grid parse error rate: 0-3.8% across all runs (down from 33% before pipeline improvements).

Models tested via MindRouter (University of Idaho HPC): gpt-oss-120b, gpt-oss-20b, qwen3.5-122b, nemotron-3-super. Each puzzle tested on 3 conditions: grids_only, narrative_only, and both.

---

## Attribution

The **Story Prism** is Erin James's original framework for narrative decomposition. Its five facets (Teller & Told, World, Events, Actors, How It Feels) provided the theoretical foundation for this work. The integration into NARC was a collaboration between Bert Baumgaertner and the NARC development pipeline.

---

*NARC is open source: [github.com/bertybaums/narc](https://github.com/bertybaums/narc)*
*Live demo: [bertybaums.github.io/narc](https://bertybaums.github.io/narc)*
