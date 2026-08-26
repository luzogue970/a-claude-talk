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
    v._amorcer_etat()          # le meme amorçage que la vraie construction
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


async def l_etat_survit_a_l_eviction():
    """Les selecteurs ne doivent JAMAIS etre vides, quelle que soit la longueur de la session.

    Le bug, reproduit : `config`, `modeles`, `efforts`, `delais` et `moteurs_stt` sont publies
    au demarrage, donc ils sont les PREMIERS dans une file bornee — et donc les premiers
    evinces. Sur une conversation longue (ou apres un rejeu d'historique de 1 200 lignes), une
    page ouverte ensuite ne recevait plus rien de tout ca : panneau de configuration absent,
    listes vides, impossible de changer de modele ou de delai. Les conversations courtes
    n'etaient pas touchees, ce qui rendait le defaut incomprehensible.

    La cause de fond : un etat n'est pas un evenement. « Quels modeles existent » reste vrai
    tant que personne ne le change ; « un outil a demarre » appartient a un instant.
    """
    print("\n=== l'etat survit a une session longue ===")
    import json
    from tableau import Tableau, MEMOIRE, ETATS

    t = Tableau(port=7893, ouvrir=False)
    url = await t.demarrer()
    try:
        # Le demarrage se fait AVANT toute connexion : c'est le cas reel, et c'est celui qui
        # cassait, parce que la capture etait placee apres le retour « aucun client ».
        depart = {
            "config": {"valeurs": {"projet": "/home/x/insnap"}},
            "modeles": {"liste": [{"cle": "opus", "libelle": "Opus 5"}], "actuel": "opus"},
            "efforts": {"liste": [{"cle": "xhigh", "libelle": "tres eleve"}],
                        "actuel": "xhigh"},
            "delais": {"paliers": [{"s": 3}, {"s": 5}], "actuel": 5.0, "plafond": 12.5},
            "moteurs_stt": {"liste": [{"cle": "local", "libelle": "local"}],
                            "chaine": ["local"], "actif": "local"},
        }
        for genre, d in depart.items():
            t.publier(genre, **d)
        dire(all(g in t.etat for g in depart),
             "l'etat est retenu meme sans client connecte")

        for i in range(MEMOIRE + 300):
            t.publier("outil", nom="Bash", cible=f"c{i}", id=f"t{i}")
        dans_flux = {e["genre"] for e in t.histoire}
        dire(not (set(depart) & dans_flux),
             "apres une session longue, l'etat a bien disparu du flux (c'est normal)")
        dire(all(g in t.etat for g in depart),
             "mais il est toujours la, hors de la file bornee")

        recus, lignes = {}, 0
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(url.replace("http", "ws") + "/flux") as ws:
                while True:
                    try:
                        m = await asyncio.wait_for(ws.receive(), timeout=2)
                    except asyncio.TimeoutError:
                        break
                    if m.type is not aiohttp.WSMsgType.TEXT:
                        break
                    d = json.loads(m.data)
                    if d.get("genre") == "_etat":
                        recus[d["evenement"]["genre"]] = d["evenement"]
                    elif d.get("genre") == "_histoire":
                        lignes += len(d["evenements"])
        manque = [g for g in depart if g not in recus]
        dire(not manque,
             f"une page ouverte APRES retrouve tout son etat"
             + (f" — MANQUENT {manque}" if manque else ""))
        dire(recus.get("modeles", {}).get("liste"),
             "et les listes ne sont pas vides")
        dire(lignes > 0, f"le flux suit, par lots ({lignes} lignes)")

        # le plus recent gagne : un changement de modele ne doit pas etre annule par l'ancien
        t.publier("modeles", liste=[{"cle": "sonnet"}], actuel="sonnet")
        dire(json.loads(t.etat["modeles"])["actuel"] == "sonnet",
             "un etat republie remplace le precedent")

        dire("outil" not in ETATS and "toi" not in ETATS,
             "les vrais evenements ne sont pas traites comme de l'etat")
    finally:
        await t.arreter()


def un_moteur_sans_direct_est_annonce():
    """Un moteur qui ne transcrit qu'a la fin doit le DIRE.

    Sinon rien ne s'ecrit pendant qu'on parle, le texte apparait plusieurs secondes plus tard
    sans explication, et « retenir » n'a rien a relire au moment de decider. Trois symptomes
    pour une seule cause, et aucun moyen de la deviner depuis l'interface.
    """
    print("\n=== un moteur sans texte en direct s'annonce ===")
    import moteurs_stt as M

    sans = [m for m in M.MOTEURS if not m.direct]
    dire(len(sans) == 2 and {m.cle for m in sans} == {"groq", "local"},
         f"deux moteurs sans direct : {[m.cle for m in sans]}")
    dire(all("fin" in m.note or "direct" in m.note for m in sans),
         "et leur note l'explique")

    avec = [m for m in M.MOTEURS if m.direct]
    dire(all(m.streaming for m in avec),
         "tout moteur avec du direct fait aussi du streaming (l'inverse est faux)")
    local = M.PAR_CLE["local"]
    dire(local.streaming and not local.direct,
         "le local EST un flux mais SANS resultats intermediaires — c'est cette nuance "
         "qui rendait le comportement incomprehensible")

    inv = {m["cle"]: m for m in M.inventaire()}
    dire(inv["local"]["direct"] is False and inv["azure"]["direct"] is True,
         "la capacite est exposee au tableau, qui peut donc l'afficher")


def tout_genre_affiche_a_un_filtre():
    """Un genre qui arrive dans le flux DOIT etre declare dans une famille de filtres.

    Sinon sa ligne est creee avec `display:none` — il n'est pas dans les actifs — et aucune
    case ne peut la montrer : elle est invisible pour toujours. C'est arrive au genre
    « session », publie par le worker et absent des libelles.

    On lit les DEUX cotes dans la source : ce qui est publie d'un cote, ce qui est declare de
    l'autre. Un test qui recopierait la liste ne verrait jamais l'oubli.
    """
    # config.py contenait 155 lignes dupliquees : les sections « ecoute et interruption » a
    # « tableau de bord » y figuraient deux fois, a l'identique. Python garde la DERNIERE
    # definition, donc modifier la premiere ne faisait rien — un reglage change qui reste sans
    # effet, sans aucun message d'erreur. C'est le pire symptome possible sur un fichier de
    # configuration : on croit avoir agi.
    # Le trou qu'il bouche : faster-whisper etait importe par stt_local et absent de
    # requirements.txt. Sur cette machine tout marchait — il avait ete installe a la main —
    # et une installation neuve plantait au premier repli vers le local. Le genre de panne
    # qui n'arrive qu'a celui a qui on partage le depot.
    # Un nom indefini ne se voit ni a la compilation ni aux tests qui ne passent pas par la
    # ligne fautive : j'ai ecrit trois `log.warning` dans worker.py ou `log` n'existait pas.
    # Ca aurait leve un NameError en pleine session, sur le chemin d'erreur — donc au pire
    # moment, celui ou l'on cherche deja pourquoi quelque chose ne marche pas.
    print("\n=== aucun nom indefini ===")
    import subprocess
    racine = Path(__file__).parent
    voix = sorted(str(f) for f in racine.glob("*.py"))
    r = subprocess.run([sys.executable, "-m", "pyflakes", *voix, str(racine.parent / "tests.py")],
                       capture_output=True, text=True)
    graves = [l for l in (r.stdout + r.stderr).splitlines()
              if "undefined name" in l or "syntax" in l.lower()]
    dire(not graves, "aucun nom indefini ni erreur de syntaxe"
                     + (" — " + " ; ".join(graves[:4]) if graves else ""))

    print("\n=== chaque plugin importe est declare dans requirements ===")
    racine = Path(__file__).parent
    exigences = (racine.parent / "requirements.txt").read_text(encoding="utf-8")
    manquants = []
    for fichier in sorted(racine.glob("*.py")):
        texte = fichier.read_text(encoding="utf-8")
        for plugin in set(re.findall(r"from livekit\.plugins import (\w+)", texte)
                          + re.findall(r"from livekit\.plugins\.(\w+)", texte)
                          + re.findall(r"livekit\.plugins\.(\w+)", texte)):
            # turn-detector s'importe sous un autre nom que celui du paquet.
            paquet = {"turn_detector": "turn-detector"}.get(plugin, plugin)
            if f"livekit-plugins-{paquet}==" not in exigences:
                manquants.append(f"{plugin} (dans {fichier.name})")
    dire(not manquants,
         "tout plugin importe est epingle" + (f" — MANQUENT : {sorted(set(manquants))}"
                                              if manquants else ""))
    # Et l'inverse : les versions doivent toutes s'accorder, sinon pip fait remonter le noyau
    # tout seul — c'est exactement ce qu'a fait livekit-plugins-deepgram en tirant la 1.7.0.
    versions = set(re.findall(r"^livekit-(?:agents|plugins-[\w-]+)==([\d.]+)", exigences, re.M))
    dire(len(versions) == 1,
         f"une seule version de livekit epinglee partout : {sorted(versions)}")

    # Une variable CSS absente ne leve RIEN : elle rend la valeur initiale. Pour une couleur
    # de fond, c'est « transparent » — donc invisible. C'est exactement ce qui est arrive :
    # var(--accent) n'etait definie nulle part et les barres du micro devenaient invisibles
    # au moment ou elles devaient montrer quelque chose. Six usages, aucun message d'erreur.
    print("\n=== toute variable CSS utilisee est definie ===")
    page = (racine / "tableau.py").read_text(encoding="utf-8")
    definies = set(re.findall(r"(--[\w-]+)\s*:", page))
    utilisees = set(re.findall(r"var\((--[\w-]+)", page))
    orphelines = sorted(utilisees - definies)
    dire(not orphelines,
         f"{len(utilisees)} variables utilisees, toutes definies"
         + (f" — ABSENTES : {orphelines}" if orphelines else ""))

    print("\n=== aucune constante definie deux fois ===")
    racine = Path(__file__).parent
    for fichier in ("config.py", "moteurs_stt.py", "consommation.py"):
        texte = (racine / fichier).read_text(encoding="utf-8")
        noms = re.findall(r"^([A-Z][A-Z0-9_]*)\s*=", texte, re.M)
        noms += re.findall(r"^def ([a-z_][a-z0-9_]*)\(", texte, re.M)
        vus, doubles = set(), []
        for n in noms:
            (doubles.append(n) if n in vus else vus.add(n))
        dire(not doubles, f"{fichier} : {len(noms)} definitions, aucune en double"
                          + (f" — DOUBLONS : {sorted(set(doubles))}" if doubles else ""))

    print("\n=== chaque genre affiche a un filtre ===")
    racine = Path(__file__).parent

    publies = set()
    for fichier in ("agent.py", "worker.py", "quota.py", "tableau.py"):
        texte = (racine / fichier).read_text(encoding="utf-8")
        publies |= set(re.findall(r'(?:publier|_voir)\(\s*"([a-z_]+)"', texte))

    page = (racine / "tableau.py").read_text(encoding="utf-8")
    declares = set(re.findall(r'\{\s*g:\s*"([a-z_]+)"', page))
    # Les genres traites avant `ajouter()` ne deviennent jamais une ligne du flux : ils
    # pilotent l'interface au lieu de s'y afficher.
    hors_flux = {
        "config", "modeles", "efforts", "delais", "delai", "travail", "etat", "quota",
        "ecoute", "retenir", "tour_quota", "_histoire",
        "pupitre", "parole_fin", "lecture", "moteurs_stt", "moteur_actif", "transcrit",
        "consommation", "conversations",
        # « vider » est un ORDRE ponctuel, pas une ligne du flux : il efface la page au
        # changement de conversation. Il n'est deliberement pas dans ETATS non plus — le
        # rejouer a la reconnexion effacerait ce qu'on vient de recharger.
        "vider",
    }
    attendus = publies - hors_flux
    manquants = sorted(attendus - declares)

    # Le pendant du meme oubli, de l'autre cote : un genre hors-flux qui pilote un morceau
    # DURABLE de l'interface doit figurer dans ETATS, sinon il est evince du deque sur une
    # longue conversation et le morceau reste vide a la reconnexion. C'est exactement ce qui
    # a vide les selecteurs de modele et de delai pendant des semaines.
    durables = {"config", "modeles", "efforts", "delais", "moteurs_stt", "moteur_actif",
                "consommation", "conversations", "micro", "quota", "pupitre", "retenir",
                "travail", "etat"}
    from tableau import ETATS
    oublies = sorted(durables - set(ETATS))
    dire(not oublies,
         "les etats durables survivent a l'eviction du deque"
         + (f" — MANQUENT dans ETATS : {oublies}" if oublies else ""))

    dire(not manquants,
         f"{len(attendus)} genres affiches, tous filtrables"
         + (f" — MANQUENT : {manquants}" if manquants else ""))

    inutiles = sorted(declares - publies)
    dire(not inutiles,
         "aucun filtre pour un genre jamais publie"
         + (f" — EN TROP : {inutiles}" if inutiles else ""))


def le_rejeu_ne_garde_que_ce_qui_se_relit():
    print("\n=== rejeu d'historique ===")
    import journal

    long = "x" * 5000
    faux = [
        {"role": "user", "content": "ma question"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": ""},          # vide sur disque, mesure
            {"type": "text", "text": "voila ce que j'ai fait"},
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/x/config.py"}},
        ]},
        {"role": "user", "content": [
            {"tool_use_id": "t1", "type": "tool_result", "content": long}]},
    ]

    class M:
        def __init__(self, d):
            self.message = d

    import unittest.mock as mock
    with mock.patch("claude_agent_sdk.get_session_messages",
                    return_value=[M(d) for d in faux]):
        evs, ecartes = journal.rejouer_session("sid-factice", "/x")

    genres = [e["genre"] for e in evs]
    dire(genres == ["toi", "texte", "outil", "resultat", "tour"],
         f"tout ce qui a du contenu est rejoue, ligne par ligne : {genres}")
    dire(not any(e["genre"] == "pensee" for e in evs),
         "la reflexion est ecartee : elle est VIDE sur disque (349 blocs, 0 Ko mesures)")

    res = next(e for e in evs if e["genre"] == "resultat")
    dire(len(res["texte"]) <= journal.RESULTAT_MAX + 8,
         f"une sortie d'outil est tronquee : {len(res['texte'])} caracteres")
    dire(res.get("tronque") is True, "et la troncature est signalee")
    dire(res.get("id") == "t1",
         "le resultat garde l'identifiant qui le rattache a son appel")

    outil = next(e for e in evs if e["genre"] == "outil")
    dire(outil.get("id") == "t1" and outil["cible"] == "config.py",
         f"l'appel garde son identifiant et sa cible : {outil}")

    bilan = next(e for e in evs if e["genre"] == "tour")
    dire(bilan["actions"] == 1 and "config.py" in bilan["texte"],
         f"le bilan du tour recapitule quand meme : {bilan['texte']!r}")
    dire(ecartes == 0, "rien d'ecarte par la limite sur un petit historique")

    # plusieurs tours : chacun son bilan, dans l'ordre de lecture
    trois = []
    for i in range(3):
        trois.append({"role": "user", "content": f"question {i}"})
        trois.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"a{i}", "name": "Bash",
             "input": {"command": "ls"}},
            {"type": "text", "text": f"reponse {i}"},
        ]})
    with mock.patch("claude_agent_sdk.get_session_messages",
                    return_value=[M(d) for d in trois]):
        evs3, _ = journal.rejouer_session("sid", "/x")
    dire(sum(1 for e in evs3 if e["genre"] == "tour") == 3,
         "trois tours -> trois bilans, pas un seul agrege")
    dire([e["genre"] for e in evs3[:4]] == ["toi", "outil", "texte", "tour"],
         f"et l'ordre est celui de la lecture : {[e['genre'] for e in evs3[:4]]}")


async def principal():
    logging.disable(logging.CRITICAL)  # le traceback attendu n'a pas a polluer la sortie
    await une_commande_qui_leve_ne_tue_pas_la_socket()
    couper_le_micro_ne_depend_plus_de_l_activite()
    await une_dictee_retenue_ne_sort_pas_dans_le_flux()
    await la_retenue_d_un_tour_ne_vaut_que_pour_lui()
    await la_retenue_pendant_le_travail()
    le_plafond_suit_le_plancher()
    await l_etat_survit_a_l_eviction()
    un_moteur_sans_direct_est_annonce()
    tout_genre_affiche_a_un_filtre()
    le_rejeu_ne_garde_que_ce_qui_se_relit()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
