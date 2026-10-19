"""Le micro apres un aller-retour hors de l'application : ce que l'agent recoit de la page.

Le defaut vecu, encore le 19 octobre 2026 : on dicte une fois depuis le telephone, on
quitte l'application, on y revient, et le micro ne repond plus. Les causes sont dans la page
(voir `testerMicroApresRetour` dans test_front_cas.js) ; ce fichier-ci verifie le CONTRAT
entre la page et l'agent qui rend ces pannes diagnosticables : la page raconte chaque etape
du micro par `{cmd: "trace"}`, et l'agent la range dans son journal — donc dans le filtre
« log » de la page — sans jamais la prendre pour une commande.

    ../.venv/bin/python voix/test_micro_retour.py
"""

import asyncio
import json
import logging
import sys

import aiohttp

from tableau import Tableau

ok = True


def dire(bon: bool, texte: str) -> None:
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def _logs(t: Tableau) -> list[str]:
    return [e["texte"] for e in t.histoire if e["genre"] == "log"]


async def la_page_raconte_son_micro_et_l_agent_l_ecrit():
    print("\n=== une trace de la page finit dans le journal de l'agent, pas dans ses commandes ===")
    recues = []

    async def commande(nom, donnees):
        recues.append(nom)

    t = Tableau(port=7893, ouvrir=False, on_commande=commande)
    # Le meme branchement que l'agent : le journal Python repart vers la page.
    journal = logging.getLogger("voix.tableau")
    journal.setLevel(logging.INFO)
    h = t.journal_handler()
    journal.addHandler(h)
    url = await t.demarrer()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(url.replace("http", "ws") + "/flux") as ws:
                await ws.receive()  # _histoire
                await ws.send_str(json.dumps(
                    {"cmd": "trace", "texte": "micro · ouverture — socket 1"}))
                await asyncio.sleep(0.2)
                dire(recues == [], "une trace n'est pas une commande : l'agent n'en voit pas une")
                lignes = _logs(t)
                dire(any("page · micro · ouverture — socket 1" in l for l in lignes),
                     f"et elle est dans le journal, prefixee « page » : {lignes[-1:]!r}")
                # Une trace mal formee ne fait pas de bruit.
                avant = len(_logs(t))
                await ws.send_str(json.dumps({"cmd": "trace", "texte": "   "}))
                await ws.send_str(json.dumps({"cmd": "trace"}))
                await ws.send_str(json.dumps({"cmd": "trace", "texte": 42}))
                await asyncio.sleep(0.2)
                dire(len(_logs(t)) == avant, "une trace vide ou absurde n'ecrit rien")
                # Une trace trop longue est coupee : la page ne doit pas pouvoir remplir le journal.
                await ws.send_str(json.dumps({"cmd": "trace", "texte": "x" * 5000}))
                await asyncio.sleep(0.2)
                derniere = _logs(t)[-1]
                dire(len(derniere) < 400, f"une trace de 5000 caracteres est coupee ({len(derniere)})")
                # La liaison survit : une sonde apres une trace recoit son pouls.
                await ws.send_str(json.dumps({"cmd": "ping"}))
                genres = []
                fin = asyncio.get_event_loop().time() + 2
                while asyncio.get_event_loop().time() < fin and "_pouls" not in genres:
                    msg = await asyncio.wait_for(ws.receive(), 2)   # _bonjour, puis le pouls
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        genres.append(json.loads(msg.data).get("genre"))
                dire("_pouls" in genres, f"la liaison repond toujours apres une trace : {genres!r}")
                # Et une vraie commande passe toujours.
                await ws.send_str(json.dumps({"cmd": "arreter"}))
                await asyncio.sleep(0.2)
                dire(recues == ["arreter"], f"une commande apres une trace arrive : {recues!r}")
    finally:
        journal.removeHandler(h)
        await t.arreter()


async def une_trace_avant_l_agent_ne_s_empile_pas():
    print("\n=== avant que l'agent soit branche, une trace ne s'empile pas comme une commande ===")
    # Les commandes arrivees avant l'agent sont gardees et rejouees a son arrivee. Une trace
    # rejouee comme commande tomberait dans le `if/elif` de l'agent sans rien faire — mais
    # elle y serait comptee, et un diagnostic ne doit pas devenir un ordre.
    t = Tableau(port=7892, ouvrir=False)
    url = await t.demarrer()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(url.replace("http", "ws") + "/flux") as ws:
                await ws.receive()
                await ws.send_str(json.dumps({"cmd": "trace", "texte": "micro · appui"}))
                await ws.send_str(json.dumps({"cmd": "envoyer"}))
                await asyncio.sleep(0.2)
                noms = [n for n, _ in t._commandes_en_attente]
                dire(noms == ["envoyer"], f"seule la commande attend l'agent, pas la trace : {noms!r}")
    finally:
        await t.arreter()


async def principal():
    await la_page_raconte_son_micro_et_l_agent_l_ecrit()
    await une_trace_avant_l_agent_ne_s_empile_pas()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
