#!/usr/bin/env python3
"""Classify all NARC puzzle narratives on the ABT spectrum.

Uses keyword heuristics first, then LLM-assisted classification for borderlines.

Output: abt_classifications.json + abt: tags added to puzzle JSON files.

Usage:
    python classify_abt.py [--llm] [--dry-run]
"""

import json
import re
import glob
from pathlib import Path

import click


# Explicit contradiction connectors (signal ABT or DHY)
EXPLICIT_BUT = re.compile(
    r'\b(but|however|yet|except|instead|only to find|until|though|although|'
    r'unfortunately|surprisingly|unexpectedly|nevertheless|still|rather than)\b',
    re.IGNORECASE
)

# Multiple contradiction connectors (signal DHY)
DHY_CONNECTORS = re.compile(
    r'\b(despite|despite this|however|yet|nevertheless|nonetheless|'
    r'even so|on the other hand|in contrast|conversely)\b',
    re.IGNORECASE
)

# Words that imply a twist/reversal even without explicit connectors
IMPLICIT_BUT_SIGNALS = re.compile(
    r'\b(turns? back|revers|flip|invert|collaps|transform|shift|'
    r'sudden|unexpected|one day|then one|the third time|'
    r'no one came|refused|failed|broke|fell|vanish|disappear|'
    r'too late|never again|for the last time|'
    r'what remains|what was lost|what survived)\b',
    re.IGNORECASE
)


def classify_narrative(narrative):
    """Classify a single narrative on the ABT spectrum.

    Returns (classification, confidence, details) where:
    - classification: 'aaa', 'abt-explicit', 'abt-implicit', 'dhy'
    - confidence: 'high', 'medium', 'low'
    - details: dict with matched patterns
    """
    if not narrative:
        return 'aaa', 'high', {'reason': 'empty narrative'}

    # Count explicit contradiction connectors
    explicit_matches = EXPLICIT_BUT.findall(narrative)
    dhy_matches = DHY_CONNECTORS.findall(narrative)
    implicit_matches = IMPLICIT_BUT_SIGNALS.findall(narrative)

    n_explicit = len(explicit_matches)
    n_dhy = len(dhy_matches)
    n_implicit = len(implicit_matches)

    details = {
        'explicit_connectors': explicit_matches,
        'dhy_connectors': dhy_matches,
        'implicit_signals': implicit_matches,
    }

    # DHY: multiple distinct contradiction connectors
    if n_dhy >= 2 or n_explicit >= 3:
        return 'dhy', 'high', details

    # ABT-explicit: has a clear "but" or similar connector
    if n_explicit >= 1:
        # Check if it's a genuine narrative turn vs. incidental "but"
        # "but" in "nothing but X" or "all but X" is not a contradiction
        false_buts = len(re.findall(r'\b(nothing but|all but|anything but|everything but)\b',
                                     narrative, re.IGNORECASE))
        real_explicit = n_explicit - false_buts
        if real_explicit >= 1:
            return 'abt-explicit', 'high', details

    # ABT-implicit: no explicit connector but has reversal/twist signals
    if n_implicit >= 1:
        return 'abt-implicit', 'medium', details

    # Check for sentence-level structure that implies ABT without keywords
    sentences = re.split(r'[.!?;—]+', narrative)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

    if len(sentences) >= 3:
        # Look for a tonal shift in the middle or end
        # This is heuristic — low confidence
        last_sentence = sentences[-1].lower()
        if any(w in last_sentence for w in ['remains', 'only', 'left', 'alone', 'silence',
                                             'empty', 'gone', 'lost', 'learned', 'dull']):
            return 'abt-implicit', 'low', {**details, 'reason': 'tonal shift in final sentence'}

    # Default: AAA
    return 'aaa', 'medium' if len(narrative) > 100 else 'high', details


def classify_with_llm(narrative, puzzle_id):
    """Use Claude to classify a borderline narrative. Returns classification string."""
    import subprocess
    prompt = f"""Classify this NARC puzzle narrative on the ABT (And, But, Therefore) spectrum.

Narrative: "{narrative}"

Classifications:
- aaa: Linear listing of facts. No contradiction, twist, or surprise. Just "X and Y and Z."
- abt-explicit: Has a clear contradiction signaled by a word like "but", "however", "yet", "except", "instead". Setup → twist → consequence.
- abt-implicit: Has a contradiction or reversal, but it's not signaled by a connector word. The twist is shown through action, environment change, or narrative pivot.
- dhy: Multiple contradictions or exceptions piled on each other. Overly complex.

Respond with ONLY one of: aaa, abt-explicit, abt-implicit, dhy"""

    try:
        result = subprocess.run(
            ['claude', '-p', prompt, '--output-format', 'text', '--no-session-persistence',
             '--model', 'haiku'],
            capture_output=True, text=True, timeout=30
        )
        classification = result.stdout.strip().lower()
        if classification in ('aaa', 'abt-explicit', 'abt-implicit', 'dhy'):
            return classification
    except Exception:
        pass
    return None


@click.command()
@click.option('--llm', is_flag=True, help='Use LLM for low-confidence classifications')
@click.option('--dry-run', is_flag=True, help='Show classifications without modifying files')
def main(llm, dry_run):
    puzzles_dir = Path('data/puzzles')
    results = {}
    counts = {'aaa': 0, 'abt-explicit': 0, 'abt-implicit': 0, 'dhy': 0}
    low_confidence = []

    for f in sorted(puzzles_dir.glob('*.json')):
        with open(f) as fh:
            d = json.load(fh)
        pid = d['puzzle_id']
        narrative = d.get('narrative', '')

        classification, confidence, details = classify_narrative(narrative)

        # LLM assist for low-confidence cases
        if llm and confidence == 'low':
            llm_class = classify_with_llm(narrative, pid)
            if llm_class:
                classification = llm_class
                confidence = 'llm-assisted'

        if confidence == 'low':
            low_confidence.append(pid)

        results[pid] = {
            'classification': classification,
            'confidence': confidence,
            'explicit_connectors': details.get('explicit_connectors', []),
            'implicit_signals': details.get('implicit_signals', []),
        }
        counts[classification] += 1

        # Add tag to puzzle JSON
        if not dry_run:
            tags = d.get('metadata', {}).get('tags', [])
            # Remove any existing abt: tags
            tags = [t for t in tags if not t.startswith('abt:')]
            tags.append(f'abt:{classification}')
            d.setdefault('metadata', {})['tags'] = tags
            with open(f, 'w') as fh:
                json.dump(d, fh, indent=2)

    # Save classifications
    if not dry_run:
        with open('abt_classifications.json', 'w') as f:
            json.dump(results, f, indent=2)

    # Report
    total = sum(counts.values())
    click.echo(f"\nABT Classification Results ({total} puzzles):")
    click.echo(f"  AAA (linear):        {counts['aaa']:3d} ({counts['aaa']/total*100:.0f}%)")
    click.echo(f"  ABT-explicit:        {counts['abt-explicit']:3d} ({counts['abt-explicit']/total*100:.0f}%)")
    click.echo(f"  ABT-implicit:        {counts['abt-implicit']:3d} ({counts['abt-implicit']/total*100:.0f}%)")
    click.echo(f"  DHY (over-narrative): {counts['dhy']:3d} ({counts['dhy']/total*100:.0f}%)")

    if low_confidence:
        click.echo(f"\n  Low confidence ({len(low_confidence)}): {', '.join(low_confidence[:10])}...")

    if not dry_run:
        click.echo(f"\nSaved: abt_classifications.json + abt: tags in puzzle JSONs")


if __name__ == '__main__':
    main()
