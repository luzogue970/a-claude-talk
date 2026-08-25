"""L'arbitrage du micro entre conversations parallèles.

Ce fichier teste la seule ressource vraiment exclusive du systeme. Deux agents lances en
meme temps se disputaient le peripherique d'entree : les deux entendaient la meme phrase, les
deux l'envoyaient a leur Claude, et les deux consommaient la meme fenetre de rate limit.

Les cas qui comptent sont les cas degrades : un detenteur tue sans rien liberer, un fichier
de bail tronque, un PID reattribue a un autre programme. Chacun a une conséquence precise —
un micro bloque pour toujours, ou pire, deux agents qui ecoutent en croyant etre seuls.

    ../.venv/bin/python voix/test_pupitre.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pupitre

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def faux_agent():
    """Un processus dont la ligne de commande ressemble a celle d'un agent."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(40)", "voix/agent.py"],
        cwd=Path(__file__).resolve().parent.parent,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def principal():
    # On travaille dans un registre isole : ce test ne doit pas toucher aux vraies sessions.
    import tempfile
    boite = Path(tempfile.mkdtemp(prefix="pupitre-"))
    pupitre.RACINE = boite
    pupitre.SESSIONS = boite / "sessions"
    pupitre.BAIL = boite / "micro.json"

    print("=== le bail : un seul micro a la fois ===")
    a = faux_agent()
    b = faux_agent()
    time.sleep(0.3)
    try:
        # deux inscriptions, comme deux « vv » lances dans deux projets
        ia = pupitre.Inscription("insnap", "/home/x/insnap", 7788)
        ia.pid = a.pid
        ia.fichier = pupitre.SESSIONS / f"{a.pid}.json"
        ia._publier()
        ib = pupitre.Inscription("claude-talk", "/home/x/claude-talk", 7789)
        ib.pid = b.pid
        ib.fichier = pupitre.SESSIONS / f"{b.pid}.json"
        ib._publier()

        dire(len(pupitre.sessions()) == 2, f"{len(pupitre.sessions())} sessions inscrites")

        dire(ia.prendre_micro() is True, "le premier prend le micro")
        dire(ia.detient_micro() and not ib.detient_micro(),
             "il l'a, l'autre ne l'a pas")
        dire(ia.prendre_micro() is False, "le reprendre alors qu'on l'a ne change rien")

        dire(ib.prendre_micro() is True, "le second le prend a son tour")
        dire(ib.detient_micro() and not ia.detient_micro(),
             "le bail a change de main, et UNE seule fois")

        marque = {d["pid"]: d["micro"] for d in pupitre.sessions()}
        dire(sum(marque.values()) == 1, f"le registre ne marque qu'un detenteur : {marque}")

        print("\n=== ceder depuis le tableau ===")
        dire(pupitre.ceder_a(a.pid) is True, "on peut rendre le micro a l'autre session")
        dire(ia.detient_micro(), "et il l'a bien recupere")
        dire(pupitre.ceder_a(999999) is False, "ceder a un PID mort est refuse")

        print("\n=== cas degrades ===")
        # le detenteur meurt sans rien liberer
        a.kill(); a.wait()
        time.sleep(0.2)
        dire(pupitre.detenteur() is None,
             "detenteur tue -> le bail est vacant, pas bloque pour toujours")
        dire(ib.detient_micro(), "et le suivant qui regarde le recupere")

        # le registre se nettoie des morts
        dire(len(pupitre.sessions()) == 1,
             f"la session morte disparait du registre ({len(pupitre.sessions())} restante)")

        # un fichier de bail illisible ne doit pas casser l'arbitrage
        pupitre.BAIL.write_text("{ tronqué", encoding="utf-8")
        dire(pupitre.detenteur() is None, "bail illisible -> traite comme vacant")
        dire(ib.detient_micro(), "et repris proprement")

        # un PID vivant qui n'est PAS un agent ne compte pas
        pupitre.BAIL.write_text(json.dumps({"pid": 1, "pris_a": time.time()}), encoding="utf-8")
        dire(pupitre.detenteur() is None,
             "un PID vivant mais etranger ne detient pas le micro")

        print("\n=== la parole : une voix a la fois ===")
        pupitre.PAROLE = boite / "parole.json"
        dire(pupitre.qui_parle() is None, "personne ne parle au depart")
        ib.prendre_parole()
        dire(pupitre.qui_parle() == b.pid, "celui qui lit est identifie")
        dire(ib.parole_ailleurs() is None, "et pour lui-meme, ce n'est pas « ailleurs »")

        # une autre conversation voit qu'elle doit attendre
        ic = pupitre.Inscription("troisieme", "/home/x/trois", 7790)
        ic.pid = 424242          # un PID mort : on ne teste que la lecture du bail
        dire(ic.parole_ailleurs() == b.pid, "l'autre voit qu'une lecture est en cours")

        ib.liberer_parole()
        dire(pupitre.qui_parle() is None, "liberee, la parole redevient libre")
        dire(ic.parole_ailleurs() is None, "et l'autre peut parler")

        # un bail perime ne doit pas rendre tout le monde muet pour toujours
        import time as _t
        pupitre.PAROLE.write_text(
            json.dumps({"pid": b.pid, "depuis": _t.time() - pupitre.PAROLE_MAX - 10}),
            encoding="utf-8")
        dire(pupitre.qui_parle() is None,
             f"un bail de plus de {pupitre.PAROLE_MAX:.0f} s est considere perime")
        pupitre.PAROLE.unlink(missing_ok=True)

        print("\n=== liberation propre ===")
        ib.prendre_micro()
        ib.liberer()
        dire(pupitre.detenteur() is None, "liberer rend le bail")
        dire(not ib.fichier.exists(), "et retire la session du registre")
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.kill(); p.wait()
        import shutil
        shutil.rmtree(boite, ignore_errors=True)

    print("\n=== l'agent : ecouter = vouloir ET avoir le bail ===")
    # C'est la semantique qui evite le pire symptome : croire qu'on est ecoute alors qu'on ne
    # l'est pas. Deux etats separes, un seul endroit qui calcule l'effectif.
    from agent import Voix

    class Entree:
        def __init__(self):
            self.audio_enabled = True

        def set_audio_enabled(self, v):
            self.audio_enabled = v

    class Session:
        def __init__(self):
            self.input = Entree()

    class Bail:
        def __init__(self, tenu):
            self.tenu = tenu

        def detient_micro(self):
            return self.tenu

    def agent(voulu, tenu):
        v = Voix.__new__(Voix)
        v.tableau = None
        v.worker = type("W", (), {"occupe": False})()
        v._session_directe = Session()
        v._activity = None
        v.micro_voulu = voulu
        v.inscription = Bail(tenu)
        return v

    for voulu, tenu, attendu in [(True, True, True), (True, False, False),
                                 (False, True, False), (False, False, False)]:
        v = agent(voulu, tenu)
        eff = v.appliquer_micro(publier=False)
        etat = v._session_directe.input.audio_enabled
        bon = eff is attendu and etat is attendu
        dire(bon, f"voulu={voulu!s:<5} bail={tenu!s:<5} -> ecoute={etat}")

    # couper le micro ne touche pas au bail : ce sont deux choses differentes
    v = agent(True, True)
    v.couper_micro()
    dire(v.micro_voulu is False and v.inscription.tenu is True,
         "couper le micro n'abandonne pas le bail")

    # reprendre le bail ne rouvre PAS un micro coupe a la main
    v = agent(False, False)
    v.inscription.tenu = True
    dire(v.appliquer_micro(publier=False) is False,
         "reprendre le bail ne rouvre pas un micro coupe a la main")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(principal())
