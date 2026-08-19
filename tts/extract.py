#!/usr/bin/env python3
"""Extract the last assistant response from a Claude Code transcript and flatten it for TTS."""

import json
import os
import re
import sys

MAX_CHARS = int(os.environ.get("CLAUDE_TTS_MAX_CHARS", "1200"))

FENCE = re.compile(r"^\s*(```|~~~)")
TABLE_ROW = re.compile(r"^\s*\|")
TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
LINK = re.compile(r"\[([^\]\n]*)\]\([^)\n]*\)")
IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
BULLET = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
QUOTE = re.compile(r"^\s*>\s?")
EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
HRULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
# Backtick spans holding a path, call or identifier are noise once spoken.
TECHNICAL = re.compile(r"[/\\]|::|[A-Za-z]\.[A-Za-z]|_|\(\)|^\W|\d+$")


def last_assistant_text(transcript_path):
    text = None
    with open(transcript_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            if entry.get("isApiErrorMessage") or entry.get("isAbortedMidStream"):
                continue
            blocks = entry.get("message", {}).get("content")
            if not isinstance(blocks, list):
                continue
            parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            joined = "\n".join(p for p in parts if p.strip())
            if joined.strip():
                text = joined
    return text


def strip_inline_code(match):
    body = match.group(1)
    return "" if TECHNICAL.search(body) else body


def flatten(markdown):
    out = []
    in_fence = False
    for raw in markdown.splitlines():
        if FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or TABLE_ROW.match(raw) or TABLE_SEP.match(raw) or HRULE.match(raw):
            continue
        line = IMAGE.sub("", raw)
        line = LINK.sub(r"\1", line)
        line = INLINE_CODE.sub(strip_inline_code, line)
        line = HEADING.sub("", line)
        line = QUOTE.sub("", line)
        line = BULLET.sub("", line)
        line = EMPHASIS.sub("", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line if line[-1] in ".!?:;" else line + ".")
    return " ".join(out)


def truncate(text):
    if len(text) <= MAX_CHARS:
        return text
    cut = text[:MAX_CHARS]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: stop + 1] if stop > MAX_CHARS // 3 else cut


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 1
    path = payload.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 1
    markdown = last_assistant_text(path)
    if not markdown:
        return 1
    spoken = truncate(flatten(markdown))
    if not spoken.strip():
        return 1
    sys.stdout.write(spoken)
    return 0


if __name__ == "__main__":
    sys.exit(main())
