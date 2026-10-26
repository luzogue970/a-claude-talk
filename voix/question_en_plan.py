"""Reconnaitre une reponse qui ATTEND une reponse.

Le defaut qu'on corrige : toutes les reponses se ressemblent. « C'est fait, 2076 vertes »
et « il me faut ton avis sur trois points » arrivent dans la meme ligne, de la meme
couleur, et se lisent a voix haute de la meme facon. Sur un telephone, on ecoute — donc
on ecoute la reponse ENTIERE a chaque fois, pour decouvrir a la fin si elle se terminait
par une question. C'est la seule facon de ne pas laisser une conversation arretee sur une
question que personne n'a vue.

Ce module repond a « est-ce que mon prochain message doit etre une reponse ? » en lisant
la FIN du texte. Pas le milieu : une question au milieu d'une reponse est presque toujours
rhetorique (« Pourquoi ? Parce que... »), ou citee. Ce qui engage la suite de la
conversation est ce sur quoi la reponse se termine.

Volontairement sans modele. Un appel de plus par tour, pour poser une pastille, couterait
chaque jour ce que cette pastille fait gagner. Et une heuristique se teste : on sait
exactement ce qu'elle rate.

En cas de doute, on PENCHE vers « attend ». Les deux erreurs ne coutent pas pareil : un
faux positif fait ecouter une reponse pour rien, un faux negatif laisse la conversation
arretee sur une question qu'on n'a jamais vue — et c'est exactement le symptome de depart.
"""

from __future__ import annotations

import re

# Ce qui demande une reponse SANS point d'interrogation. Volontairement etroit, et toujours
# a la deuxieme personne : « precise » peut etre un adjectif, « precise-moi » ne peut pas
# etre autre chose qu'une demande. Les formes larges (« j'ai besoin de ») ont ete essayees
# et jetees : « j'ai besoin de relancer le service » n'attend rien de personne.
MARQUEURS = [
    r"\bdis-?\s?moi\b",
    r"\bpr[ée]viens-?\s?moi\b",
    r"\bpr[ée]cise-?\s?moi\b",
    r"\bindique-?\s?moi\b",
    r"\bdonne-?\s?moi\b",
    r"\bconfirme-?\s?moi\b",
    r"\br[ée]ponds-?\s?moi\b",
    r"\bje te laisse (choisir|d[ée]cider|trancher|juger|voir)\b",
    r"\b[àa] toi de (voir|choisir|d[ée]cider|trancher|dire)\b",
    r"\bj'ai besoin (de savoir|que tu)\b",
    r"\bil me faut (ton|ta|tes)\b",
    r"\bsur lequel\b|\bsur laquelle\b",
    r"\bun mot et je\b",
]
_MARQUEURS = re.compile("|".join(MARQUEURS), re.IGNORECASE)

# Une question suivie de sa propre reponse n'attend rien : c'est une figure de style, et
# elle est frequente dans un bilan technique (« Pourquoi deux suites ? Parce que... »).
_RHETORIQUE = re.compile(r"^\s*(parce\s+qu|car\b|oui\b|non\b|eh bien\b|la r[ée]ponse\b)",
                         re.IGNORECASE)

# Ce qui suit la question sans l'annuler. Au-dela, la question n'est plus ce sur quoi la
# reponse se termine : elle a ete reglee, ou c'etait une transition.
QUEUE_MAX = 220


def _nettoyer(texte: str) -> str:
    """Oter ce qui porte des « ? » sans jamais rien demander : code, URL, chemins."""
    t = re.sub(r"```.*?```", " ", texte, flags=re.S)      # blocs de code
    t = re.sub(r"```.*", " ", t, flags=re.S)              # bloc ouvert et jamais referme
    t = re.sub(r"`[^`\n]*`", " ", t)                      # code en ligne
    t = re.sub(r"https?://\S+", " ", t)                   # une URL finit souvent par « ? »
    return t


def _queue(texte: str) -> str:
    """Le dernier bloc, plus celui qui le precede quand c'est une liste.

    « J'ai besoin de savoir : » suivi de trois puces met la demande dans le bloc d'AVANT.
    Ne regarder que le dernier bloc ratait donc exactement la forme la plus courante.
    """
    blocs = [b.strip() for b in re.split(r"\n\s*\n", texte) if b.strip()]
    if not blocs:
        return ""
    queue = blocs[-1]
    est_liste = all(re.match(r"^\s*([-*+•]|\d+[.)])\s", l)
                    for l in queue.splitlines() if l.strip())
    if est_liste and len(blocs) > 1:
        queue = blocs[-2] + "\n\n" + queue
    return queue


def _phrases(bloc: str) -> list[str]:
    """Decoupe grossiere, mais qui garde la ponctuation : c'est elle qu'on interroge."""
    morceaux = re.split(r"(?<=[.!?…])\s+", bloc.replace("\n", " "))
    return [m.strip() for m in morceaux if m.strip()]


def question_posee(texte: str) -> str | None:
    """La question laissee en plan a la fin de ce texte, ou None.

    Rend la phrase elle-meme, pas un booleen : c'est elle qu'on affiche en infobulle, et
    pouvoir la relire sans rouvrir toute la reponse est la moitie de l'interet.
    """
    if not texte or not texte.strip():
        return None
    queue = _queue(_nettoyer(texte))
    if not queue:
        return None
    # Une citation n'engage personne : c'est du rapporte, pas une demande.
    lignes = [l for l in queue.splitlines() if not l.lstrip().startswith(">")]
    queue = "\n".join(lignes)
    phrases = _phrases(queue)

    for i, ph in enumerate(phrases):
        porte = ph.rstrip().endswith("?") or bool(_MARQUEURS.search(ph))
        if not porte:
            continue
        suite = " ".join(phrases[i + 1:]).strip()
        if _RHETORIQUE.match(suite):
            continue                      # elle repond elle-meme : rien ne nous est demande
        if len(suite) > QUEUE_MAX:
            continue                      # la reponse ne s'arrete pas la-dessus
        return ph.strip()
    return None


def attend_une_reponse(*textes: str | None) -> str | None:
    """La question posee par l'un de ces textes — l'ecrit ET le parle.

    Les deux, parce qu'ils peuvent differer : le bilan parle est une REECRITURE, et il lui
    arrive de laisser tomber la question finale. Prendre l'ecrit seul raterait l'inverse,
    une question ajoutee a l'oral. Le premier des deux qui demande quelque chose gagne.
    """
    for t in textes:
        trouve = question_posee(t or "")
        if trouve:
            return trouve
    return None
