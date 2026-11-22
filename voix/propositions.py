"""Les propositions faites en passant, pour qu'elles ne tombent pas dans le flux.

Le defaut qu'on corrige, dit tel quel : « regulierement je dev quelque chose, le dev est
fini, et en fait je ne regarde pas le message de reponse — je renvoie un message pour une
nouvelle chose ». Or la fin d'une reponse est precisement l'endroit ou l'on ecrit « ce qui
reste », « on pourrait aussi », « je ferais ensuite ». Ces phrases-la sont les seules de
toute la conversation qui proposent quelque chose, et ce sont celles qu'on lit le moins :
elles arrivent quand la tache est finie, donc quand l'attention est deja partie ailleurs.

Elles ne se perdent pas faute d'etre ecrites. Elles se perdent parce qu'elles vivent dans
un flux qui defile, et qu'un flux n'a pas d'etat : rien ne distingue une proposition qu'on
a traitee d'une proposition qu'on n'a jamais lue. Ce module leur donne cet etat, et un
endroit ou le lire — ouverte, faite, ecartee.

Volontairement sans modele, meme raison que `question_en_plan` : un appel de plus par tour
couterait chaque jour ce que la liste fait gagner, et une heuristique se teste — on sait
exactement ce qu'elle rate.

En cas de doute, on GARDE. Les deux erreurs ne coutent pas pareil : une proposition de trop
s'ecarte d'un doigt, une proposition manquee ne se rattrape jamais — c'est le symptome de
depart.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import config

log = logging.getLogger("voix.propositions")

FICHIER = Path(config.JOURNAL_DIR) / "propositions.json"

# Ce qui PROPOSE. Au conditionnel ou au futur proche, presque toujours : une proposition se
# reconnait a ce qu'elle parle d'un travail qui n'a pas eu lieu. Les formes au passe
# (« il fallait », « j'ai propose ») decrivent ce qui est deja fait et n'engagent rien.
# La page porte la MEME liste (voix/tableau.py, MARQUEURS_PROP), parce qu'elle surligne sans
# attendre l'agent. Les deux ne peuvent pas etre partagees — l'une est en Python, l'autre dans
# du JavaScript servi en dur — alors `test_propositions.py` verifie qu'elles ne divergent pas :
# une phrase surlignee en jaune qui n'est PAS dans la liste fait chercher pendant dix minutes.
# Pas de \b devant un caractere accentue : JavaScript ne tient pas « à » pour un caractere de
# mot, et le motif n'y matcherait jamais.
MARQUEURS = [
    r"\bje (te )?propose\b",
    r"\bje sugg[èe]re\b",
    r"\bon (pourrait|devrait|gagnerait)\b",
    r"\bil (faudrait|faudra|resterait)\b",
    r"\bil reste [àa]\b",
    r"\breste [àa] faire\b",
    r"\bce qui reste\b",
    r"\bprochaine[s]? [ée]tape[s]?\b",
    r"[àa] faire (ensuite|plus tard|un jour)\b",
    r"\bje ferais (ensuite|bien)?\b",
    r"\bsi tu veux,? je (peux|pourrais)\b",
    r"\bje peux (aussi|ensuite|le faire)\b",
    r"(ça|cela|ca) m[ée]riterait\b",
    r"\bm[ée]riterait (d'|de |un|une)\b",
    r"\bune piste\b",
    r"\bpiste[s]? ([àa] creuser|possible)\b",
    r"\bla suite logique\b",
    r"\bl'[ée]tape d'apr[èe]s\b",
    r"\bje n'ai pas (trait[ée]|fait|couvert|touch[ée])\b",
    r"\bnon trait[ée]\b",
    r"\bpas (encore )?trait[ée]\b",
    r"\bun cran plus loin\b",
    r"\bam[ée]lioration[s]? (possible|[àa] faire)\b",
    r"\bdis-?\s?moi si j'(enchaîne|enchaine|y vais|attaque)\b",
    r"\bveux-tu que je\b",
    r"\btu veux que je\b",
]
_MARQUEURS = re.compile("|".join(MARQUEURS), re.IGNORECASE)

# Ce qui ANNULE un marqueur dans la meme phrase. « Il faudrait » precede d'une negation ou
# suivi d'un constat passe ne propose rien : « il aurait fallu », « on ne pourrait pas ».
_ANNULE = re.compile(r"\b(aurait fallu|ne pourrait pas|n'aurait|il fallait|j'ai propos[ée])\b",
                     re.IGNORECASE)

# Bornes de ce qu'on retient. En dessous, c'est un bout de phrase sans sujet (« à faire
# ensuite. ») qui ne veut plus rien dire hors de son contexte ; au-dessus, c'est un
# paragraphe entier, illisible dans une liste faite pour etre balayee du pouce.
MINI = 25
MAXI = 320
# Combien d'une seule reponse. Un bilan bavard en contient cinq ou six, et une liste de
# cinquante lignes est une liste que personne n'ouvre. Les premieres gagnent : une reponse
# annonce ce qui compte avant de deriver.
PAR_TOUR = 3


def _nettoyer(texte: str) -> str:
    """Oter les blocs de code et les URL, GARDER le code en ligne sans ses accents graves.

    La difference avec `question_en_plan` est voulue : la il s'agissait d'oter des « ? »
    parasites, ici il s'agit de lire une phrase. « 94 conversations n'ont pas de ligne
    `_fin` » perd son objet si on efface `_fin`, et la proposition devient illisible dans
    une liste ou elle est seule, sans le message autour.
    """
    t = re.sub(r"```.*?```", " ", texte, flags=re.S)
    t = re.sub(r"```.*", " ", t, flags=re.S)
    t = re.sub(r"`([^`\n]*)`", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", t)        # le gras n'ajoute rien a une liste
    return "\n".join(l for l in t.splitlines() if not l.lstrip().startswith(">"))


def _phrases(texte: str) -> list[str]:
    """Phrase par phrase, mais une puce est une phrase meme sans point final.

    Les propositions s'ecrivent en liste une fois sur deux — « Ce qui reste : » puis trois
    tirets. Decouper sur la seule ponctuation collerait les trois en un seul bloc, et la
    liste afficherait un pave au lieu de trois choses a traiter separement.
    """
    out = []
    for ligne in texte.splitlines():
        ligne = re.sub(r"^\s*([-*+•]|\d+[.)])\s+", "", ligne).strip()
        if not ligne:
            continue
        out.extend(m.strip() for m in re.split(r"(?<=[.!?…])\s+", ligne) if m.strip())
    return out


def _cle(texte: str) -> str:
    """L'identite d'une proposition, insensible a la ponctuation et a la casse.

    Sert a ne pas empiler dix fois la meme : une proposition non traitee revient souvent
    dans le bilan du tour suivant, mot pour mot ou presque."""
    mots = re.findall(r"\w+", texte.lower())
    return hashlib.sha1(" ".join(mots).encode("utf-8")).hexdigest()[:12]


def _mots(texte: str) -> set[str]:
    """Les mots qui PORTENT du sens. Les outils grammaticaux sont partout, donc ne
    distinguent rien : deux phrases sans rapport partagent « que », « de », « le »."""
    return {m for m in re.findall(r"\w{4,}", texte.lower())
            if m not in {"dans", "pour", "avec", "sans", "cette", "plus", "tout", "tous",
                         "faire", "fait", "mettre", "chose", "choses", "quand", "donc",
                         "mais", "elle", "elles", "leur", "comme", "aussi", "bien"}}


def extraire(texte: str) -> list[str]:
    """Les propositions de ce texte, dans l'ordre ou elles sont ecrites.

    On balaie TOUT le texte, pas seulement la fin — l'inverse exact de `question_en_plan`,
    et pour une raison dite telle quelle : « des fois elles sont au milieu du message, je ne
    les regarde pas ». Une question engage la suite de la conversation, donc elle se tient a
    la fin ; une proposition se glisse ou elle veut.
    """
    if not texte or not texte.strip():
        return []
    trouvees: list[str] = []
    vues: set[str] = set()
    for ph in _phrases(_nettoyer(texte)):
        if not _MARQUEURS.search(ph) or _ANNULE.search(ph):
            continue
        ph = ph.strip(" -—•\t")
        if not MINI <= len(ph) <= MAXI:
            continue
        cle = _cle(ph)
        if cle in vues:
            continue
        vues.add(cle)
        trouvees.append(ph)
        if len(trouvees) >= PAR_TOUR:
            break
    return trouvees


# --- le registre ---------------------------------------------------------------------
def _lire() -> list[dict]:
    try:
        donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return donnees if isinstance(donnees, list) else []


def _ecrire(tout: list[dict]) -> None:
    try:
        FICHIER.parent.mkdir(parents=True, exist_ok=True)
        FICHIER.write_text(json.dumps(tout, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        log.debug("propositions non enregistrees", exc_info=True)


def enregistrer(textes: list[str], projet: str = "", session: str = "",
                conversation: str = "", quand: float | None = None) -> list[dict]:
    """Ajouter ce qui est neuf, et rendre ce qui vient d'etre ajoute.

    Une proposition deja connue n'est PAS re-ouverte : l'ecarter puis la voir revenir au
    tour suivant parce que le bilan l'a répétée serait la meilleure facon de faire fermer
    la liste pour de bon.
    """
    if not textes:
        return []
    tout = _lire()
    connues = {p.get("cle") for p in tout}
    neuves = []
    for texte in textes:
        cle = _cle(texte)
        if cle in connues:
            continue
        connues.add(cle)
        neuves.append({"cle": cle, "texte": texte, "projet": projet, "session": session,
                       "conversation": conversation, "quand": quand or time.time(),
                       "etat": "ouverte"})
    if neuves:
        _ecrire(tout + neuves)
    return neuves


def ouvertes(projet: str = "") -> list[dict]:
    """Ce qui attend encore, la plus RÉCENTE d'abord.

    L'ordre compte : une proposition d'il y a trois semaines a de bonnes chances d'etre
    perimee, et la liste se lit par le haut sur un telephone.
    """
    tout = [p for p in _lire() if p.get("etat") == "ouverte"]
    if projet:
        tout = [p for p in tout if p.get("projet") == projet]
    return sorted(tout, key=lambda p: p.get("quand") or 0, reverse=True)


def compter(projet: str = "") -> int:
    return len(ouvertes(projet))


def marquer(cle: str, etat: str) -> bool:
    """Changer l'etat d'une proposition. Vrai si elle existait."""
    tout = _lire()
    touche = False
    for p in tout:
        if p.get("cle") == cle:
            p["etat"] = etat
            p["repondu"] = time.time()
            touche = True
    if touche:
        _ecrire(tout)
    return touche


# Ce qu'il faut de mots communs pour dire « c'est de ça qu'il parle ». Mesure a la main sur
# les propositions du dossier : en dessous, deux phrases du meme projet se ressemblent
# toujours un peu — elles parlent des memes fichiers — et tout se fermerait tout seul.
SEUIL_REPRISE = 0.55


def resoudre(message: str) -> list[str]:
    """Fermer les propositions que CE message reprend, et dire lesquelles.

    « Tant que je n'ai pas dit : ce truc-là, mets-le en place » — c'est le moment ou une
    proposition cesse d'attendre. On le reconnait au recouvrement des mots qui portent du
    sens : demander la mise en place d'une proposition, c'est presque toujours la citer.

    On penche vers GARDER : une proposition fermee a tort disparait sans bruit, alors qu'une
    proposition gardee a tort se voit, et s'ecarte d'un doigt.
    """
    mots = _mots(message or "")
    if len(mots) < 3:
        return []
    fermees = []
    for p in ouvertes():
        siens = _mots(p.get("texte", ""))
        if not siens:
            continue
        if len(siens & mots) / len(siens) >= SEUIL_REPRISE:
            marquer(p["cle"], "faite")
            fermees.append(p["texte"])
    return fermees


# --- l'amorce ---------------------------------------------------------------------------
# Combien de jours en arriere, et combien au plus. Les deux bornes servent la meme chose :
# une liste de quatre-vingts lignes heritees de trois semaines ne se traite pas, elle se
# ferme. Dix jours, c'est l'horizon ou une proposition a encore des chances d'etre d'actualite.
JOURS_AMORCE = 10
PLAFOND_AMORCE = 25


def _blocs_de_claude(texte: str) -> list[str]:
    """Ce que Claude a ECRIT dans ce transcript, section par section.

    Les sections « toi » sont ecartees : une demande n'est pas une proposition, et les
    reprendre ferait proposer a quelqu'un ce qu'il vient de demander lui-meme.
    """
    blocs = []
    for bloc in re.split(r"\n## ", "\n" + texte):
        entete, _, corps = bloc.partition("\n")
        if "claude" in entete and corps.strip():
            blocs.append(corps)
    return blocs


def amorcer(jours: int = JOURS_AMORCE, plafond: int = PLAFOND_AMORCE) -> int:
    """Remplir la liste avec ce qui a deja ete propose, UNE SEULE FOIS.

    Sans ça, la liste naît vide et le bouton reste invisible jusqu'à la prochaine réponse
    qui propose quelque chose — alors que les propositions à traiter, elles, existent déjà :
    elles sont dans les transcripts des derniers jours, et c'est précisément là qu'elles ont
    été perdues. « Rappelle-moi dans l'historique toutes les choses qui ont été proposées »
    a déjà été demandé à la main ; c'est ce travail-là qu'on fait une fois pour toutes.

    Ne s'exécute que si le registre n'existe pas encore : relancer l'amorce ferait revenir
    tout ce qui a été écarté entre-temps.
    """
    if FICHIER.exists():
        return 0
    racine = Path(config.JOURNAL_DIR)
    limite = time.time() - jours * 86400
    try:
        fichiers = [f for f in racine.glob("*.md") if f.stat().st_mtime >= limite]
    except OSError:
        return 0
    # Le nom porte la date de DÉBUT, le mtime celle de la dernière écriture : on classe
    # sur le second, et le premier départage. Sans ce départage, deux transcripts écrits
    # dans la même seconde s'ordonnent au hasard du système de fichiers, et le plafond
    # coupe alors n'importe où.
    fichiers.sort(key=lambda f: (f.stat().st_mtime, f.name))
    trouvees: list[tuple] = []
    for f in fichiers:
        try:
            texte = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Le nom porte la date et le projet : 2026-11-22_1219_claude-talk.md
        projet = f.stem.split("_", 2)[-1]
        quand = f.stat().st_mtime
        for bloc in _blocs_de_claude(texte):
            for t in extraire(bloc):
                trouvees.append((t, projet, f.name, quand))
    # Les plus RÉCENTES : une proposition de la semaine derniere a de bonnes chances d'avoir
    # ete faite depuis, et c'est le dernier bilan qu'on n'a pas lu qui compte.
    trouvees = trouvees[-plafond:]
    n = 0
    for t, projet, fichier, quand in trouvees:
        n += len(enregistrer([t], projet=projet, conversation=fichier, quand=quand))
    # Le fichier doit exister meme vide, sinon l'amorce recommence a chaque demarrage.
    if not FICHIER.exists():
        _ecrire([])
    log.info("propositions : %d retenues dans les %d derniers jours", n, jours)
    return n
