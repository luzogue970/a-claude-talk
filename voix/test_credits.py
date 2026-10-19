#!/usr/bin/env python3
"""Le garde-fou des crédits : ce qu'on ne peut pas choisir, et quand on s'arrête pour demander.

Ce qui serait cassé pour moi si ces tests tombaient : je poserais « Fable » et « ultracode »
ensemble sans que rien ne m'arrête, et une poignée de tours videraient des crédits que le
forfait ne plafonne pas — Fable se paie à part, ultracode lance plusieurs agents sur le même
tour. Le compte serait à sec avant que la première carte du tableau ne vire au rouge.

La règle vit en double, ici en Python et dans `server/src/reglages-talk.js` côté tableau,
parce que les portes sont indépendantes : la voix et la page de conversation passent par
`config.refus_du_couple`, le fichier de réglages généraux passe par le tableau. `test-reglages`
vérifie que les deux disent la même chose, en appelant vraiment ce fichier-ci.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class TableauMuet:
    """Retient ce que l'agent aurait publié, pour qu'on puisse lire ce qu'il a dit."""

    def __init__(self):
        self.lignes = []

    def publier(self, genre, **donnees):
        self.lignes.append((genre, donnees))

    def textes(self):
        return " | ".join(str(d.get("texte", "")) for _, d in self.lignes)


class ClientDouble:
    def __init__(self):
        self.modeles = []

    async def set_model(self, identifiant):
        self.modeles.append(identifiant)


async def changer_effort_sans_sdk(w, cle, construits=None):
    """Changer d'effort reconstruit le client : on double le SDK, pas la regle."""
    import worker as module_worker

    class ClientReconstruit(ClientDouble):
        def __init__(self, options):
            super().__init__()
            if construits is not None:
                construits.append(options)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    ancien = module_worker.ClaudeSDKClient
    module_worker.ClaudeSDKClient = ClientReconstruit
    try:
        return await w.changer_effort(cle)
    finally:
        module_worker.ClaudeSDKClient = ancien


# ─── TU : la règle, sans session ni réseau ──────────────────────────────────

def le_couple_qui_vide_le_compte_est_nomme():
    import config
    print("\n=== Fable en ultracode : refusé, et dit en clair ===")
    refus = config.refus_du_couple("fable", "ultracode")
    dire(bool(refus), "le couple est refusé")
    dire("crédits" in refus, "le refus dit POURQUOI — sinon on le prend pour une panne")
    dire(config.refus_du_couple("claude-fable-5-1", "ultracode") == refus,
         "refusé aussi sous son identifiant complet : les réglages généraux l'écrivent ainsi")

    print("\n=== tout le reste passe : on refuse un couple, pas un modèle ===")
    dire(not config.refus_du_couple("fable", "max"),
         "Fable au maximum d'effort reste possible — c'est un seul agent")
    dire(not config.refus_du_couple("fable", "xhigh"), "Fable en très élevé aussi")
    dire(not config.refus_du_couple("opus", "ultracode"),
         "ultracode sur Opus reste possible — Opus se paie dans le forfait")
    dire(not config.refus_du_couple("haiku", "ultracode"), "… et sur Haiku")
    dire(not config.refus_du_couple("", ""), "deux cases vides ne refusent rien")
    dire(not config.refus_du_couple("modele-inventé", "effort-inventé"),
         "ce qu'on ne connaît pas ne déclenche pas un refus au hasard")


def au_demarrage_la_session_s_ouvre_quand_meme():
    """Une session qui refuse de démarrer coûte plus cher qu'une session un cran plus bas."""
    import subprocess
    print("\n=== le couple posé au lancement redescend l'effort, il n'empêche pas de démarrer ===")
    code = ("import sys; sys.path.insert(0, 'voix'); import config; "
            "print(config.WORKER_EFFORT)")
    lance = lambda env: subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=str(Path(__file__).parent.parent), env=env).stdout.strip()
    import os
    base = dict(os.environ)
    base.pop("VOIX_WORKER_MODEL", None)
    base.pop("VOIX_WORKER_EFFORT", None)
    pire = dict(base, VOIX_WORKER_MODEL="claude-fable-5-1", VOIX_WORKER_EFFORT="ultracode")
    dire(lance(pire) == "xhigh",
         "Fable + ultracode au lancement : l'effort redescend à très élevé, la session s'ouvre")
    garde = dict(base, VOIX_WORKER_MODEL="claude-opus-5", VOIX_WORKER_EFFORT="ultracode")
    dire(lance(garde) == "ultracode", "ultracode sur Opus au lancement n'est pas touché")
    autre = dict(base, VOIX_WORKER_MODEL="claude-fable-5-1", VOIX_WORKER_EFFORT="max")
    dire(lance(autre) == "max", "Fable au maximum n'est pas touché non plus")


# ─── TI : les deux portes de la session refusent vraiment ───────────────────

def les_deux_portes_refusent():
    import config
    from worker import Worker

    async def essai():
        print("\n=== la porte du modèle : on est en ultracode, on demande Fable ===")
        w = Worker.__new__(Worker)
        w.client = ClientDouble()
        w._tableau = TableauMuet()
        w.refus_credits = ""
        w.effort = "ultracode"
        w.modele = config.MODELES["opus"][0]
        w._modele_base = w.modele
        w._rendre_apres_tour = False
        dit = await w.changer_modele("fable")
        dire(dit == "", "le changement est refusé")
        dire(w.modele == config.MODELES["opus"][0], "le modèle courant n'a pas bougé")
        dire(w.client.modeles == [], "et rien n'est parti au SDK")
        dire("crédits" in w.refus_credits, "la raison est gardée, pour que la voix la dise")
        dire("crédits" in w._tableau.textes(), "… et écrite dans le journal de la page")

        print("\n=== la même porte laisse passer ce qui est légitime ===")
        w.refus_credits = ""
        dit = await w.changer_modele("haiku")
        dire(dit.startswith("Haiku"), "Haiku en ultracode passe")
        dire(w.client.modeles == [config.MODELES["haiku"][0]], "et part bien au SDK")

        print("\n=== la porte de l'effort : on est sur Fable, on demande ultracode ===")
        w = Worker.__new__(Worker)
        w.client = ClientDouble()
        w._tableau = TableauMuet()
        w.refus_credits = ""
        w.effort = "medium"
        w.modele = config.MODELES["fable"][0]
        w.occupe = False
        dit = await w.changer_effort("ultracode")
        dire(dit == "", "le changement est refusé")
        dire(w.effort == "medium", "le niveau courant n'a pas bougé")
        dire("crédits" in w.refus_credits, "la raison est gardée")

        print("\n=== et l'effort se juge sur le nom du niveau, pas sur ce qu'il envoie au SDK ===")
        dire(config.effort_sdk("ultracode") == "xhigh",
             "ultracode s'envoie en xhigh — d'où le piège")
        w.refus_credits = ""
        w._pompe = None
        w.session_id = None
        w.reprise = None
        w._can_use_tool = None
        dit = await changer_effort_sans_sdk(w, "xhigh")
        dire(dit != "", "« très élevé » sur Fable passe : c'est un seul agent")
        dire(w.effort == "xhigh", "et le niveau est bien posé")

    asyncio.run(essai())


def ultracode_reste_ultracode():
    """Un bug trouvé en posant ce garde-fou : `self.effort` retenait le niveau SDK.

    Mesuré en lisant le code : `changer_effort` écrivait `self.effort = niveau`, donc passer
    en ultracode y rangeait « xhigh ». Invisible sur les cinq autres niveaux, où la clé vaut
    le niveau. Conséquences réelles : les consignes ultracode ne partaient jamais au worker,
    le sélecteur de la page retombait sur « très élevé » tout seul — et ce garde-fou-ci
    n'aurait rien vu, puisqu'il demande à self.effort si on est en ultracode.
    """
    import config
    from worker import Worker

    async def essai():
        print("\n=== rester en ultracode, c'est le savoir ===")
        w = Worker.__new__(Worker)
        w.client = ClientDouble()
        w._tableau = TableauMuet()
        w._pompe = None
        w.refus_credits = ""
        w.effort = "medium"
        w.modele = config.MODELES["opus"][0]
        w.occupe = False
        w.session_id = None
        w.reprise = None
        w._can_use_tool = None
        construits = []
        dit = await changer_effort_sans_sdk(w, "ultracode", construits)
        dire(dit != "", "le passage en ultracode est accepté sur Opus")
        dire(w.effort == "ultracode",
             "et l'agent SAIT qu'il y est — il retenait « xhigh », donc il l'oubliait")
        dire(bool(construits) and getattr(construits[0], "effort", "") == "xhigh",
             "ce qui part au SDK reste « xhigh » : lui ne connaît pas ultracode")
        dire(bool(config.refus_du_couple(config.cle_du_modele(w.modele), w.effort)) is False
             and bool(config.refus_du_couple("fable", w.effort)),
             "et le garde-fou peut enfin voir qu'on y est")

    asyncio.run(essai())


def main():
    le_couple_qui_vide_le_compte_est_nomme()
    au_demarrage_la_session_s_ouvre_quand_meme()
    les_deux_portes_refusent()
    ultracode_reste_ultracode()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
