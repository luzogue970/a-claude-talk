"""L'historique est là quand la page arrive, pas six secondes plus tard.

Ce qui serait cassé pour moi si ces tests tombaient : je rouvre une conversation depuis le
tableau, j'atterris sur une page VIDE qui dit « l'agent démarre… », et je crois que je
viens d'ouvrir une conversation neuve. C'est mot pour mot ce qui était rapporté : « je
quitte la conversation, je reviens dessus, et ça rouvre une nouvelle conversation comme si
elle n'était pas ouverte. »

Elle n'était pas neuve. Le contexte de Claude était intact. C'est la PAGE qui ne montrait
rien, parce que le rejeu de l'historique n'était lancé qu'APRÈS le démarrage du worker —
donc après le lancement du processus « claude ». Mesure du 8 novembre 2026 : la page de
l'agent répond à 7 s, c'est à cet instant que le tableau rend la main et que le téléphone
y arrive ; le worker n'est debout qu'à 13 s.

Rien dans ce rejeu n'a besoin du worker : la session à reprendre se lit dans l'index
local, son historique dans un fichier. Il part donc maintenant en même temps que le reste,
et ces tests tiennent les trois choses qui le permettent.

    ../.venv/bin/python voix/test_historique_tot.py
"""

import asyncio
import inspect
import json
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


# ─── TU ── la session à reprendre se devine sans rien lancer ───────────────────────────
def tu_la_decision():
    print("\n=== TU — quelle conversation on va reprendre, avant d'avoir rien lancé ===")
    import journal

    boite = Path(tempfile.mkdtemp(prefix="histoire-tot-"))
    os.environ["VOIX_JOURNAL"] = str(boite)
    import importlib
    importlib.reload(journal)

    ici = "/a/projet/repris"
    c = journal.Conversation("repris", "claude-opus-5", "xhigh", chemin=ici)
    c.note_session("sess-la-bonne")
    c.tour_utilisateur("une question")
    c.tour_claude("une réponse")
    time.sleep(0.01)

    dire(journal.session_a_reprendre(ici) == "sess-la-bonne",
         "dans un dossier où l'on a déjà parlé, on retrouve CETTE conversation")
    dire(journal.session_a_reprendre(ici, demandee="sess-choisie") == "sess-choisie",
         "une conversation demandée à la main gagne sur la dernière du dossier")
    dire(journal.session_a_reprendre(ici, auto=False) is None,
         "« nouvelle conversation » ne va rien rechercher — sinon elle ne serait pas neuve")
    dire(journal.session_a_reprendre("/a/jamais-vu") is None,
         "un dossier inconnu n'a rien à reprendre, et ne doit pas emprunter celle du voisin")
    dire(journal.session_a_reprendre(None) is None or True,
         "sans dossier, elle répond sans tomber")

    # Le piège qui coûterait le plus cher : afficher l'historique d'une conversation
    # pendant qu'on parle à une autre. La devinette et le worker doivent décider PAREIL.
    attendue = (journal.derniere_conversation(ici) or {}).get("session_id")
    dire(journal.session_a_reprendre(ici) == attendue,
         "elle désigne exactement la conversation que le worker reprendra")

    shutil.rmtree(boite, ignore_errors=True)
    return boite


# ─── TI ── les deux chemins qui décident ne peuvent pas diverger ───────────────────────
def ti_un_seul_juge():
    print("\n=== TI — un seul juge pour « quelle conversation » ===")
    import journal
    import worker

    corps = inspect.getsource(worker.Worker._a_reprendre)
    dire("journal.derniere_conversation(config.WORKDIR)" in corps,
         "le worker reprend bien la dernière conversation du dossier, par la même lecture")
    dire("config.REPRENDRE" in corps and "config.REPRISE_AUTO" in corps,
         "et il tient compte des deux mêmes réglages que la devinette")

    source = inspect.getsource(journal.session_a_reprendre)
    dire("derniere_conversation" in source,
         "la devinette passe par la même fonction, pas par une relecture à elle")


# ─── TI ── le rejeu n'attend pas le worker ─────────────────────────────────────────────
def ti_lordre_du_demarrage():
    print("\n=== TI — le rejeu part avant « claude », pas après ===")
    import agent

    corps = inspect.getsource(agent.entrypoint)
    depart = corps.find("_rejouer_historique(pressentie)")
    worker_debout = corps.find("await worker.start()")
    page_ouverte = corps.find("await tableau.demarrer()")

    dire(depart > 0, "le démarrage lance un rejeu d'historique de son propre chef")
    dire(page_ouverte >= 0 and depart > page_ouverte,
         "après que la page répond : publier dans le vide ne servirait à rien")
    dire(worker_debout > 0 and depart < worker_debout,
         "et AVANT le lancement du worker — c'est tout l'objet du correctif")
    dire(corps.find("session_a_reprendre") < worker_debout,
         "la session à reprendre est lue sans le worker, donc sans attendre « claude »")

    # Le filet d'après le worker reste : la devinette peut se tromper, et c'est « claude »
    # qui a le dernier mot sur ce qu'il a vraiment repris.
    dire(corps.find("_rejouer_historique(reprise_initiale)") > worker_debout,
         "le rejeu d'après le worker reste en filet, pour corriger un pari perdu")

    # Le garde-fou anti-doublon a son revers : il refuse de rejouer une conversation déjà
    # affichée. Quand on change de conversation, le flux est vidé — donc elle ne l'est plus,
    # et revenir dessus rendrait une page blanche. Trouvé en relisant le correctif.
    bascule = inspect.getsource(agent)
    vidage = bascule.find('tableau.vider()\n                    # Et on oublie')
    dire(vidage > 0 and 'rejoue["sid"] = ""' in bascule[vidage:vidage + 700],
         "changer de conversation oublie ce qui a été rejoué : on revient, on revoit")


# ─── TF ── le parcours : j'arrive, et la conversation est là ───────────────────────────
async def tf_la_page_arrive_apres_lhistorique():
    print("\n=== TF — j'ouvre la conversation : l'historique y est déjà ===")
    import aiohttp
    from tableau import Tableau

    tab = Tableau(port=7899, ouvrir=False)
    url = await tab.demarrer()
    try:
        # Ce que fait le rejeu : il publie le passé AVANT qu'un navigateur soit là.
        for i in range(12):
            tab.publier("toi" if i % 2 == 0 else "texte", texte=f"ligne {i}", passe=True)

        vus: list[dict] = []
        bonjour = None
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"{url.rstrip('/')}/flux") as ws:
                debut = time.monotonic()
                while time.monotonic() - debut < 5:
                    message = await asyncio.wait_for(ws.receive(), timeout=5)
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        break
                    d = json.loads(message.data)
                    if d.get("genre") == "_histoire":
                        vus.extend(d.get("evenements") or [])
                    if d.get("genre") == "_bonjour":
                        bonjour = d
                        break

        textes = [e.get("texte") for e in vus]
        dire(bonjour is not None, "la page reçoit son « bonjour » : le rejeu est terminé")
        dire(all(f"ligne {i}" in textes for i in range(12)),
             f"les 12 lignes d'avant sont là dès la connexion ({len(textes)} reçues)")
        dire(bool(bonjour) and bonjour.get("rejoue", 0) >= 12,
             "et la page sait les compter — c'est ce qui lui fait écrire « lignes retrouvées »")

        # Ce qui change pour l'œil : arrivées dans le rejeu, ces lignes ne s'animent pas.
        # Publiées après coup — l'ancien ordre — elles arrivaient comme du direct, et une
        # conversation de la semaine dernière se rejouait sous les yeux comme neuve.
        dire(all(e.get("passe") for e in vus if e.get("texte", "").startswith("ligne")),
             "elles sont marquées « passé » : la page ne les rejoue pas comme du direct")
    finally:
        await tab.arreter()


def main():
    tu_la_decision()
    ti_un_seul_juge()
    ti_lordre_du_demarrage()
    asyncio.run(tf_la_page_arrive_apres_lhistorique())
    print("\nTOUT VERT" if ok else "\nDES TESTS SONT ROUGES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
