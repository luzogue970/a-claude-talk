#!/usr/bin/env python3
"""Sur quel modèle et à quelle puissance cette conversation tourne EN CE MOMENT.

Ce qui serait cassé pour moi si ces tests tombaient : je lirais sur la liste des
conversations du tableau de bord « Opus · medium » pendant que la session tourne en
Fable, ou en ultracode. C'est pire que de ne rien afficher — on n'ouvre plus la
conversation pour vérifier, justement parce qu'un chiffre est là.

Le défaut mesuré en écrivant ces tests : `modele` et `effort` étaient publiés comme des
événements qui passent, pas comme des ÉTATS. Ils n'étaient donc pas rejoués à la
reconnexion, et seul le couple du DÉMARRAGE survivait dans `/etat.json`. Une session
lancée en medium puis montée en ultracode se présentait en medium pour le reste de sa
vie — et l'effort s'ajuste tout seul à chaque demande, donc le couple de départ est
presque toujours faux au bout de trois tours.
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
    def __init__(self):
        self.lignes = []

    def publier(self, genre, **donnees):
        self.lignes.append((genre, donnees))

    def dernier(self, genre):
        for g, d in reversed(self.lignes):
            if g == genre:
                return d
        return None


class ClientDouble:
    def __init__(self):
        self.modeles = []

    async def set_model(self, identifiant):
        self.modeles.append(identifiant)


async def changer_effort_sans_sdk(w, cle):
    """Changer d'effort reconstruit le client : on double le SDK, pas la règle.

    Recopié de `test_credits` plutôt qu'importé : cette suite-là s'exécute à l'import —
    l'importer lancerait ses quarante vérifications puis appellerait `sys.exit`, et celle-ci
    ne dirait jamais un mot. Quatre lignes valent mieux que ce piège.
    """
    import worker as module_worker

    class ClientReconstruit(ClientDouble):
        def __init__(self, options):
            super().__init__()

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


# ─── TU : la forme de l'état, sans session ni réseau ────────────────────────

def l_etat_dit_le_couple_courant():
    import config
    print("\n=== le couple courant se décrit en un seul endroit ===")
    e = config.etat_modeles("claude-fable-5-1")
    dire(e["actuel"] == "fable", f"un identifiant complet devient sa clé courte ({e['actuel']})")
    dire(config.etat_modeles("fable")["actuel"] == "fable",
         "une clé courte reste elle-même — les deux formes circulent")
    dire(len(e["liste"]) == len(config.MODELES),
         "la liste complète voyage avec : sans elle, le menu de la page se viderait")
    dire(all(set(x) == {"cle", "libelle"} for x in e["liste"]),
         "chaque entrée porte sa clé et son libellé, rien d'autre")

    f = config.etat_efforts("ultracode")
    dire(f["actuel"] == "ultracode",
         "ultracode se dit ultracode, et pas « xhigh » — c'est le mot qui inquiète")
    dire(len(f["liste"]) == len(config.EFFORTS), "tous les niveaux sont proposés")
    # Un niveau inventé ne doit pas s'afficher comme s'il existait : on retombe sur le
    # niveau de lancement plutôt que d'écrire un mot que personne ne peut recocher.
    dire(config.etat_efforts("inventé")["actuel"] == config.WORKER_EFFORT,
         "un niveau inconnu retombe sur celui du lancement")


# ─── TI : l'agent republie son état quand il change ─────────────────────────

def changer_de_modele_republie_l_etat():
    import config
    from worker import Worker

    async def essai():
        print("\n=== changer de modèle met l'état à jour, pas seulement l'événement ===")
        w = Worker.__new__(Worker)
        w.client = ClientDouble()
        w._tableau = TableauMuet()
        w.refus_credits = ""
        w.effort = "high"
        w.modele = config.MODELES["opus"][0]
        w._modele_base = w.modele
        w._rendre_apres_tour = False

        await w.changer_modele("haiku", temporaire=False)
        etat = w._tableau.dernier("modeles")
        dire(etat is not None, "un état « modeles » est republié")
        dire(etat and etat["actuel"] == "haiku",
             f"et il porte le NOUVEAU modèle ({etat and etat['actuel']})")
        dire(w._tableau.dernier("modele") is not None,
             "l'événement d'instant continue d'exister — la page l'écoute au vol")

        # L'emprunt d'un tour : l'effort automatique descend sur un modèle léger pour une
        # question simple, puis rend. Les deux mouvements doivent se voir, sinon l'état
        # reste coincé sur l'emprunt.
        print("\n=== un modèle emprunté pour un tour se rend, et ça se voit ===")
        w._rendre_apres_tour = False
        await w.changer_modele("haiku", temporaire=True)
        dire(w._tableau.dernier("modeles")["actuel"] == "haiku", "l'emprunt s'affiche")
        await w._rendre_le_modele()
        dire(w._tableau.dernier("modeles")["actuel"] == "opus",
             f"et le retour aussi ({w._tableau.dernier('modeles')['actuel']})")

        print("\n=== un changement refusé ne change pas l'affichage ===")
        w.effort = "ultracode"
        w.modele = config.MODELES["opus"][0]
        w._modele_base = w.modele
        avant = len(w._tableau.lignes)
        dit = await w.changer_modele("fable")
        dire(dit == "", "Fable en ultracode reste refusé")
        # Le pire affichage possible : annoncer un modèle sur lequel on ne tourne pas.
        dire(all(g != "modeles" for g, _ in w._tableau.lignes[avant:]),
             "et aucun état n'est publié — sinon la carte annoncerait Fable")

    asyncio.run(essai())


def changer_d_effort_republie_l_etat():
    import config
    from worker import Worker

    async def essai():
        print("\n=== changer d'effort met l'état à jour ===")
        w = Worker.__new__(Worker)
        w.client = ClientDouble()
        w._tableau = TableauMuet()
        w.refus_credits = ""
        w._pompe = None
        w.effort = "medium"
        w.modele = config.MODELES["opus"][0]
        w._modele_base = w.modele
        w._rendre_apres_tour = False
        w.occupe = False
        w.session_id = None
        w.reprise = None
        w._can_use_tool = None

        await changer_effort_sans_sdk(w, "ultracode")
        etat = w._tableau.dernier("efforts")
        dire(etat is not None and etat["actuel"] == "ultracode",
             f"l'état porte ultracode ({etat and etat['actuel']})")
        # Le SDK ne connaît pas « ultracode » et reçoit « xhigh » : c'est le mot d'ici
        # qu'on affiche, parce que c'est lui qui dit qu'on lance plusieurs agents.
        dire(w.effort == "ultracode", "et l'agent le retient sous ce nom-là")

    asyncio.run(essai())


# ─── TI : /etat.json le dit à qui regarde de l'extérieur ────────────────────

def le_couple_se_lit_sans_ouvrir_la_conversation():
    import json as _json
    import aiohttp
    from tableau import Tableau

    async def essai():
        print("\n=== le tableau de bord lit le couple sans ouvrir la conversation ===")
        t = Tableau(port=7897, ouvrir=False)
        url = await t.demarrer()
        try:
            t.publier("config", valeurs={"projet": "/home/x/piano"})
            async with aiohttp.ClientSession() as s:
                async with s.get(url + "/etat.json") as r:
                    d = _json.loads(await r.text())
            # Avant toute publication, mieux vaut une chaîne vide qu'un modèle supposé :
            # une carte qui affiche « Opus » par défaut mentirait sur une session Fable.
            dire(d["modele"] == "" and d["effort"] == "",
                 "tant que rien n'est publié, rien n'est affirmé")

            t.publier("modeles", liste=[{"cle": "fable", "libelle": "Fable"}], actuel="fable")
            t.publier("efforts", liste=[{"cle": "ultracode", "libelle": "ultracode"}],
                      actuel="ultracode")
            async with aiohttp.ClientSession() as s:
                async with s.get(url + "/etat.json") as r:
                    d = _json.loads(await r.text())
            dire(d["modele"] == "fable", f"le modèle courant se lit ({d['modele']})")
            dire(d["effort"] == "ultracode", f"le niveau d'effort aussi ({d['effort']})")

            # Le point qui a motivé tout ça : ces deux-là sont de l'ENTRETIEN, pas de la
            # vie. Une session oubliée ne doit pas paraître active parce que son effort
            # s'est réajusté — sinon rien ne la ferme jamais.
            await asyncio.sleep(0.2)
            async with aiohttp.ClientSession() as s:
                async with s.get(url + "/etat.json") as r:
                    avant = _json.loads(await r.text())["inactif"]
            t.publier("efforts", liste=[{"cle": "high", "libelle": "élevé"}], actuel="high")
            async with aiohttp.ClientSession() as s:
                async with s.get(url + "/etat.json") as r:
                    apres = _json.loads(await r.text())["inactif"]
            dire(apres >= avant,
                 f"un changement d'effort ne rajeunit pas la session ({avant} s -> {apres} s)")

            # Et il suit les changements : c'est tout l'intérêt, puisque l'effort
            # s'ajuste à chaque demande.
            async with aiohttp.ClientSession() as s:
                async with s.get(url + "/etat.json") as r:
                    d = _json.loads(await r.text())
            dire(d["effort"] == "high", f"le dernier état gagne ({d['effort']})")
        finally:
            await t.arreter()

    asyncio.run(essai())


def main():
    l_etat_dit_le_couple_courant()
    changer_de_modele_republie_l_etat()
    changer_d_effort_republie_l_etat()
    le_couple_se_lit_sans_ouvrir_la_conversation()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
