"""Les reglages generaux de claude-talk : un seul endroit pour toutes les conversations.

Chaque reglage avait sa variable d'environnement, posee au lancement par
`outils/claude-talk-session` — un script shell d'un AUTRE depot. Changer le modele par
defaut demandait donc d'editer ce script, puis de relancer chaque conversation une par une.
Ce fichier tient le meme role, en donnees : le tableau de bord l'ecrit, les sessions neuves
le lisent au demarrage, et les sessions deja ouvertes recoivent l'ordre correspondant par
leur port — c'est ce qui rend le changement immediat au lieu de « a la prochaine fois ».

La regle de priorite est celle des secrets, et pour la meme raison : **une variable
d'environnement posee a la main gagne toujours sur le fichier**. Lancer une session a part
avec `VOIX_WORKER_MODEL=claude-haiku-4-5 voix/agent.py ...` doit marcher sans toucher au
reglage general, et sans que le reglage general l'ecrase une seconde plus tard.

Ce que ce fichier ne contient jamais : une cle, un jeton, un mot de passe. Les secrets
restent dans `secrets.env`, qui n'est ecrit par personne d'autre que moi — surtout pas par
une page web.
"""

import json
import os
from pathlib import Path

FICHIER = Path(os.environ.get("VOIX_REGLAGES")
               or Path.home() / ".config" / "claude-talk" / "reglages.json")

# cle du fichier -> variable d'environnement correspondante. Les valeurs sont celles que la
# variable attend, telles quelles : le modele s'ecrit « claude-opus-5 » et non « opus ».
# Stocker la cle courte aurait oblige ce module a embarquer la table des modeles, donc a la
# tenir a jour en double de config.MODELES — et une table en double finit toujours par
# diverger. config.cle_du_modele() fait la traduction inverse la ou on en a besoin.
VARIABLES = {
    "modele": "VOIX_WORKER_MODEL",
    "effort": "VOIX_WORKER_EFFORT",
    "effort_auto": "VOIX_EFFORT_AUTO",
    "ecoute_min": "VOIX_ECOUTE_MIN",
    "modele_parole": "VOIX_SPEAKER_MODEL",
    "voix": "AZURE_SPEECH_VOICE",
    "permission": "VOIX_PERMISSION",
    "retenir_occupe": "VOIX_RETENIR_OCCUPE",
    "tour_manuel": "VOIX_TOUR_MANUEL",
}


def _texte(valeur) -> str:
    """Un reglage JSON vers ce qu'attend une variable d'environnement.

    `True` doit devenir « 1 », pas « True ». Les lectures de config.py testent
    l'appartenance a ("0", "non", "false") : « True » y echapperait et donnerait le bon
    resultat, mais par accident — et « False » donnerait VRAI, ce qui est le defaut exact
    qu'on ne verrait jamais en relisant le fichier, puisqu'il y est ecrit correctement.

    Et un flottant s'ecrit « 5 » plutot que « 5.0 » : les deux se relisent, mais c'est le
    premier que la page affiche et que je retrouve dans l'environnement du processus.
    """
    if isinstance(valeur, bool):
        return "1" if valeur else "0"
    if isinstance(valeur, float):
        return f"{valeur:g}"
    return str(valeur)


def lire() -> dict:
    """Le contenu du fichier, ou {} — jamais une exception.

    Un fichier absent est le cas normal au premier lancement. Un fichier tronque par un
    disque plein ne doit pas davantage empecher une session de demarrer : elle repart sur
    ses valeurs d'origine, ce qui est exactement ce qu'on attend d'un reglage illisible.
    """
    try:
        donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return donnees if isinstance(donnees, dict) else {}


def appliquer(donnees: dict | None = None) -> dict:
    """Poser les reglages dans l'environnement sans ecraser ce qui s'y trouve deja.

    Rend ce qui a ete propose, variable par variable — y compris ce qui a ete refuse parce
    que l'environnement avait deja son mot a dire. Le dire plutot que de le taire : c'est la
    seule facon de comprendre pourquoi une session n'a pas le modele qu'affiche le tableau.
    """
    donnees = lire() if donnees is None else donnees
    proposes = {}
    for cle, variable in VARIABLES.items():
        valeur = donnees.get(cle)
        if valeur is None:
            continue
        texte = _texte(valeur)
        if not texte:
            continue
        proposes[variable] = texte
        # Pas `setdefault` : une variable PRESENTE MAIS VIDE n'est pas un choix, c'est un
        # oubli — un `export VOIX_ECOUTE_MIN=` laisse dans un script, par exemple. Et elle
        # ne se contentait pas de bloquer le reglage : `float("")` fait tomber config.py a
        # l'import, donc la session ne demarre pas du tout, sur un message qui ne nomme ni
        # le script fautif ni la variable.
        if not (os.environ.get(variable) or "").strip():
            os.environ[variable] = texte
    return proposes
