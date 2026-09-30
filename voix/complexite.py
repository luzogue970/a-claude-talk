"""Quel effort de raisonnement cette demande merite-t-elle ?

Le probleme qu'on resout : l'effort etait fige a « xhigh » pour toute une session. Or une
session alterne « corrige la faute de frappe » et « diagnostique ce bug qui ne se reproduit
qu'au troisieme essai ». Le premier n'a aucun besoin de quinze mille jetons de reflexion, et
ce sont les memes jetons qui manqueront au second en fin de semaine.

Pourquoi ici, en local, et pas par un petit modele qui classerait. Trois raisons, dans
l'ordre d'importance : ce classement s'intercale entre la parole et la reponse, donc chaque
milliseconde s'y voit ; il serait absurde de depenser des jetons pour decider d'en depenser
moins ; et un classificateur distant est une chose de plus qui peut tomber, sur le chemin
qui doit le moins tomber.

Le prix de ce choix est assume : des heuristiques se trompent. Elles se trompent donc dans le
sens le moins couteux — le doute rend « moyen », qui repond correctement a presque tout. Un
classement trop bas se rattrape en redemandant ; un classement trop haut ne se rattrape pas,
il se paie.

Ce que ces regles ne font PAS : deviner le sujet. « refactor » n'est pas un mot savant, c'est
un mot qui annonce du travail sur plusieurs fichiers a la fois. Chaque signal retenu ici
decrit une PROPRIETE de la tache — son etendue, son nombre de contraintes, la profondeur
demandee — pas son domaine.
"""

import re
from dataclasses import dataclass

# Les quatre niveaux, du moins cher au plus cher, avec ce qu'on dit a l'ecran. Les cles sont
# celles de config.EFFORTS : ce module choisit, il n'invente pas de vocabulaire.
NIVEAUX = ("low", "medium", "high", "xhigh")
LIBELLES = {"low": "faible", "medium": "moyen", "high": "élevé", "xhigh": "très élevé"}

# « ultracode » n'est jamais choisi automatiquement : il lance des agents en parallele et
# coute dix fois un tour normal. Il se demande, il ne se devine pas.
JAMAIS_AUTO = ("ultracode",)


# --- ce qui tire vers le BAS ---------------------------------------------------------------
# Des demandes dont la reponse est connue d'avance ou mecanique. Le verbe suffit : « renomme »
# ne devient pas complexe parce que la variable a un nom complique.
SIMPLE = re.compile(
    r"\b(renomm\w*|corrig\w* la faute|reformul\w*|traduis\w*|commente\w*|formate\w*"
    r"|indente\w*|supprime cette|enl[èe]ve (ce|cette|le|la)|remplace (ce|cette|le|la)"
    r"|ajoute un commentaire|mets? (en|a) (majuscule|minuscule)"
    r"|combien|quelle? est|c'est quoi|ou (est|se trouve)|liste[sz]?|affiche[sz]?"
    r"|montre[sz]? moi|dis moi (juste|simplement)?)\b", re.I)

# Une question fermee, courte : « ça marche ? », « c'est fini ? ». Rien a raisonner.
FERMEE = re.compile(r"^\s*(est-ce que|c'est|ça|tu (as|peux)|il (y a|reste))\b.*\?\s*$", re.I)


# --- ce qui tire vers le HAUT ---------------------------------------------------------------
# Une ETENDUE : la tache touche plusieurs endroits, ou demande de les trouver.
ETENDUE = re.compile(
    r"\b(refactor\w*|architecture|refonte|r[ée][ée]cri\w*|restructur\w*|migration|migre\w*"
    r"|partout|l'ensemble|tout le (projet|code|codebase)|tous les (fichiers|cas|endroits)"
    r"|plusieurs (fichiers|endroits|modules)|de bout en bout|d'un bout a l'autre)\b", re.I)

# Une ENQUETE : on ne sait pas d'avance ou est la reponse, il faut chercher et comparer.
ENQUETE = re.compile(
    r"\b(pourquoi|diagnostic|diagnostique\w*|investigu\w*|creus\w*|analys\w*|comprend\w*"
    r"|explique\w* pourquoi|d'ou vient|cause|origine|reprodui\w*|intermittent|al[ée]atoire"
    r"|parfois|des fois|bizarre|etrange|inexplicable|ne marche (pas|plus))\b", re.I)

# Une PROFONDEUR demandee explicitement. C'est le signal le plus fiable : l'utilisateur dit
# lui-meme qu'il veut qu'on y passe du temps.
PROFONDEUR = re.compile(
    r"\b(en profondeur|approfondi\w*|minutieu\w*|exhaustif|exhaustive|rigoureu\w*"
    r"|prends? (tout )?le temps|prends? les ressources|assure[- ]toi|verifie bien"
    r"|ne (rate|manque) rien|tous les angles|quoi qu'il (en )?coute)\b", re.I)

# Une CONCEPTION : il faut arbitrer entre des possibilites avant d'ecrire quoi que ce soit.
CONCEPTION = re.compile(
    r"\b(con[çc]oi\w*|conception|architectur\w*|plani\w*|strat[ée]gie|approche"
    r"|compare\w*|choisi\w*|quelle (solution|option|approche)|meilleure? (fa[çc]on|maniere)"
    r"|trade[- ]?off|compromis)\b", re.I)


@dataclass
class Choix:
    """Le niveau retenu, et de quoi l'expliquer sans relire ce fichier."""

    niveau: str
    pourquoi: str

    @property
    def libelle(self) -> str:
        return LIBELLES.get(self.niveau, self.niveau)


def _compter_demandes(texte: str) -> int:
    """Combien de choses distinctes sont demandees.

    Une demande qui en contient trois est plus difficile qu'une demande qui en contient une,
    quel que soit son sujet — il faut les tenir toutes en meme temps, et ne pas en laisser
    tomber une en route. C'est le signal le plus honnete dont on dispose, parce qu'il ne
    depend d'aucun vocabulaire : il se compte.
    """
    marqueurs = re.findall(
        r"\b(et (aussi|ensuite|puis)|deuxi[èe]m\w*|troisi[èe]m\w*|premi[èe]rement"
        r"|d'abord|ensuite|par ailleurs|autre chose|aussi,)\b", texte, re.I)
    # Les listes dictees ressemblent a « un, deux, trois » ou « 1. 2. 3. ».
    puces = re.findall(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s+", texte)
    return 1 + len(marqueurs) + len(puces)


def evaluer(texte: str) -> Choix:
    """Le niveau d'effort que cette demande merite.

    L'ordre des tests n'est pas indifferent : on cherche d'abord ce qui justifie de MONTER,
    parce qu'une demande complexe mal servie coute un aller-retour entier, puis ce qui
    justifie de descendre. Entre les deux, « moyen » — qui repond correctement a presque
    tout, et c'est bien pour ça qu'il est le defaut.
    """
    propos = " ".join((texte or "").split())
    if not propos:
        return Choix("medium", "rien à évaluer")

    mots = len(propos.split())
    demandes = _compter_demandes(propos)

    # --- ce qui monte ----------------------------------------------------------------------
    if PROFONDEUR.search(propos):
        return Choix("xhigh", "tu demandes explicitement d'y passer du temps")
    if ETENDUE.search(propos) and ENQUETE.search(propos):
        return Choix("xhigh", "chercher, puis changer beaucoup de choses")
    # Le nombre de demandes passe AVANT les signaux de vocabulaire : quand un message en
    # contient quatre, c'est cela qui le rend difficile, et c'est cela qu'il faut dire. Une
    # demande dictee est compacte — pas de seuil de longueur, on compte ce qui est demande.
    if demandes >= 4:
        return Choix("xhigh", f"{demandes} demandes en une seule fois")
    if demandes >= 3:
        return Choix("high", f"{demandes} demandes à tenir ensemble")

    if ETENDUE.search(propos):
        return Choix("high", "la tâche touche plusieurs endroits")
    if ENQUETE.search(propos):
        return Choix("high", "il faut chercher avant de savoir")
    if CONCEPTION.search(propos):
        return Choix("high", "il faut arbitrer avant d'écrire")
    if demandes >= 2:
        return Choix("high", "deux demandes à tenir ensemble")

    # --- ce qui descend --------------------------------------------------------------------
    # Court ET mecanique : les deux, pas l'un ou l'autre. « corrige ça » est court mais peut
    # cacher n'importe quoi ; « renomme la variable truc en machin » est explicite.
    if FERMEE.match(propos) and mots <= 12:
        return Choix("low", "question fermée")
    if SIMPLE.search(propos) and mots <= 25:
        return Choix("low", "demande directe, sans recherche")
    if mots <= 6:
        return Choix("low", "demande très courte")

    return Choix("medium", "tâche ordinaire")
