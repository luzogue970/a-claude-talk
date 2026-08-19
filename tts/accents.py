#!/usr/bin/env python3
"""Restore French accents on words written bare, so the voice pronounces them right."""

import os
import re
import sys
import unicodedata

TTS_DIR = os.path.expanduser("~/.claude/tts")
OVERRIDE = os.path.join(TTS_DIR, "accents-override.tsv")
UNAMBIGUOUS = os.path.join(TTS_DIR, "accents.tsv")
AMBIGUOUS = os.path.join(TTS_DIR, "accents-ambiguous.tsv")
PARTICIPLES = os.path.join(TTS_DIR, "accents-participles.tsv")
WORD = re.compile(r"[A-Za-zÀ-ɏ]+")

AUXILIARIES = {
    "a", "ai", "as", "avons", "avez", "ont", "avait", "avais", "avaient", "aura", "auras",
    "aurons", "aurez", "auront", "aurait", "auraient", "eu", "avoir", "ayant",
    "est", "es", "suis", "sommes", "etes", "êtes", "sont", "etait", "était", "etais", "étais",
    "etaient", "étaient", "sera", "seras", "serons", "serez", "seront", "serait", "seraient",
    "ete", "été", "soit", "soient", "etre", "être", "etant", "étant",
}
BEFORE_INFINITIVE = {
    "de", "pour", "sans", "faire", "fait", "laisser", "va", "vais", "vas", "allons", "allez",
    "vont", "peut", "peux", "peuvent", "pouvoir", "doit", "dois", "doivent", "devoir", "veut",
    "veux", "veulent", "vouloir", "faut", "sait", "savent", "vient", "viennent", "afin",
}
SUBJECTS = {
    "il", "elle", "on", "ils", "elles", "ca", "ça", "cela", "qui", "je", "tu", "nous", "vous",
    "ne", "y", "se",
}
DETERMINERS = {
    "le", "la", "les", "un", "une", "des", "du", "ce", "cet", "cette", "ces", "mon", "ma", "mes",
    "ton", "ta", "tes", "son", "sa", "ses", "notre", "nos", "votre", "vos", "leur", "leurs",
    "chaque", "quel", "quelle", "tout", "toute", "plusieurs", "aucun", "aucune",
}
# "a bien fonctionne" must still see the auxiliary, so these are stepped over.
TRANSPARENT = {
    "pas", "plus", "jamais", "rien", "bien", "deja", "déjà", "toujours", "enfin", "vraiment",
    "aussi", "encore", "souvent", "juste", "seulement", "surtout", "peut", "sans", "doute",
    "tres", "très", "trop", "presque", "quasiment", "immediatement", "immédiatement",
    "correctement", "effectivement", "manifestement", "probablement", "certainement",
}


def previous_significant(tokens, index):
    while index > 0:
        word = tokens[index - 1].group(0).lower()
        if word not in TRANSPARENT:
            return word
        index -= 1
    return ""


def strip_accents(word):
    return "".join(c for c in unicodedata.normalize("NFD", word) if unicodedata.category(c) != "Mn")


def load_pairs(path):
    table = {}
    if not os.path.isfile(path):
        return table
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            bare, _, value = line.rstrip("\n").partition("\t")
            bare, value = bare.strip().lower(), value.strip()
            if bare and value and bare not in table:
                table[bare] = value
    return table


def participle_rank(form):
    """How strongly a form reads as a past participle. Zero means it does not."""
    if form.endswith("é"):
        return 3
    if form.endswith("és"):
        return 2
    if form.endswith(("ée", "ées")):
        return 1
    return 0


def pick(candidates, previous, fallback):
    if previous in AUXILIARIES:
        best = max(candidates, key=participle_rank)
        return best if participle_rank(best) else ""
    if previous in BEFORE_INFINITIVE:
        infinitives = [c for c in candidates if c.endswith("er")]
        if len(infinitives) == 1:
            return infinitives[0]
    if previous in SUBJECTS or previous in DETERMINERS:
        lowest = participle_rank(min(candidates, key=participle_rank))
        pool = [c for c in candidates if participle_rank(c) == lowest]
        if fallback in pool:
            return fallback
        # Several equally plausible readings and nothing to separate them: leave it.
        return pool[0] if len(pool) == 1 else ""
    return fallback if fallback in candidates else ""


def match_case(word, replacement):
    if word.isupper():
        return replacement.upper()
    if word[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def restore(text, override, unambiguous, ambiguous, participles):
    tokens = list(WORD.finditer(text))
    out, cursor = [], 0
    for index, match in enumerate(tokens):
        word = match.group(0)
        out.append(text[cursor:match.start()])
        cursor = match.end()
        if word != strip_accents(word):
            out.append(word)
            continue
        key = word.lower()
        previous = previous_significant(tokens, index)
        replacement = unambiguous.get(key)
        if not replacement and key in ambiguous:
            replacement = pick(ambiguous[key].split("|"), previous, override.get(key, ""))
        if not replacement and previous in AUXILIARIES:
            replacement = participles.get(key)
        if not replacement:
            replacement = override.get(key)
        out.append(match_case(word, replacement) if replacement else word)
    out.append(text[cursor:])
    return "".join(out)


def main():
    override = load_pairs(OVERRIDE)
    unambiguous = load_pairs(UNAMBIGUOUS)
    text = sys.stdin.read()
    if not unambiguous:
        sys.stdout.write(text)
        return
    sys.stdout.write(restore(text, override, unambiguous, load_pairs(AMBIGUOUS), load_pairs(PARTICIPLES)))


if __name__ == "__main__":
    main()
