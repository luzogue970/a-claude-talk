#!/usr/bin/env python3
"""Respell English technical terms so a French voice pronounces them correctly."""

import os
import re
import sys

TABLE = os.environ.get("CLAUDE_TTS_LEXICON", os.path.expanduser("~/.claude/tts/en2fr.tsv"))


def load_pairs(path):
    pairs = []
    if not os.path.isfile(path):
        return pairs
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            source, _, target = line.partition("\t")
            source, target = source.strip(), target.strip()
            if source and target:
                pairs.append((source, target))
    # Longest first so "pull request" wins over "pull".
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def build_pattern(pairs):
    if not pairs:
        return None, {}
    lookup = {source.lower(): target for source, target in pairs}
    alternation = "|".join(re.escape(source) for source, _ in pairs)
    return re.compile(rf"(?<![\w-])({alternation})(?![\w-])", re.IGNORECASE), lookup


def main():
    pattern, lookup = build_pattern(load_pairs(TABLE))
    text = sys.stdin.read()
    if pattern:
        text = pattern.sub(lambda m: lookup[m.group(1).lower()], text)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
