"""Invariants du serveur du tableau de bord.

Ce fichier existe a cause d'un bug precis, et il est ecrit pour qu'il ne revienne pas.

Symptome rapporte : « quand je clique sur micro coupe, des fois le micro ne s'arrete pas de
suite, il faut recliquer, et a gauche l'etat passe en deconnecte ».

Cause : `await self.on_commande(...)` n'etait pas protege. UNE exception dans UNE commande
sortait de la boucle `async for` du WebSocket, passait par le `finally` qui retire le client,
et tuait la connexion. La page affichait « deconnecte », la commande n'avait pas eu lieu, et
le clic suivant marchait parce que la page s'etait rebranchee entre-temps.

Ce qui levait : `Agent.session` passe par `_get_activity_or_raise()`, qui leve des que
l'activite est momentanement absente. Le tableau appelle `couper_micro()` depuis l'EXTERIEUR
d'un tour de parole — d'ou l'echec intermittent, et d'ou le fait que seul *couper* echouait,
puisque *rouvrir* passait par l'objet session capture dans l'entrypoint.

    ../.venv/bin/python voix/test_tableau.py
"""

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import aiohttp

from tableau import Tableau

ok = True


def faux_voix(**etat):
    """Un Voix complet sans passer par son __init__ (qui exige une vraie session).

    Centralise parce que chaque nouvel attribut cassait sinon tous les fixtures un par un —
    c'est arrive avec `_retenir_ce_tour`. Ici, un attribut ajoute se declare une fois.
    """

    from agent import Voix   # importe ici : agent charge tout le venv, inutile a l'import

    v = Voix.__new__(Voix)
    v.tableau = None
    v.worker = None
    v.conv = None
    v.quota = None
    v.permission_en_cours = None
    v.retenir = False
    v._retenir_ce_tour = False
    v._tape_en_attente = False
    v._debut_tour = None
    v._dernier_debrief = None
    v._quota_depart = {}
    v._session_directe = None
    v._activity = None
    v.inscription = None          # aucun bail : cet agent est seul, il ecoute
    v.micro_voulu = True
    v._lecture = None
    v._paroles = {}
    v._id_parole = None
    for k, val in etat.items():
        setattr(v, k, val)
    return v


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


async def une_commande_qui_leve_ne_tue_pas_la_socket():
    print("\n=== une commande en echec ne doit pas tuer la connexion ===")
    recues = []

    async def commande(nom, donnees):
        recues.append(nom)
        if nom == "micro":
            raise RuntimeError("no activity context found, the agent is not running")

    t = Tableau(port=7899, ouvrir=False, on_commande=commande)
    url = await t.demarrer()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(url.replace("http", "ws") + "/flux") as ws:
                await ws.receive()  # _histoire
                await ws.send_str(json.dumps({"cmd": "micro", "actif": False}))
                await asyncio.sleep(0.25)
                await ws.send_str(json.dumps({"cmd": "arreter"}))
                await asyncio.sleep(0.25)
                dire(not ws.closed, "la socket est toujours ouverte")
                dire("arreter" in recues, "la commande suivante arrive bien")
        erreurs = [e for e in t.histoire if e["genre"] == "erreur"]
        dire(bool(erreurs), "l'echec est publie sur la page, pas avale en silence")
        if erreurs:
            dire("micro" in erreurs[0]["texte"], f"il nomme la commande : {erreurs[0]['texte']!r}")
    finally:
        await t.arreter()


def couper_le_micro_ne_depend_plus_de_l_activite():
    print("\n=== couper le micro hors activite ===")

    class Entree:
        def __init__(self):
            self.audio_enabled = True

        def set_audio_enabled(self, v):
            self.audio_enabled = v

    class Session:
        def __init__(self):
            self.input = Entree()

    class Worker:
        occupe = False

    v = faux_voix(worker=Worker())   # _activity a None : l'etat qui faisait tout tomber

    try:
        v.session
        dire(False, "Agent.session aurait du lever hors activite")
    except RuntimeError:
        dire(True, "Agent.session leve bien hors activite (le piege d'origine)")

    s = Session()
    v.attacher_session(s)
    dire(v.sess is s, "sess court-circuite le controle d'activite")
    message = v.couper_micro()
    dire(s.input.audio_enabled is False, "le micro est coupe DU PREMIER appel")
    dire("coupé" in message, "la confirmation nomme bien la coupure")

    # sans reference directe, on doit retomber sur le comportement d'origine
    v2 = faux_voix(worker=Worker())
    s2 = Session()

    class Activite:
        def __init__(self, sess):
            self.session = sess

    v2._activity = Activite(s2)
    dire(v2.sess is s2, "sans reference directe, repli sur Agent.session")


async def une_dictee_retenue_ne_sort_pas_dans_le_flux():
    """« toi » doit signifier « pris en compte », et rien d'autre.

    Le bug : la ligne etait publiee AVANT le test du mode retenu. Le flux affichait donc un
    message parti alors qu'il attendait dans la barre — en relisant les logs, impossible de
    savoir ce qui avait reellement ete envoye.
    """
    print("\n=== mode retenu : rien ne doit apparaitre comme envoye ===")

    vus = []

    class FauxTableau:
        def publier(self, genre, **d):
            vus.append((genre, d.get("texte")))

    class Worker:
        occupe = False

        async def envoyer(self, t):
            vus.append(("ENVOYE-A-CLAUDE", t))

    class Item:
        role = "user"
        text_content = "ajoute un bouton pour exporter en CSV"

    class Ctx:
        items = [Item()]

    v = faux_voix(tableau=FauxTableau(), worker=Worker(), retenir=True)

    async for _ in v.llm_node(Ctx(), None, None):
        pass
    genres = [g for g, _ in vus]
    dire("toi" not in genres, f"aucune ligne « toi » publiee : {genres}")
    dire("dictee" in genres, "une ligne « dictee » est publiee a la place")
    dire("ENVOYE-A-CLAUDE" not in genres, "rien n'est parti chez Claude")

    # et en mode normal, la ligne revient
    vus.clear()
    v.retenir = False
    async for _ in v.llm_node(Ctx(), None, None):
        pass
    genres = [g for g, _ in vus]
    dire("toi" in genres, "mode normal : la ligne « toi » est bien publiee")
    dire("ENVOYE-A-CLAUDE" in genres, "mode normal : le message part bien chez Claude")


async def la_retenue_d_un_tour_ne_vaut_que_pour_lui():
    """« Retenir au dernier moment » rattrape le message en vol, sans armer le mode.

    Sinon il faudrait armer « retenir » AVANT de parler, donc savoir a l'avance qu'on allait
    se tromper. Et si le rattrapage laissait le mode arme, le tour suivant serait retenu sans
    qu'on l'ait demande — une surprise dans le sens ou l'on croit avoir envoye.
    """
    print("\n=== retenue d'un seul tour ===")

    vus = []

    class FauxTableau:
        def publier(self, genre, **d):
            vus.append(genre)

    class Worker:
        occupe = False

        async def envoyer(self, t):
            vus.append("ENVOYE")

    class Item:
        role = "user"
        text_content = "ajoute un export CSV"

    class Ctx:
        items = [Item()]

    # le mode n'est PAS arme, mais on a clique pendant le decompte
    v = faux_voix(tableau=FauxTableau(), worker=Worker(),
                  retenir=False, _retenir_ce_tour=True)

    async for _ in v.llm_node(Ctx(), None, None):
        pass
    dire("dictee" in vus and "ENVOYE" not in vus, f"le tour rattrape est retenu : {vus}")
    dire(v._retenir_ce_tour is False, "le rattrapage est consomme")
    dire(v.retenir is False, "le mode permanent n'a pas ete arme au passage")

    vus.clear()
    async for _ in v.llm_node(Ctx(), None, None):
        pass
    dire("ENVOYE" in vus, f"le tour SUIVANT part normalement : {vus}")


async def la_retenue_pendant_le_travail():
    """Parler pendant que Claude travaille ne doit rien envoyer — sauf ce qui doit passer.

    Le probleme : le worker acceptait le second message, repondait « note, j'ajoute ca », et
    il partait sans qu'on ait rien relu. Or c'est le moment ou l'on parle pour REAGIR a ce
    qu'on voit passer, donc celui ou une phrase mal transcrite coute le plus cher.

    Ce qui doit continuer de passer : les ordres locaux et les reponses a une permission. Ce
    sont des reactions, pas des taches a relire — les parquer dans une boite serait absurde,
    et « arrete » parqué serait dangereux.
    """
    print("\n=== retenue automatique pendant une tache ===")
    import asyncio
    vus = []

    class FauxTableau:
        def publier(self, genre, **d):
            vus.append((genre, d))

    class Worker:
        def __init__(self, occupe):
            self.occupe = occupe

        async def envoyer(self, t):
            vus.append(("ENVOYE", {}))

        async def interrompre(self):
            vus.append(("INTERROMPU", {}))

    def ctx(texte):
        class I:
            role = "user"
            text_content = texte
        class C:
            items = [I()]
        return C()

    class Entree:
        def __init__(self):
            self.audio_enabled = True

        def set_audio_enabled(self, v):
            self.audio_enabled = v

    class Session:
        def __init__(self):
            self.input = Entree()

        def interrupt(self):
            vus.append(("COUPE-PAROLE", {}))

    async def tour(texte, occupe, **etat):
        vus.clear()
        # _session_directe plutot que .session : Agent.session est une propriete en lecture
        # seule, et c'est justement la raison d'exister de Voix.sess.
        v = faux_voix(tableau=FauxTableau(), worker=Worker(occupe),
                      _session_directe=Session(), **etat)
        async for _ in v.llm_node(ctx(texte), None, None):
            pass
        return [g for g, _ in vus]

    # une tache est en cours : la phrase est retenue, rien ne part
    g = await tour("ajoute aussi un export CSV", occupe=True)
    dire("dictee" in g and "ENVOYE" not in g, f"occupe -> retenu, rien envoye : {g}")
    auto = next(d for n, d in vus if n == "dictee")
    dire(auto.get("auto") is True, "la ligne dit que c'est automatique, pas un reglage")
    dire("toi" not in g, "aucune ligne « toi » : le message n'a pas ete pris en compte")

    # au repos, la meme phrase part normalement
    g = await tour("ajoute aussi un export CSV", occupe=False)
    dire("ENVOYE" in g and "toi" in g, f"au repos -> envoye : {g}")

    # LE point : un ordre local doit passer MEME pendant une tache
    g = await tour("coupe le micro", occupe=True)
    dire("ordre" in g and "dictee" not in g,
         f"« coupe le micro » passe pendant une tache : {g}")
    g = await tour("stop", occupe=True)
    dire("ordre" in g and "INTERROMPU" in g and "dictee" not in g,
         f"« stop » passe et interrompt vraiment : {g}")

    # et une reponse a une permission aussi
    boucle = asyncio.get_running_loop()
    attente = boucle.create_future()
    g = await tour("oui", occupe=True, permission_en_cours=attente)
    dire("dictee" not in g and attente.done() and attente.result() is True,
         f"« oui » repond bien a la permission en attente : {g}")

    # le reglage peut etre coupe
    import config
    ancien = config.RETENIR_SI_OCCUPE
    config.RETENIR_SI_OCCUPE = False
    try:
        g = await tour("ajoute un export CSV", occupe=True)
        dire("ENVOYE" in g, f"réglage coupé -> l'ancien comportement revient : {g}")
    finally:
        config.RETENIR_SI_OCCUPE = ancien


def le_plafond_suit_le_plancher():
    print("\n=== plafond du delai d'envoi ===")
    import config
    for plancher in (2.0, 3.0, 5.0, 10.0, 20.0):
        plafond = config.plafond_ecoute(plancher)
        dire(plafond > plancher,
             f"plancher {plancher:g} s -> plafond {plafond:g} s (strictement au-dessus)")


def le_rejeu_ne_garde_que_ce_qui_se_relit():
    print("\n=== rejeu d'historique ===")
    import journal

    faux = [
        {"role": "user", "content": "ma question"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "des pages et des pages"},
            {"type": "text", "text": "voila ce que j'ai fait"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/config.py"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "content": "beaucoup de sortie"}]},
    ]

    class M:
        def __init__(self, d):
            self.message = d

    import unittest.mock as mock
    with mock.patch("claude_agent_sdk.get_session_messages",
                    return_value=[M(d) for d in faux]):
        evs, ecartes = journal.rejouer_session("sid-factice", "/x")
    genres = [e["genre"] for e in evs]
    dire(genres == ["toi", "texte", "tour"],
         f"un tour se lit : ce que tu dis, ce qu'il repond, ses actions -> {genres}")
    dire(all("thinking" not in str(e) for e in evs), "la reflexion est ecartee")
    dire(not any(e["genre"] == "resultat" for e in evs), "les sorties d'outils sont ecartees")
    dire(evs[2]["actions"] == 1 and "config.py" in evs[2]["texte"],
         f"les actions sont recapitulees en UNE ligne : {evs[2]['texte']!r}")
    dire(ecartes == 0, "rien d'ecarte par la limite sur un petit historique")

    # deux blocs de texte dans un meme message ne doivent pas faire deux lignes
    deux = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "premier bloc"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/a.py"}},
            {"type": "text", "text": "second bloc"},
        ]},
    ]
    with mock.patch("claude_agent_sdk.get_session_messages",
                    return_value=[M(d) for d in deux]):
        evs2, _ = journal.rejouer_session("sid", "/x")
    lignes_texte = [e for e in evs2 if e["genre"] == "texte"]
    dire(len(lignes_texte) == 1, f"deux blocs -> une seule ligne de texte : {len(lignes_texte)}")
    dire("premier bloc" in lignes_texte[0]["texte"]
         and "second bloc" in lignes_texte[0]["texte"], "et les deux sont conserves")

    # plusieurs tours : chacun son bilan
    trois = []
    for i in range(3):
        trois.append({"role": "user", "content": f"question {i}"})
        trois.append({"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": f"reponse {i}"},
        ]})
    with mock.patch("claude_agent_sdk.get_session_messages",
                    return_value=[M(d) for d in trois]):
        evs3, _ = journal.rejouer_session("sid", "/x")
    dire(sum(1 for e in evs3 if e["genre"] == "tour") == 3,
         "trois tours -> trois bilans, pas un seul agrege")
    dire([e["genre"] for e in evs3[:3]] == ["toi", "texte", "tour"],
         f"et l'ordre est celui de la lecture : {[e['genre'] for e in evs3[:3]]}")


def tout_genre_affiche_a_un_filtre():
    """Un genre qui arrive dans le flux DOIT etre declare dans une famille de filtres.

    Sinon sa ligne est creee avec `display:none` (parce qu'il n'est pas dans les actifs) et
    aucune case ne peut la montrer : elle est invisible pour toujours. C'est arrive au genre
    « session », publie par le worker et absent des libelles.

    On lit les deux cotes dans la source : ce qui est publie d'un cote, ce qui est declare de
    l'autre. Un test qui recopierait la liste ne verrait jamais l'oubli.
    """
    print("\n=== chaque genre affiche a un filtre ===")
    racine = Path(__file__).parent

    publies = set()
    for fichier in ("agent.py", "worker.py", "quota.py", "tableau.py"):
        texte = (racine / fichier).read_text(encoding="utf-8")
        publies |= set(re.findall(r'(?:publier|_voir)\(\s*"([a-z_]+)"', texte))

    page = (racine / "tableau.py").read_text(encoding="utf-8")
    declares = set(re.findall(r'\{\s*g:\s*"([a-z_]+)"', page))
    # Les genres traites avant `ajouter()` n'apparaissent jamais comme ligne du flux : ils
    # pilotent l'interface au lieu de s'y afficher.
    hors_flux = {
        "config", "modeles", "efforts", "delais", "delai", "travail", "etat", "quota",
        "ecoute", "retenir", "tour_quota", "_histoire",
        "pupitre", "parole_fin", "lecture", "moteurs_stt", "moteur_actif",
    }
    attendus = publies - hors_flux
    manquants = sorted(attendus - declares)
    dire(not manquants,
         f"{len(attendus)} genres affiches, tous filtrables"
         + (f" — MANQUENT : {manquants}" if manquants else ""))

    inutiles = sorted(declares - publies)
    dire(not inutiles,
         "aucun filtre pour un genre jamais publie"
         + (f" — EN TROP : {inutiles}" if inutiles else ""))


async def principal():
    logging.disable(logging.CRITICAL)  # le traceback attendu n'a pas a polluer la sortie
    await une_commande_qui_leve_ne_tue_pas_la_socket()
    couper_le_micro_ne_depend_plus_de_l_activite()
    await une_dictee_retenue_ne_sort_pas_dans_le_flux()
    await la_retenue_d_un_tour_ne_vaut_que_pour_lui()
    await la_retenue_pendant_le_travail()
    le_plafond_suit_le_plancher()
    tout_genre_affiche_a_un_filtre()
    le_rejeu_ne_garde_que_ce_qui_se_relit()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
