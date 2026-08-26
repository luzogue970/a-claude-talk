"""Le filtre par sous-arbre de l'historique des conversations.

Ce que ce fichier protege : `vvconv` ne doit montrer que ce qui a ete lance depuis le dossier
courant ou l'un de ses descendants. Depuis la racine on voit tout, depuis un projet on ne voit
que lui.

Les deux cas qui comptent :

- **le piege du prefixe** : « /a/projet-autre » commence par « /a/projet » sans en etre un
  descendant. Une comparaison de chaines naive le ferait apparaitre dans la liste d'un autre
  projet, ce qui est exactement le melange qu'on cherche a eviter.
- **ce qui ne doit PAS etre filtre** : `actives()` arbitre le micro entre toutes les
  conversations vivantes ou qu'elles soient, et `resoudre()` sert a reprendre par identifiant
  depuis n'importe quel dossier. Les filtrer rendrait la premiere aveugle et la seconde
  inutilisable.

    ../.venv/bin/python voix/test_journal.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def principal():
    boite = Path(tempfile.mkdtemp(prefix="journal-"))
    os.environ["VOIX_JOURNAL"] = str(boite)
    import journal
    journal.RACINE = boite
    journal.INDEX = boite / "index.jsonl"

    lieux = [
        ("racine-projet", "/a/projet"),
        ("sous-dossier", "/a/projet/bridge"),
        ("plus-profond", "/a/projet/app/src"),
        ("voisin-piege", "/a/projet-autre"),   # meme prefixe, PAS un descendant
        ("ailleurs", "/a/rien"),
    ]
    for nom, chemin in lieux:
        c = journal.Conversation(nom, "claude-opus-5", "xhigh", chemin=chemin)
        c.note_session(f"sess-{nom}")
        c.tour_utilisateur("x")
        c.tour_claude("y")
        time.sleep(0.01)

    def noms(ici, sous_arbre=True):
        return sorted(d["projet"]
                      for d in journal.historique(50, ici, sous_arbre=sous_arbre))

    print("=== depuis la racine du projet ===")
    v = noms("/a/projet")
    dire(v == ["plus-profond", "racine-projet", "sous-dossier"], f"vu : {v}")
    dire("voisin-piege" not in v,
         "« projet-autre » n'est pas un descendant de « projet » (piege du prefixe)")
    dire("ailleurs" not in v, "un dossier sans rapport est exclu")

    print("\n=== depuis un sous-dossier ===")
    dire(noms("/a/projet/app") == ["plus-profond"], "seulement ce qui est dessous")

    print("\n=== depuis un parent commun ===")
    dire(len(noms("/a")) == 5, "tout redevient visible")

    print("\n=== sans filtre ===")
    dire(len(noms("/a/projet/app", sous_arbre=False)) == 5,
         "sous_arbre=False montre tout, comme avant")

    print("\n=== le sous-chemin est relatif ===")
    sous = {d["projet"]: d.get("sous")
            for d in journal.historique(50, "/a/projet", sous_arbre=True)}
    dire(sous.get("sous-dossier") == "bridge" and sous.get("plus-profond") == "app/src",
         f"chemins relatifs : {sous}")
    dire(sous.get("racine-projet") == "", "le dossier courant n'a pas de sous-chemin")

    print("\n=== ce qui ne doit pas etre filtre ===")
    ancien = os.getcwd()
    os.chdir("/tmp")
    try:
        d = journal.resoudre("sess-ailleurs")
        dire(d is not None and d["projet"] == "ailleurs",
             "resoudre() trouve une conversation d'ailleurs — sinon vvreprendre casse")
        # actives() n'a pas de conversation vivante ici (aucun PID d'agent), mais elle doit
        # regarder l'ensemble : on verifie qu'elle n'applique pas le filtre.
        import inspect
        corps = inspect.getsource(journal.actives)
        dire("sous_arbre" not in corps,
             "actives() n'applique pas le filtre : elle arbitre le micro partout")
    finally:
        os.chdir(ancien)

    print("\n=== l'apercu dit ce qu'il masque ===")
    texte = "\n".join(journal.apercu(50, ici="/a/projet"))
    dire("vvconv --tout" in texte, "il annonce comment voir le reste")
    dire("2 conversation" in texte, f"et combien sont masquees : "
         + [l for l in texte.splitlines() if "--tout" in l][0].strip())
    vide = "\n".join(journal.apercu(50, ici="/a/nulle-part"))
    dire("aucune conversation lancée depuis ici" in vide,
         "un dossier sans historique le dit sans laisser croire a une perte")

    # --- une conversation, pas un lancement ---------------------------------------------
    # Le defaut qu'on corrige : rouvrir l'agent six fois sur le meme projet en reprenant la
    # meme session Claude ecrivait six lignes pour UNE conversation. Sur la vraie machine,
    # 23 lignes pour 5 conversations : on croyait que les conversations se multipliaient et
    # qu'on perdait le contexte, alors que le contexte etait intact et que c'est la LISTE qui
    # comptait mal.
    print("\n=== une ligne par conversation, pas par lancement ===")
    for i in range(3):
        c = journal.Conversation("repris", "claude-opus-5", "xhigh", chemin="/a/projet/repris")
        c.note_session("sess-partagee")          # LA MEME session, trois lancements
        c.tour_utilisateur(f"question {i}")
        c.tour_claude("reponse")
        time.sleep(0.01)

    lancements = [d for d in journal.historique(50, "/a/projet") if d["projet"] == "repris"]
    convs = [d for d in journal.conversations("/a/projet") if d["projet"] == "repris"]
    dire(len(lancements) == 3, f"l'index garde bien les 3 lancements ({len(lancements)})")
    dire(len(convs) == 1, f"mais ils ne font qu'UNE conversation ({len(convs)})")
    dire(convs[0]["reprises"] == 3, "elle sait en combien de fois elle a ete reprise")
    dire(convs[0]["tours"] == 3,
         f"et ses tours sont additionnes sur tous ses lancements ({convs[0]['tours']})")

    # Le debut est le PLUS ANCIEN, la mise a jour la PLUS RECENTE : la conversation s'etend
    # sur toute sa duree, ce n'est pas une suite de conversations courtes.
    dire(convs[0]["debut"] <= min(d["debut"] for d in lancements),
         "son debut est celui du premier lancement")
    dire(convs[0]["maj"] >= max(d["maj"] for d in lancements),
         "sa derniere activite est celle du dernier")

    # Sans identifiant de session il n'y a pas d'identite : les fusionner inventerait une
    # continuite qui n'existe pas.
    # tour_claude et pas seulement tour_utilisateur : c'est la reponse qui declenche
    # l'indexation, une question restee sans reponse n'est indexee qu'a la fermeture.
    for nom in ("anonyme-a", "anonyme-b"):
        c = journal.Conversation(nom, "claude-opus-5", "xhigh", chemin="/a/projet/anon")
        c.tour_utilisateur("x")
        c.tour_claude("y")
        time.sleep(0.01)
    anons = [d for d in journal.conversations("/a/projet")
             if str(d["projet"]).startswith("anonyme")]
    dire(len(anons) == 2,
         "deux lancements sans session_id restent deux entrees : rien ne prouve leur lien")

    # --- le rang designe ce qui est affiche ---------------------------------------------
    # C'etait faux, et le symptome ressemblait a « une conversation qui repart toute seule » :
    # l'affichage numerotait une liste restreinte au sous-arbre, la resolution cherchait dans
    # la liste complete. « vvreprendre 3 » pouvait donc reprendre une AUTRE conversation.
    print("\n=== le rang resolu est celui qu'on a vu ===")
    for depuis in ("/a/projet", "/a/projet/app", "/a"):
        vus = journal.conversations(depuis)
        # Comparaison sur l'identite METIER, pas sur l'objet : chaque appel reconstruit ses
        # dictionnaires, donc `is` serait toujours faux et le test toujours vert par accident.
        def signe(d):
            return (d or {}).get("session_id") or (d or {}).get("fichier")
        bon = all(signe(journal.resoudre(str(i), ici=depuis)) == signe(vus[i - 1])
                  for i in range(1, len(vus) + 1))
        dire(bon, f"depuis {depuis} : les {len(vus)} rangs designent les lignes affichees")

    # Un identifiant, lui, marche de PARTOUT : exiger le bon dossier pour l'utiliser
    # demanderait l'information qu'on vient justement chercher.
    d = journal.resoudre("sess-partagee", ici="/a/rien")
    dire(d is not None and d["session_id"] == "sess-partagee",
         "un identifiant se resout depuis n'importe quel dossier")

    # --- ce qu'on reprend par defaut ----------------------------------------------------
    print("\n=== la reprise par defaut ===")
    d = journal.derniere_conversation("/a/projet/repris")
    dire(d is not None and d["session_id"] == "sess-partagee",
         "dans un dossier connu, on reprend sa derniere conversation")
    dire(journal.derniere_conversation("/a/vide-jamais-vu") is None,
         "dans un dossier inconnu, il n'y a rien a reprendre — donc une conversation neuve")
    dire(journal.derniere_conversation("/a/projet/anon") is None,
         "une conversation sans session_id n'est pas reprenable : Claude n'en a pas trace")

    shutil.rmtree(boite, ignore_errors=True)
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(principal())
