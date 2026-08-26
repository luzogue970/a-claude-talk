#!/usr/bin/env python3
"""Regrouper les transcripts d'une meme conversation en un seul fichier.

Pourquoi ce script existe. Chaque LANCEMENT de l'agent ecrit son propre fichier, meme quand il
reprend la meme session Claude : sur cette machine, 23 fichiers pour 5 conversations. La liste
sait maintenant les regrouper — `journal.conversations()` — mais les FICHIERS restent
eparpilles, et relire une conversation demande d'en ouvrir sept dans le bon ordre.

Ce que ce script fait, et surtout ce qu'il ne fait pas :

- il ne touche PAS aux sessions de Claude Code. Elles vivent dans ~/.claude/projects, elles
  sont la source d'autorite du contexte, et rien ici ne les modifie. Le contexte n'a jamais
  ete perdu — ce sont nos transcripts de lecture qui etaient fragmentes.
- il ne fusionne QUE des lancements partageant un `session_id`. Sans identifiant commun, rien
  ne prouve que deux fichiers appartiennent a la meme conversation, et les coller inventerait
  une continuite qui n'existe pas.
- il ne supprime rien avant d'avoir ecrit et relu le fichier fusionne. Un transcript est une
  archive : le perdre pour gagner de la proprete serait un mauvais echange.
- il ne fait rien du tout sans `--vraiment`. Par defaut il montre ce qu'il ferait.

    ../.venv/bin/python voix/fusionner.py            # montre
    ../.venv/bin/python voix/fusionner.py --vraiment # fait
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import journal  # noqa: E402


def groupes(ici: str | None) -> list[tuple[str, list[dict]]]:
    """Les session_id ayant plus d'un fichier, du plus fourni au moins fourni."""
    par_sid: dict[str, list[dict]] = defaultdict(list)
    for lc in journal.historique(10_000, ici, sous_arbre=False):
        if lc.get("session_id"):
            par_sid[lc["session_id"]].append(lc)
    multiples = [(sid, sorted(l, key=lambda d: d.get("debut") or ""))
                 for sid, l in par_sid.items() if len(l) > 1]
    return sorted(multiples, key=lambda kv: -sum(d.get("tours", 0) for d in kv[1]))


def corps(fichier: Path) -> str:
    """Le contenu d'un transcript sans son en-tete ni son pied.

    On garde les tours et on jette les cadres : dans un fichier fusionne, sept en-tetes
    « # Conversation — projet » et sept « _fin ... _ » au milieu du texte rendraient la
    lecture plus penible que les sept fichiers separes.
    """
    try:
        texte = fichier.read_text(encoding="utf-8")
    except OSError:
        return ""
    # Le premier tour commence au premier « ## » ; tout ce qui precede est l'en-tete.
    i = texte.find("\n## ")
    interieur = texte[i + 1:] if i >= 0 else ""
    # Le pied commence a la derniere regle horizontale suivie de « _fin ».
    j = interieur.rfind("\n---\n")
    if j >= 0 and "_fin" in interieur[j:]:
        interieur = interieur[:j]
    return interieur.strip("\n")


def fusionner(sid: str, lancements: list[dict], vraiment: bool) -> str:
    racine = journal.RACINE
    fichiers = [racine / d["fichier"] for d in lancements]
    presents = [f for f in fichiers if f.is_file()]
    if len(presents) < 2:
        return f"  {sid[:8]} — un seul fichier présent, rien à faire"

    projet = lancements[-1].get("projet") or "conversation"
    debut = min(d.get("debut") or "" for d in lancements)
    # L'identifiant court fait partie du nom, et ce n'est pas decoratif : deux sessions du
    # meme projet commencees le meme jour visaient exactement le meme fichier, et la seconde
    # fusion ecrasait la premiere. Un script de rangement qui perd des donnees est pire que
    # le desordre qu'il corrige.
    cible = racine / f"{debut[:10]}_{projet}_{sid[:8]}_complet.md".replace("/", "-")

    morceaux = [
        f"# Conversation — {projet}",
        "",
        f"- **session** `{sid}`",
        f"- **début** {debut}",
        f"- **dernière activité** {max(d.get('maj') or '' for d in lancements)}",
        f"- **tours** {sum(d.get('tours', 0) for d in lancements)}",
        f"- **lancements** {len(presents)} — regroupés par `voix/fusionner.py`",
        f"- **chemin** {lancements[-1].get('chemin') or '?'}",
        "",
        "> Une seule conversation Claude, reprise plusieurs fois. Les séparateurs ci-dessous",
        "> marquent les reprises : le contexte, lui, n'a jamais été interrompu.",
        "",
    ]
    for n, (d, f) in enumerate(zip(lancements, fichiers), 1):
        if not f.is_file():
            continue
        interieur = corps(f)
        if not interieur:
            continue
        morceaux += ["---", "",
                     f"### reprise {n} — {d.get('debut', '?')} "
                     f"({d.get('tours', 0)} tour(s), `{f.name}`)", "",
                     interieur, ""]
    contenu = "\n".join(morceaux)

    if not vraiment:
        return (f"  {sid[:8]} — {len(presents)} fichiers → {cible.name} "
                f"({len(contenu) // 1024} Ko, {sum(d.get('tours', 0) for d in lancements)} tours)")

    cible.write_text(contenu, encoding="utf-8")
    # Relu avant toute suppression : ecrire n'est pas avoir ecrit. Un disque plein ou un
    # encodage refuse laisserait un fichier tronque, et supprimer les sources ensuite serait
    # une perte definitive pour rien.
    relu = cible.read_text(encoding="utf-8")
    if len(relu) < len(contenu) * 0.99:
        return f"  {sid[:8]} — ÉCHEC : le fichier fusionné est incomplet, sources gardées"
    garde = journal.RACINE / "avant-fusion"
    garde.mkdir(exist_ok=True)
    for f in presents:
        if f != cible:
            f.rename(garde / f.name)
    return (f"  {sid[:8]} — {len(presents)} fichiers → {cible.name} "
            f"(sources déplacées dans avant-fusion/)")


def principal() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vraiment", action="store_true",
                    help="écrire pour de vrai (sinon on montre seulement)")
    ap.add_argument("--ici", default=None, help="limiter à un dossier")
    a = ap.parse_args()

    g = groupes(a.ici)
    if not g:
        print("\n  Aucune conversation éparpillée : chaque session tient déjà en un fichier.\n")
        return 0

    total = sum(len(l) for _, l in g)
    print(f"\n  {len(g)} conversation(s) éparpillée(s) sur {total} fichiers.\n")
    for sid, lancements in g:
        print(fusionner(sid, lancements, a.vraiment))
    if not a.vraiment:
        print("\n  Rien n'a été écrit. « --vraiment » pour le faire.")
        print("  Les sessions Claude Code ne sont PAS touchées : le contexte ne dépend pas")
        print("  de ces fichiers, qui ne servent qu'à relire.\n")
    else:
        print("\n  Les fichiers d'origine sont dans conversations/avant-fusion/ —")
        print("  supprime-les quand tu auras vérifié.\n")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
