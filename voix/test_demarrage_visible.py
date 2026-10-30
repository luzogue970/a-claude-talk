#!/usr/bin/env python3
"""Entrer dans une conversation pendant que l'agent démarre, sans rien perdre.

Ce qui serait cassé pour moi si ces tests tombaient : je clique sur une conversation, je
regarde un bouton « ouverture… » pendant treize secondes, et si j'arrive plus tôt, ce que
je dis ou j'écris pendant le démarrage part dans le vide.

La mesure du 30 octobre 2026 : le serveur de l'agent écoute à 7 s, « pret » arrive à 13 s.
Le tableau de bord attendait « pret ». Six secondes de liste figée alors que la page
existait déjà — et la page, elle, sait dire « l'agent démarre… » et garde ce qu'on y écrit.
Elle refusait en revanche un enregistrement du micro, net, et le son était perdu.
"""

import asyncio
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tableau import Tableau  # noqa: E402

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def lire(url, delai=3):
    with urllib.request.urlopen(url, timeout=delai) as r:
        return r.read().decode()


def poster(url, corps, delai=5):
    requete = urllib.request.Request(
        url, data=corps.encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(requete, timeout=delai) as r:
        return r.read().decode()


# --- TU : la règle d'attente, seule ----------------------------------------------------

def l_attente_rend_la_main_si_l_agent_ne_vient_pas():
    """Un agent qui ne démarre jamais ne doit pas retenir la requête pour l'éternité."""
    async def essai():
        t = Tableau(port=7891, ouvrir=False)
        depart = time.monotonic()
        venu = await t.attendre_branchement(delai=0.3, pas=0.02)
        passe = time.monotonic() - depart
        dire(venu is False, "l'agent absent est declare absent, pas attendu sans fin")
        dire(0.25 < passe < 1.5, f"elle attend le delai demande, pas plus ({passe:.2f} s)")
    asyncio.run(essai())


def l_attente_repart_des_que_l_agent_se_branche():
    """Et surtout : elle ne fait pas attendre une seconde de plus que nécessaire."""
    async def essai():
        t = Tableau(port=7891, ouvrir=False)

        async def brancher():
            await asyncio.sleep(0.1)
            t.on_commande = lambda nom, ordre: asyncio.sleep(0)

        asyncio.create_task(brancher())
        depart = time.monotonic()
        venu = await t.attendre_branchement(delai=5, pas=0.02)
        passe = time.monotonic() - depart
        dire(venu is True, "l'agent qui arrive est vu arriver")
        dire(passe < 1.0, f"et vu TOUT DE SUITE, pas au prochain tour de garde ({passe:.2f} s)")
    asyncio.run(essai())


# --- TI : la page répond avant que l'agent soit branché --------------------------------

def la_page_repond_bien_avant_que_l_agent_soit_pret():
    """C'est exactement ce sur quoi le tableau de bord redirige maintenant."""
    async def essai():
        t = Tableau(port=7892, ouvrir=False)
        await t.demarrer()
        base = f"http://127.0.0.1:{t.port}"
        try:
            # `lire` bloque : dans un fil a part, sinon on bloque la boucle qui sert la page.
            etat = await asyncio.to_thread(lire, f"{base}/etat.json")
            dire('"pret": false' in etat,
                 "la page repond, et elle dit franchement que l'agent n'est pas branche")
            t.on_commande = lambda nom, ordre: asyncio.sleep(0)
            await asyncio.sleep(0.05)
            dire('"pret": true' in await asyncio.to_thread(lire, f"{base}/etat.json"),
                 "et elle bascule a « pret » des que l'agent se branche")
        finally:
            await t.arreter()
    asyncio.run(essai())


def ce_qu_on_ecrit_pendant_le_demarrage_est_garde_et_rejoue():
    async def essai():
        t = Tableau(port=7893, ouvrir=False)
        await t.demarrer()
        base = f"http://127.0.0.1:{t.port}"
        recus = []
        try:
            reponse = await asyncio.to_thread(
                poster, f"{base}/commande", '{"cmd": "envoyer", "texte": "bonjour"}')
            dire('"differee": true' in reponse.replace('"differee":true', '"differee": true'),
                 "le message ecrit trop tot est garde, et la page le SAIT")

            async def recevoir(nom, ordre):
                recus.append((nom, ordre))

            t.on_commande = recevoir
            await asyncio.sleep(0.1)
            dire(recus and recus[0][0] == "envoyer" and recus[0][1].get("texte") == "bonjour",
                 "et il part des que l'agent se branche, sans qu'on le retape")
        finally:
            await t.arreter()
    asyncio.run(essai())


# --- TF : le parcours tel que je le vis ------------------------------------------------

def le_champ_promet_l_attente_pendant_le_demarrage():
    """J'arrive dans la conversation avant l'agent : le champ doit me dire quoi attendre."""
    page = (Path(__file__).parent / "tableau.py").read_text()
    dire('"l\'agent démarre — ce que tu écris partira dès qu\'il est prêt"' in page,
         "le champ de saisie dit que ce qu'on ecrit est garde")
    dire('champ.placeholder = etatRecu ? ECRIRE_ICI' in page,
         "et il ne le dit QUE tant que l'agent n'a pas donne signe de vie")
    dire('if (champ && champ.placeholder.startsWith("l\'agent démarre")) champ.placeholder = ECRIRE_ICI;'
         in page,
         "la promesse s'efface des que l'agent se branche")


def le_micro_attend_l_agent_au_lieu_de_jeter_le_son():
    page = (Path(__file__).parent / "tableau.py").read_text()
    dire("if not await self.attendre_branchement():" in page,
         "un enregistrement arrive pendant le demarrage attend l'agent")
    dire(page.count('"erreur": "l\'agent n\'est pas encore branché"') == 1,
         "et le refus ne reste que pour l'agent qui ne vient vraiment jamais")


def main():
    l_attente_rend_la_main_si_l_agent_ne_vient_pas()
    l_attente_repart_des_que_l_agent_se_branche()
    la_page_repond_bien_avant_que_l_agent_soit_pret()
    ce_qu_on_ecrit_pendant_le_demarrage_est_garde_et_rejoue()
    le_champ_promet_l_attente_pendant_le_demarrage()
    le_micro_attend_l_agent_au_lieu_de_jeter_le_son()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
