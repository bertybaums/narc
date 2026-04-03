# Can Narratives Help AI Recover the Order of Events?

## A NARC Ordering Experiment

**Date:** April 3, 2026
**Prepared by:** Bert Baumgaertner, University of Idaho
**For:** Erin James, University of Idaho

---

## The Idea in a Nutshell

Suppose you are handed five illustrated cards that together tell a story, but someone has shuffled them. Your task is to put them back in the right order. You might look for visual cues: a glass filling up, a tower growing taller. But if someone also told you the story --- "they built it one floor each day, and on the fifth day set a golden crown upon its peak" --- you would have a much easier time.

We ran exactly this experiment with AI models. Instead of illustrated cards, we used the colored grids from our NARC puzzle set. Instead of a human, we asked four large language models to recover the correct chronological sequence. We tested each puzzle twice: once with the grids alone (shuffled, unlabeled), and once with the grids plus the narrative clue. The question: does the narrative help the model figure out the order?

The answer is a clear yes, and the effect is large.

---

## How the Experiment Works

### The NARC puzzle set, briefly

A NARC puzzle is a sequence of 3--8 colored grids that tell an abstract "story." In the standard NARC task, one or more grids are hidden, and the solver must reconstruct them. A short narrative clue accompanies each puzzle. The benchmark tests whether narratives help solvers reason about abstract visual patterns.

For background on the puzzle set and the Story Prism integration, see the earlier reports ([Story Prism Results](story_prism_results.md), [Four Narrative Strategies](four_narrative_strategies_preliminary.md)).

### The ordering task

The ordering experiment flips the standard NARC task. Instead of hiding a grid and asking the model to reconstruct it, we show *all* the grids but shuffle their order. The model must recover the correct chronological sequence.

Here is an example. Consider [The Lullaby](https://bertybaums.github.io/narc/#narc_prism_011), a puzzle with five grids. The narrative reads:

> *"The child's mind is a patchwork of the day's colors. As the parent begins to sing, the red thoughts fade first --- the argument at school dissolves into darkness. 'Hush now,' the parent whispers, and next the orange flickers of the evening TV go dark. In the third verse, the parent sings about the garden, and oddly the yellow dandelions the child picked that afternoon vanish from his mind, but the blue sky and the green grass stubbornly persist --- those were the happiest memories. It takes one more verse before the blue drains away, leaving only a few green embers of the grass where he played. By the last verse, even those fade, and the child sleeps in perfect darkness."*

The grids show a pattern of colored cells gradually going dark. We present the model with all five grids, labeled A through E in a shuffled order, and ask: *what is the correct chronological sequence?*

Without the narrative, the model sees five grids of colored cells going dark but has no way to know whether colors are disappearing or appearing, whether the story runs forward or backward. In fact, all four models got the order *perfectly backwards* without the narrative. With the narrative, all four recovered the correct order perfectly. The narrative pins down the direction: red fades first, then orange, then yellow, and so on. The temporal structure of the story disambiguates the visual sequence.

### Measuring success: Kendall's tau

We need a way to measure how close a predicted ordering is to the correct one, with credit for partial success. A model that gets the first four grids right but swaps the last two should score better than one that reverses the entire sequence.

We use **Kendall's tau**, a standard statistical measure of rank agreement. It ranges from +1 (perfect agreement) through 0 (no better than chance) to -1 (perfectly reversed). The measure works by looking at every possible pair of grids and asking: did the model put these two in the right relative order? If most pairs are correct, tau is close to +1. If most are swapped, tau is close to -1.

We define the **narrative ordering lift** as the difference: tau with narrative minus tau without. A positive lift means the narrative helped the model recover the correct sequence. A negative lift means the narrative confused it.

### Which puzzles and which models

We tested the 70 active NARC puzzles that have four or more grids. Puzzles with only two or three grids were excluded because the task is too easy by chance alone (with three grids, there are only six possible orderings, so a random guess has a 17% chance of being correct).

| Grid count | Number of puzzles |
|:---:|:---:|
| 4 | 39 |
| 5 | 21 |
| 6 | 7 |
| 7 | 2 |
| 8 | 1 |

We tested four models via the MindRouter inference platform at the University of Idaho: gpt-oss-120b (the largest), gpt-oss-20b (the smallest), qwen3.5-122b, and nemotron-3-super. These are the same models used in the original NARC cross-model experiments.

### Reproducibility

Each puzzle has a fixed, deterministic shuffle: the order in which the grids are presented is derived from the puzzle's identifier using a cryptographic hash function. This means that every model sees the same shuffled arrangement for a given puzzle, and the experiment can be reproduced exactly.

---

## Results

### The narrative helps --- a lot

Across all four models and 70 puzzles, the narrative produced a substantial improvement in ordering accuracy.

| Model | Avg tau (grids only) | Avg tau (+ narrative) | Narrative lift | Exact match (grids) | Exact match (+ narr) |
|---|:---:|:---:|:---:|:---:|:---:|
| gpt-oss-120b | +0.303 | +0.674 | **+0.371** | 26/70 (37%) | 45/70 (64%) |
| qwen3.5-122b | +0.331 | +0.698 | **+0.367** | 27/70 (39%) | 46/70 (66%) |
| nemotron-3-super | +0.162 | +0.514 | **+0.352** | 18/70 (26%) | 33/70 (47%) |
| gpt-oss-20b | +0.259 | +0.512 | **+0.253** | 19/70 (27%) | 29/70 (41%) |

Several things stand out. First, the narrative lift is remarkably consistent across the three larger models (roughly +0.35--0.37), with the smaller gpt-oss-20b showing a somewhat reduced but still substantial effect (+0.25). Second, the lift is much larger than what we observed in the original NARC reconstruction task, where narrative lift for correct grid reconstruction was on the order of 7--12 percentage points. Here, exact match rates roughly double. This makes intuitive sense: narratives are fundamentally about temporal and causal sequence, so they should be especially helpful for an ordering task.

Third, models perform poorly on ordering from grids alone. Average tau without narrative ranges from +0.16 to +0.33, meaning the models can extract some sequential information from visual patterns but not much. The narrative roughly doubles their rank accuracy.

### More grids, more lift

The narrative advantage grows as the number of grids increases.

| Grids | Puzzles | Avg tau (grids only) | Avg tau (+ narrative) | Avg lift |
|:---:|:---:|:---:|:---:|:---:|
| 4 | 39 | +0.334 | +0.658 | **+0.324** |
| 5 | 21 | +0.282 | +0.628 | **+0.346** |
| 6 | 7 | -0.092 | +0.329 | **+0.421** |
| 7 | 2 | +0.149 | +0.250 | **+0.024** |
| 8 | 1 | -0.268 | +0.714 | **+0.982** |

(Values averaged across all four models.)

The 6-grid and 8-grid puzzles show the largest lifts. At 6 grids, the average grids-only tau is actually *negative* --- models are doing worse than random, likely because longer sequences produce more plausible-looking false orderings. But with the narrative, performance jumps above +0.3. The single 8-grid puzzle, [Halfway There](https://bertybaums.github.io/narc/#narc_043), shows a dramatic lift of nearly +1.0: models went from reversed or random orderings to near-perfect with the narrative.

The 7-grid bin is an anomaly, showing almost no lift, but with only two puzzles this is not reliable. One of them, [The Tower](https://bertybaums.github.io/narc/#narc_008), is a case where the narrative actually hurt performance on three of four models, pulling the average down.

### Puzzles where narrative helps most

Nine puzzles showed positive narrative lift on *all four models* --- we might call these "ordering-robust" puzzles, analogous to the NARC-robust classification in the original benchmark.

| Puzzle | Grids | Lift (120b) | Lift (20b) | Lift (nemo) | Lift (qwen) |
|---|:---:|:---:|:---:|:---:|:---:|
| [The Lullaby](https://bertybaums.github.io/narc/#narc_prism_011) | 5 | +2.00 | +2.00 | +2.00 | +2.00 |
| [The Heist](https://bertybaums.github.io/narc/#narc_011) | 6 | +1.20 | +1.47 | +1.33 | +1.73 |
| [The Dial](https://bertybaums.github.io/narc/#narc_ai_003) | 4 | +1.33 | +0.67 | +2.00 | +2.00 |
| [The Confession](https://bertybaums.github.io/narc/#narc_prism_003) | 4 | +1.00 | +1.00 | +1.00 | +0.33 |
| [The Ugly Duckling](https://bertybaums.github.io/narc/#narc_focal_008) | 5 | +0.20 | +0.80 | +0.80 | +2.00 |
| [Plate Collision](https://bertybaums.github.io/narc/#narc_focal_027) | 4 | +0.33 | +0.33 | +0.33 | +2.00 |
| [The Five Stages](https://bertybaums.github.io/narc/#narc_gap_203) | 6 | +0.93 | +0.40 | +0.27 | +0.27 |
| [Stride Counter](https://bertybaums.github.io/narc/#narc_gap_102) | 5 | +0.40 | +0.20 | +0.40 | +1.00 |
| [Spectrum 18](https://bertybaums.github.io/narc/#narc_sp_018) | 4 | +0.33 | +1.33 | +0.33 | +1.00 |

No puzzle showed negative lift on all four models. Six puzzles showed zero lift across the board --- in these cases, all models got the ordering correct (or equally wrong) regardless of the narrative.

### The Lullaby: a perfect ordering-NARC

[The Lullaby](https://bertybaums.github.io/narc/#narc_prism_011) deserves special attention. This is a Story Prism puzzle designed with the Feeling facet, in which a parent sings a child to sleep and the colored thoughts fade one by one: red, orange, yellow, blue, green. The grids show a progressively darkening pattern.

Without the narrative, all four models ordered the grids *perfectly backwards* (tau = -1.0). This makes sense: visually, a grid full of colors looks more "developed" or "complete" than a dark grid, so the models assumed the colorful grid comes last rather than first. With the narrative, all four models achieved perfect ordering (tau = +1.0). The narrative specifies the *direction* of the process (fading, not building), which resolves the visual ambiguity.

This is the clearest possible case of narrative disambiguation in the ordering task: the visual evidence actively misleads, and only the narrative corrects the interpretation.

### When narrative hurts

On a smaller number of puzzles, the narrative actually *worsened* ordering performance for some models.

| Puzzle | Grids | Avg lift | What happened |
|---|:---:|:---:|---|
| [The Mentor](https://bertybaums.github.io/narc/#narc_focal_054) | 4 | -0.58 | Two models had the order right from grids alone; narrative reversed their answers |
| [The Inheritance](https://bertybaums.github.io/narc/#narc_prism_012) | 5 | -0.65 | Narrative language caused models to reverse start and end |
| [Stage Fright](https://bertybaums.github.io/narc/#narc_focal_047) | 4 | -0.42 | All four models scored worse with narrative |
| [The Tower](https://bertybaums.github.io/narc/#narc_008) | 7 | -0.43 | Narrative describes both building and collapse; models misread the turning point |

[The Mentor](https://bertybaums.github.io/narc/#narc_focal_054) is an instructive failure. Its narrative describes a teacher passing knowledge to a student who grows over time. Two models (gpt-oss-120b and qwen3.5-122b) had the correct order from the grids alone, but when given the narrative, they reversed the sequence --- perhaps interpreting "the teacher steps back, their work done" as describing the first frame rather than the last. Interestingly, gpt-oss-20b showed the *opposite* pattern: wrong from grids, correct with narrative. The same narrative disambiguated in different directions for different models.

---

## What This Means

### Ordering as a new dimension of the NARC property

In the standard NARC task, a puzzle has the NARC property when the narrative is necessary for correct grid reconstruction: the model fails without it, succeeds with it. The ordering experiment introduces an analogous concept: a puzzle has the **ordering-NARC property** when the narrative is necessary for correct sequencing.

Because we measure ordering with Kendall's tau rather than binary correct/incorrect, this property is naturally graded. A puzzle with a lift of +2.0 (from perfectly reversed to perfectly correct) is "more ordering-NARC" than one with a lift of +0.3. This graded measure may turn out to be more informative than the binary NARC classification, which loses information about partial success.

Of the 70 puzzles tested, 9 showed positive narrative lift on all four models. We might call these "robust ordering-NARC" puzzles. A further 41 individual model-puzzle pairs met a stricter criterion: grids-only tau below zero (the model was actively confused by the visual sequence) but narrative tau above +0.8 (the narrative brought it close to correct). These are cases where the narrative doesn't just help --- it rescues an otherwise hopeless interpretation.

### Why narratives help ordering more than reconstruction

The narrative lift for ordering (+0.25 to +0.37 in tau) is much larger than the narrative lift for grid reconstruction in the original NARC experiments (+3.7 to +12.5 percentage points in solve rate). This is not surprising when we consider what narratives are fundamentally about.

A narrative, at minimum, describes a sequence of events. The temporal structure --- what happened first, what happened next, what changed --- is the backbone of any story. When we write a NARC narrative, even the simplest version encodes causal and temporal ordering: "they built it one floor each day" tells you the tower grows; "the storms began, and each day took a floor away" tells you it then shrinks. This temporal information maps directly onto the ordering task.

By contrast, the standard NARC reconstruction task asks the model to infer the specific pixel-level content of a missing grid. This requires extracting much more precise information from the narrative: not just *what happens* but *exactly how it looks*. The narrative encodes temporal sequence much more reliably than it encodes spatial layout.

### Connection to discourse order vs. story-world order

An important caveat for future iterations of this work: we have implicitly assumed that the narrative describes events in chronological order. In narratological terms, we assume **discourse order** (the order in which the narrative presents events) matches **story-world order** (the order in which events actually occurred). For most of our current puzzles, this is true.

But narratives regularly violate this assumption. Flashbacks, in medias res openings, and nonlinear narration are common storytelling techniques. A narrative that begins "By the time the smoke cleared, only ashes remained" and then describes the fire retrospectively would create a deliberate mismatch between discourse and story-world order.

This opens an interesting design space for future puzzles: what happens when the narrative tells the story out of order? The model would need to distinguish between the order of the telling and the order of the events --- a distinction that narratologists have long studied but that we have not yet tested in NARC.

### Connection to the compression hypothesis

In the [Four Narrative Strategies report](four_narrative_strategies_preliminary.md), we proposed that rich literary expression may function as a kind of compression that AI models struggle to decompress. The ordering results add a nuance to this hypothesis.

For ordering, the relevant information is temporal and causal structure: what comes before what, and why. This is precisely the kind of information that narrative form encodes most naturally. Even a rich, literary narrative --- with characters, emotions, sensory detail --- reliably conveys temporal order. "As the parent begins to sing, the red thoughts fade first" is richly literary, but its temporal content is unmistakable.

This suggests that the compression hypothesis may apply differently to different aspects of narrative reasoning. For spatial reconstruction (the original NARC task), literary compression hurts AI because the spatial information is buried in figurative language. For temporal ordering, the compression is less lossy because narrative form inherently preserves sequence. The "signal" for ordering is built into the medium itself.

---

## Summary of Findings

1. **Narratives substantially improve AI ordering performance.** Across four models and 70 puzzles, the average narrative lift in Kendall's tau was +0.25 to +0.37. Exact-match rates roughly doubled.

2. **The effect is consistent across models.** All four models showed positive average lift. Nine puzzles showed positive lift on all four models; no puzzle showed negative lift on all four.

3. **Longer sequences benefit more.** Six-grid and eight-grid puzzles show larger lifts than four-grid puzzles, because longer sequences are harder to order from visual patterns alone.

4. **Some puzzles show ordering-NARC properties.** In 41 model-puzzle cases, the model was actively confused by the visual sequence (tau < 0) but achieved near-correct ordering with the narrative (tau > 0.8). The Lullaby is the clearest example: all four models reversed the sequence without the narrative and got it perfect with it.

5. **Narrative occasionally hurts.** A small number of puzzles showed negative lift, particularly when the narrative described both a forward and a reverse process (e.g., building then collapse), causing models to misread the turning point.

6. **Ordering lift is larger than reconstruction lift.** This makes sense: narratives inherently encode temporal sequence, which maps directly onto the ordering task. Spatial reconstruction requires extracting more precise information that narrative form encodes less reliably.

---

## What's Next

1. **Discourse vs. story-world order.** Design puzzles where the narrative tells events out of chronological order. This would test whether models can distinguish discourse order from story-world order --- a core narratological distinction.

2. **Human comparison.** Do human solvers show the same ordering-lift pattern, or do they rely more on visual cues? If humans are better at ordering from grids alone, the narrative lift might be smaller for humans, which would invert the usual NARC pattern.

3. **Integration with the NARC property.** Some puzzles are NARC for reconstruction *and* for ordering; others for one but not the other. Mapping the overlap could reveal whether narratives aid spatial and temporal reasoning through the same or different mechanisms.

4. **ABT and ordering.** We know from the earlier ABT experiment that narrative structure (And-But-Therefore vs. And-And-And) has a small effect on reconstruction. Does ABT structure also affect ordering? The "But" in an ABT narrative marks a temporal pivot, which could be especially useful for ordering.

---

## Attribution

- **NARC Benchmark:** Bert Baumgaertner, University of Idaho.
- **Story Prism:** Erin James, University of Idaho. Original narrative decomposition framework.
- **ABT Framework:** Randy Olson (2012). *Connection: Hollywood Storytelling Meets Critical Thinking.*

---

*NARC is open source: [github.com/bertybaums/narc](https://github.com/bertybaums/narc)*
*Live demo: [bertybaums.github.io/narc](https://bertybaums.github.io/narc)*
