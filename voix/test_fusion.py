#!/usr/bin/env python3
"""La fusion des transcripts eparpilles : ce qu'elle regroupe, et ce qu'elle refuse.

Un script qui deplace des fichiers doit etre teste avant d'etre propose. Ce qui est verifie
ici est surtout ce qu'il NE doit pas faire — un rangement qui perd une archive est pire que le
desordre qu'il corrige :

- ne fusionner que des lancements partageant un session_id ;
- ne jamais viser deux fois le meme fichier de sortie ;
- ne rien supprimer avant d'avoir relu ce qui a ete ecrit ;
- ne rien faire du tout sans qu'on le demande explicitement.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def principal():
    boite = Path(tempfile.mkdtemp(prefix="fusion-"))
    os.environ["VOIX_JOURNAL"] = str(boite)
    import journal
    journal.RACINE = boite
    journal.INDEX = boite / "index.jsonl"
    import fusionner
    fusionner.journal = journal

    def lancement(projet, sid, chemin, tours=2):
        c = journal.Conversation(projet, "claude-opus-5", "xhigh", chemin=chemin)
        if sid:
            c.note_session(sid)
        for i in range(tours):
            c.tour_utilisateur(f"question {i} de {projet}")
            c.tour_claude(f"reponse {i}")
        c.clore("test")
        time.sleep(0.01)
        return c

    # Trois lancements d'UNE conversation, deux lancements d'une autre, et deux sans identite.
    for _ in range(3):
        lancement("projet", "sess-A", "/w/projet")
    for _ in range(2):
        lancement("projet", "sess-B", "/w/projet")
    lancement("orphelin", None, "/w/projet")
    lancement("orphelin", None, "/w/projet")

    print("=== ce qui est reconnu comme eparpille ===")
    g = dict(fusionner.groupes(None))
    dire(set(g) == {"sess-A", "sess-B"},
         f"seules les sessions a plusieurs fichiers : {sorted(g)}")
    dire(len(g["sess-A"]) == 3 and len(g["sess-B"]) == 2,
         "et chacune connait ses lancements")
    dire(all(d.get("session_id") for l in g.values() for d in l),
         "aucun lancement sans identite : rien ne prouverait leur lien")

    print("\n=== deux sorties ne peuvent pas se marcher dessus ===")
    # Le defaut qu'on a eu : meme projet, meme jour de debut, donc meme nom de fichier — et
    # la seconde fusion ecrasait la premiere.
    noms = set()
    for sid, lancements in g.items():
        ligne = fusionner.fusionner(sid, lancements, vraiment=False)
        noms.add(ligne.split("→")[1].split("(")[0].strip())
    dire(len(noms) == 2, f"deux sessions du meme projet visent deux fichiers : {sorted(noms)}")

    print("\n=== a blanc, rien n'est ecrit ===")
    avant = sorted(f.name for f in boite.glob("*.md"))
    for sid, lancements in g.items():
        fusionner.fusionner(sid, lancements, vraiment=False)
    dire(sorted(f.name for f in boite.glob("*.md")) == avant,
         f"les {len(avant)} fichiers sont intacts")
    dire(not (boite / "avant-fusion").exists(), "et aucune archive n'est creee")

    print("\n=== pour de vrai ===")
    resultats = [fusionner.fusionner(sid, l, vraiment=True) for sid, l in g.items()]
    dire(not any("ÉCHEC" in r for r in resultats), f"les deux fusions aboutissent")
    complets = sorted(f.name for f in boite.glob("*_complet.md"))
    dire(len(complets) == 2, f"deux fichiers regroupes : {complets}")

    fusionne = (boite / complets[0]).read_text(encoding="utf-8")
    dire("### reprise 1" in fusionne and "### reprise 2" in fusionne,
         "les reprises sont marquees : le contexte n'a pas ete interrompu, la lecture si")
    dire(fusionne.count("# Conversation —") == 1,
         "un seul en-tete, pas un par lancement — sinon la lecture serait pire qu'avant")
    dire("_fin " not in fusionne,
         "et aucun pied de page au milieu du texte")
    dire("question 0 de projet" in fusionne and "reponse 1" in fusionne,
         "tous les tours sont la, questions et reponses")

    garde = boite / "avant-fusion"
    dire(garde.is_dir() and len(list(garde.glob("*.md"))) == 5,
         f"les 5 sources sont archivees, pas supprimees "
         f"({len(list(garde.glob('*.md'))) if garde.is_dir() else 0})")

    # Les orphelins n'ont pas ete touches : ils sont encore a leur place.
    dire(len(list(boite.glob("*orphelin*.md"))) == 2,
         "les lancements sans identite restent ou ils sont")

    print("\n=== une ecriture tronquee ne detruit rien ===")
    # Le garde-fou qui compte : ecrire n'est pas avoir ecrit. Un disque plein laisserait un
    # fichier incomplet, et supprimer les sources ensuite serait une perte definitive.
    boite2 = Path(tempfile.mkdtemp(prefix="fusion2-"))
    journal.RACINE = boite2
    journal.INDEX = boite2 / "index.jsonl"
    for _ in range(2):
        lancement("p2", "sess-C", "/w/p2")
    g2 = dict(fusionner.groupes(None))
    vrai_write = Path.write_text

    def write_tronque(self, contenu, **kw):
        return vrai_write(self, contenu[: len(contenu) // 3], **kw)

    Path.write_text = write_tronque
    try:
        r = fusionner.fusionner("sess-C", g2["sess-C"], vraiment=True)
    finally:
        Path.write_text = vrai_write
    dire("ÉCHEC" in r, f"l'ecriture incomplete est detectee : {r.strip()}")
    dire(len(list(boite2.glob("*p2*.md"))) >= 2,
         "et les sources sont gardees plutot que sacrifiees")
    dire(not (boite2 / "avant-fusion").exists(), "aucune archive : rien n'a bouge")

    shutil.rmtree(boite, ignore_errors=True)
    shutil.rmtree(boite2, ignore_errors=True)
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(principal())
