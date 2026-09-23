"""Recognise the handful of orders the voice layer executes itself, on any phrasing.

Why not a regex over word sequences: it only matches the phrasings you thought of. "coupe le
micro" worked, "tu peux couper le micro s'il te plaît" did not, and there is no end to that
list. Why not a small LLM classifier either: these orders exist because they are instant, and
putting a model call in front of every single utterance would cost 300-500 ms on the one path
that must never wait — plus a second thing that can fail.

So: co-occurrence. An order fires when a verb and an object of the same intent appear
anywhere in the utterance, in any order, under any conjugation. That covers natural speech
without enumerating it.

The trap this has to avoid is real, not theoretical: "ajoute un bouton pour couper le micro
dans le front" is a sentence actually said to this project, and it contains both words. So an
order also has to look like an order — short, and free of the vocabulary of a coding task.
Anything longer or task-shaped goes to Claude, which is the safe default.
"""

import re
from dataclasses import dataclass, field

MAX_MOTS = 12  # beyond that it is a task being described, not an order being given

# Vocabulary that marks a request to *do work*. Its presence sends the utterance to Claude
# even when an order's words are in there too.
TACHE = re.compile(
    r"\b(ajoute\w*|cr[ée]e\w*|impl[ée]ment\w*|[ée]cri\w*|corrig\w*|refactor\w*|renomm\w*"
    r"|supprim\w*|remplac\w*|d[ée]plac\w*|test\w*|commit\w*|push\w*|installe?\w*"
    r"|fichier|fonction|bouton|variable|classe|module|script|code|front|back|dossier"
    r"|composant|endpoint|branche|pull request)\b",
    re.I,
)


@dataclass(frozen=True)
class Intention:
    nom: str
    # Phrases that need no object: they are unambiguous on their own.
    seules: tuple = ()
    verbes: tuple = ()
    objets: tuple = ()
    # Words that veto this intent specifically.
    sauf: tuple = ()


def _mot(*racines: str) -> re.Pattern:
    """Word-prefix match, so one entry covers every conjugation: coup -> coupe, couper,
    coupes, coupez, coupé. The leading \\b keeps "beaucoup" out of "coup"."""
    return re.compile(r"\b(?:" + "|".join(racines) + r")\w*", re.I)


INTENTIONS = (
    Intention(
        nom="micro",
        seules=(r"\bmute\b", r"\bchut\b.*\bmicro\b",
                r"\bn'?[ée]coute\w*\s+plus\b", r"\bne m'?[ée]coute\w*\s+plus\b"),
        verbes=("coup", "ferm", "[ée]tein", "d[ée]sactiv", "arr[êe]t", "enl[èe]v", "vire",
                "baiss", "silenc"),
        objets=("micro", "microphone", "[ée]cout", "entend"),
    ),
    Intention(
        nom="silence",
        # Shut up without stopping the work — distinct from stopping the work itself.
        seules=(r"\bchut\b", r"\btais[- ]toi\b", r"\bta gueule\b", r"\bsilence\b",
                r"\bstp arr[êe]te de parler\b"),
        verbes=("arr[êe]t", "coup", "cess"),
        objets=("parl", "caus", "voix", "commentair", "narration"),
    ),
    Intention(
        nom="arret",
        seules=(r"\bstop\b", r"\blaisse tomber\b", r"\bannule\b", r"\boublie [çc]a\b"),
        verbes=("arr[êe]t", "stopp", "interromp", "annul", "abandonn"),
        objets=("travail", "t[âa]che", "tout", "[çc]a", "boulot", "run"),
    ),
    Intention(
        nom="repete",
        seules=(r"\br[ée]p[èe]te\b", r"\bpardon\b", r"\bcomment\b\s*\?*$",
                r"\bj'?ai pas (?:bien )?(?:compris|entendu|saisi)\b",
                r"\bqu'?est[- ]ce que tu (?:as )?dit\b"),
        verbes=("r[ée]p[èé]t", "redi"),
        objets=("[çc]a", "dernier", "phrase", "tout"),
    ),
    Intention(
        # Its nouns are specific enough to fire alone, now that a task sentence is vetoed
        # before any of this runs.
        nom="quota",
        # "il reste combien" est laisse a statut : sans objet, il parle bien plus souvent du
        # travail en cours que du quota. Avec « me », il devient personnel donc quota.
        seules=(r"\bquota\b", r"\brate limit\b", r"\bfen[êe]tres?\b", r"\bjetons?\b",
                r"\btokens?\b", r"\bcombien il me reste\b"),
        verbes=("rest", "consomm", "utilis", "brul"),
        objets=("quota", "limite", "fen[êe]tre", "jeton", "token", "semaine", "cinq heures"),
    ),
    Intention(
        nom="modele",
        seules=(r"\bmod[èe]le plus (?:rapide|l[ée]ger|simple|puissant|fort|capable)\b",
                r"\b(?:passe|bascule|mets?)[- ]?(?:en|sur|toi en)?\s*(?:haiku|sonnet|opus|fable)\b",
                r"\butilise (?:haiku|sonnet|opus|fable)\b"),
        verbes=("utilis", "pass", "bascul", "prend", "chang", "met", "revien", "retourn"),
        # « fable » n'est PAS dans les objets, contrairement aux trois autres noms : c'est
        # aussi un mot francais courant, et la porte « verbe + objet » est large. Avec lui
        # dedans, « prends la fable du corbeau » ou « mets la fable en annexe » basculaient
        # de modele au lieu de partir chez Claude. Il ne se demande donc que par les motifs
        # ci-dessus, qui exigent le verbe de bascule COLLE au nom — et la, « fable » ne peut
        # plus vouloir dire autre chose.
        objets=("mod[èe]le", "haiku", "sonnet", "opus", "rapide", "l[ée]ger", "puissant"),
    ),
    Intention(
        # Last on purpose: its vocabulary is the loosest, so everything more specific gets
        # a chance first.
        nom="statut",
        seules=(r"\bo[uù] (?:tu )?en es\b", r"\bt'?en es o[uù]\b", r"\b[çc]a avance\b",
                r"\b[çc]a en est o[uù]\b", r"\bquoi de neuf\b"),
        verbes=("fai", "avanc", "en es", "rest"),
        objets=("quoi", "o[uù]", "combien", "point"),
    ),
)

_COMPILE = {
    i.nom: (
        [re.compile(p, re.I) for p in i.seules],
        _mot(*i.verbes) if i.verbes else None,
        _mot(*i.objets) if i.objets else None,
        _mot(*i.sauf) if i.sauf else None,
    )
    for i in INTENTIONS
}


# Fable ne se demande QUE par son nom : aucun adjectif ne lui est associe, contrairement aux
# trois autres. C'est voulu — « fable » est aussi un mot francais courant, et le relier a
# « recent » ou « nouveau » ferait basculer de modele sur une phrase qui parle d'autre chose.
# Le motif reste simple parce qu'il n'est consulte QU'APRES reconnaissance de l'intention
# « modele » : a ce stade, « fable » ne peut plus designer un recit.
FABLE = re.compile(r"\bfables?\b", re.I)
RAPIDE = re.compile(r"\b(rapide|vite|l[ée]ger|simple|court|haiku)\w*", re.I)
MOYEN = re.compile(r"\b(sonnet|[ée]quilibr|moyen|interm[ée]diaire)\w*", re.I)
FORT = re.compile(r"\b(opus|puissant|fort|capable|meilleur|normal|d[ée]faut|habituel)\w*", re.I)


def modele_demande(texte: str) -> str | None:
    """Which model an utterance asks for. Checked most-specific-first: a name beats an
    adjective, so "opus" wins over a generic "rapide" if both somehow appear."""
    if FABLE.search(texte):
        return "fable"
    if FORT.search(texte):
        return "opus"
    if MOYEN.search(texte):
        return "sonnet"
    if RAPIDE.search(texte):
        return "haiku"
    return None


def reconnaitre(texte: str) -> tuple[str | None, str]:
    """Returns (intent, why). The reason is published to the dashboard: a local order that
    hijacks an utterance has to be auditable, or a wrong detection looks like a bug in
    Claude rather than a bug here."""
    propre = (texte or "").strip()
    if not propre:
        return None, ""

    # Guards first, for direct phrasings too. Letting them bypass this is exactly how
    # "renomme la variable qui gère le silence" cut the agent off mid-sentence: the word was
    # there, the intent was not.
    mots = re.findall(r"\w+", propre)
    if len(mots) > MAX_MOTS:
        return None, ""
    if TACHE.search(propre):
        return None, ""

    for nom, (seules, _, _, sauf) in _COMPILE.items():
        if sauf and sauf.search(propre):
            continue
        for motif in seules:
            if motif.search(propre):
                return nom, f"formulation directe ({motif.pattern[:34]})"

    for nom, (_, verbes, objets, sauf) in _COMPILE.items():
        if not (verbes and objets):
            continue
        if sauf and sauf.search(propre):
            continue
        v = verbes.search(propre)
        o = objets.search(propre)
        if v and o:
            return nom, f"« {v.group(0)} » + « {o.group(0)} »"
    return None, ""
