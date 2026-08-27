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
    ("consommation", "test_consommation.py", "py",
     "quotas des moteurs : ce qui est compté, ce qui prime dessus", False),
    ("fenetre", "test_fenetre.py", "py",
     "fenêtre avant envoi : décompte, retenue, ordres immédiats", False),
    ("fusion", "test_fusion.py", "py",
     "regroupement des transcrits d'une même conversation", False),
    ("micro", "test_micro_coupe.py", "py",
     "micro coupé = plus un octet vers le nuage (et la veille)", False),
    ("fil", "test_fil.py", "py",
     "les moteurs se construisent depuis le fil de travail", False),
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


# Les suites n'ont pas toutes le meme dialecte : certaines ecrivent « OK ... », d'autres
# « ok    ... », intentions ne rend qu'un bilan « 49/49 ». Un compteur qui n'en comprend
# qu'un seul affichait « VERTE 0 verif. » — un vert sur rien, la pire des sorties : il dit
# que c'est verifie alors qu'il n'a rien su lire.
import re as _re
_BILAN = _re.compile(r"^\s*[\w' ]+\s*:\s*(\d+)/(\d+)\s*$")


def compter(sortie: str) -> tuple[int, int]:
    """Combien de vérifications ont passé, combien ont échoué."""
    ok = ko = 0
    for l in sortie.splitlines():
        nu = l.lstrip()
        if nu.startswith(("OK", "ok ", "ok\t")):
            ok += 1
        elif nu.startswith(("ECHEC", "echec", "ÉCHEC")):
            ko += 1
        else:
            m = _BILAN.match(l)
            if m:
                passe, total = int(m.group(1)), int(m.group(2))
                ok += passe
                ko += total - passe
    return ok, ko


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("-k", metavar="MOTIFS",
                    help="ne lancer que les suites correspondantes ; plusieurs motifs "
                         "séparés par des virgules (« -k front,tableau »)")
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

    # Plusieurs motifs, parce que « -k front,tableau » est ce qu'on tape naturellement — et
    # qu'avant, cette forme ne correspondait a rien et rendait un vert sur zero test.
    motifs = [m.strip() for m in (a.k or "").split(",") if m.strip()]
    choisies = [s for s in SUITES
                if (not motifs or any(m in s[0] or m in s[1] for m in motifs))
                and not (a.rapide and s[4])]

    if a.liste:
        print(f"\n  {len(SUITES)} suites, par domaine :\n")
        for nom, fichier, genre, quoi, lourde in SUITES:
            marque = " (lourde)" if lourde else ""
            print(f"  {nom:<13} {quoi}{marque}")
        print()
        return 0

    # Un feu vert sur zero test est le pire resultat possible : il dit « rien de casse »
    # alors que rien n'a ete verifie. On refuse plutot que de rassurer a tort.
    if not choisies:
        connues = ", ".join(s[0] for s in SUITES)
        print(f"\n  Aucune suite ne correspond à « {a.k} ».\n  Suites : {connues}\n",
              file=sys.stderr)
        return 2

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
        elif code == 0 and ok == 0:
            # Sortie zero mais rien de compte : soit la suite ne verifie plus rien, soit elle
            # parle un dialecte que `compter` ne lit pas. Dans les deux cas ce n'est pas une
            # preuve, et l'appeler VERTE serait mentir sur la couverture.
            etat = "MUETTE"
        elif code == 0:
            etat = "VERTE"
        else:
            etat = "ROUGE"
        resultats.append((nom, etat, ok, ko, duree, quoi))
        signe = {"VERTE": "·", "ROUGE": "!", "IGNORÉE": "~", "MUETTE": "?"}[etat]
        print(f"  {signe} {nom:<13} {etat:<8} {ok:>4} vérif."
              + (f"  {ko} ÉCHEC" if ko else "") + f"   {duree:5.1f} s")
        if etat == "ROUGE" or a.verbeux:
            for l in sortie.splitlines():
                if a.verbeux or l.lstrip().startswith(("ECHEC", "Traceback", "  File", "    ")):
                    print(f"        {l}")

    print(f"\n  ── couverture par domaine ──\n")
    for nom, etat, ok, ko, duree, quoi in resultats:
        print(f"  {nom:<13} {etat:<8} {quoi}")

    muettes = [r for r in resultats if r[1] == "MUETTE"]
    if muettes:
        print("\n  MUETTE : " + ", ".join(r[0] for r in muettes)
              + " — sortie verte mais aucune vérification comptée.")
    rouges = [r for r in resultats if r[1] == "ROUGE"]
    absentes = [r for r in resultats if r[1] == "ABSENTE"]
    print(f"\n  {total_ok} vérifications passées, {total_ko} en échec, "
          f"{len(rouges)} suite(s) rouge(s)"
          + (f", {len(absentes)} absente(s)" if absentes else ""))
    if rouges:
        print("  ROUGE : " + ", ".join(r[0] for r in rouges))
        print("\n  Ne pas fusionner tant qu'une suite est rouge.")
    elif total_ok == 0:
        # Meme garde-fou une couche plus bas : des suites choisies mais toutes ignorees
        # (Chrome absent, aucune cle) ne prouvent rien non plus.
        print("\n  Aucune vérification n'a tourné — rien n'est prouvé. "
              "Vérifier Chrome et les clés.")
        return 2
    else:
        print("\n  Rien de cassé. Bon pour la fusion.")
    return 1 if rouges or absentes or muettes else 0


if __name__ == "__main__":
    sys.exit(main())
