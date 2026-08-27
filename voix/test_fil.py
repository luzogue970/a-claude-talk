#!/usr/bin/env python3
"""Les moteurs se construisent depuis le FIL DE TRAVAIL, pas seulement le fil principal.

Le defaut que ce fichier existe pour empecher, tel qu'il s'est produit : la chaine
s'annonçait « AssemblyAI → Deepgram → Speechmatics → Gladia → local → Soniox → Azure » et il
ne restait que Deepgram et le local. Cinq moteurs sur sept ecartes, chacun avec un
« RuntimeError: Plugins must be registered on the main thread » noye dans un mur de traces au
demarrage.

La cause : un plugin LiveKit s'enregistre A L'IMPORT, et `Plugin.register_plugin` refuse si on
n'est pas sur le fil principal. Or l'agent tourne dans un fil de travail (JobExecutorType
.THREAD). Un `from livekit.plugins import X` ecrit paresseusement dans `construire()` arrive
donc toujours au mauvais moment, sur le mauvais fil.

Pourquoi RIEN ne l'avait vu : le banc d'essai, les tests, mes verifications a la main —
tout tournait sur le fil principal. Le seul contexte ou le defaut se produit est
l'application reelle. C'est la meme lecon que la cle AssemblyAI lue avec `source` en bash au
lieu du parseur de l'application : verifier autrement que l'application ne verifie rien.

Ce test reproduit donc le contexte reel, et rien d'autre.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def dans_un_fil(fonction):
    """Executer sur un fil secondaire, comme le fait le job runner de LiveKit."""
    sortie = {}

    def courir():
        try:
            sortie["valeur"] = fonction()
        except BaseException as exc:            # noqa: BLE001 — on veut TOUT capturer
            sortie["erreur"] = exc

    t = threading.Thread(target=courir)
    t.start()
    t.join(120)
    if "erreur" in sortie:
        raise sortie["erreur"]
    return sortie.get("valeur")


def principal():
    import moteurs_stt

    print("=== le fil de travail est bien un fil secondaire ===")
    dire(dans_un_fil(lambda: threading.current_thread() is not threading.main_thread()),
         "le harnais reproduit bien le contexte de l'agent")

    print("\n=== sans prechargement, un plugin non importe refuse ===")
    # On ne peut pas « desimporter » proprement un plugin deja enregistre, donc on verifie la
    # REGLE de LiveKit elle-meme : c'est elle qui condamne, et elle ne changera pas sans
    # qu'on le sache.
    from livekit.agents.plugin import Plugin
    import inspect
    regle = inspect.getsource(Plugin.register_plugin)
    dire("main_thread" in regle and "RuntimeError" in regle,
         "LiveKit refuse toujours d'enregistrer un plugin hors du fil principal")

    print("\n=== avec prechargement, chaque moteur se construit dans le fil ===")
    charges, refuses = moteurs_stt.precharger()      # sur le fil principal, comme l'agent
    for cle, pourquoi in refuses:
        print(f"  ·     {cle} sans plugin utilisable : {pourquoi}")
    dire(bool(charges), f"des plugins sont precharges : {', '.join(charges) or 'aucun'}")

    from livekit.plugins import silero
    vad = dans_un_fil(silero.VAD.load) if False else None   # le VAD n'est pas requis ici

    dispos = [c for c in moteurs_stt.chaine() if c != "local"]
    dire(bool(dispos), f"{len(dispos)} moteur(s) en ligne dans la chaine")
    for cle in dispos:
        try:
            m = dans_un_fil(lambda c=cle: moteurs_stt.construire(c, vad))
            dire(m is not None, f"« {cle} » se construit depuis le fil de travail")
        except Exception as exc:
            fatal = "main thread" in str(exc)
            dire(not fatal,
                 f"« {cle} » : {type(exc).__name__}: {str(exc)[:90]}"
                 + (" ← C'EST LE DEFAUT DU FIL" if fatal else " (autre cause)"))

    print("\n=== et la chaine reelle n'est pas amputee ===")
    # Le symptome visible du defaut : une chaine annoncee complete et vide en pratique.
    annoncee = moteurs_stt.chaine()
    survivants = []
    for cle in annoncee:
        try:
            dans_un_fil(lambda c=cle: moteurs_stt.construire(c, vad))
            survivants.append(cle)
        except Exception as exc:
            if "main thread" not in str(exc):
                survivants.append(cle)          # echec pour une autre raison : pas notre sujet
    perdus = [c for c in annoncee if c not in survivants]
    dire(not perdus,
         f"{len(annoncee)} moteurs annonces, {len(survivants)} constructibles"
         + (f" — PERDUS PAR LE FIL : {perdus}" if perdus else ""))

    print("\n=== l'AGENT lui-meme precharge, pas seulement ce test ===")
    # Dans un INTERPRETEUR NEUF, et c'est tout l'interet. Ma premiere version reimportait
    # l'agent dans ce processus — mais le test avait deja appele precharger() plus haut, donc
    # les plugins etaient dans sys.modules quoi qu'il arrive. Verifie en retirant l'appel de
    # agent.py : le test restait VERT. Il prouvait que precharger() fonctionne, pas que
    # l'agent l'appelle, et c'est precisement la difference qui compte.
    import json
    import subprocess
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import agent, moteurs_stt, json\n"
        "attendus = [moteurs_stt.PAR_CLE[c].module for c in moteurs_stt.chaine()\n"
        "            if moteurs_stt.PAR_CLE[c].module]\n"
        "print('RESULTAT ' + json.dumps({'attendus': attendus,\n"
        "      'manquants': [m for m in attendus if m not in sys.modules]}))\n"
    ) % str(Path(__file__).parent)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=180)
    ligne = next((l for l in r.stdout.splitlines() if l.startswith("RESULTAT ")), None)
    if not ligne:
        dire(False, f"l'agent ne s'importe pas seul : {(r.stderr or '')[-200:]}")
    else:
        d = json.loads(ligne[len("RESULTAT "):])
        dire(not d["manquants"],
             f"importer l'agent charge les {len(d['attendus'])} plugins de la chaine"
             + (f" — MANQUENT : {d['manquants']}" if d["manquants"] else ""))

    print("\n=== la journalisation n'est pas doublee ===")
    # Le symptome : chaque ligne du demarrage s'imprimait DEUX fois, une fois brute et une
    # fois au format colore. Une trace d'erreur apparaissait donc deux fois de suite, ce qui
    # donne l'impression que le probleme s'est produit deux fois. La cause : notre
    # basicConfig posait un handler sur la racine, et LiveKit ajoute le sien sans jamais
    # retirer ceux qui existent (cli/log.py : root.addHandler, pas de removeHandler).
    import logging
    source_agent = (Path(__file__).parent / "agent.py").read_text(encoding="utf-8")
    dire("logging.basicConfig" not in source_agent,
         "l'agent n'appelle plus basicConfig : LiveKit configure deja la racine")

    racine = logging.getLogger()
    anciens = list(racine.handlers)
    try:
        for h in anciens:
            racine.removeHandler(h)
        from livekit.agents.cli.log import setup_logging
        setup_logging("DEBUG", devmode=True, console=True)
        dire(len(racine.handlers) == 1,
             f"LiveKit seul pose UN handler ({len(racine.handlers)})")
        logging.basicConfig(level=logging.INFO, force=False)
        dire(len(racine.handlers) == 1,
             "et basicConfig ne fait rien quand un handler existe deja — donc l'ordre "
             "des appels est ce qui comptait")
    finally:
        for h in list(racine.handlers):
            racine.removeHandler(h)
        for h in anciens:
            racine.addHandler(h)

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(principal())
