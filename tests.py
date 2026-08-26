#!/usr/bin/env python3
"""Toutes les suites, en une commande. À passer avant chaque fusion.

    ./tests.py              # tout
    ./tests.py -k tableau   # les suites dont le nom contient « tableau »
    ./tests.py --liste      # ce qui existe, et ce que ça couvre

Le tableau final dit ce qui est couvert PAR DOMAINE, pas seulement combien de tests passent :
un compte global rassure sans informer, alors qu'un domaine à zéro test se voit.

Certaines suites ont des exigences externes et s'abstiennent proprement plutôt que d'échouer :
`test_rendu` a besoin de Chrome, `test_noyau` et `test_boucle` appellent le vrai Claude Code
(donc consomment du quota), `test_conversation` synthétise de l'audio via Azure. Une suite
absente est signalée comme telle, jamais confondue avec une suite verte.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent
VOIX = RACINE / "voix"
PYTHON = RACINE / ".venv" / "bin" / "python"

# domaine -> ce qu'il protège. L'ordre va du plus isolé au plus intégré : une erreur de base
# se lit mieux avant les échecs en cascade qu'elle provoque.
SUITES = [
    ("intentions", "test_intentions.py", "py", "ordres locaux : toute formulation, et ce qui n'en est PAS un", False),
    ("journal", "test_journal.py", "py", "historique : portée par dossier, états, reprise", False),
    ("pupitre", "test_pupitre.py", "py", "conversations parallèles : baux micro et parole", False),
    ("tableau", "test_tableau.py", "py", "serveur du tableau : état, commandes, rejeu, retenue", False),
    ("front", "test_front.js", "node", "interface, sans navigateur : dictée, filtres, indicateurs", False),
    ("rendu", "test_rendu.js", "node", "mise en page réelle, mesurée dans Chrome", True),
    ("noyau", "test_noyau.py", "py", "worker et porte-parole, avec le vrai Claude (coûte du quota)", True),
    ("boucle", "test_boucle.py", "py", "boucle vocale complète via AgentSession.run()", True),
    ("conversation", "test_conversation.py", "py", "assistant « Hey Claude » (désactivé) — audio via Azure", True),
]


def lancer(fichier: str, genre: str) -> tuple[int, str, float]:
    debut = time.monotonic()
    cmd = [str(PYTHON), fichier] if genre == "py" else ["node", fichier]
    try:
        r = subprocess.run(cmd, cwd=VOIX, capture_output=True, text=True, timeout=900)
        return r.returncode, (r.stdout + r.stderr), time.monotonic() - debut
    except subprocess.TimeoutExpired:
        return 124, "délai dépassé (900 s)", time.monotonic() - debut
    except FileNotFoundError as e:
        return 127, f"outil manquant : {e}", time.monotonic() - debut


def compter(sortie: str) -> tuple[int, int]:
    """Combien de vérifications ont passé, combien ont échoué."""
    ok = sum(1 for l in sortie.splitlines() if l.lstrip().startswith("OK"))
    ko = sum(1 for l in sortie.splitlines() if l.lstrip().startswith("ECHEC"))
    return ok, ko


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("-k", metavar="MOTIF", help="ne lancer que les suites correspondantes")
    ap.add_argument("--liste", action="store_true", help="lister sans rien lancer")
    ap.add_argument("--rapide", action="store_true",
                    help="sauter les suites qui coûtent du quota ou exigent Chrome")
    ap.add_argument("-v", "--verbeux", action="store_true", help="tout afficher")
    a = ap.parse_args()

    if not PYTHON.exists():
        print(f"venv introuvable : {PYTHON}", file=sys.stderr)
        print("  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
              file=sys.stderr)
        return 2

    choisies = [s for s in SUITES
                if (not a.k or a.k in s[0] or a.k in s[1])
                and not (a.rapide and s[4])]

    if a.liste:
        print(f"\n  {len(SUITES)} suites, par domaine :\n")
        for nom, fichier, genre, quoi, lourde in SUITES:
            marque = " (lourde)" if lourde else ""
            print(f"  {nom:<13} {quoi}{marque}")
        print()
        return 0

    print(f"\n  {len(choisies)} suite(s) — « ./tests.py --liste » pour le détail\n")
    resultats, total_ok, total_ko = [], 0, 0
    for nom, fichier, genre, quoi, lourde in choisies:
        if not (VOIX / fichier).exists():
            resultats.append((nom, "ABSENTE", 0, 0, 0.0, quoi))
            continue
        code, sortie, duree = lancer(fichier, genre)
        ok, ko = compter(sortie)
        total_ok += ok
        total_ko += ko
        # Une suite qui s'abstient (Chrome absent, pas de clé) n'est pas une suite verte :
        # la confondre donnerait une couverture imaginaire.
        if code == 0 and ok == 0 and ("ignore" in sortie or "abstient" in sortie):
            etat = "IGNORÉE"
        elif code == 0:
            etat = "VERTE"
        else:
            etat = "ROUGE"
        resultats.append((nom, etat, ok, ko, duree, quoi))
        signe = {"VERTE": "·", "ROUGE": "!", "IGNORÉE": "~"}[etat]
        print(f"  {signe} {nom:<13} {etat:<8} {ok:>4} vérif."
              + (f"  {ko} ÉCHEC" if ko else "") + f"   {duree:5.1f} s")
        if etat == "ROUGE" or a.verbeux:
            for l in sortie.splitlines():
                if a.verbeux or l.lstrip().startswith(("ECHEC", "Traceback", "  File", "    ")):
                    print(f"        {l}")

    print(f"\n  ── couverture par domaine ──\n")
    for nom, etat, ok, ko, duree, quoi in resultats:
        print(f"  {nom:<13} {etat:<8} {quoi}")

    rouges = [r for r in resultats if r[1] == "ROUGE"]
    absentes = [r for r in resultats if r[1] == "ABSENTE"]
    print(f"\n  {total_ok} vérifications passées, {total_ko} en échec, "
          f"{len(rouges)} suite(s) rouge(s)"
          + (f", {len(absentes)} absente(s)" if absentes else ""))
    if rouges:
        print("  ROUGE : " + ", ".join(r[0] for r in rouges))
        print("\n  Ne pas fusionner tant qu'une suite est rouge.")
    else:
        print("\n  Rien de cassé. Bon pour la fusion.")
    return 1 if rouges or absentes else 0


if __name__ == "__main__":
    sys.exit(main())
