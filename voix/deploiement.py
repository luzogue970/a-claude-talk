"""Quelle version de claude-talk cette conversation fait-elle tourner.

Une page qui n'affiche aucun numero ne peut pas repondre a la seule question qui
compte quand on vient de corriger quelque chose : « est-ce que ce que je tiens dans
la main contient ma correction ? ». Le tableau de bord savait le dire pour les apps ;
les conversations, elles, ne disaient rien — et comme un agent charge son code au
demarrage et ne le relit jamais, une session ouverte depuis la veille continuait de
tourner sur le code de la veille sans que rien ne le signale.

Le principe tient en une phrase : on FIGE au demarrage ce qui est charge, et on relit
le disque a la demande. L'ecart entre les deux est exactement « ce qui est ecrit mais
pas encore en service ».
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        r = subprocess.run(("git", "-C", str(RACINE), *args),
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _version_disque() -> str:
    f = RACINE / "VERSION"
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# Fige a l'import, c'est-a-dire au demarrage de l'agent : ces trois valeurs decrivent le
# code REELLEMENT en memoire, et elles ne doivent plus jamais bouger ensuite.
VERSION = _version_disque()
COMMIT = _git("rev-parse", "HEAD")
DEMARRE = time.time()


_CACHE: dict = {}
_CACHE_QUAND = 0.0
_FRAICHEUR = 30.0          # secondes


def etat() -> dict:
    """L'ecart entre ce qui tourne et ce qui est ecrit, pret a publier.

    Mis en cache : la reponse voyage avec le pouls, qui part toutes les vingt secondes,
    et deux appels a git par pouls pour une valeur qui ne bouge qu'a un commit serait
    payer cher une information immobile.
    """
    global _CACHE, _CACHE_QUAND
    maintenant = time.time()
    if _CACHE and maintenant - _CACHE_QUAND < _FRAICHEUR:
        return _CACHE

    disque = _version_disque()
    tete = _git("rev-parse", "HEAD")
    # Le nombre de commits ecrits depuis le demarrage. Zero veut dire « a jour », et c'est
    # la seule chose que la pastille a besoin de savoir pour choisir sa couleur.
    retard = 0
    if COMMIT and tete and COMMIT != tete:
        compte = _git("rev-list", "--count", f"{COMMIT}..{tete}")
        retard = int(compte) if compte.isdigit() else 1
    _CACHE = {
        "version": VERSION or "?",
        "version_disque": disque or "?",
        "commit": COMMIT[:8],
        "depuis": DEMARRE,
        "retard": retard,
        "a_jour": retard == 0 and (not disque or disque == VERSION),
    }
    _CACHE_QUAND = maintenant
    return _CACHE


def oublier() -> None:
    """Forcer la prochaine lecture a repasser par git — apres un commit, par exemple."""
    global _CACHE_QUAND
    _CACHE_QUAND = 0.0
