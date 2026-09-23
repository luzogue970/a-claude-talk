"""Live dashboard: everything the agent hears, thinks, does and says, in a browser.

The point is not decoration. Once permissions are on `auto`, nothing stops a tool call any
more, so the only thing standing between "it works" and "what did it just do" is visibility.
The terminal is a poor place for that — no navigable history, no diffs, log lines interleaved
with speech. A page holds the whole session, filterable, and stays readable while the voice
keeps going.

One HTML file served from this process over aiohttp (already a LiveKit dependency), one
WebSocket carrying a JSON event stream. No build step, no CDN, nothing leaves the machine.
"""

import asyncio
import json
import logging
import os
import time
import webbrowser
from collections import deque
from pathlib import Path
from datetime import datetime

from aiohttp import WSMsgType, web

log = logging.getLogger("voix.tableau")

# Assez pour contenir un historique rejoue (jusqu'a 400 lignes) SANS chasser la session en
# cours : une page ouverte en cours de route doit montrer les deux.
MEMOIRE = 3000

# Les genres qui decrivent un ETAT plutot qu'un instant. Ils survivent a l'eviction du flux et
# sont renvoyes a chaque nouvelle connexion, avant l'historique.
ETATS = frozenset({
    "config", "modeles", "efforts", "delais", "moteurs_stt", "moteur_actif",
    "consommation", "conversations",
    "micro", "quota", "pupitre", "retenir", "session", "travail", "etat",
})


class Tableau:
    def __init__(self, port: int = 7788, ouvrir: bool = True, on_commande=None):
        self.port = port
        self.ouvrir = ouvrir
        # Awaited coroutine (nom, donnees). The page is a control surface, not just a log:
        # cutting the microphone from there is the fastest stop button available, which
        # matters more now that nothing asks permission.
        # Les commandes recues AVANT que l'agent branche son gestionnaire. Le serveur
        # ecoute plusieurs secondes avant que la session soit prete, et la page — donc
        # l'utilisateur — arrive dans cette fenetre : un message tape la etait jete sans
        # un mot. On le garde, et on le rejoue des que le gestionnaire est la.
        self._commandes_en_attente: list[tuple[str, dict]] = []
        self._on_commande = on_commande
        self.histoire: deque = deque(maxlen=MEMOIRE)
        # Une file bornee par client, servie par un seul ecrivain. La version precedente
        # creait une tache asyncio PAR evenement et PAR client, sans jamais les attendre :
        # sur une session de deux jours avec des deltas de reflexion, ca faisait des dizaines
        # de milliers de taches orphelines et 1,5 Go de RSS.
        self.clients: dict[web.WebSocketResponse, asyncio.Queue] = {}
        self._runner: web.AppRunner | None = None
        self._t0 = time.monotonic()
        # Un numero par evenement, croissant. C'est ce qui rend le rejeu d'historique
        # idempotent : une page qui se rebranche recoit toute l'histoire, et doit pouvoir
        # ignorer ce qu'elle affiche deja. Sans ca, trois reconnexions donnaient trois copies
        # de la session — panneau de configuration compris.
        self._n = 0
        self._images = 0
        # L'ETAT, garde a part du flux. Un etat n'est pas un evenement : « quels modeles
        # existent » reste vrai tant que personne ne le change, alors qu'« un outil a demarre »
        # appartient a un instant. Les melanger avait une consequence precise et mesuree : ces
        # lignes sont les PREMIERES publiees, donc les premieres evincees d'une file bornee.
        # Sur une conversation longue, une page ouverte ensuite se retrouvait sans panneau de
        # configuration et avec des selecteurs VIDES.
        self.etat: dict[str, str] = {}

    # --- publication ---------------------------------------------------------
    def vider(self):
        """Oublier tout le flux garde en memoire.

        Appele au changement de conversation. Sans ça, une reconnexion republierait
        l'historique de la conversation PRECEDENTE par-dessus la nouvelle, et on ne saurait
        plus laquelle on lit — ni a quelle conversation appartient un message qu'on relit.
        """
        self.histoire.clear()

    def publier(self, genre: str, **donnees):
        """Never awaited by callers: the voice path must not block on the UI."""
        # Deux horodatages, et ce n'est pas une redondance. « h » est l'heure de l'horloge,
        # la seule qui permette de recouper un événement avec un souvenir, un commit ou un
        # message reçu ailleurs. « t » reste les secondes depuis le lancement : illisible en
        # colonne, mais c'est ce qui donne une latence quand on compare deux lignes. La page
        # affiche l'heure et garde t en infobulle.
        self._n += 1
        evenement = {
            "genre": genre,
            "n": self._n,
            "t": round(time.monotonic() - self._t0, 2),
            "h": datetime.now().strftime("%H:%M:%S"),
            **donnees,
        }
        self.histoire.append(evenement)
        charge = json.dumps(evenement, ensure_ascii=False, default=str)
        if genre in ETATS:
            # AVANT le retour « aucun client » : l'etat de demarrage est publie alors que le
            # navigateur n'est pas encore connecte, et c'est precisement celui qu'on doit
            # retenir. Le placer apres ne l'enregistrait jamais.
            self.etat[genre] = charge
        if not self.clients:
            return
        for file in list(self.clients.values()):
            try:
                file.put_nowait(charge)
            except asyncio.QueueFull:
                # Un client lent ne doit pas faire grossir la memoire : on jette le plus
                # ancien pour garder le plus recent, qui est ce qui interesse.
                try:
                    file.get_nowait()
                    file.put_nowait(charge)
                except Exception:
                    pass

    def journal_handler(self) -> logging.Handler:
        """Forwards Python logging into the page, so LiveKit's own lines land there too."""
        tableau = self

        class _H(logging.Handler):
            def emit(self, record):
                try:
                    tableau.publier(
                        "log",
                        niveau=record.levelname,
                        source=record.name,
                        texte=record.getMessage()[:2000],
                    )
                except Exception:
                    pass

        h = _H()
        h.setLevel(logging.INFO)
        return h

    # --- serveur -------------------------------------------------------------
    @property
    def on_commande(self):
        return self._on_commande

    @on_commande.setter
    def on_commande(self, gestionnaire):
        self._on_commande = gestionnaire
        if gestionnaire and self._commandes_en_attente:
            asyncio.create_task(self._rejouer_commandes())

    async def _rejouer_commandes(self):
        file, self._commandes_en_attente = self._commandes_en_attente, []
        for nom, ordre in file:
            await self._executer(nom, ordre)

    async def _executer(self, nom: str, ordre: dict):
        # Chaque commande est isolee. Sans ce garde-fou, UNE exception dans UNE commande
        # sortait de la boucle de lecture, passait par le finally qui retire le client, et
        # tuait la WebSocket : la page affichait « deconnecte », la commande n'avait pas eu
        # lieu, et il fallait recliquer une fois la reconnexion faite.
        try:
            await self._on_commande(nom, ordre)
        except Exception:
            log.exception("commande « %s » en echec", nom)
            # Un message qui echoue est NOMME : la page peut alors le rendre a son auteur
            # au lieu de le laisser retaper. Les autres commandes disent seulement laquelle.
            supplement = {"message": ordre.get("texte")} if nom == "texte" and ordre.get("texte") else {}
            self.publier("erreur", commande=nom,
                         texte=f"la commande « {nom} » a echoue — details dans les logs",
                         **supplement)

    async def _page(self, _req):
        return web.Response(text=PAGE, content_type="text/html")

    async def _image(self, requete):
        """Recevoir une photo, et rendre son chemin.

        Une maquette griffonnee, une erreur a l'ecran, un bout de partition : le decrire
        au clavier prend plus de temps que de le montrer, et la description perd ce qu'on
        n'a pas pense a dire. Le fichier est ecrit sur la machine, et c'est son CHEMIN qui
        part dans le message — Claude Code sait lire une image depuis un chemin, et rien
        ne transite en base64 dans le flux d'evenements.

        Hors de l'arborescence du projet, volontairement : une photo prise depuis un
        telephone n'a rien a faire dans un depot, et `git status` le dirait a chaque fois.
        """
        TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                 "image/gif": ".gif", "image/heic": ".heic", "image/heif": ".heif"}
        PLAFOND = 25 * 1024 * 1024   # une photo de telephone en fait trois ou quatre

        if not (requete.content_type or "").startswith("multipart/"):
            return web.json_response({"erreur": "envoi multipart attendu"}, status=400)
        lecteur = await requete.multipart()
        piece = await lecteur.next()
        while piece is not None and piece.name != "image":
            piece = await lecteur.next()
        if piece is None:
            return web.json_response({"erreur": "aucune image dans l'envoi"}, status=400)

        mime = (piece.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if mime not in TYPES:
            return web.json_response(
                {"erreur": f"type refuse : {mime or 'inconnu'}"}, status=415)

        dossier = Path(os.environ.get("VOIX_IMAGES")
                       or Path.home() / ".cache" / "claude-talk" / "images")
        dossier.mkdir(parents=True, exist_ok=True)
        self._images += 1
        # Le nom vient de nous, jamais du client : un « ../ » dans le nom d'origine
        # ecrirait ou il veut, et deux photos prises a la meme seconde se marcheraient
        # dessus sans le compteur.
        chemin = dossier / (time.strftime("%Y%m%d-%H%M%S") + f"-{self._images:03d}{TYPES[mime]}")

        octets = 0
        with chemin.open("wb") as sortie:
            while True:
                bloc = await piece.read_chunk()
                if not bloc:
                    break
                octets += len(bloc)
                if octets > PLAFOND:
                    sortie.close()
                    chemin.unlink(missing_ok=True)
                    return web.json_response(
                        {"erreur": f"image trop lourde (plus de {PLAFOND // 1024 // 1024} Mo)"},
                        status=413)
                sortie.write(bloc)
        if not octets:
            chemin.unlink(missing_ok=True)
            return web.json_response({"erreur": "image vide"}, status=400)
        return web.json_response({"chemin": str(chemin), "octets": octets})

    async def _etat(self, _req):
        """« Qui travaille encore ? », en un appel et sans devenir un client de plus.

        Un superviseur qui montre plusieurs sessions cote a cote devait sinon ouvrir un
        WebSocket par session, et recevoir tout leur historique, pour lire deux booleens.
        """
        etat = {}
        for genre, charge in self.etat.items():
            try:
                etat[genre] = json.loads(charge)
            except ValueError:
                continue
        valeurs = (etat.get("config") or {}).get("valeurs") or {}
        session = etat.get("session") or {}
        # Le dernier signe de vie — mais de VIE, pas d'entretien. La consommation des
        # moteurs se republie toutes les vingt secondes, le quota a chaque tour : une
        # session oubliee ouverte depuis ce matin paraissait donc active en permanence,
        # et un superviseur qui ferme les sessions inactives ne fermait jamais rien.
        BRUIT = {"consommation", "quota", "moteurs_stt", "moteur_actif", "pupitre",
                 "conversations", "modeles", "efforts", "delais", "config", "log"}
        dernier = 0.0
        for evenement in reversed(self.histoire):
            if evenement.get("genre") not in BRUIT:
                dernier = evenement.get("t") or 0.0
                break
        for genre, e in etat.items():
            if genre not in BRUIT:
                dernier = max(dernier, e.get("t") or 0.0)
        return web.json_response({
            "projet": valeurs.get("projet", ""),
            "port": self.port,
            "etat": (etat.get("etat") or {}).get("vers", ""),
            "travail": bool((etat.get("travail") or {}).get("actif")),
            "micro": bool((etat.get("micro") or {}).get("actif")),
            "session": {"id": session.get("id", ""), "titre": session.get("titre") or ""},
            # « pret » : l'agent a branche ses commandes. Avant, la page repond mais un
            # message n'a personne pour le recevoir — c'est ce qu'un lanceur doit attendre.
            "pret": self._on_commande is not None,
            "en_attente": len(self._commandes_en_attente),
            "depuis": round(time.monotonic() - self._t0, 1),
            "inactif": round(max(0.0, time.monotonic() - self._t0 - dernier), 1),
            "evenements": self._n,
        })

    # Toutes les 25 s. Sous les 30 s d'inactivite a partir desquelles un proxy, un reseau
    # mobile ou un navigateur en arriere-plan coupe une liaison qu'il croit morte — et c'est
    # exactement ce qui se passait : une session silencieuse pendant que Claude reflechit ne
    # produit AUCUN evenement, donc rien ne traversait la socket pendant des minutes.
    POULS = 25

    def _pouls(self) -> dict:
        """Ce que le serveur sait de lui-meme, envoye regulierement.

        Deux roles pour une seule trame. Cote reseau, elle tient la liaison ouverte : du
        trafic applicatif visible, pas seulement un ping de protocole que ni le navigateur ni
        les intermediaires ne montrent. Cote page, elle repond a « est-ce que ca vit encore ? »
        sans avoir a deviner : tant que le pouls arrive, la liaison est bonne, et son absence
        se mesure en secondes affichables."""
        etat = {}
        for genre, charge in self.etat.items():
            try:
                etat[genre] = json.loads(charge)
            except ValueError:
                continue
        return {
            "genre": "_pouls",
            # L'etat de l'agent voyage avec le pouls : c'est lui qui permet a la page de
            # decider, en fin de reprise, si quelque chose peut ENCORE etre en cours.
            "etat": (etat.get("etat") or {}).get("vers", ""),
            "depuis": round(time.monotonic() - self._t0, 1),
            "clients": len(self.clients),
            "travail": bool((etat.get("travail") or {}).get("actif")),
            "pret": self._on_commande is not None,
            "en_attente": len(self._commandes_en_attente),
            "evenements": self._n,
        }

    async def _ecrivain(self, ws, file: asyncio.Queue):
        while True:
            try:
                charge = await asyncio.wait_for(file.get(), timeout=self.POULS)
            except asyncio.TimeoutError:
                # Rien a dire depuis 25 s : on le dit quand meme. Le pouls passe par le meme
                # ecrivain que les evenements, donc il ne peut pas doubler un envoi en cours
                # ni creer une tache de plus par client.
                charge = json.dumps(self._pouls(), ensure_ascii=False)
            try:
                await ws.send_str(charge)
            except Exception:
                return

    async def _flux(self, requete):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(requete)
        file: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.clients[ws] = file
        ecrivain = asyncio.create_task(self._ecrivain(ws, file))
        try:
            # Par lots de 200 : une reprise complete fait plus de mille evenements, et une
            # trame WebSocket unique de plusieurs centaines de kilo-octets se heurte aux
            # limites du navigateur comme d'aiohttp. Le client les traite dans l'ordre.
            # L'etat d'abord : c'est ce qui rend la page utilisable. Le flux ensuite, et il
            # peut manquer sans que rien ne casse.
            for charge in self.etat.values():
                await ws.send_str(f'{{"genre": "_etat", "evenement": {charge}}}')

            passe = list(self.histoire)
            for i in range(0, len(passe) or 1, 200):
                await ws.send_str(json.dumps(
                    {"genre": "_histoire", "evenements": passe[i:i + 200]},
                    ensure_ascii=False, default=str,
                ))
            # Le mot de la fin de la reprise. La page savait qu'elle etait rebranchee, elle ne
            # savait pas SUR QUOI : agent pret ou non, tour en cours ou non, combien de lignes
            # viennent d'etre rejouees. Rebrancher sur une session morte et rebrancher sur une
            # session qui travaille depuis dix minutes se ressemblaient trait pour trait.
            await ws.send_str(json.dumps(
                {**self._pouls(), "genre": "_bonjour", "rejoue": len(passe)},
                ensure_ascii=False, default=str,
            ))
            async for message in ws:
                if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                if message.type is WSMsgType.TEXT:
                    try:
                        ordre = json.loads(message.data)
                    except (ValueError, TypeError):
                        continue
                    nom = ordre.pop("cmd", None)
                    if not nom:
                        continue
                    if nom == "ping":
                        # Pas une commande : une question sur la liaison elle-meme. Une page
                        # qui revient d'une mise en veille ne peut PAS se fier a l'etat que
                        # lui montre le navigateur — une socket tuee par le systeme reste
                        # « ouverte » cote JavaScript, et tout ce qu'on y ecrit part dans le
                        # vide sans la moindre erreur. Seule une reponse prouve la liaison.
                        await file.put(json.dumps(self._pouls(), ensure_ascii=False))
                        continue
                    if not self._on_commande:
                        # L'agent finit de demarrer : on garde la commande pour lui.
                        self._commandes_en_attente.append((nom, ordre))
                        continue
                    await self._executer(nom, ordre)
        finally:
            ecrivain.cancel()
            self.clients.pop(ws, None)
        return ws

    async def demarrer(self):
        app = web.Application()
        app.router.add_get("/", self._page)
        app.router.add_get("/flux", self._flux)
        app.router.add_get("/etat.json", self._etat)
        app.router.add_post("/image", self._image)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        # Loopback only: this stream carries the content of your code.
        # Un agent laisse tourne garde le port : on le dit et on prend le suivant, plutot
        # que de faire echouer toute la session sur un « address already in use ».
        demande = self.port
        for essai in range(demande, demande + 10):
            try:
                await web.TCPSite(self._runner, os.environ.get("VOIX_UI_HOTE", "127.0.0.1"), essai).start()
                self.port = essai
                break
            except OSError:
                continue
        else:
            raise RuntimeError(
                f"aucun port libre entre {demande} et {demande + 9} — un agent est-il resté "
                f"lancé ? `pkill -f voix/agent.py`")
        if self.port != demande:
            log_defaut = f"port {demande} occupé, tableau de bord sur {self.port}"
            print(f"  ATTENTION : {log_defaut}")
        url = f"http://127.0.0.1:{self.port}"
        if self.ouvrir:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return url

    async def arreter(self):
        for ws in list(self.clients):
            try:
                await ws.close()
            except Exception:
                pass
        self.clients.clear()
        if self._runner:
            await self._runner.cleanup()


PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>claude-talk</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2032%2032%27%3E%3Cpath%20d%3D%27M15.13%2011.89Q15.38%207.50%2016.00%202.20Q16.62%207.50%2016.87%2011.89Z%20M17.30%2012.01Q19.12%209.54%2021.40%206.65Q20.03%2010.07%2018.81%2012.88Z%20M19.12%2013.19Q23.05%2011.21%2027.95%209.10Q23.67%2012.29%2019.99%2014.70Z%20M20.11%2015.13Q23.15%2015.47%2026.80%2016.00Q23.15%2016.53%2020.11%2016.87Z%20M19.99%2017.30Q23.67%2019.71%2027.95%2022.90Q23.05%2020.79%2019.12%2018.81Z%20M18.81%2019.12Q20.03%2021.93%2021.40%2025.35Q19.12%2022.46%2017.30%2019.99Z%20M16.87%2020.11Q16.62%2024.50%2016.00%2029.80Q15.38%2024.50%2015.13%2020.11Z%20M14.70%2019.99Q12.88%2022.46%2010.60%2025.35Q11.97%2021.93%2013.19%2019.12Z%20M12.88%2018.81Q8.95%2020.79%204.05%2022.90Q8.33%2019.71%2012.01%2017.30Z%20M11.89%2016.87Q8.85%2016.53%205.20%2016.00Q8.85%2015.47%2011.89%2015.13Z%20M12.01%2014.70Q8.33%2012.29%204.05%209.10Q8.95%2011.21%2012.88%2013.19Z%20M13.19%2012.88Q11.97%2010.07%2010.60%206.65Q12.88%209.54%2014.70%2012.01Z%27%20fill%3D%27%2358a6ff%27%2F%3E%3C%2Fsvg%3E">
<style>
:root{
  color-scheme:dark;
  --fond:#0e1116; --carte:#161b22; --bord:#272e37; --texte:#d7dde5; --faible:#8b949e;
  --toi:#58a6ff; --voix:#3fb950; --pensee:#a371f7; --outil:#d29922; --erreur:#f85149;
  --permission:#ff7b72; --tour:#39c5cf;
  /* « ce qui est vif en ce moment » : le micro qui entend, la conversation courante, une
     jauge qui se remplit. Distinct de --toi (ce que TU dis) et de --voix (ce qu'il répond) :
     ici c'est l'état de l'application, pas un locuteur.
     Elle a manqué pendant six utilisations : var(--accent) n'était définie nulle part, donc
     les barres du micro devenaient TRANSPARENTES dès qu'on parlait — le rond paraissait vide
     au moment précis où il devait montrer quelque chose. Une variable CSS absente ne lève
     rien : elle rend la valeur initiale, et pour une couleur de fond c'est « invisible ». */
  --accent:#4f9dde;
  /* La hauteur de la barre de saisie, declaree UNE fois. Trois elements reservent de la
     place au-dessus d'elle — le bas du flux, le bouton « suivre », la pile de notes — et
     chacun portait sa propre constante. Quand la barre est passee a deux lignes, les trois
     se sont retrouvees fausses en meme temps, et « suivre » a fini derriere la barre.
     14 haut + 52 (le champ) + 8 (l'interligne) + 48 (les boutons) + 20 bas = 142. */
  --barre:142px;
  /* « ça attend ta relecture » : la note contre la barre quand une dictée est retenue.
     L'ambre plutôt que le rouge — rien n'est cassé, quelque chose demande un geste. */
  --retenu:#d8a657;
}
*{box-sizing:border-box}

/* Les barres de defilement natives cassaient l'ensemble : une gouttiere claire avec des
   fleches, au milieu d'une interface sombre. Fines, sans fleches, et de la couleur des
   bordures — elles se voient quand on les cherche et disparaissent sinon. */
*{scrollbar-width:thin;scrollbar-color:#39414d transparent}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#2f3641;border-radius:99px;
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:#454f5d;background-clip:content-box}
::-webkit-scrollbar-corner{background:transparent}
::-webkit-scrollbar-button{display:none}

/* Une interface qui bouge doit bouger doucement. Un seul reglage plutot qu'une transition
   recopiee sur chaque element — et 120 ms, assez pour etre percu, trop court pour attendre. */
button,select,summary,input,textarea,.q,.act,.pip,#etat,#travail,#compte{
  transition:background-color .12s ease,border-color .12s ease,color .12s ease,opacity .12s ease}
:focus-visible{outline:2px solid var(--toi);outline-offset:1px}
body{margin:0;background:var(--fond);color:var(--texte);
  font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:#0e1116ee;backdrop-filter:blur(8px);
  border-bottom:1px solid var(--bord);padding:9px 16px;display:flex;gap:10px 14px;
  align-items:center;flex-wrap:wrap}
/* Le chemin du retour. La page est servie comme une destination parmi d'autres : sans lui,
   revenir a la liste des sessions demandait le bouton « precedent » du navigateur — qui
   n'existe pas quand la page tourne en plein ecran sur un telephone. */
#joindre{cursor:pointer;font-size:17px}
#jointes{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}
#jointes[hidden]{display:none}
#jointes .jointe{position:relative;width:52px;height:52px;border-radius:9px;overflow:hidden;
  border:1px solid var(--bord);background:var(--carte)}
#jointes .jointe img{width:100%;height:100%;object-fit:cover;display:block}
#jointes .jointe.envoi{opacity:.45}
#jointes .jointe button{position:absolute;top:1px;right:1px;width:18px;height:18px;padding:0;
  border:0;border-radius:50%;background:#000000b0;color:#fff;font-size:11px;line-height:18px;
  cursor:pointer}
#jointes .rate{border-color:var(--erreur);color:var(--erreur);font-size:9.5px;padding:3px;
  width:auto;max-width:150px;height:auto;line-height:1.3}
#retour{display:none;align-items:center;justify-content:center;width:30px;height:30px;
  border:1px solid var(--bord);border-radius:9px;color:var(--faible);text-decoration:none;
  font-size:16px;line-height:1;flex:none}
#retour[href]{display:inline-flex}
#retour:hover{border-color:#4b5563;color:var(--texte)}
/* Une zone ne se coupe pas en deux : ses elements se replient ensemble ou pas du tout. */
.zone{display:inline-flex;gap:8px;align-items:center;flex-wrap:nowrap}
.zone-direct{margin-left:auto}

/* Les conversations en parallele. Une seule ecoute a la fois : le micro est la seule
   ressource vraiment exclusive, et deux agents qui ecoutent transcrivent la meme phrase
   deux fois, chacun pour son Claude. */
#pupitre{display:inline-flex;gap:5px;align-items:center}
#pupitre:empty{display:none}
#pupitre a,#pupitre span.sess{border:1px solid var(--bord);border-radius:999px;
  padding:2px 9px;font-size:11.5px;color:var(--faible);text-decoration:none;
  white-space:nowrap;display:inline-flex;gap:5px;align-items:center}
#pupitre a:hover{border-color:#4b5563;color:var(--texte)}
/* Celle qu'on regarde : pleine. Celle qui ecoute : un point vert. Les deux se distinguent,
   parce qu'on peut regarder une conversation sans lui parler. */
#pupitre .moi{color:var(--texte);border-color:#4b5563;background:#1f242c;font-weight:650}
#pupitre .ecoute::before{content:"";width:6px;height:6px;border-radius:50%;
  background:var(--voix)}
#pupitre .muette::before{content:"";width:6px;height:6px;border-radius:50%;
  background:#3a424d}
#pupitre .parle::after{content:"◗";color:var(--voix);font-size:9px}
#pupitre button{background:transparent;border:1px solid var(--toi);border-radius:999px;
  color:#9ecbff;padding:2px 9px;font:inherit;font-size:11.5px;cursor:pointer;white-space:nowrap}
#pupitre button:hover{background:#132133}

/* Qui transcrit, en ce moment. C'est la premiere question qu'on se pose quand une
   transcription est mauvaise, et la reponse n'etait nulle part. */
#moteur{border:1px solid var(--bord);border-radius:999px;padding:2px 10px;font-size:11.5px;
  color:var(--faible);cursor:pointer;white-space:nowrap;display:none;
  gap:5px;align-items:center}
#moteur.montre{display:inline-flex}
#moteur:hover{border-color:#4b5563;color:var(--texte)}
#moteur.replie{border-color:var(--outil);color:#e3b341}

/* Le bouton des conversations n'avait AUCUN style propre : il héritait du bouton générique et
   détonnait à côté des pastilles de l'en-tête, plus grand et plus dur. Même forme que
   #moteur — c'est la forme de référence ici, et deux éléments voisins qui font la même chose
   (ouvrir un panneau) doivent se ressembler.
   Le chiffre est mis en avant dans le libellé plutôt que le mot : c'est lui qu'on lit. */
#convs{border:1px solid var(--bord);border-radius:999px;padding:2px 10px 2px 8px;
  font-size:11.5px;color:var(--faible);cursor:pointer;white-space:nowrap;
  display:inline-flex;gap:5px;align-items:center;background:none;font-family:inherit;
  transition:border-color .15s,color .15s,background .15s}
#convs:hover{border-color:#4b5563;color:var(--texte);background:#1a1f27}
/* Borne + ellipse : le titre est genere par un modele, donc sa longueur ne se controle pas
   depuis ici. Sans borne, un titre bavard elargissait l'en-tete et poussait les pastilles de
   quota hors de vue. */
#convs b{color:var(--texte);font-weight:650;max-width:230px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
#convs .rond{width:7px;height:7px;border-radius:50%;background:var(--accent);flex:none}
#moteur::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--voix)}
#moteur.replie::before{background:var(--outil)}

/* Le choix du moteur : une liste avec ce que donne chaque palier gratuit. Sans cette
   information, choisir revient a tirer au sort. */
.avec-choix{position:relative;display:inline-flex}
/* Ancré à DROITE et borné à la fenêtre. Ancré à gauche avec une largeur minimale de 430 px,
   il dépassait de 124 px — mesuré — et élargissait la page entière : on se retrouvait à
   défiler latéralement sans raison, avec une partie du contenu cachée. Un panneau flottant ne
   doit jamais pouvoir agrandir la page qui le porte.
   `max-height` + défilement interne : le même raisonnement en vertical, neuf moteurs sur un
   petit écran sortaient par le bas sans qu'on puisse les atteindre. */
#choix-moteur{position:absolute;top:calc(100% + 8px);right:0;z-index:30;
  width:max-content;max-width:min(430px, calc(100vw - 32px));
  max-height:min(70vh, 620px);overflow-y:auto;
  background:var(--carte);border:1px solid var(--bord);border-radius:10px;padding:11px 13px;
  box-shadow:0 12px 32px #00000080;font-size:11.5px;color:var(--faible)}
#choix-moteur[hidden]{display:none}
#choix-moteur .m{display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:8px;
  align-items:baseline;padding:4px 3px;border-radius:5px}
#choix-moteur .m:hover{background:#1f242c}
#choix-moteur .nom{color:var(--texte);font-weight:650}
#choix-moteur .quoi{color:var(--faible)}
#choix-moteur .rang{color:#5a636e;font-variant-numeric:tabular-nums}
#choix-moteur .absent{opacity:.5}
#choix-moteur .jauge{height:3px;border-radius:2px;background:#252b34;margin-top:5px;
  overflow:hidden}
#choix-moteur .jauge i{display:block;height:100%;background:var(--accent);
  transition:width .4s ease}
#choix-moteur .jauge.tendu i{background:#d8a657}
#choix-moteur .jauge.vide i{background:#c9605e}
#choix-moteur .reste{color:var(--faible);font-variant-numeric:tabular-nums;
  white-space:nowrap;text-align:right}
#choix-moteur .reste b{color:var(--texte);font-weight:650}
#choix-moteur .reste .vide{color:#c9605e;font-weight:650}
/* « en cours » : ce moteur est en train de consommer, là, maintenant. Sans ce repère, un
   chiffre qui monte tout seul ressemble à une erreur d'affichage. */
#choix-moteur .reste .vif{color:var(--accent);font-weight:650}

/* Le sélecteur de conversations. Volontairement proche d'une liste de messagerie plutôt que
   d'un tableau : on choisit une conversation en la RECONNAISSANT, pas en lisant sa fiche
   technique. D'où la dernière phrase dite en évidence, et l'horodatage en retrait. */
#choix-conv{position:absolute;top:calc(100% + 8px);right:0;z-index:30;
  width:min(560px, calc(100vw - 32px));max-height:min(64vh, 560px);overflow-y:auto;
  background:var(--carte);border:1px solid var(--bord);border-radius:12px;
  padding:8px;font-size:12.5px;box-shadow:0 14px 40px #0009}
#choix-conv[hidden]{display:none}
#choix-conv .c{display:block;width:100%;text-align:left;background:none;border:0;
  border-radius:9px;padding:9px 11px;cursor:pointer;color:inherit;font:inherit;
  border-left:2px solid transparent}
#choix-conv .c:hover{background:#1f242c}
#choix-conv .c.active{border-left-color:var(--accent);background:#1b2027}
#choix-conv .c.morte{opacity:.45}
#choix-conv .haut{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
/* Une pastille d'état à gauche du nom. La FORME porte l'information autant que la couleur —
   pleine, creuse, barrée — pour que ça reste lisible sans distinguer les teintes. */
#choix-conv .etat{display:inline-block;width:9px;height:9px;border-radius:50%;
  margin-right:7px;flex:none;vertical-align:baseline;border:1.5px solid var(--faible)}
#choix-conv .etat.vit{background:var(--accent);border-color:var(--accent);
  box-shadow:0 0 0 3px #4f9dde26}
#choix-conv .etat.close{background:transparent;border-color:#4a525e}
#choix-conv .etat.coupee{background:transparent;border-color:#d8a657;
  border-style:dashed}
#choix-conv .etat.prise{background:#6b7480;border-color:#6b7480}
#choix-conv .c{position:relative}
/* Le nombre de reprises est une information de densité, pas une phrase : une puce compacte
   se lit d'un coup, « repris 7 fois » noyé dans une ligne de métadonnées non. */
#choix-conv .repr{display:inline-block;background:#252b34;border-radius:5px;
  padding:1px 6px;margin-left:6px;color:var(--faible);font-size:11px}
#choix-conv .nom{color:var(--texte);font-weight:650}
#choix-conv .quand{color:var(--faible);white-space:nowrap;font-variant-numeric:tabular-nums}
#choix-conv .dit{color:var(--faible);margin-top:3px;line-height:1.45;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
#choix-conv .meta{color:#5a636e;margin-top:3px}
#choix-conv .ici{color:var(--accent)}
#choix-conv .titre{color:#5a636e;padding:8px 11px 4px;letter-spacing:.02em}
/* L'action est distincte des entrées de liste : bordée en pointillé, sans pastille d'état.
   La mêler aux conversations existantes ferait cliquer dessus par erreur. */
#choix-conv .c.neuve{border:1px dashed #3a4250;border-left-width:1px;margin-bottom:4px}
#choix-conv .c.neuve .nom{color:var(--accent)}
#choix-conv .c.neuve .plus{display:inline-block;width:15px;text-align:center;font-weight:700}
#choix-conv .pied{border-top:1px solid var(--bord);margin-top:8px;padding:9px 11px 4px;
  color:var(--faible);line-height:1.5}
#choix-moteur .pied{border-top:1px solid var(--bord);margin-top:8px;padding-top:8px;
  line-height:1.5}
#choix-moteur .pied b{color:var(--texte)}
h1{font-size:14px;margin:0;font-weight:650;letter-spacing:.02em;
  display:flex;gap:8px;align-items:center}

/* La marque de l'application : une gerbe radiale aux rayons inégaux. Même famille visuelle
   que l'éclat de Claude, sans en être une copie — et les longueurs alternées donnent la
   lecture « une voix qui rayonne », qui appartient à cette application.
   Elle ne bouge pas. Une identité n'a pas de raison de tourner : le mouvement veut dire
   « quelque chose se passe », et le dire en permanence, c'est ne plus rien dire. L'état du
   travail est porté par .spin, en bas, et par lui seul. */
.marque{width:17px;height:17px;flex:none;color:var(--toi)}
.marque path{fill:currentColor}
#cogitation .marque{color:var(--pensee)}

/* L'indicateur d'activité, distinct de la marque : un simple arc qui tourne. Il ne
   ressemble pas au logo, justement parce qu'il ne dit pas la même chose. */
.spin{width:11px;height:11px;flex:none;border:1.7px solid #ffffff26;
  border-top-color:currentColor;border-radius:50%;
  animation:tourne .75s linear infinite}

/* Le bandeau de cogitation, posé juste au-dessus de la barre de saisie. En position absolue
   pour qu'il apparaisse et disparaisse sans jamais pousser le contenu du flux. */
#pile-barre{position:absolute;bottom:100%;left:16px;right:16px;margin-bottom:8px;
  display:flex;flex-direction:column;align-items:flex-start;gap:6px;pointer-events:none}
#pile-barre > *{pointer-events:auto;max-width:100%}
#cogitation{display:flex;gap:8px;align-items:center;background:var(--carte);
  border:1px solid var(--bord);border-radius:999px;padding:5px 13px 5px 10px;
  font-size:12.5px;color:var(--pensee)}
#cogitation[hidden]{display:none}

/* La note de la barre : pourquoi ce texte est là et ce qu'on en attend. Placée contre la
   barre, du côté opposé à l'indicateur d'activité pour qu'ils puissent coexister. Elle
   s'efface d'elle-même : une explication qui reste affichée alors que la situation a changé
   devient un mensonge. */
#note-barre{align-self:flex-end;
  display:flex;gap:7px;align-items:center;background:var(--carte);
  border:1px solid var(--retenu);border-radius:999px;padding:5px 13px;
  font-size:12.5px;color:var(--retenu);
  opacity:0;transition:opacity .25s ease;pointer-events:none}
#note-barre.montre{opacity:1}
#note-barre[hidden]{display:none}

/* Le message qui attend la fin de la lecture. Il PART bien — LiveKit le met en file derrière
   la parole en cours — mais la barre se vidait aussitôt et aucune ligne n'apparaissait avant
   plusieurs secondes : le texte semblait s'être évaporé. Le montrer en grisé dit les deux
   choses qui manquaient, qu'il existe encore et pourquoi il ne part pas tout de suite. */
#en-attente,#en-vol{align-self:stretch;
  display:flex;gap:9px;align-items:flex-start;background:var(--carte);
  border:1px solid var(--bord);border-left:2px solid var(--voix);border-radius:10px;
  padding:9px 13px;font-size:12.5px;color:var(--faible);max-width:1100px}
#en-attente[hidden],#en-vol[hidden]{display:none}
#en-vol{flex-direction:column;gap:6px;border-left-color:var(--toi)}
#en-vol .vol{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
#en-vol .vol .quoi{flex:none;color:var(--toi)}
#en-vol .vol.tarde .quoi{color:var(--erreur)}
#en-vol .vol button{background:transparent;color:var(--texte);border:1px solid var(--bord);
  border-radius:999px;padding:4px 11px;font:inherit;font-size:12px;cursor:pointer;min-height:32px}
#note-barre.collante{border-color:var(--erreur);color:#ffb3ad;cursor:pointer}
#en-attente .dit,#en-vol .dit{color:#9aa4b0;flex:1;min-width:0;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
#en-attente .quoi{color:var(--voix);white-space:nowrap;flex:none}
#cogitation .marque{color:var(--pensee)}
#mot{font-weight:600;letter-spacing:.01em}
.points i{font-style:normal;animation:clignote 1.4s ease-in-out infinite}
.points i:nth-child(2){animation-delay:.18s}
.points i:nth-child(3){animation-delay:.36s}
@keyframes clignote{0%,100%{opacity:.18}45%{opacity:1}}
#etat{font-weight:600;padding:2px 11px;border-radius:999px;border:1px solid var(--bord);
  font-size:12px;display:inline-flex;gap:6px;align-items:center}
/* Un point de la couleur de l'etat : on le lit avant d'avoir lu le mot. */
#etat::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.e-listening{color:var(--toi);border-color:var(--toi)}
.e-initializing{color:var(--faible);border-color:var(--bord)}
.e-thinking{color:var(--pensee);border-color:var(--pensee)}
.e-speaking{color:var(--voix);border-color:var(--voix)}
.mesures{display:flex;gap:10px;align-items:center;min-width:0}
#compteurs{display:flex;gap:8px;align-items:center;
  color:var(--faible);font-size:12px;font-variant-numeric:tabular-nums}
.q{border:1px solid var(--bord);border-radius:999px;padding:2px 9px;white-space:nowrap;
  display:inline-flex;gap:5px;align-items:baseline}
.q b{font-weight:650;color:var(--texte)}
/* Le temps restant : present, mais secondaire au pourcentage. */
.q i{font-style:normal;color:#6e7681;font-size:11px}
.q.chaud i{color:#d99f9a}
.q.vide{border-style:dashed;color:#5a636e}
.q.tiede{border-color:#8a6d1f} .q.tiede b{color:#e3b341}
.q.chaud{border-color:var(--erreur)} .q.chaud b{color:#ffb3ad}
#micro,#arreter{background:transparent;color:var(--faible);border:1px solid var(--bord);
  border-radius:999px;padding:3px 12px;font-size:12px;cursor:pointer;font-family:inherit;
  white-space:nowrap}
#micro:hover,#arreter:not(:disabled):hover{border-color:#4b5563;color:var(--texte)}
#micro.coupe{background:#3d1518;border-color:var(--erreur);color:#ffb3ad;font-weight:650}
/* Un clic parti dans le vide doit se voir attendre, pas passer pour un clic sans effet. */
#micro.attente{border-color:#8a6d1f;color:#e3b341;animation:pulse 1s ease-in-out infinite}
#arreter:not(:disabled){border-color:#8a6d1f;color:#e3b341}
#arreter:disabled{opacity:.35;cursor:default}
#modele,#effort{background:var(--carte);color:var(--texte);border:1px solid var(--bord);
  border-radius:999px;padding:3px 8px;font-size:12px;font-family:inherit;cursor:pointer}
#modele.temporaire{border-color:#8a6d1f;color:#e3b341}
/* Grisé pendant une tâche : le niveau d'effort est figé à la construction de la session, donc
   le changer impose de reconstruire le client — impossible sans tuer le travail en cours. */
#effort:disabled{opacity:.4;cursor:not-allowed}
#travail{border:1px solid var(--pensee);color:#c3a6f5;border-radius:999px;padding:2px 10px;
  font-size:12px;font-variant-numeric:tabular-nums;display:none;white-space:nowrap}
/* Le compte à rebours avant envoi. Il existe parce que « quand est-ce que ça part ? » était
   invisible : on parlait, ça partait, et on découvrait la coupure après coup. */
#compte{display:none;align-items:center;gap:0;border:1px solid var(--toi);border-radius:999px;
  overflow:hidden;font-size:12px;white-space:nowrap}
#compte.montre{display:inline-flex}
#compte.prolonge{border-color:var(--pensee)}
#reste{padding:2px 10px;color:#9ecbff;font-variant-numeric:tabular-nums}
#compte.prolonge #reste{color:#c3a6f5}
#envoi-vite{background:transparent;border:0;border-left:1px solid var(--toi);
  color:#9ecbff;padding:3px 11px;font:inherit;font-size:12px;cursor:pointer;
  font-weight:650}
#compte.prolonge #envoi-vite{border-left-color:var(--pensee);color:#c3a6f5}
#envoi-vite:hover{background:#1f2d3f;color:#cde3ff}
/* Rattraper le message pendant son décompte, sans avoir armé « retenir » à l'avance. */
#retenir-vite{background:transparent;border:0;border-left:1px solid var(--toi);
  color:#9ecbff;padding:3px 11px;font:inherit;font-size:12px;cursor:pointer}
#compte.prolonge #retenir-vite{border-left-color:var(--pensee);color:#c3a6f5}
#retenir-vite:hover{background:#1f2d3f;color:#cde3ff}
#compte.rattrape #reste{color:#c3a6f5}
#compte.rattrape #retenir-vite{display:none}
/* Les filtres, par famille. Dix-huit boutons alignes ne disaient ni ce qu'ils montraient ni
   pourquoi on voudrait les couper. Une liste deroulante par famille, avec une phrase par
   ligne, se lit sans documentation. */
/* Le second rang : les filtres a gauche, les mesures a droite. */
.rang-bas{flex:1 0 100%;display:flex;gap:14px;align-items:center;justify-content:space-between;
  padding-top:8px;border-top:1px solid #171c23;margin-top:2px}
#filtres{display:flex;gap:6px;flex-wrap:wrap;min-width:0}
.famille{position:relative}
.famille summary{list-style:none;cursor:pointer;user-select:none;
  background:transparent;color:var(--faible);border:1px solid var(--bord);
  border-radius:999px;padding:2px 11px;font-size:12px;white-space:nowrap;
  display:inline-flex;gap:5px;align-items:center}
.famille summary::-webkit-details-marker{display:none}
.famille summary::after{content:"▾";font-size:9px;opacity:.6}
.famille summary:hover{border-color:#4b5563;color:var(--texte)}
.famille summary b{font-weight:650;font-variant-numeric:tabular-nums;font-size:11px}
/* L'etat de la famille se lit sans l'ouvrir : tout affiche, rien affiche, ou partiel. */
.famille summary.pleine{color:var(--texte);border-color:#4b5563;background:#1f242c}
.famille summary.vide{color:#5a636e;border-style:dashed}
.famille[open] summary{border-color:var(--toi);color:#9ecbff}

.famille .panneau{position:absolute;top:calc(100% + 6px);left:0;z-index:20;min-width:340px;
  background:var(--carte);border:1px solid var(--bord);border-radius:10px;padding:10px 12px;
  box-shadow:0 12px 32px #00000073}
.famille .aide{margin:0 0 8px;font-size:11.5px;color:var(--faible);line-height:1.4}
.famille label{display:grid;grid-template-columns:auto 78px 1fr;gap:8px;align-items:baseline;
  padding:4px 2px;cursor:pointer;border-radius:5px}
.famille label:hover{background:#1f242c}
.famille label input{margin:0;accent-color:var(--toi);cursor:pointer}
.famille .nom{font-size:12px;font-weight:650;color:var(--texte);
  text-transform:uppercase;letter-spacing:.03em}
.famille .quoi{font-size:11.5px;color:var(--faible);line-height:1.4}
.famille .tout-rien{display:flex;gap:6px;margin-top:9px;padding-top:8px;
  border-top:1px solid var(--bord)}
.famille .tout-rien button{background:transparent;color:var(--faible);
  border:1px solid var(--bord);border-radius:999px;padding:2px 12px;font-size:11.5px;
  cursor:pointer;font-family:inherit}
.famille .tout-rien button:hover{border-color:#4b5563;color:var(--texte)}
main{padding:14px 16px calc(var(--barre) + 30px);max-width:1100px;margin:0 auto}

/* Écrire au lieu de parler. Utile quand le micro est coupé, quand le mot est trop
   technique pour être dicté proprement, ou quand quelqu'un dort à côté. */
/* La barre respire : 18 px sous le champ plutot que le bord de l'ecran. Collee en bas, elle
   donnait l'impression d'une fenetre coupee — et sur un portable, la zone la plus basse est
   celle qu'on atteint le moins bien. */
body{overflow-x:hidden}
#saisie-barre{position:fixed;bottom:0;left:0;right:0;z-index:6;
  background:linear-gradient(to top,#0e1116 62%,#0e1116e0);backdrop-filter:blur(10px);
  border-top:1px solid var(--bord);
  padding:14px 16px 20px;display:flex;justify-content:center}
/* Deux lignes, toujours : le champ occupe SA ligne, les boutons viennent dessous. Sur une
   seule ligne, sept controles et un textarea se disputaient la largeur — le champ finissait
   a une poignee de pixels, les libelles se chevauchaient, et c'est la zone ou l'on passe
   tout son temps qui reculait. Separes, le champ garde toute la largeur quelle que soit la
   taille de l'ecran, et les boutons gardent la leur. */
#saisie-barre form{display:flex;flex-wrap:wrap;gap:8px 10px;width:100%;max-width:1100px;
  align-items:center}

/* Un textarea, pas un input : une longue dictee doit rester ENTIEREMENT visible. Sur une
   seule ligne, le texte defilait hors du champ et on ne voyait plus ce qu'on dictait.
   La hauteur est calculee en JS (scrollHeight) ; le CSS ne fait que l'animer. */
/* La zone principale de l'interface, et elle doit le paraître. Elle faisait 36 px de haut en
   13 px de texte : on la cherchait. C'est pourtant là qu'on passe tout son temps — on y dicte,
   on y relit, on y corrige, on y envoie.
   Plus haute (52 px), plus grande en texte (14,5 px), et un fond légèrement détaché du reste
   pour qu'elle se trouve d'un coup d'œil. Elle grandit toujours avec le contenu et redescend
   quand on efface : plus visible, pas plus envahissante. */
#saisie{flex:1 0 100%;min-width:0;background:#161b22;color:var(--texte);
  border:1px solid #303845;border-radius:24px;padding:14px 18px;font:inherit;
  font-size:14.5px;line-height:1.55;outline:none;resize:none;
  display:block;height:52px;max-height:55vh;overflow-y:hidden;
  transition:height .12s ease-out,border-radius .12s ease-out,border-color .12s,
             box-shadow .15s}
/* Une seule ligne : la pastille arrondie du reste de l'interface. Plusieurs lignes : un coin
   plus sobre, sinon la boite ressemble a une gelule etiree. */
#saisie.une-ligne{border-radius:999px}
/* Au focus, la zone s'affirme franchement : un halo, pas un simple filet de 1 px qui se
   confond avec le reste dans une interface sombre. */
#saisie:focus{border-color:var(--toi);box-shadow:0 0 0 3px #4f9dde22;background:#1a212b}
#saisie::placeholder{color:#5a636e}

/* Une gouttiere discrete plutot que celle du systeme, qui arrivait comme une barre grise
   large au milieu d'une interface sombre. */
#saisie::-webkit-scrollbar{width:8px}
#saisie::-webkit-scrollbar-thumb{background:#39414c;border-radius:4px}
#saisie::-webkit-scrollbar-track{background:transparent}

/* --- le micro du bas -------------------------------------------------------------------
   Rond et assez grand pour etre atteint sans viser (40 px : la cible confortable au pouce
   comme a la souris). Les cinq ondes bougent quand la parole est DETECTEE — pas un
   volume : on ne mesure pas le niveau audio ici, et animer au hasard donnerait une fausse
   confirmation d'etre entendu, ce qui est pire que pas d'indicateur du tout. */
.micro-rond{flex:none;width:48px;height:48px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  background:var(--carte);border:1px solid var(--bord);cursor:pointer;
  transition:background .15s,border-color .15s,box-shadow .15s}
.micro-rond:hover{background:#1f242c}
.micro-rond.ouvert{border-color:var(--toi)}
.micro-rond.parle{border-color:var(--accent);box-shadow:0 0 0 3px #4f9dde1f}
.micro-rond.coupe{border-color:#5a3a3a;background:#22181a}
/* Cinq barres, et elles doivent SE VOIR. La première version faisait 2,5 x 5 px dans un rond
   de 48 : mesuré, et à cette taille le bouton paraît vide. Un indicateur qu'on ne remarque pas
   ne remplit pas son office — il fait juste croire que rien ne marche.
   3,5 px de large, et un profil d'égaliseur AU REPOS (6-11-14-11-6) plutôt que cinq traits
   identiques : la forme dit « micro » avant même qu'on ait bougé. */
.ondes{display:flex;align-items:center;justify-content:center;gap:3px;height:24px}
.ondes i{display:block;width:3.5px;border-radius:2px;background:#8b95a3;
  transition:height .14s ease-out,background .15s,opacity .15s}
.ondes i:nth-child(1),.ondes i:nth-child(5){height:6px}
.ondes i:nth-child(2),.ondes i:nth-child(4){height:11px}
.ondes i:nth-child(3){height:14px}
.micro-rond.ouvert .ondes i{background:#aab4c0}

/* Parole détectée : elles montent, décalées, pour que ça ondule au lieu de clignoter en bloc.
   Les hauteurs de fin diffèrent par barre — une animation où tout monte à la même hauteur
   ressemble à un chargement, pas à une voix. */
.micro-rond.parle .ondes i{background:var(--accent);
  animation:onde .85s ease-in-out infinite}
@keyframes onde{0%,100%{height:7px}50%{height:22px}}
@keyframes onde-b{0%,100%{height:11px}50%{height:18px}}
.micro-rond.parle .ondes i:nth-child(1){animation-delay:0s;animation-name:onde-b}
.micro-rond.parle .ondes i:nth-child(2){animation-delay:.1s}
.micro-rond.parle .ondes i:nth-child(3){animation-delay:.2s}
.micro-rond.parle .ondes i:nth-child(4){animation-delay:.3s}
.micro-rond.parle .ondes i:nth-child(5){animation-delay:.15s;animation-name:onde-b}

/* Coupé : tout s'aplatit à la même hauteur. La FORME porte l'état autant que la couleur,
   donc ça reste lisible sans distinguer le rouge du bleu. */
.micro-rond.coupe .ondes i{height:4px !important;background:#b07575;opacity:.6;
  animation:none}

/* Le bouton de coupure de lecture : même forme et même taille que le micro, parce que c'est
   le même genre de geste — « tais-toi » à côté de « ne m'écoute plus ». Il pulse doucement
   pour dire qu'une lecture est en cours ; sans ça, un carré immobile ne se distingue pas
   d'un bouton décoratif. */
/* L'emoji appareil photo rendait un pave colore, different sur chaque plateforme et jamais
   accorde au reste de la barre — tous les autres ronds sont des traits fins. Un dessin au
   trait, de la meme epaisseur et de la meme couleur que les ondes du micro, prend sa place :
   il change de teinte au survol comme n'importe quel bouton, ce que l'emoji ne savait pas
   faire. */
#joindre{color:#8b95a3}
#joindre:hover{color:var(--texte)}
#joindre svg{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.5;
  stroke-linecap:round;stroke-linejoin:round;pointer-events:none}
.micro-rond.coupe-son{font-size:15px;color:var(--voix);border-color:#2f4a37}
.micro-rond.coupe-son:hover{background:#1b2a20;border-color:var(--voix)}
.micro-rond.coupe-son[hidden]{display:none}
@keyframes lit{0%,100%{box-shadow:0 0 0 0 #3fb95000}50%{box-shadow:0 0 0 4px #3fb95024}}
.micro-rond.coupe-son{animation:lit 1.8s ease-in-out infinite}
@media (prefers-reduced-motion: reduce){ .micro-rond.coupe-son{animation:none} }
@media (prefers-reduced-motion: reduce){
  /* Pas d'animation, mais un profil plus haut : l'état « on t'entend » doit rester visible
     sans mouvement, sinon on prive d'information ceux qui coupent les animations. */
  .micro-rond.parle .ondes i{animation:none}
  .micro-rond.parle .ondes i:nth-child(1),
  .micro-rond.parle .ondes i:nth-child(5){height:12px}
  .micro-rond.parle .ondes i:nth-child(2),
  .micro-rond.parle .ondes i:nth-child(4){height:18px}
  .micro-rond.parle .ondes i:nth-child(3){height:22px}
}
/* Au bout de combien de silence le message part. Sous la barre, parce que c'est un
   réglage de la dictée, pas de la session. */
/* La marge auto coupe la seconde ligne en deux : à gauche ce qui touche à la parole (micro,
   couper la lecture, joindre), à droite ce qui décide du départ du message. Sans elle, les
   six boutons formaient un bloc compact où « envoyer » se confondait avec « retenir ». */
#depart{margin-left:auto;display:flex;gap:8px;align-items:center;flex:0 0 auto}
#delai{background:var(--carte);color:var(--texte);border:1px solid var(--bord);
  border-radius:999px;padding:8px 10px;font:inherit;font-size:12.5px;cursor:pointer}
#delai:hover{border-color:#4b5563}

/* Le bascule dictée : envoyer tout seul, ou garder dans la barre pour corriger. */
#retenir{background:transparent;border:1px solid var(--bord);border-radius:999px;
  padding:8px 14px;font:inherit;font-size:12.5px;cursor:pointer;color:var(--faible);
  white-space:nowrap;display:inline-flex;gap:7px;align-items:center}
#retenir:hover{border-color:#4b5563;color:var(--texte)}
#retenir.on{border-color:var(--toi);color:#9ecbff;background:#132133}
/* Retenue subie, pas choisie : en pointillés, pour la distinguer d'un réglage volontaire. */
#retenir.auto{border-color:var(--outil);border-style:dashed;color:#e3b341}
#retenir.auto::before{background:currentColor;opacity:.8}
#retenir::before{content:"";width:8px;height:8px;border-radius:50%;
  background:currentColor;opacity:.35}
#retenir.on::before{opacity:1}
/* Pendant qu'on dicte, la barre montre que ce texte n'est pas encore le tien. */
#saisie.dictee{border-color:var(--toi);color:#9ecbff;font-style:italic}
#envoyer{background:transparent;color:var(--faible);border:1px solid var(--bord);
  min-height:38px;
  border-radius:999px;padding:8px 16px;font:inherit;font-size:13px;cursor:pointer;
  white-space:nowrap}
#envoyer:not(:disabled):hover{border-color:var(--toi);color:#9ecbff}
#envoyer:disabled{opacity:.3;cursor:default}
.g-toi .tape{color:var(--faible);font-size:11px;margin-right:5px}
.ev{display:grid;grid-template-columns:62px 92px 14px 1fr;gap:12px;padding:7px 0;
  border-bottom:1px solid #1c2129;align-items:baseline}
/* Seules les lignes qui arrivent VRAIMENT en direct s'animent. Animer le rejeu d'historique
   lancerait cinq cents animations d'un coup, ce qui fait exactement l'effet inverse. */
@keyframes pose{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.ev.neuve{animation:pose .16s ease-out}
.t{color:#5a636e;font-size:11px;font-variant-numeric:tabular-nums;text-align:right}
.badge{font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.04em}
.corps{min-width:0;overflow-wrap:anywhere;white-space:pre-wrap}
/* Survol : la ligne se detache. Dans un flux de deux cents lignes, suivre une ligne du
   regard sans repere est fatigant. */
.ev{border-radius:6px;padding-left:6px;margin-left:-6px}
.ev:hover{background:#141a22}
.g-toi .badge{color:var(--toi)} .g-toi .corps{color:#cde3ff}
.g-partiel .badge{color:var(--toi);opacity:.5} .g-partiel .corps{color:var(--faible);font-style:italic}
.g-voix .badge{color:var(--voix)} .g-voix .corps{color:#c6f0cf}
.g-pensee .badge{color:var(--pensee)} .g-pensee .corps{color:#c3a6f5;font-size:13px}
.g-texte .badge{color:#7d8590} .g-texte .corps{color:#b6bec8}
.g-outil .badge{color:var(--outil)}
.g-resultat .badge{color:#7d8590} .g-resultat .corps{color:var(--faible);font-size:13px}
.g-ordre .badge{color:var(--tour)} .g-ordre .corps{color:#9fe6ec;font-size:13px}
.g-permission .badge{color:var(--permission)} .g-permission .corps{color:#ffc9c4}
.g-tour .badge{color:var(--tour)} .g-tour .corps{color:#9fe6ec}
/* Un tour interrompu porte la couleur de l'alerte, pas celle du tour : il faut pouvoir le
   repérer en faisant défiler, sans lire. */
.apres.coupe{color:var(--outil);display:block;margin-top:2px}
/* L'écart de fenêtre arrive après la ligne du tour : on le distingue pour qu'on voie qu'il
   s'agit d'une mesure rapportée, pas d'un chiffre connu au moment du bilan. */
/* Les commandes de lecture, accrochees a LA reponse concernee : couper « la parole en
   general » ne dit pas laquelle, et relire la derniere n'est pas relire celle-ci. */
.lecture{display:inline-flex;gap:5px;margin-left:9px;vertical-align:1px}
.lecture button{background:transparent;border:1px solid var(--bord);border-radius:999px;
  color:var(--faible);padding:1px 8px;font:inherit;font-size:10.5px;cursor:pointer;
  white-space:nowrap}
.lecture button:hover{border-color:#4b5563;color:var(--texte)}
.lecture button.vif{border-color:var(--voix);color:#7ee08d}
.fenetre{color:#e3b341;margin-left:2px}
.fenetre.nul{color:#7d8590}
.g-erreur .badge{color:var(--erreur)} .g-erreur .corps{color:#ffb3ad}
/* Le passe rejoue est lisible mais visiblement passe : sans ca, relire soixante tours
   d'historique donne l'impression que tout vient de se produire. */
/* Le passé rejoué n'est PAS grisé : c'est la même conversation, on la reprend. Un
   affaiblissement visuel donnait l'impression de lire une archive alors qu'on relit son
   propre travail. La classe reste, mais pour le COMPORTEMENT — aucun indicateur ne
   s'allume, le compteur d'actions ne gonfle pas — jamais pour l'apparence.
   Ce qui sépare l'avant du maintenant est une frontière explicite, pas une nuance de gris. */
/* PAS de display:none sur le pip d'une ligne passée. Dans une grille, display:none retire
   l'element du flux : le corps glissait alors dans la colonne de 14 px prévue pour
   l'indicateur, et 563 caracteres dans 14 px donnent UN CARACTERE PAR LIGNE — des lignes
   de 7 400 px de haut. Un pip sans etat n'a ni bordure ni contenu : il est deja invisible,
   il suffit de le laisser occuper sa cellule. */
.g-attente .badge{color:var(--faible)} .g-attente .corps{color:#8b949e;font-size:12px}
.g-dictee .badge{color:var(--outil)} .g-dictee .corps{color:#e8d9a8}
/* « Voilà l'historique, voilà maintenant. » Une ligne pleine largeur, pas une ligne de flux
   parmi les autres : c'est un repère, il doit se voir sans être cherché. */
.ev.g-reprise{grid-template-columns:62px 1fr;gap:12px;padding:14px 6px 12px;
  border-bottom:1px solid var(--tour);border-top:1px solid var(--tour);
  background:#101a1c;margin:10px -6px}
.g-reprise .badge,.g-reprise .pip{display:none}
.g-reprise .corps{color:#9fe6ec;font-size:12px;letter-spacing:.04em;
  text-transform:uppercase;font-weight:650}
.apres{color:#5a636e;margin-left:9px;font-size:11px}
.g-log .badge{color:#4d5560} .g-log .corps{color:#6e7681;font-size:12px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#1b212a;
  border:1px solid var(--bord);border-radius:4px;padding:1px 5px;font-size:12.5px}
details summary{cursor:pointer;color:var(--faible);font-size:12px;outline:none}
details pre{margin:6px 0 0;background:#11161d;border:1px solid var(--bord);border-radius:6px;
  padding:8px 10px;overflow-x:auto;font-size:12px;color:#b6bec8}
.conf{border:1px solid var(--bord);background:var(--carte);border-radius:10px;
  padding:12px 16px;margin:0 0 16px}
.conf-titre{font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.06em;
  color:var(--faible);margin-bottom:8px;display:flex;gap:10px;align-items:center}
.conf dl{display:grid;grid-template-columns:auto 1fr;gap:3px 16px;margin:0;font-size:13px}
.conf dt{color:var(--faible)}
.conf dd{margin:0;color:var(--texte);overflow-wrap:anywhere}
.alerte{background:#3d1518;border:1px solid var(--erreur);color:#ffb3ad;border-radius:999px;
  padding:1px 9px;font-size:10.5px;letter-spacing:.03em}
/* Une action en cours doit bouger, une action finie doit rendre un verdict. Avant ca les
   lignes apparaissaient sans qu'on sache si elles tournaient encore, avaient abouti, ou
   etaient mortes en silence — le defaut qui a fait chercher une panne pendant deux jours. */
@keyframes tourne{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:.35}50%{opacity:1}}
.pip{display:inline-block;width:11px;height:11px;line-height:11px;font-size:12px;
  font-weight:700;text-align:center}
.pip.encours{border:1.6px solid #39414d;border-top-color:var(--tour);border-radius:50%;
  animation:tourne .7s linear infinite}
.pip.ok::before{content:"✓";color:var(--voix)}
.pip.echec::before{content:"✗";color:var(--erreur)}
.pip.coupe::before{content:"–";color:var(--faible)}

/* L'en-tete dit ce qui se passe MAINTENANT, et ne peut pas etre masque par un filtre :
   c'est la reponse a « est-ce que quelque chose tourne, la, tout de suite ». */
#activite{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.act{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--bord);
  border-radius:999px;padding:2px 10px;font-size:12px;color:var(--texte);white-space:nowrap}
.act::before{content:"";width:9px;height:9px;border:1.5px solid #ffffff26;
  border-top-color:currentColor;border-radius:50%;animation:tourne .7s linear infinite}
.act.stt{border-color:var(--toi);color:#9ecbff}
.act.pensee{border-color:var(--pensee);color:#c3a6f5}
.act.outil{border-color:var(--outil);color:#e3b341}
.act.voix{border-color:var(--voix);color:#7ee08d}
#travail::before{content:"⟳ ";display:inline-block;animation:tourne 1.1s linear infinite}
/* Un indicateur qui tourne AFFIRME qu'il se passe quelque chose. Liaison coupee, on ne sait
   plus : il s'arrete, et perd sa couleur, plutot que de continuer a jouer l'activite. */
#travail.fige{border-color:var(--bord);color:var(--faible)}
#travail.fige::before{animation:none;opacity:.5}
#etat.vif{animation:pulse 1.4s ease-in-out infinite}

#bas{position:fixed;bottom:calc(var(--barre) + 10px);right:16px;z-index:5;background:var(--carte);border:1px solid var(--bord);
  color:var(--faible);border-radius:999px;padding:6px 14px;font-size:12px;cursor:pointer;display:none;font-family:inherit}

/* ── Telephone ─────────────────────────────────────────────────────────────
   Regles mesurees, pas devinees : chaque bloc corrige un defaut constate en
   rendant la page dans WebKit a la taille d un iPhone (inspect-tableau).
   Rien ne change au-dessus de 760 px. */
@media (max-width: 760px) {

  html, body { overflow-x: clip; max-width: 100vw; }
  body { font-size: 15px; }

  /* --- En-tete ------------------------------------------------------------
     Mesure : .rang-bas faisait 856 px dans 390. Ses deux enfants (filtres et
     compteurs) ne se repliaient pas. Chacun devient une rangee qui defile. */
  header { padding: calc(8px + env(safe-area-inset-top)) 12px 8px; gap: 6px 8px;
    background: #0e1116; backdrop-filter: none; -webkit-backdrop-filter: none; }
  header h1 { font-size: 15px; gap: 6px; }
  header h1 .marque { width: 19px; height: 19px; }
  #retour { width: 40px; height: 40px; font-size: 18px; }
  #etat { order: 1; font-size: 12px; }
  .zone-controles { order: 2; margin-left: 0; flex: 1 1 100%; flex-wrap: wrap; gap: 6px; }
  .zone-controles button { min-height: 40px; padding: 6px 11px; font-size: 13px; }
  .zone-direct { order: 3; margin-left: 0; flex: 1 1 100%; flex-wrap: wrap; gap: 6px 8px; }
  #alerte-entete { order: 1; flex: 0 1 auto; min-width: 0; max-width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #pupitre { order: 4; width: 100%; font-size: 12px; }
  #activite { order: 5; width: 100%; gap: 5px; }
  .act { font-size: 11px; padding: 2px 8px; }
  .rang-bas { order: 9; flex-wrap: wrap; gap: 6px; justify-content: flex-start; }
  #filtres, #compteurs {
    flex: 1 1 100%; min-width: 0; flex-wrap: nowrap; overflow-x: auto;
    scrollbar-width: none; -webkit-overflow-scrolling: touch; padding-bottom: 2px;
  }
  #filtres::-webkit-scrollbar, #compteurs::-webkit-scrollbar { display: none; }
  #filtres button { flex: 0 0 auto; min-height: 34px; padding: 5px 11px; font-size: 12px; }
  #compteurs { font-size: 11px; gap: 6px; }
  #compteurs > * { flex: 0 0 auto; white-space: nowrap; }
  #compte, #travail { white-space: nowrap; }
  #pupitre, .zone-direct, .rang-bas, #alerte-entete { max-height: 240px;
    transition: max-height .2s ease-out, opacity .15s ease-out, margin .2s ease-out; }
  header.compact #pupitre, header.compact .zone-direct, header.compact .rang-bas,
  header.compact #alerte-entete {
    max-height: 0; opacity: 0; overflow: hidden; pointer-events: none;
    padding-top: 0; padding-bottom: 0; border-top-width: 0; margin: -6px 0 0; }

  /* --- Selects --------------------------------------------------------------
     Mesure : appearance « auto » → WebKit peint le controle natif en blanc,
     quel que soit le fond declare. On dessine nous-memes. */
  select {
    -webkit-appearance: none; appearance: none;
    background-color: var(--carte); color: var(--texte);
    border: 1px solid var(--bord); border-radius: 999px;
    padding: 6px 26px 6px 10px; font: inherit; font-size: 13px; min-height: 40px;
    background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27 width%3D%2710%27 height%3D%276%27%3E%3Cpath d%3D%27M1 1l4 4 4-4%27 fill%3D%27none%27 stroke%3D%27%239aa1b1%27 stroke-width%3D%271.6%27%2F%3E%3C%2Fsvg%3E");
    min-width: 64px; text-overflow: ellipsis;
    background-repeat: no-repeat; background-position: right 9px center; background-size: 10px 6px;
  }

  /* --- Flux ------------------------------------------------------------------
     Mesure : la grille de bureau (62 | 92 | 14 | 1fr) laisse ~200 px au texte
     sur 390. Sur telephone : une ligne d en-tete compacte, puis le corps sur
     toute la largeur. */
  /* 8 haut + 48 (le champ) + 8 + 46 (les boutons) + 8 bas = 118, plus la barre du systeme. */
  :root { --barre: calc(118px + env(safe-area-inset-bottom)); }
  #flux { padding: 8px 10px calc(var(--barre) + 24px); }
  .ev { grid-template-columns: auto minmax(0, 1fr) auto; gap: 3px 8px; padding: 9px 0; }
  .ev .t { grid-column: 1; text-align: left; font-size: 10.5px; }
  .ev .badge { grid-column: 2; font-size: 10.5px; }
  .ev .pip { grid-column: 3; align-self: center; }
  .ev .corps { grid-column: 1 / -1; font-size: 15px; line-height: 1.5; }
  .ev.g-reprise { grid-template-columns: auto minmax(0, 1fr); }
  .ev.g-reprise > * { grid-column: auto; }
  .ev .corps pre, .ev .corps code { font-size: 12px; max-width: 100%; overflow-x: auto;
                                    white-space: pre-wrap; overflow-wrap: anywhere; }
  .lecture button { min-height: 32px; padding: 4px 12px; }

  /* --- Barre de saisie ---------------------------------------------------------
     Le decoupage en deux lignes vaut maintenant a toutes les tailles : il ne reste ici
     que les mesures propres au pouce — cibles a 44 px minimum, et un champ a 16 px sans
     quoi iOS zoome dessus a la mise au point et desaxe toute la page. */
  #saisie-barre { padding: 8px 12px calc(8px + env(safe-area-inset-bottom)); }
  #micro-bas, #couper-lecture, #joindre { width: 44px; height: 44px; }
  #saisie-barre form { gap: 8px; }
  #depart { gap: 6px; }
  #saisie {
    font-size: 16px;
    padding: 12px 14px; height: 48px; max-height: 36vh; border-radius: 22px;
  }
  #envoyer { min-height: 44px; padding: 0 14px; font-size: 14px; }
  #delai { min-height: 44px; font-size: 12.5px; padding: 4px 24px 4px 10px; }
  #retenir { min-height: 44px; padding: 5px 12px; font-size: 13px; }

  #pile-barre { left: 12px; right: 12px; }
  #cogitation, #note-barre, #en-attente { font-size: 12px; max-width: 100%; }
  #bas { bottom: calc(var(--barre) + 8px); }

  /* --- Panneaux flottants -------------------------------------------------------- */
  #choix-moteur, #choix-conv {
    left: 10px !important; right: 10px !important; width: auto !important;
    max-width: calc(100vw - 20px) !important; max-height: 65vh; overflow-y: auto;
  }
  #choix-conv button, #choix-moteur .m { min-height: 42px; }
}

@media (max-width: 420px) {
  /* Mesure a 360 px (iPhone SE, Android d'entree de gamme) : les ronds et le groupe de
     depart faisaient 351 px pour 336 disponibles — onze pixels de trop, et « envoyer »
     basculait seul sur une troisieme ligne. On les reprend sur les marges internes plutot
     que sur la taille des cibles, qui reste au-dessus de 42 px. */
  #micro-bas, #couper-lecture, #joindre { width: 42px; height: 42px; }
  #saisie-barre form { gap: 8px 6px; }
  #depart { gap: 5px; }
  #delai { padding: 4px 20px 4px 9px; }
  #retenir { padding: 5px 10px; }
  #envoyer { padding: 0 12px; }
  header { padding-left: 10px; padding-right: 10px; }
  header h1 { font-size: 14px; }
  #flux { padding-left: 8px; padding-right: 8px; }
  .zone-controles button { padding: 6px 9px; font-size: 12.5px; }
}

@media (max-width: 900px) and (orientation: landscape) {
  header { padding-top: 5px; padding-bottom: 5px; }
  header #activite, header #pupitre { display: none; }
  #flux { padding-bottom: calc(var(--barre) + 16px); }
  #saisie { max-height: 28vh; }
}
</style></head><body>
<header>
  <!--RETOUR-->
  <h1>
    <svg class="marque" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M15.13 11.89Q15.38 7.50 16.00 2.20Q16.62 7.50 16.87 11.89Z M17.30 12.01Q19.12 9.54 21.40 6.65Q20.03 10.07 18.81 12.88Z M19.12 13.19Q23.05 11.21 27.95 9.10Q23.67 12.29 19.99 14.70Z M20.11 15.13Q23.15 15.47 26.80 16.00Q23.15 16.53 20.11 16.87Z M19.99 17.30Q23.67 19.71 27.95 22.90Q23.05 20.79 19.12 18.81Z M18.81 19.12Q20.03 21.93 21.40 25.35Q19.12 22.46 17.30 19.99Z M16.87 20.11Q16.62 24.50 16.00 29.80Q15.38 24.50 15.13 20.11Z M14.70 19.99Q12.88 22.46 10.60 25.35Q11.97 21.93 13.19 19.12Z M12.88 18.81Q8.95 20.79 4.05 22.90Q8.33 19.71 12.01 17.30Z M11.89 16.87Q8.85 16.53 5.20 16.00Q8.85 15.47 11.89 15.13Z M12.01 14.70Q8.33 12.29 4.05 9.10Q8.95 11.21 12.88 13.19Z M13.19 12.88Q11.97 10.07 10.60 6.65Q12.88 9.54 14.70 12.01Z"/>
    </svg>
    claude-talk
  </h1>
  <span id="etat" class="e-listening">connexion…</span>
  <span id="alerte-entete" class="alerte" style="display:none"></span>

  <!-- Trois zones plutôt qu'une rangée qui se replie n'importe comment : ce que tu PILOTES,
       ce qui se PASSE, et les mesures. Sans elles, la pastille « parole » sautait à la ligne
       suivante et atterrissait à gauche des filtres — la position d'un élément changeait
       selon la largeur de la fenêtre, et on ne savait plus quoi lire où. -->
  <span id="pupitre" class="zone"></span>
  <span class="zone zone-controles">
    <button id="micro" title="couper le micro (touche m)">🎤 micro</button>
    <button id="arreter" title="arrêter le travail en cours (touche s)" disabled>⏹ arrêter</button>
    <select id="modele" title="modèle utilisé pour le travail"></select>
    <select id="effort" title="niveau d'effort de réflexion"></select>
  </span>

  <span class="zone zone-direct">
  <span class="avec-choix">
    <span id="moteur" title="moteur de reconnaissance vocale"></span>
    <div id="choix-moteur" hidden></div>
  </span>
  <span class="avec-choix">
    <button type="button" id="convs" title="les conversations de ce dossier"></button>
    <div id="choix-conv" hidden></div>
  </span>
  <span id="compte" title="temps avant envoi automatique">
    <span id="reste"></span>
    <button id="retenir-vite" type="button"
            title="garder CE message dans la barre au lieu de l'envoyer">retenir</button>
    <button id="envoi-vite" type="button" title="envoyer tout de suite (Entrée)">envoyer</button>
  </span>
  <div id="activite"></div>
  </span>

  <!-- Filtres et mesures partagent le second rang : les mesures seules occupaient un rang
       entier pour deux pastilles. -->
  <!-- La durée du tour en cours est une MESURE, et elle vit donc avec les mesures. Elle a
       d'abord été posée dans la zone « ce qui se passe », à côté des pastilles d'activité :
       à sa taille réelle, elle poussait toute cette zone sur un troisième rang, et l'en-tête
       volait trente-cinq pixels au flux pendant chaque tour — c'est-à-dire pendant tout le
       temps où l'on regarde la page. Ici, le rang était à moitié vide. -->
  <div class="rang-bas">
    <div id="filtres"></div>
    <span class="mesures">
      <span id="travail"></span>
      <span id="compteurs"></span>
    </span>
  </div>
</header>
<main id="flux"></main>
<button id="bas">↓ suivre</button>
<div id="saisie-barre">
  <!-- Une PILE, et pas trois éléments flottants au même endroit. Ils étaient tous ancrés à
       « bottom:100% » : la note d'historique se dessinait par-dessus l'extrémité droite de la
       zone d'attente, et l'indicateur de cogitation par-dessus sa gauche. Ça ne se voyait
       qu'avec des textes assez longs — donc au pire moment. Empilés, ils ne peuvent plus se
       recouvrir quelle que soit leur taille. -->
  <div id="pile-barre">
    <div id="cogitation" hidden>
      <span class="spin"></span>
      <span id="mot"></span><span class="points"><i>.</i><i>.</i><i>.</i></span>
    </div>
    <div id="en-attente" hidden></div>
    <div id="en-vol" hidden></div>
    <div id="note-barre" hidden></div>
    <div id="jointes" hidden></div>
  </div>
  <form id="composer" autocomplete="off">
    <!-- Le champ D'ABORD, parce qu'il se dessine en premier : il occupe seul la premiere
         ligne de la barre. Le placer apres les boutons et le remonter en CSS aurait donne
         une tabulation qui traverse trois ronds avant d'atteindre la zone qu'on regarde. -->
    <textarea id="saisie" rows="1" class="une-ligne"
              placeholder="écrire au lieu de parler — touche /"
              aria-label="message à envoyer" maxlength="4000"></textarea>
    <!-- Le micro est aussi ICI, pas seulement dans l'en-tete. C'est au bas de la page que le
         regard est quand on dicte : le texte s'y ecrit, et la relecture s'y fait. Devoir
         remonter en haut pour couper l'ecoute avant de corriger un mot casse le geste. Les
         deux boutons pilotent le meme etat — il n'y a qu'un micro. -->
    <button id="micro-bas" type="button" class="micro-rond"
            title="couper ou rouvrir le micro (touche m)">
      <span class="ondes"><i></i><i></i><i></i><i></i><i></i></span>
    </button>
    <!-- Couper la lecture depuis ICI. Le bouton existait déjà, mais collé à la réponse
         concernée, dans le flux : il fallait remonter la retrouver pour s'en servir, alors
         qu'on veut couper au moment où l'on décide de reprendre la parole — et à ce
         moment-là on est en bas. Il n'apparaît que pendant une lecture : un bouton grisé en
         permanence occuperait la place sans jamais servir. -->
    <button id="couper-lecture" type="button" class="micro-rond coupe-son" hidden
            title="couper la lecture en cours (ou dis « chut »)">⏹</button>
    <!-- Joindre une image. Le bouton est a cote du micro parce qu'il repond au meme
         besoin : dire quelque chose qu'on ne veut pas taper. -->
    <label id="joindre" class="micro-rond" title="joindre une photo ou une capture">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path
        d="M3.75 8.75h2.9l1.4-2.5h7.9l1.4 2.5h2.9a1 1 0 0 1 1 1v8.5a1 1 0 0 1-1 1H3.75a1 1 0
           0 1-1-1v-8.5a1 1 0 0 1 1-1Z"/><circle cx="12" cy="14" r="3.4"/></svg>
      <input type="file" accept="image/*" multiple hidden>
    </label>
    <!-- Les trois controles du DEPART tiennent ensemble : quand partir (le delai), si ca
         part tout seul (retenir), et le faire partir maintenant (envoyer). Groupes, ils
         restent cote a cote et cales a droite meme sur un ecran etroit — separes, « envoyer »
         se retrouvait seul sur une troisieme ligne, a gauche, loin de ce qu'il declenche. -->
    <div id="depart">
      <select id="delai" title="au bout de combien de silence le message part"></select>
      <button id="retenir" type="button"
              title="retenir la dictée dans la barre au lieu de l'envoyer (touche r)">retenir</button>
      <button id="envoyer" type="submit" disabled>envoyer</button>
    </div>
  </form>
</div>
<script>
// Bandeau de diagnostic : une exception au chargement laissait une page muette, sans
// rien dans le flux pour l'expliquer. Ici elle s'affiche en bas de l'écran, avec sa ligne.
(function(){
  function montrer(t){
    var b=document.getElementById("diag-js");
    if(!b){b=document.createElement("div");b.id="diag-js";
      b.style.cssText="position:fixed;bottom:0;left:0;right:0;z-index:99;max-height:40vh;overflow:auto;"
        +"padding:8px 12px;font:12px/1.4 ui-monospace,monospace;color:#ffb86b;background:#1a1408;"
        +"border-top:1px solid #ffb86b66;white-space:pre-wrap;word-break:break-all";
      var x=document.createElement("button");x.textContent="✕";
      x.style.cssText="float:right;background:none;border:0;color:inherit;font-size:16px;cursor:pointer";
      x.onclick=function(){b.remove()};b.appendChild(x);
      document.body.appendChild(b);}
    var l=document.createElement("div");l.textContent=t;b.appendChild(l);
  }
  addEventListener("error",function(e){
    montrer("⚠ JS : "+(e.message||e)+(e.filename?" @"+e.lineno+":"+e.colno:""));
  });
  addEventListener("unhandledrejection",function(e){
    montrer("⚠ promesse : "+(e.reason&&e.reason.message||e.reason));
  });
})();
// --- les familles de lignes -------------------------------------------------------------
// Dix-huit boutons alignes ne disent ni ce qu'ils montrent ni pourquoi on voudrait les
// couper : il fallait les avoir ecrits pour s'en souvenir. Regroupes par famille, avec une
// phrase par ligne, le filtre devient lisible sans documentation.
//
// Source unique : le libelle du badge, l'explication et le defaut sont declares ICI. Une
// deuxieme liste aurait derive de celle-ci, et c'est deja arrive — le genre « session »
// arrivait dans le flux sans figurer dans les libelles, donc sa ligne etait creee
// invisible et AUCUN filtre ne pouvait la montrer.
const GROUPES = [
  { nom: "Conversation", aide: "ce qui a été dit, d'un côté comme de l'autre", genres: [
    { g: "toi",     lib: "toi",      quoi: "tes messages — une ligne par message pris en compte" },
    { g: "partiel", lib: "toi…",     quoi: "la transcription en cours, avant validation", cache: true },
    { g: "voix",    lib: "claude",   quoi: "ce que Claude dit à voix haute" },
    // Décoché par défaut : le texte retenu est déjà DANS la barre de saisie, avec sa note
    // qui dit pourquoi. La ligne du flux ne fait que répéter ce qu'on a sous les yeux, et
    // elle le répète au moment le plus chargé. Elle reste disponible pour relire après coup.
    { g: "dictee",  lib: "retenu",   quoi: "dit mais pas envoyé — ça attend dans la barre",
      cache: true },
    { g: "attente", lib: "attente",  quoi: "une autre conversation parle, celle-ci patiente" },
  ]},
  { nom: "Travail", aide: "ce que Claude fait pendant qu'il travaille", genres: [
    { g: "pensee",   lib: "réflexion", quoi: "sa réflexion, au fil de sa production" },
    { g: "texte",    lib: "écrit",     quoi: "ce qu'il écrit, avant réécriture pour la voix" },
    { g: "outil",    lib: "outil",     quoi: "chaque action : lecture, édition, commande" },
    { g: "resultat", lib: "résultat",  quoi: "la sortie des outils — souvent longue", cache: true },
    { g: "tour",     lib: "tour",      quoi: "le bilan d'un tour : actions, durée, jetons, fenêtre" },
  ]},
  { nom: "Commandes", aide: "ce que tu pilotes, à la voix ou depuis cette page", genres: [
    { g: "ordre",      lib: "ordre local", quoi: "un ordre exécuté ici, jamais transmis à Claude" },
    { g: "micro",      lib: "micro",       quoi: "ouverture et coupure du micro", cache: true },
    { g: "arret",      lib: "arrêt",       quoi: "arrêt du travail en cours" },
    { g: "modele",     lib: "modèle",      quoi: "changement de modèle, permanent ou d'un tour" },
    { g: "effort",     lib: "effort",      quoi: "changement du niveau d'effort" },
    { g: "permission", lib: "permission",  quoi: "une autorisation demandée, et ta réponse" },
  ]},
  { nom: "Système", aide: "l'état de la machinerie — utile quand quelque chose cloche", genres: [
    { g: "session",     lib: "session", quoi: "l'identifiant de session, celui qui sert à reprendre" },
    { g: "reprise",     lib: "reprise", quoi: "le rechargement d'une conversation précédente" },
    { g: "quota_seuil", lib: "quota",   quoi: "franchissement d'un palier de rate limit" },
    { g: "erreur",      lib: "erreur",  quoi: "ce qui a échoué" },
    { g: "log",         lib: "log",     quoi: "le journal technique de LiveKit et des modules", cache: true },
  ]},
];

const LIB = {};
const actifs = new Set();
for (const fam of GROUPES) {
  for (const e of fam.genres) {
    LIB[e.g] = e.lib;
    if (!e.cache) actifs.add(e.g);
  }
}
const flux = document.getElementById("flux");
const barre = document.getElementById("filtres");
let suivre = true, actions = 0, jetons = 0, quota = [];
// Le numéro du dernier événement affiché. Toute la protection contre les doublons tient
// là : le serveur numérote, la page ignore ce qui est déjà passé. Ça couvre le rejeu
// d'historique à la reconnexion ET le cas où deux sockets seraient vivantes en même temps.
let vuJusqua = 0;
// Les états rejoués à la connexion, par numéro : l'historique qui suit les contient aussi et
// ne doit pas les repasser. Seuls « config » et « session » ajoutent une ligne au flux ;
// les autres états ne font que régler des panneaux, et se rejouent sans risque.
const etatsRejoues = new Set();
const ETATS_EN_LIGNE = new Set(["config", "session"]);
function dejaVu(e) {
  if (typeof e.n !== "number") return false;   // événement d'une version sans numéro
  if (etatsRejoues.has(e.n)) return true;
  if (e.n <= vuJusqua) return true;
  vuJusqua = e.n;
  return false;
}

// Une liste deroulante par famille. <details> porte l'ouverture et la fermeture sans une
// ligne de JS ; on ne code que ce qui a du sens metier : l'etat des cases et le compteur.
function appliquerFiltres() {
  for (const el of flux.children) {
    if (el.dataset.g) el.style.display = actifs.has(el.dataset.g) ? "" : "none";
  }
}

for (const fam of GROUPES) {
  const bloc = document.createElement("details");
  bloc.className = "famille";

  const titre = document.createElement("summary");
  const compteur = document.createElement("b");
  const majCompteur = () => {
    const n = fam.genres.filter(e => actifs.has(e.g)).length;
    compteur.textContent = `${n}/${fam.genres.length}`;
    // Une famille entierement coupee doit se voir sans l'ouvrir.
    titre.classList.toggle("vide", n === 0);
    titre.classList.toggle("pleine", n === fam.genres.length);
  };
  titre.append(document.createTextNode(fam.nom + " "), compteur);
  titre.title = fam.aide;
  bloc.appendChild(titre);

  const panneau = document.createElement("div");
  panneau.className = "panneau";

  const aide = document.createElement("p");
  aide.className = "aide";
  aide.textContent = fam.aide;
  panneau.appendChild(aide);

  const cases = [];
  for (const e of fam.genres) {
    const ligne = document.createElement("label");
    const boite = document.createElement("input");
    boite.type = "checkbox";
    boite.checked = actifs.has(e.g);
    boite.onchange = () => {
      boite.checked ? actifs.add(e.g) : actifs.delete(e.g);
      majCompteur();
      appliquerFiltres();
    };
    cases.push({ boite, g: e.g });
    const nom = document.createElement("span");
    nom.className = "nom";
    nom.textContent = e.lib;
    const quoi = document.createElement("span");
    quoi.className = "quoi";
    quoi.textContent = e.quoi;
    ligne.append(boite, nom, quoi);
    panneau.appendChild(ligne);
  }

  // « tout / rien » : sans ca, couper une famille de cinq lignes demande cinq clics.
  const barreTout = document.createElement("div");
  barreTout.className = "tout-rien";
  for (const [libelle, veut] of [["tout", true], ["rien", false]]) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = libelle;
    b.onclick = () => {
      for (const c of cases) {
        c.boite.checked = veut;
        veut ? actifs.add(c.g) : actifs.delete(c.g);
      }
      majCompteur();
      appliquerFiltres();
    };
    barreTout.appendChild(b);
  }
  panneau.appendChild(barreTout);

  bloc.appendChild(panneau);
  majCompteur();
  barre.appendChild(bloc);
}

// Un clic ailleurs referme les listes : elles recouvrent le flux, et rester ouvertes en
// lisant est exactement ce qui gene.
addEventListener("click", ev => {
  for (const d of barre.querySelectorAll("details[open]")) {
    if (!d.contains(ev.target)) d.open = false;
  }
});

const fmtJetons = n => n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)} k` : String(n);

// Sur un siege entreprise le montant en dollars ne veut rien dire ; ce qui contraint le
// travail, c'est la fenetre de rate limit. L'en-tete affiche donc les pourcentages.
// Le temps qui reste avant reinitialisation, calcule ICI a partir de l'echeance absolue
// envoyee par le serveur. Une echeance connue n'a pas besoin d'etre redemandee pour etre
// affichee en temps reel : le pourcentage se rafraichit aux moments utiles, le decompte
// avance tout seul, et le reseau ne bouge pas.
function resteAvant(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (isNaN(t)) return "";
  const min = Math.floor((t - Date.now()) / 60000);
  if (min <= 0) return "maintenant";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60), m = min % 60;
  if (h < 24) return `${h} h ${String(m).padStart(2, "0")}`;
  return `${Math.floor(h / 24)} j ${h % 24} h`;
}

function majCompteurs() {
  const pastilles = quota.map(f => {
    const c = f.pct >= 90 ? "chaud" : f.pct >= 70 ? "tiede" : "";
    const reste = resteAvant(f.reset_iso);
    // « 5h » laissait croire qu'il restait cinq heures : c'etait la LARGEUR de la fenetre.
    // Le nom dit maintenant sa fonction, et le temps restant est affiche a cote.
    const detail = [ech(f.taille || f.cle)];
    if (reste) detail.push(`renouvelée dans ${reste}`);
    if (f.reset) detail.push(`soit à ${ech(f.reset)}`);
    return `<span class="q ${c}" title="${detail.join(" — ")}">${ech(f.cle)} `
         + `<b>${f.pct.toFixed(0)} %</b>`
         + (reste ? `<i>${ech(reste)}</i>` : "") + `</span>`;
  }).join("");
  const gauche = `${actions} action${actions > 1 ? "s" : ""}`
    + (jetons ? ` · ${fmtJetons(jetons)} jetons` : "");
  // Tant qu'aucune lecture n'est arrivee, on le DIT : une en-tete vide laissait croire a une
  // panne alors que la premiere lecture etait simplement en cours.
  const attente = quota.length ? "" : `<span class="q vide">quota…</span>`;
  document.getElementById("compteurs").innerHTML =
    `<span>${gauche}</span>${pastilles}${attente}`;
}

// Le decompte avance seul, une fois par minute : c'est la granularite affichee, donc rien de
// plus fin n'aurait d'effet visible — et c'est zero requete.
setInterval(() => { if (quota.length) majCompteurs(); }, 30000);

const ech = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

// La configuration n'est pas une ligne du flux : c'est l'en-tete de la session. Elle reste
// en haut, non filtrable, parce qu'en bypassPermissions c'est le seul endroit qui dit
// noir sur blanc que plus rien ne sera demande.
// Le titre de la fenêtre porte le projet. Avec trois conversations ouvertes, trois onglets
// nommés « claude-talk » sont indiscernables — et c'est le titre qu'on lit dans la barre des
// tâches, pas le contenu de la page.
function nommerFenetre(valeurs) {
  const chemin = (valeurs || {})["projet"] || "";
  const nom = chemin.replace(/\/+$/, "").split("/").filter(Boolean).pop();
  if (nom) document.title = `claude-talk — ${nom}`;
}

function carteConfig(e) {
  const champs = Object.entries(e.valeurs || {}).filter(([k]) => !k.startsWith("_"));
  const alerte = e.valeurs?._alerte
    ? `<span class="alerte">⚠ ${ech(e.valeurs._alerte)}</span>` : "";
  nommerFenetre(e.valeurs);
  const carte = document.createElement("section");
  carte.className = "conf";
  carte.innerHTML = `<div class="conf-titre">configuration ${alerte}</div><dl>`
    + champs.map(([k, v]) => `<dt>${ech(k)}</dt><dd>${ech(v)}</dd>`).join("")
    + `</dl>`;
  flux.appendChild(carte);
  dernier = null;
  if (e.valeurs?._alerte) {
    const en = document.getElementById("alerte-entete");
    en.textContent = "⚠ " + e.valeurs._alerte;
    en.style.display = "";
  }
}

// Trouve la dernière ligne « tour » et lui accroche l'écart de fenêtre. Si elle a déjà
// disparu de la mémoire de la page, la mesure est simplement perdue — sans bruit, parce
// qu'elle n'est qu'un complément d'information.
let ligneTour = null;   // la dernière ligne « tour », gardée pour la compléter

function completerTour(e) {
  if (!ligneTour || ligneTour.dejaComplete) return;
  ligneTour.dejaComplete = true;
  const cible = ligneTour.querySelector(".corps");
  if (!cible) return;
  const nul = (e.ecarts || []).every(x => !x.delta);
  const marque = document.createElement("span");
  marque.className = "fenetre" + (nul ? " nul" : "");
  marque.textContent = " · " + (e.texte || "");
  marque.title = nul
    ? "la fenêtre n'a pas bougé d'un point entier pendant ce tour"
    : "de combien la fenêtre a bougé pendant ce tour — elle est partagée "
      + "avec les autres sessions Claude Code";
  cible.appendChild(marque);
}

function corps(e) {
  switch (e.genre) {
    case "outil": {
      const args = e.args ? `<details><summary>arguments</summary><pre>${ech(JSON.stringify(e.args, null, 2))}</pre></details>` : "";
      return `<code>${ech(e.nom)}</code> ${ech(e.cible || "")}${args}`;
    }
    case "permission":
      return `${ech(e.texte)}${e.decision ? ` — <b>${ech(e.decision)}</b>` : ""}`;
    case "tour": {
      const bouts = [];
      if (e.actions != null) bouts.push(`${e.actions} action${e.actions > 1 ? "s" : ""}`);
      if (e.duree != null) bouts.push(`${e.duree}s`);
      if (e.tours != null) bouts.push(`${e.tours} tour${e.tours > 1 ? "s" : ""} modèle`);
      if (e.jetons != null) bouts.push(`${fmtJetons(e.jetons)} jetons`);
      if (e.texte) bouts.push(e.texte);
      const ligne = bouts.join(" · ") || "terminé";
      // Un tour coupé se lisait EXACTEMENT comme un tour fini : mêmes actions, même durée,
      // même couleur. La raison est donc affichée en clair, et le détail technique rendu par
      // le CLI reste en infobulle — utile pour un rapport, illisible dans le flux.
      if (!e.pourquoi) return ligne;
      const detail = [e.fin, ...(e.erreurs || [])].filter(Boolean).join(" · ");
      return ligne + `<span class="apres coupe" title="${ech(detail)}">⚠ ${ech(e.pourquoi)}</span>`;
    }
    case "log":
      return `<span style="opacity:.7">${ech(e.source)}</span> ${ech(e.texte)}`;
    case "dictee":
      // Dire POURQUOI, pas seulement « retenu ». Les trois raisons ne se corrigent pas de la
      // même façon : le mode se désarme, la retenue d'office s'explique par le travail en
      // cours, le rattrapage est un geste qu'on vient de faire. « retenu » tout court laissait
      // chercher lequel des trois s'appliquait.
      return ech(e.texte) + `<span class="apres">` + ({
        attente: "retenu — un texte attend déjà dans la barre, relis et envoie",
        occupe: "retenu — Claude travaillait, à relire avant d'envoyer",
        mode: "retenu — le mode « retenir » est armé",
        tour: "retenu — tu l'as rattrapé pendant le décompte",
      }[e.raison] || (e.auto ? "retenu — Claude travaillait"
                             : "retenu à ta demande")) + `</span>`;
    case "session":
      // Sans ce cas la ligne s'affichait VIDE : l'événement porte `id`, pas `texte`. Or
      // c'est précisément l'identifiant qu'on vient chercher pour reprendre.
      return `<code>${ech(e.id || "?")}</code>`
        + `<span class="apres">reprendre : vvreprendre ${ech(e.id || "")}</span>`;
    case "toi":
      // Même badge, même couleur : c'est le même tour de conversation. Le petit glyphe dit
      // seulement par quel canal il est arrivé, ce qui compte pour relire un transcript.
      return (e.tape ? `<span class="tape" title="écrit au clavier">⌨</span>` : "")
        + ech(e.texte);
    case "micro":
      return e.actif ? "micro rouvert" : "micro coupé — il ne t'entend plus";
    case "arret":
      return ech(e.texte);
    case "modele":
      return `${ech(e.libelle)}${e.temporaire ? " (juste pour ce tour)" : ""}`;
    default:
      return ech(e.texte);
  }
}

// ---------------------------------------------------------------------------------------
// Suivi du cycle de vie : ce qui tourne, ce qui a abouti, ce qui a échoué.
//
// Chaque action longue reçoit un indicateur qui tourne, remplacé par un verdict quand elle
// se termine. Le principe qui gouverne tout ce bloc : un indicateur bloqué mentirait, et
// mentirait dans le sens le plus coûteux — « ça travaille » alors que rien ne tourne. Donc
// tout ce qui s'allume ici a un chemin garanti pour s'éteindre : soit l'événement de
// résolution, soit la fin de tour, soit un garde-fou temporel.
// ---------------------------------------------------------------------------------------
const encours = new Map();          // clé de suivi -> élément .pip qui tourne

// L'état d'un indicateur à sa création. Les événements terminaux naissent déjà résolus :
// une transcription finale ou un tour fini n'a rien à attendre.
function etatInitial(e) {
  if (e.passe) return "";
  switch (e.genre) {
    case "outil": case "partiel": case "pensee": case "voix": return "encours";
    case "toi": case "tour": return "ok";
    case "resultat": return e.echec ? "echec" : "ok";
    case "erreur": return "echec";
    case "permission": return e.decision === "refusé" ? "echec" : "ok";
    default: return "";
  }
}

// Ce qui identifie une action en attente de verdict. L'identifiant de l'outil vient du SDK,
// donc le rattachement action/résultat est exact et non deviné.
function cleDeSuivi(e) {
  // Rien du passé ne « tourne » : un outil rejoué a fini il y a des heures.
  if (e.passe) return null;
  if (e.genre === "outil") return "outil:" + (e.id || "?");
  if (e.genre === "partiel") return "stt";
  if (e.genre === "pensee" || e.genre === "voix") return e.genre;
  return null;
}

// Une même clé peut renaître : la réflexion reprend après un outil, et crée une ligne. La
// précédente ne doit pas rester à tourner pour toujours — elle est close, pas abandonnée.
function marquer(cle, el) {
  if (encours.has(cle)) resoudre(cle, "ok");
  encours.set(cle, el);
  majActivite();
}

function resoudre(cle, etat, pourquoi) {
  const el = encours.get(cle);
  if (!el) return;
  encours.delete(cle);
  el.className = "pip " + etat;
  if (pourquoi) el.title = pourquoi;
  majActivite();
}

// Fin de tour : tout ce qui traîne encore est clos. Un outil sans résultat n'a pas réussi,
// il a été interrompu — le dire d'un tiret plutôt que d'une croix, parce que ce n'est pas
// une erreur de l'outil.
function toutClore(raison) {
  for (const cle of [...encours.keys()]) {
    resoudre(cle, cle.startsWith("outil:") ? "coupe" : "ok", raison);
  }
}

// L'en-tête liste les activités en cours. Les garde-fous : le STT peut émettre une
// transcription partielle jamais suivie d'une finale (faux positif du VAD, barge-in), et la
// réflexion peut être coupée net par une déconnexion. Sans échéance, la pastille resterait
// allumée sur un système inerte.
// 12 s pour la transcription : le moteur local met 4 a 7 s, et un garde-fou de 4 s eteignait
// le signal EN PLEINE attente — la page redevenait muette juste avant que le texte arrive.
//
// « parole » n'en avait AUCUN, et c'etait le trou le plus visible : l'indicateur s'allume au
// premier morceau du debrief et ne s'eteint qu'a l'etat suivant publie par l'agent. Si cet
// etat ne vient jamais — liaison coupee pendant la lecture, agent tue, session reprise — la
// pastille « parole » tourne pour toujours, et comme le bandeau de cogitation ne regarde que
// `encours.size`, les mots defilent eux aussi sur une page ou plus rien ne se passe. Le tour
// est deja fini quand la parole commence, donc meme la cloture de fin de tour ne l'attrape
// pas. 60 s, rearme a chaque signe de parole : au-dela, c'est que personne ne parle plus.
const GARDE = { stt: 12000, pensee: 30000, voix: 60000 };
const echeances = {};
function battre(nom) {
  clearTimeout(echeances[nom]);
  echeances[nom] = setTimeout(() => resoudre(nom, "coupe", "sans suite"), GARDE[nom]);
}

const zoneActivite = document.getElementById("activite");
const LIB_ACT = { stt: "transcription", pensee: "réflexion", voix: "parole" };
// Sur un moteur sans texte en direct, l'attente de transcription est la seule chose qui se
// passe pendant plusieurs secondes. Sans ce libellé, la page semble figée puis du texte
// apparaît sans explication — c'est exactement ce qui rendait le comportement incompréhensible.
let sttDirect = true;
function majActivite() {
  const outils = [...encours.keys()].filter(c => c.startsWith("outil:")).length;
  const bouts = [];
  for (const nom of ["stt", "pensee", "voix"]) {
    if (!encours.has(nom)) continue;
    const lib = (nom === "stt" && !sttDirect) ? "transcription (fin de phrase)" : LIB_ACT[nom];
    bouts.push(`<span class="act ${nom}">${lib}</span>`);
  }
  if (outils) {
    bouts.push(`<span class="act outil">${outils} outil${outils > 1 ? "s" : ""}</span>`);
  }
  zoneActivite.innerHTML = bouts.join("");
  majCogitation();
}

// Les deltas s'agrègent dans une seule ligne : un jeton par ligne serait illisible et
// ferait ramer la page sur un run long. Nuance importante : une transcription partielle
// est renvoyée entière et grandissante par le STT, donc elle se REMPLACE au lieu de
// s'ajouter — sinon on lit « bon bonj bonjou bonjour ».
const AGREGE = new Set(["pensee", "texte", "voix"]);
let dernier = null;
// Vrai pendant le rejeu de l'historique : les lignes du passé ne s'animent pas, sinon une
// reconnexion déclencherait cinq cents animations simultanées.
let enRejeu = false;
function ajouter(e) {
  if (e.genre === "config") { carteConfig(e); return; }
  // Un résultat vide sert à clore l'indicateur de son outil, pas à remplir le flux d'une
  // ligne sans contenu.
  if (e.genre === "resultat" && !e.texte && e.id) {
    resoudre("outil:" + e.id, e.echec ? "echec" : "ok",
             e.echec ? "l'outil a renvoyé une erreur" : "sans sortie");
    return;
  }
  const meme = dernier && dernier.dataset.g === e.genre;
  let nouvelle = false;
  if (e.genre === "partiel" && meme) {
    dernier.querySelector(".corps").textContent = e.texte;
  } else if (AGREGE.has(e.genre) && meme && e.suite) {
    dernier.querySelector(".corps").textContent += e.texte;
  } else {
    const ligne = document.createElement("div");
    ligne.className = `ev g-${e.genre}` + (e.passe ? " passe" : "")
                    + (enRejeu || e.passe ? "" : " neuve");
    ligne.dataset.g = e.genre;
    ligne.style.display = actifs.has(e.genre) ? "" : "none";
    // L'heure de l'horloge plutôt que des secondes depuis le lancement : « 14:23:07 » se
    // recoupe avec un commit ou un souvenir, « 812.4 » ne se recoupe avec rien. L'écoulé
    // reste en infobulle, parce qu'il sert à mesurer une latence entre deux lignes.
    const ecoule = e.t?.toFixed ? `${e.t.toFixed(1)} s après le lancement` : "";
    ligne.innerHTML = `<div class="t" title="${ecoule}">${ech(e.h || "")}</div>`
      + `<div class="badge">${LIB[e.genre] || e.genre}</div>`
      + `<div class="pip ${etatInitial(e)}"></div>`
      + `<div class="corps">${corps(e)}</div>`;
    flux.appendChild(ligne);
    dernier = ligne;
    nouvelle = true;
  }

  // Enregistrement après la création OU la réutilisation de la ligne. Les deux cas diffèrent
  // et le test l'a montré : une nouvelle ligne prend la relève de l'ancienne, tandis qu'un
  // delta qui prolonge une ligne existante doit RALLUMER l'indicateur si un garde-fou
  // l'avait éteint — sinon l'en-tête affiche « rien ne tourne » pendant que ça tourne.
  const cle = cleDeSuivi(e);
  if (cle && dernier) {
    if (nouvelle) {
      marquer(cle, dernier.querySelector(".pip"));
    } else if (!encours.has(cle)) {
      const el = dernier.querySelector(".pip");
      el.className = "pip encours";
      marquer(cle, el);
    }
  }
  if (e.genre === "modele") {
    if (e.cle) selModele.value = e.cle;
    selModele.className = e.temporaire ? "temporaire" : "";
    selModele.title = (e.temporaire ? "temporaire, revient après ce tour — " : "") + ech(e.libelle);
  }
  // --- ce qui clôt ce qui tournait -------------------------------------------------
  if (e.genre === "partiel") battre("stt");
  if (e.genre === "toi") { clearTimeout(echeances.stt); resoudre("stt", "ok"); accuser(e.texte); }
  if (e.genre === "resultat" && e.id) {
    resoudre("outil:" + e.id, e.echec ? "echec" : "ok",
             e.echec ? "l'outil a renvoyé une erreur" : "");
  }
  if (e.genre === "pensee") battre("pensee");
  if (e.genre === "voix") battre("voix");
  // La réflexion s'arrête dès qu'elle produit quelque chose : du texte, ou un outil.
  if (e.genre === "texte" || e.genre === "outil") {
    clearTimeout(echeances.pensee); resoudre("pensee", "ok");
  }
  if (e.genre === "tour") toutClore("tour terminé");
  if (e.genre === "arret") toutClore("interrompu");

  // Les tours rejoues ne captent pas la mesure de fenetre : elle appartient a un tour vif.
  if (e.genre === "voix" && e.id && dernier) lignesVoix.set(e.id, dernier);
  if (e.genre === "tour" && !e.passe) ligneTour = dernier;
  if (e.genre === "effort" && e.cle) selEffort.value = e.cle;
  if (e.genre === "outil" && !e.passe) actions++;
  if (e.genre === "tour" && e.jetons) jetons += e.jetons;
  majCompteurs();
  if (suivre) window.scrollTo(0, document.body.scrollHeight);
}

// L'etat du micro appartient au serveur : le bouton demande, il n'agit pas. Sinon la page
// pourrait afficher « coupe » alors que le flux audio tourne toujours.
let socket = null, microActif = true, reconnexionPrevue = false;
let etatRecu = false, attenteAgent = null;   // l'agent a-t-il déjà dit où il en est ?

// Sur telephone, la console du navigateur est inaccessible : une socket qui
// refuse de s ouvrir donnait « connexion... » sans jamais dire pourquoi.
// On affiche l erreur dans la page, c est le seul endroit ou elle sera lue.
function __diagBoite() {
  let b = document.getElementById("diag-ws");
  if (!b) {
    b = document.createElement("div");
    b.id = "diag-ws";
    b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99;padding:8px 12px;"
      + "font:12px/1.4 ui-monospace,monospace;color:#f85149;background:#1a0f10;"
      + "border-bottom:1px solid #f8514966;white-space:pre-wrap;word-break:break-all";
    document.body.prepend(b);
  }
  return b;
}
function __diagSocket(ws) {
  // Le talon des tests donne une socket sans addEventListener : rien a surveiller.
  if (!ws || typeof ws.addEventListener !== "function") return;
  const t0 = Date.now();
  const attente = setTimeout(() => {
    if (ws.readyState === WebSocket.CONNECTING) {
      __diagBoite().textContent = "⚠ WebSocket bloqué en CONNECTING depuis 6 s\n" + ws.url;
    }
  }, 6000);
  ws.addEventListener("open", () => {
    clearTimeout(attente);
    document.getElementById("diag-ws")?.remove();
  });
  ws.addEventListener("error", () => {
    __diagBoite().textContent = "⚠ WebSocket : erreur\n" + ws.url + "\npage : " + location.href;
  });
  ws.addEventListener("close", (e) => {
    clearTimeout(attente);
    if (e.code === 1000 || e.code === 1001) return;
    __diagBoite().textContent = "⚠ WebSocket fermé code=" + e.code
      + (e.reason ? " raison=" + e.reason : "")
      + " après " + Math.round((Date.now() - t0) / 1000) + " s\n" + ws.url;
  });
}

// --- aucun clic ne doit disparaitre -----------------------------------------------------
// Le bug qu'on croyait cote serveur etait ici : `if (socket.readyState === OPEN) send(...)`
// avalait silencieusement le clic quand la socket etait morte — apres une veille du
// portable, un onglet longtemps en arriere-plan, ou un redemarrage de l'agent. La page ne
// decouvrait la coupure QU'A CE MOMENT, affichait « reconnexion… », et le clic suivant
// passait. D'ou « le bouton ne marche pas du premier coup ».
//
// Desormais la commande attend la reconnexion au lieu d'etre perdue.
let enAttente = [];

function envoyerCmd(ordre) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(ordre));
    return true;
  }
  // Le DERNIER etat voulu gagne : cliquer trois fois hors ligne ne doit pas rejouer trois
  // bascules a la reconnexion, ca ramenerait a l'etat de depart. Sauf pour « texte » :
  // deux messages ecrits sont deux messages, pas un etat qu'on ecrase.
  if (ordre.cmd !== "texte") enAttente = enAttente.filter(o => o.cmd !== ordre.cmd);
  enAttente.push(ordre);
  reconnecter();          // ne pas attendre le prochain clic pour s'en apercevoir
  return false;
}

function viderFile() {
  const file = enAttente;
  enAttente = [];
  for (const o of file) {
    try { socket.send(JSON.stringify(o)); } catch (_) { enAttente.push(o); }
  }
}

// Rebrancher tout de suite, sans doublonner les tentatives. Un seul chemin de reconnexion
// dans toute la page : rebrancherMaintenant(), declare plus bas avec la gestion de la veille,
// du reseau et du compte a rebours. Il y en a eu deux pendant un temps — celui-ci et le sien —
// et ils se contredisaient exactement la ou ca comptait : celui-ci refusait de rebrancher
// tant que la socket se disait « ouverte », ce qui est precisement l'etat d'une socket morte
// au retour de veille. On revenait sur la page, rien ne se passait.
function reconnecter() {
  rebrancherMaintenant("une commande attend la liaison");
}
const btnMicro = document.getElementById("micro");
const microBas = document.getElementById("micro-bas");
// La parole est-elle détectée EN CE MOMENT. Vient du serveur (le VAD), jamais d'une
// supposition : une animation qui bouge sans raison donnerait une fausse confirmation d'être
// entendu, ce qui est pire que pas d'indicateur — on parlerait dans le vide en croyant que
// tout va bien. Le niveau audio, lui, n'est pas accessible en mode console : on montre donc
// ce qu'on sait (« ça t'entend »), pas ce qu'on n'a pas (« à ce volume »).
let paroleDetectee = false;

// Un seul endroit décide de l'apparence des DEUX boutons : il n'y a qu'un micro, et deux
// mises à jour séparées finissent toujours par se contredire — le symptôme serait le pire
// possible, croire qu'on est écouté alors qu'on ne l'est pas.
function majMicro() {
  btnMicro.textContent = microActif ? "🎤 micro" : "🔇 micro coupé";
  btnMicro.className = microActif ? "" : "coupe";
  btnMicro.title = (microActif ? "couper" : "rouvrir") + " le micro (touche m)";
  if (!microBas) return;
  microBas.className = "micro-rond " + (!microActif ? "coupe"
    : paroleDetectee ? "ouvert parle" : "ouvert");
  microBas.title = microActif
    ? (paroleDetectee ? "ça t'entend — clic pour couper (touche m)"
                      : "micro ouvert, silence — clic pour couper (touche m)")
    : "micro coupé — clic pour rouvrir (touche m).\nCoupe-le pour corriger le texte, "
      + "rouvre-le pour continuer à dicter.";
}
function basculerMicro() {
  const voulu = !microActif;
  // Retour immediat : un clic doit toujours se voir, meme si la reponse tarde. L'etat REEL
  // reste celui du serveur — c'est une attente affichee, pas une verite affirmee.
  const parti = envoyerCmd({ cmd: "micro", actif: voulu });
  btnMicro.classList.toggle("attente", !parti);
  if (!parti) btnMicro.title = "en attente de la reconnexion…";
}
btnMicro.onclick = basculerMicro;
if (microBas) microBas.onclick = basculerMicro;   // le même geste, à l'autre bout de la page
{
  const bc = document.getElementById("couper-lecture");
  if (bc) bc.onclick = () => envoyerCmd({ cmd: "couper_lecture" });
}

// Le travail en cours est montre, pas annonce. La narration parlee (« toujours dessus, je
// viens de lancer cat ») a ete retiree : on ne peut pas survoler du son, alors qu'un
// compteur dans l'en-tete se lit d'un coup d'oeil et ne coupe pas la parole.
// Le selecteur reflete l'etat du worker, il ne le devine pas : un changement vocal doit
// mettre la liste a jour, et une bascule temporaire doit se voir comme temporaire.
const selModele = document.getElementById("modele");
// « Opus 5 » plutot que « Opus 5 — le plus capable » : un select affiche le libelle complet
// de l'option choisie, et 250 px pour dire « tres eleve — defaut » ecrasait tout l'en-tete.
// La nuance reste, en infobulle.
const court = t => String(t).split(" — ")[0];
function remplirModeles(liste, actuel) {
  selModele.innerHTML = liste.map(m =>
    `<option value="${ech(m.cle)}" title="${ech(m.libelle)}">${ech(court(m.libelle))}</option>`
  ).join("");
  if (actuel) selModele.value = actuel;
  const choisi = liste.find(m => m.cle === actuel);
  if (choisi) selModele.title = "modèle utilisé pour le travail — " + choisi.libelle;
}
selModele.onchange = () => { envoyerCmd({ cmd: "modele", cle: selModele.value }); };

// Le niveau d'effort, à côté du modèle. Les cinq niveaux du SDK, pas plus : low, medium,
// high, xhigh, max. Contrairement au modèle, il ne peut pas changer en pleine tâche — il est
// figé à la construction de la session — donc le sélecteur se verrouille pendant le travail
// plutôt que d'accepter un clic qui n'aurait aucun effet.
const selEffort = document.getElementById("effort");
function remplirEfforts(liste, actuel) {
  selEffort.innerHTML = liste.map(e =>
    `<option value="${ech(e.cle)}" title="${ech(e.libelle)}">${ech(court(e.libelle))}</option>`
  ).join("");
  if (actuel) selEffort.value = actuel;
  const choisi = liste.find(e => e.cle === actuel);
  if (choisi) selEffort.title = "niveau d'effort — " + choisi.libelle;
}
selEffort.onchange = () => { envoyerCmd({ cmd: "effort", cle: selEffort.value }); };

const btnStop = document.getElementById("arreter");
const pilTravail = document.getElementById("travail");
// Vrai tant que la liaison est coupee. Ce qui suit n'est pas un detail d'affichage : pendant
// une coupure, la page continuait d'egrener « travaille 14 min » a la seconde et de faire
// defiler les mots du bandeau de cogitation. Or ce bandeau ne sert qu'a UNE chose — dire que
// la machine n'est pas morte — et c'est exactement ce qu'on ne sait plus. Le chiffre, lui,
// etait mesure depuis l'horloge locale : il continuait de monter meme si l'agent etait mort
// depuis dix minutes. On garde donc la pastille, parce que le tour tourne probablement
// encore cote serveur, mais elle cesse de compter et de s'animer, et elle le dit.
let liaisonPerdue = false;
let debutTravail = null, minuteur = null;
function majTravail() {
  if (debutTravail == null) {
    pilTravail.style.display = "none";
    btnStop.disabled = true;
    if (minuteur) { clearInterval(minuteur); minuteur = null; }
    selEffort.disabled = false;
    selEffort.title = "niveau d'effort de réflexion";
    majRetenir();
    majCogitation();
    return;
  }
  const s = Math.round((Date.now() - debutTravail) / 1000);
  const duree = s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s";
  // Sans le mot « travaille » : l'arc qui tourne devant le chiffre le dit deja, et la
  // pastille tient dans l'en-tete. Avec, elle faisait 140 px et poussait toute la zone
  // « ce qui se passe » sur un troisieme rang — un rang de plus pris au flux, en permanence
  // pendant un tour, pour repeter ce que l'animation montre. La phrase entiere reste en
  // infobulle, ou elle ne coute rien.
  pilTravail.textContent = liaisonPerdue ? "liaison perdue" : duree;
  pilTravail.classList.toggle("fige", liaisonPerdue);
  pilTravail.title = liaisonPerdue
    ? `travail commencé il y a ${duree} — il continue peut-être côté serveur, mais la page ne `
      + "le voit plus : le compteur est arrêté jusqu'à la reconnexion"
    : `Claude travaille depuis ${duree}`;
  // « inline-block », pas "" : la regle de base porte `display:none`, donc vider le style
  // en ligne ne revele rien du tout — ca REND la main a la feuille de style, qui cache.
  // Consequence longtemps invisible parce qu'elle se lit comme son contraire : la pastille
  // « travaille 3 min » n'est jamais apparue, sur aucun tour.
  pilTravail.style.display = "inline-block";
  btnStop.disabled = false;
  selEffort.disabled = true;
  selEffort.title = "tâche en cours — l'effort ne peut changer qu'entre deux tâches";
  majRetenir();
  majCogitation();
}
// --- le bandeau de cogitation -----------------------------------------------------------
// Quarante mots, entre le jargon de consultant et le vieux français de bricolage. Aucun ne
// décrit ce qui se passe réellement — c'est le point : le vrai avancement est dans le flux
// juste au-dessus, ici on montre seulement que la machine n'est pas morte.
const MOTS = [
  "Solutionnage", "Cogitation", "Élucubration", "Ratiocination", "Tergiversation",
  "Réflexionnage", "Conceptualisage", "Optimisationnage", "Synergisation", "Alambiquage",
  "Tripatouillage", "Farfouillage", "Trifouillage", "Emberlificotage", "Entortillage",
  "Déverminage", "Pinaillage", "Fignolage", "Peaufinage", "Rafistolage",
  "Décorticage", "Débroussaillage", "Bidouillage", "Gribouillage", "Chipotage",
  "Mijotage", "Percolation", "Décantation", "Macération", "Touillage",
  "Turbinage", "Moulinage", "Engrenage", "Neuronage", "Synaptisage",
  "Chauffe-méninges", "Ruminage", "Marmonnage", "Circonvolution", "Perplexation",
];

// Un tirage purement aléatoire retombe assez souvent sur le même mot deux fois de suite
// pour donner l'impression que l'affichage est figé — exactement le doute qu'on veut lever.
// Un sac mélangé garde la surprise sans jamais bégayer : on vide la liste avant de la
// remélanger, donc les quarante mots passent tous avant qu'un seul revienne.
let sac = [], dernierMot = null;
function motSuivant() {
  if (!sac.length) {
    sac = MOTS.slice();
    for (let i = sac.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [sac[i], sac[j]] = [sac[j], sac[i]];
    }
    // Le sac empêche les répétitions à l'intérieur d'un tour, mais pas à la jointure : le
    // mélange peut replacer en tête le mot qui vient d'être affiché. Mesuré, ça arrivait
    // 3 fois sur 4000 tirages — rare, et pourtant c'est le seul cas visible à l'oeil, parce
    // qu'un mot qui ne change pas est exactement ce qui fait douter que ça tourne encore.
    if (sac[sac.length - 1] === dernierMot) {
      const j = Math.floor(Math.random() * (sac.length - 1));
      [sac[sac.length - 1], sac[j]] = [sac[j], sac[sac.length - 1]];
    }
  }
  dernierMot = sac.pop();
  return dernierMot;
}

const zoneCogite = document.getElementById("cogitation");
const zoneMot = document.getElementById("mot");
let rouleau = null;

// Visible tant que quelque chose tourne vraiment : un tour de travail en cours, ou une
// action non résolue. Le minuteur ne vit que pendant ce temps — laisser tourner un
// setInterval sur une page au repos, c'est ce qui avait fini par coûter 1,5 Go de mémoire.
function majCogitation() {
  // Liaison coupee : on ne peut rien affirmer, donc on n'affirme rien. Le bandeau ne dit que
  // « la machine est vivante » — c'est precisement l'information qu'on a perdue.
  const occupe = !liaisonPerdue && (debutTravail != null || encours.size > 0);
  if (occupe && rouleau == null) {
    zoneMot.textContent = motSuivant();
    zoneCogite.hidden = false;
    rouleau = setInterval(() => { zoneMot.textContent = motSuivant(); }, 2600);
  } else if (!occupe && rouleau != null) {
    clearInterval(rouleau);
    rouleau = null;
    zoneCogite.hidden = true;
  }
}

// --- le compte a rebours avant envoi ----------------------------------------------------
// La fenêtre est déterminée : à partir de la fin de la parole le tour part soit à `min`,
// soit à `max` si le détecteur juge la phrase inachevée. Donc si le compteur atteint zéro
// sans que le message soit parti, c'est nécessairement que le détecteur a prolongé — et on
// peut le dire, au lieu d'afficher un zéro bloqué qui ressemble à une panne.
const zoneCompte = document.getElementById("compte");
const zoneReste = document.getElementById("reste");
const btnEnvoiVite = document.getElementById("envoi-vite");
let finMin = null, finMax = null, tic = null;

function majCompte() {
  if (finMin == null) {
    zoneCompte.className = "";
    if (tic) { clearInterval(tic); tic = null; }
    return;
  }
  const maintenant = Date.now();
  const prolonge = maintenant >= finMin;
  const cible = prolonge ? finMax : finMin;
  const reste = Math.max(0, (cible - maintenant) / 1000);
  // Le libellé doit dire ce qui va RÉELLEMENT se passer. En mode retenu, ou après un
  // rattrapage, rien ne s'envoie au bout du compte : annoncer « envoi » serait faux, et
  // c'est le genre de faux qui fait douter de tout l'affichage.
  const garde = retenir || rattrape;
  zoneCompte.className = "montre" + (prolonge ? " prolonge" : "")
                       + (rattrape ? " rattrape" : "");
  const quoi = garde ? "retenu dans" : "envoi dans";
  zoneReste.textContent = prolonge && !garde
    ? `phrase inachevée — ${reste.toFixed(1)} s`
    : `${quoi} ${reste.toFixed(1)} s`;
  btnEnvoiVite.textContent = garde ? "terminer" : "envoyer";
  // Passé le plafond, il n'y a plus rien à annoncer : le tour part, ou la parole a reprise.
  if (maintenant >= finMax) arreterCompte();
}

// `fin` est l'échéance RÉELLE, en horloge murale, publiée par l'agent qui commettra le tour.
// L'utiliser plutôt que de recompter localement supprime la seule vraie cause du « je vois
// encore 4 s et le message est déjà parti » : il n'y a plus qu'une horloge, et c'est celle
// qui décide. Le repli sur `min` couvre l'ancien mode automatique, où LiveKit décide et où
// la page ne peut effectivement qu'estimer.
// Un mot contre la barre, effacé au premier signe que la situation a changé : on écrit, on
// envoie, ou une nouvelle dictée s'ouvre. Une explication qui survit à son objet devient
// fausse, et l'ancienne version restait affichée jusqu'au tour suivant.
// Les messages envoyés pendant que Claude parle. Ils partent, mais LiveKit les met en file
// derrière la parole en cours : rien ne se passe pendant plusieurs secondes, et comme la barre
// s'est vidée le texte a l'air perdu. On le garde affiché jusqu'à ce qu'il soit pris en compte.
let enAttenteLecture = [];
function majEnAttente() {
  const z = document.getElementById("en-attente");
  if (!z) return;
  if (!enAttenteLecture.length) { z.hidden = true; return; }
  const n = enAttenteLecture.length;
  z.innerHTML = `<span class="quoi">⏳ ${n > 1 ? n + " messages" : "envoi"} à la fin de la `
    + `lecture</span><span class="dit">${ech(enAttenteLecture.join(" · "))}</span>`;
  z.hidden = false;
}

let tempsNote = null;
// Suit l'état de la barre pour n'envoyer le signal qu'AU CHANGEMENT : une commande par frappe
// de touche noierait le journal et la liaison.
let barreVide = true;
function noteBarre(texte, collante = false) {
  const n = document.getElementById("note-barre");
  if (!n) return;
  if (tempsNote) { clearTimeout(tempsNote); tempsNote = null; }
  if (!texte) { n.classList.remove("montre", "collante"); n.hidden = true; return; }
  n.textContent = texte;
  n.hidden = false;
  // Une erreur qui disparait toute seule au bout de douze secondes n'a pas ete lue si on
  // avait le regard ailleurs. Collante, elle reste jusqu'a ce qu'on ecrive ou qu'on la touche.
  n.classList.toggle("collante", collante);
  n.onclick = () => noteBarre(null);
  requestAnimationFrame(() => n.classList.add("montre"));
  if (!collante) tempsNote = setTimeout(() => noteBarre(null), 12000);
}

// --- ce qui est parti mais pas encore pris en compte ---------------------------------------
// Entre le clic et la ligne « toi » qui revient du serveur, le message n'existait nulle part
// a l'ecran : la barre etait vide, le flux n'avait rien. Sur un lien lent, ou pendant que
// l'agent finit de demarrer, ca dure des secondes — et on ne sait pas s'il est parti. On le
// garde donc visible jusqu'a l'echo du serveur ; s'il echoue ou ne revient pas, on le rend.
const enVol = [];   // { texte, quand }
const ATTENTE_ACCUSE = 20000;

function majEnVol() {
  const z = document.getElementById("en-vol");
  if (!z) return;
  if (!enVol.length) { z.hidden = true; z.innerHTML = ""; return; }
  const horsLigne = !socket || socket.readyState !== WebSocket.OPEN;
  z.innerHTML = enVol.map((m, i) => {
    const tarde = !horsLigne && Date.now() - m.quand > ATTENTE_ACCUSE;
    const quoi = horsLigne ? "part à la reconnexion" : tarde ? "sans confirmation" : "envoi…";
    return `<div class="vol${tarde ? " tarde" : ""}"><span class="quoi">${tarde ? "⚠" : "⏳"} ${quoi}</span>`
      + `<span class="dit">${ech(m.texte)}</span>`
      + (tarde ? `<button type="button" data-vol="${i}">remettre dans le champ</button>` : "")
      + `</div>`;
  }).join("");
  z.hidden = false;
  z.querySelectorAll("[data-vol]").forEach(b => { b.onclick = () => rendreMessage(Number(b.dataset.vol)); });
}

// Rendre un message a son auteur : dans le champ, devant ce qu'il a pu commencer a ecrire.
function rendreMessage(i, raison) {
  const [m] = enVol.splice(i, 1);
  if (!m) return;
  champ.value = champ.value.trim() ? m.texte + "\n" + champ.value : m.texte;
  barreVide = false;
  ajusterHauteur(); majEnvoyer(); majEnVol();
  if (raison) noteBarre(raison, true);
  champ.focus();
}

function accuser(texte) {
  const i = enVol.findIndex(m => m.texte === texte);
  if (i >= 0) { enVol.splice(i, 1); majEnVol(); }
}

// Renvoyer ce qui n'est jamais arrive. Appele une seule fois par reconnexion, a la fin du
// rejeu : avant, on ne saurait pas distinguer « perdu » de « pas encore rejoue », et on
// posterait le message en double.
function renvoyerEnVol() {
  if (!enVol.length || !socket || socket.readyState !== WebSocket.OPEN) return 0;
  let renvoyes = 0;
  for (const m of enVol) {
    try { socket.send(JSON.stringify({ cmd: "texte", texte: m.texte })); renvoyes++; m.quand = Date.now(); }
    catch (_) { break; }
  }
  majEnVol();
  return renvoyes;
}
// Le temps qui passe change ce qu'on affiche (« envoi… » puis « sans confirmation »).
setInterval(() => { if (enVol.length) majEnVol(); }, 1000);

function lancerCompte(min, max, fin) {
  if (fin) {
    // Échéance exacte, sans extension possible : en manuel il n'y a pas de détecteur pour
    // juger la phrase inachevée, donc afficher « phrase inachevée — 3 s » serait inventé.
    finMin = finMax = fin;
  } else {
    finMin = Date.now() + min * 1000;
    finMax = Date.now() + max * 1000;
  }
  if (!tic) tic = setInterval(majCompte, 100);
  majCompte();
}

function arreterCompte() {
  finMin = finMax = null;
  rattrape = false;   // le rattrapage ne vaut que pour le tour qui vient de finir
  majCompte();
}

// Envoyer sans attendre. La fenêtre reste longue pour pouvoir réfléchir à voix haute ; ce
// bouton dit simplement « j'ai fini », sans raccourcir la fenêtre pour tous les tours.
function envoyerVite() {
  envoyerCmd({ cmd: "envoyer" });
  arreterCompte();
}
btnEnvoiVite.onclick = envoyerVite;

function arreter() {
  if (btnStop.disabled) return;
  envoyerCmd({ cmd: "arreter" });
}
btnStop.onclick = arreter;
addEventListener("keydown", ev => {
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (/^(INPUT|TEXTAREA)$/.test(ev.target.tagName)) return;
  if (ev.key === "m") basculerMicro();
  if (ev.key === "s") arreter();
  if (ev.key === "/") { ev.preventDefault(); champ.focus(); }
  if (ev.key === "r") basculerRetenir();
  // Entrée hors du champ : « j'ai fini de parler ». Le geste le plus naturel pour couper
  // l'attente, et il ne peut pas entrer en conflit avec la saisie, qui est exclue plus haut.
  if (ev.key === "Enter") envoyerVite();
});
majMicro();

// --- écrire au lieu de parler -----------------------------------------------------------
// Le message part au serveur, qui l'injecte comme un tour utilisateur : il suit donc la
// même route qu'une phrase entendue. La page ne l'affiche pas elle-même — c'est le serveur
// qui republie « toi », comme pour la voix. Sinon un envoi refusé laisserait sur l'écran un
// message que l'agent n'a jamais reçu.
const champ = document.getElementById("saisie");
const btnEnvoyer = document.getElementById("envoyer");
const composer = document.getElementById("composer");

// Combien d'images sont pretes a partir. Tenu a jour par majJointes(), plus bas.
let imagesPretes = 0;
function majEnvoyer() {
  // Le bouton suit le CONTENU du champ, pas l'état de la liaison. Il en dépendait, et Entrée
  // non : deux chemins pour la même intention, avec deux comportements différents — le clavier
  // mettait en file, le bouton refusait. Un bouton grisé alors que la touche marche est un
  // mensonge sur ce qui est possible.
  btnEnvoyer.disabled = champ.value.trim().length === 0 && imagesPretes === 0;
  // L'attente se voit, sans empêcher le geste : le message sera mis en file.
  const horsLigne = !socket || socket.readyState !== WebSocket.OPEN;
  btnEnvoyer.classList.toggle("attente", horsLigne && !btnEnvoyer.disabled);
  btnEnvoyer.title = horsLigne
    ? "hors ligne — le message sera envoyé à la reconnexion"
    : "envoyer (Entrée)";
}
// --- la dictée s'écrit dans la barre ----------------------------------------------------
// Le principe qui évite tous les conflits : on retient le contenu du champ au DÉBUT de
// l'énoncé, et le texte reconnu s'ajoute à cette base. Champ vide, la dictée le remplit ;
// champ déjà rempli d'une correction, elle s'ajoute à la suite. Rien n'est jamais écrasé,
// parce qu'écraser une correction qu'on vient de taper serait la pire des trahisons.
// Trois choses distinctes, et les confondre produisait deux bugs reproduits :
//
// - `brouillon` : ce que TU avais tapé avant de parler. Il doit survivre à l'envoi de la
//   dictée — c'est ton texte, pas celui de la machine.
// - `segments`  : les morceaux déjà finalisés de la dictée EN COURS. Une pause de deux
//   secondes coupe la phrase en deux transcriptions finales, et chacune ne contient que son
//   propre segment. En les remplaçant au lieu de les cumuler, le début disparaissait de la
//   barre.
// - `dicteeOuverte` : y a-t-il une dictée en cours. Sans ce drapeau, une transcription
//   arrivant APRÈS l'envoi du tour — LiveKit le fait, il le journalise même — remettait dans
//   la barre un texte déjà envoyé, qui repartait au message suivant.
let brouillon = null, segments = "", dicteeOuverte = false;
let retenir = false;

// Une dictée s'ouvre quand tu commences à parler, et pas avant : c'est ce qui délimite un
// énoncé, et donc ce qui permet d'ignorer les transcriptions en retard.
function ouvrirDictee() {
  if (dicteeOuverte) return;
  brouillon = champ.value ? champ.value.trimEnd() + " " : "";
  segments = "";
  dicteeOuverte = true;
}

function poserDictee(texte, definitif) {
  // Rien à écrire hors dictée : une transcription tardive appartient à un tour déjà parti.
  if (!dicteeOuverte) return;
  if (definitif) {
    // Cumulé, pas remplacé : la suite de la phrase s'écrit derrière ce segment.
    segments = (segments + texte).trim() + " ";
    texte = "";
  }
  // Seule la JONCTION est normalisée. Nettoyer tout le champ détruirait la mise en forme
  // d'un brouillon tapé — retours à la ligne compris.
  const suite = segments ? texte.replace(/^\s+/, "") : texte;
  champ.value = (brouillon + segments + suite).trimEnd();
  champ.classList.toggle("dictee", true);
  majEnvoyer();
  // La dictée écrit sans passer par oninput : sans cet appel le champ ne grandirait que
  // quand on tape, c'est-à-dire jamais pendant qu'on parle.
  ajusterHauteur();
}

// La dictée est consommée. `garde` porte le texte à laisser dans la barre — celui que le
// serveur a retenu — ou rien du tout si le tour est parti chez Claude.
function fermerDictee(garde) {
  const debut = brouillon || "";
  champ.value = (garde ? (debut + garde) : debut).trimEnd();
  brouillon = null;
  segments = "";
  dicteeOuverte = false;
  champ.classList.remove("dictee");
  majEnvoyer();
  ajusterHauteur();
}

// Fin de tour en mode envoi direct : le texte est parti chez Claude, la barre se vide.
// Le tour est parti chez Claude : la dictée disparaît de la barre, le brouillon tapé reste.
function viderDictee() {
  // Ne retire QUE la partie dictée, jamais le brouillon tapé : on peut avoir écrit une phrase,
  // parlé ensuite, et le tour vocal parti ne doit pas emporter ce qu'on avait écrit à côté.
  //
  // J'ai voulu vider inconditionnellement, pour empêcher un texte retenu de survivre à un
  // envoi — et ça détruisait ce brouillon. Le vrai correctif est ailleurs, côté agent : la
  // retenue est COLLANTE, donc plus aucun tour vocal ne peut partir tant qu'un texte attend
  // une relecture. Le mauvais scénario ne peut plus se produire, il n'y a rien à rattraper ici.
  if (dicteeOuverte) fermerDictee(null);
  barreVide = !champ.value.trim();
}

// --- au bout de combien de silence le message part -------------------------------------
// 3, 5 et 10 s sont les repères ; les autres paliers existent pour pouvoir descendre plus
// court ou monter plus long sans toucher à la configuration. Le plafond « phrase inachevée »
// suit le plancher : le régler à 15 s ne doit pas le placer au-dessus d'un plafond figé.
const selDelai = document.getElementById("delai");
function remplirDelais(paliers, actuel, plafond) {
  const vus = paliers.map(p => p.s);
  // Une valeur venue de la configuration et absente des paliers doit rester proposée,
  // sinon le sélecteur afficherait autre chose que ce qui est réellement en vigueur.
  if (actuel != null && !vus.includes(actuel)) vus.push(actuel);
  vus.sort((a, b) => a - b);
  selDelai.innerHTML = vus.map(s =>
    `<option value="${s}">${s} s</option>`).join("");
  if (actuel != null) selDelai.value = String(actuel);
  majTitreDelai(actuel, plafond);
}
function majTitreDelai(actuel, plafond) {
  selDelai.title = `envoi après ${actuel} s de silence`
    + (plafond ? ` — jusqu'à ${plafond} s si la phrase semble inachevée` : "");
}
selDelai.onchange = () => {
  envoyerCmd({ cmd: "delai", secondes: parseFloat(selDelai.value) });
};

// Rattraper le message pendant son décompte, sans avoir armé « retenir » à l'avance.
const btnRetenirVite = document.getElementById("retenir-vite");
let rattrape = false;
btnRetenirVite.onclick = () => {
  if (!envoyerCmd({ cmd: "retenir_tour" })) return;
  rattrape = true;
  majCompte();
};

const btnRetenir = document.getElementById("retenir");
function majRetenir() {
  // Pendant une tâche, la retenue s'applique d'office. Le bouton le dit AVANT qu'on parle :
  // découvrir après coup que son message n'est pas parti est la pire façon de l'apprendre.
  const auto = !retenir && debutTravail != null;
  btnRetenir.className = retenir ? "on" : (auto ? "auto" : "");
  btnRetenir.textContent = retenir ? "retenu" : (auto ? "retenu ·" : "retenir");
  btnRetenir.title = (retenir
    ? "la dictée reste dans la barre — tu relis, tu corriges, tu envoies"
    : auto
      ? "Claude travaille : ce que tu dis est retenu dans la barre, pas envoyé"
      : "la dictée part dès que tu as fini de parler") + " (touche r)";
}
function basculerRetenir() {
  retenir = !retenir;
  majRetenir();
  envoyerCmd({ cmd: "retenir", actif: retenir });
}
btnRetenir.onclick = basculerRetenir;
majRetenir();

// Taper reprend la main. L'événement input ne se déclenche que pour une vraie frappe — une
// valeur posée par le script ne le déclenche pas — donc ceci ne peut venir que de toi, et ce
// que tu écris devient la nouvelle base de la dictée suivante.
champ.oninput = () => {
  // Une note collante (une erreur) a ete lue des qu'on se remet a ecrire.
  if (document.getElementById("note-barre")?.classList.contains("collante")) noteBarre(null);
  // Taper reprend la main : ce que tu écris devient le brouillon, et la dictée en cours est
  // abandonnée. Sans ça, la transcription suivante écraserait ta correction.
  brouillon = null;
  segments = "";
  dicteeOuverte = false;
  champ.classList.remove("dictee");
  // La barre vidée à la main libère la retenue collante. Sans ce signal, l'agent croirait
  // qu'un texte attend encore une relecture et retiendrait TOUT indéfiniment — une
  // amélioration qui se transforme en blocage silencieux est pire que le défaut d'origine.
  // Taper sort de la navigation : ce qu'on modifie devient le brouillon courant, et
  // l'historique n'est jamais réécrit. Sans ça, corriger un message remonté puis redescendre
  // aurait perdu la correction.
  if (histoPos >= 0) { histoPos = -1; noteBarre(null); }
  const videMaintenant = !champ.value.trim();
  if (videMaintenant !== barreVide) {
    barreVide = videMaintenant;
    if (videMaintenant) envoyerCmd({ cmd: "barre_vide" });
  }
  majEnvoyer();
  ajusterHauteur();
};

// --- le champ grandit avec ce qu'on y met ------------------------------------------------
// Sur une seule ligne, une longue dictée sortait du champ : on ne voyait plus ce qui était en
// train d'être reconnu, donc on ne pouvait plus le corriger. Le champ suit maintenant son
// contenu, jusqu'à 40 % de la hauteur de fenêtre — au-delà il défile à l'intérieur, parce
// qu'un champ qui mange l'écran cache le flux qu'on est venu lire.
// Doit suivre la hauteur CSS du champ : les deux se contredisant, le champ oscillait d'un
// pixel a chaque frappe et « une-ligne » clignotait.
const HAUTEUR_MINI = 52;
const barreSaisie = document.getElementById("saisie-barre");
const btnBas = document.getElementById("bas");
const zoneFlux = document.querySelector("main");

function ajusterHauteur() {
  // « auto » d'abord : sans ça scrollHeight reste bloqué sur la hauteur précédente et le
  // champ ne redescend jamais quand on efface.
  champ.style.height = "auto";
  // 55 % de la hauteur : une longue dictee doit se relire sans naviguer, et c'est ce
  // qu'on fait juste avant d'envoyer. En dessous, le texte defilait hors de vue des trois
  // phrases — or c'est precisement le moment ou l'on veut tout voir d'un coup.
  const plafond = Math.round((window.innerHeight || 800) * 0.55);
  const voulue = Math.min(Math.max(champ.scrollHeight || HAUTEUR_MINI, HAUTEUR_MINI), plafond);
  champ.style.height = voulue + "px";
  // La barre de defilement n'apparait qu'au plafond. Laisser `overflow-y:auto` en
  // permanence affichait une gouttiere des la deuxieme ligne, pour rien.
  champ.style.overflowY = voulue >= plafond ? "auto" : "hidden";
  champ.classList.toggle("une-ligne", voulue <= HAUTEUR_MINI + 2);
  reserverPlace();
}

// La barre est en position fixe : si elle grandit sans que le flux recule, elle recouvre les
// dernières lignes — précisément celles qu'on veut voir en dictant.
function reserverPlace() {
  const h = barreSaisie.offsetHeight || 58;
  if (zoneFlux) zoneFlux.style.paddingBottom = (h + 60) + "px";
  if (btnBas) btnBas.style.bottom = (h + 14) + "px";
  if (suivre) window.scrollTo(0, document.body.scrollHeight);
}

// --- l'historique des messages, aux flèches ---------------------------------------------
// Ce que ça sert : retrouver ce qu'on a écrit. Un message qui n'est pas parti, un envoi qu'on
// veut refaire avec une correction, une phrase perdue par une coupure de liaison — sans
// historique, il faut la retaper de mémoire.
//
// Le comportement copie celui des champs de recherche d'un éditeur, parce qu'il est juste :
//
// - **La flèche ne navigue que si le curseur ne peut pas bouger.** Haut sur la première ligne,
//   bas sur la dernière. Sinon elle déplace le curseur, comme dans n'importe quel champ
//   multiligne. Confondre les deux rendrait la correction d'un long texte insupportable.
// - **Le brouillon en cours est gardé à l'indice -1.** Remonter puis redescendre le rend
//   intact : naviguer ne doit jamais détruire ce qu'on était en train d'écrire.
// - **Taper quoi que ce soit sort de la navigation.** On édite une copie, pas l'historique.
// - **Ça survit au rechargement de la page.** C'est tout l'intérêt quand « il y a eu un bug » :
//   un historique en mémoire disparaîtrait avec le problème qu'il devait réparer.
const HISTO_CLE = "claude-talk:historique";
const HISTO_MAX = 100;
let histo = [], histoPos = -1, histoBrouillon = "";

function histoCharger() {
  try {
    const brut = localStorage.getItem(HISTO_CLE);
    histo = brut ? JSON.parse(brut) : [];
    if (!Array.isArray(histo)) histo = [];
  } catch (_) {
    // Navigation privée, stockage refusé, JSON abîmé : on repart sur rien plutôt que de
    // casser la page pour un confort.
    histo = [];
  }
}
histoCharger();

function histoAjouter(texte) {
  texte = (texte || "").trim();
  if (!texte) return;
  // Le même message deux fois de suite n'occupe qu'une entrée : remonter dix fois le même
  // texte ne rend pas service.
  if (histo[0] === texte) { histoPos = -1; return; }
  histo.unshift(texte);
  if (histo.length > HISTO_MAX) histo.length = HISTO_MAX;
  histoPos = -1;
  histoBrouillon = "";
  try { localStorage.setItem(HISTO_CLE, JSON.stringify(histo)); } catch (_) {}
}

// Le curseur peut-il encore monter (ou descendre) dans le champ ? Si oui, la flèche lui
// appartient : c'est ce test qui empêche l'historique de voler la navigation d'un long texte.
function surPremiereLigne() {
  return champ.selectionStart === champ.selectionEnd
    && champ.value.lastIndexOf("\n", Math.max(0, champ.selectionStart - 1)) < 0;
}
function surDerniereLigne() {
  return champ.selectionStart === champ.selectionEnd
    && champ.value.indexOf("\n", champ.selectionStart) < 0;
}

function histoAller(pas) {
  const cible = histoPos + pas;
  if (cible < -1 || cible >= histo.length) return false;
  // Sauver le brouillon AVANT de quitter le présent, une seule fois : le réécrire à chaque
  // pas le remplacerait par une entrée d'historique.
  if (histoPos === -1) histoBrouillon = champ.value;
  histoPos = cible;
  champ.value = cible === -1 ? histoBrouillon : histo[cible];
  // La dictée en cours est abandonnée : on vient de choisir un texte, une transcription qui
  // arriverait derrière n'aurait rien à faire dedans.
  brouillon = null; segments = ""; dicteeOuverte = false;
  champ.classList.remove("dictee");
  ajusterHauteur();
  majEnvoyer();
  // Curseur à la fin : on veut compléter ou corriger la fin, presque jamais le début.
  const n = champ.value.length;
  champ.setSelectionRange(n, n);
  noteBarre(cible === -1 ? null
    : `message ${cible + 1} sur ${histo.length} · ↑↓ pour naviguer, Échap pour revenir`);
  return true;
}

champ.addEventListener("keydown", ev => {
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (ev.key === "ArrowUp" && surPremiereLigne()) {
    if (histoAller(+1)) ev.preventDefault();
  } else if (ev.key === "ArrowDown" && surDerniereLigne() && histoPos >= 0) {
    if (histoAller(-1)) ev.preventDefault();
  }
});

// Entrée envoie, Maj+Entrée passe à la ligne. Un textarea insère un retour par défaut et ne
// déclenche pas le submit du formulaire : sans ça, la touche la plus naturelle ne ferait
// plus rien.
champ.addEventListener("keydown", ev => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.ctrlKey && !ev.metaKey) {
    ev.preventDefault();
    if (composer.requestSubmit) composer.requestSubmit();
    else composer.onsubmit({ preventDefault() {} });
  }
});

addEventListener("resize", () => {
  ajusterHauteur();
  placerPanneau(document.getElementById("choix-moteur"));
  placerPanneau(document.getElementById("choix-conv"));
});
// Échap rend le clavier aux raccourcis, sans envoyer. Attaché ici et pas plus haut : le
// gestionnaire global s'exécute au chargement, et y toucher « champ » avant sa déclaration
// tuait tout le script — page blanche, sans rien dans le flux pour le dire.
champ.addEventListener("keydown", ev => {
  if (ev.key !== "Escape") return;
  // Échap en cours de navigation ramène au brouillon plutôt que de rendre le clavier : on
  // vient de remonter dans l'historique et on veut annuler CE geste, pas quitter le champ.
  // Un second Échap sort, comme avant.
  if (histoPos >= 0) { histoAller(-1 - histoPos); return; }
  noteBarre(null);
  champ.blur();
});

// --- images jointes ----------------------------------------------------------------------
// Ce qui part n'est pas l'image mais son CHEMIN sur la machine : Claude Code sait lire une
// image a partir d'un chemin, et le flux d'evenements reste du texte.
const jointes = [];   // { chemin, url } — l'url ne sert qu'a la vignette locale
const zoneJointes = document.getElementById("jointes");
const champFichier = document.querySelector("#joindre input");

function messageAvecImages(texte, chemins) {
  if (!chemins.length) return texte;
  // Sans phrase, le message serait une liste de chemins sans verbe : on en met une, parce
  // qu'une photo envoyee seule veut presque toujours dire « regarde ca ».
  return (texte || "regarde cette image.")
    + "\n\nimages jointes :\n" + chemins.map(c => "- " + c).join("\n");
}

function majJointes() {
  imagesPretes = jointes.filter(j => j.chemin).length;
  zoneJointes.hidden = jointes.length === 0;
  zoneJointes.innerHTML = jointes.map((j, i) => j.rate
    ? `<div class="jointe rate">${ech(j.rate)}</div>`
    : `<div class="jointe${j.chemin ? "" : " envoi"}"><img src="${j.url}" alt="">`
      + `<button type="button" data-jointe="${i}" title="retirer">&#10005;</button></div>`).join("");
  zoneJointes.querySelectorAll("[data-jointe]").forEach(b => {
    b.onclick = () => { jointes.splice(Number(b.dataset.jointe), 1); majJointes(); majEnvoyer(); };
  });
  majEnvoyer();
}

async function joindre(fichier) {
  const entree = { url: URL.createObjectURL(fichier), chemin: null, rate: null };
  jointes.push(entree);
  majJointes();
  try {
    const corps = new FormData();
    corps.append("image", fichier, fichier.name || "photo.jpg");
    // Relatif au chemin de la page : servie derriere un proxy, une adresse absolue
    // viserait la racine du proxy — le meme piege que pour le WebSocket.
    const r = await fetch(location.pathname.replace(/\/$/, "") + "/image",
                          { method: "POST", body: corps });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || !d.chemin) throw new Error(d.erreur || `envoi refuse (${r.status})`);
    entree.chemin = d.chemin;
  } catch (e) {
    entree.rate = "image non envoyee — " + (e.message || e);
  }
  majJointes();
}

champFichier.onchange = () => {
  for (const f of champFichier.files) joindre(f);
  champFichier.value = "";   // sans ca, choisir deux fois la meme photo ne declenche rien
};

composer.onsubmit = ev => {
  ev.preventDefault();
  const prets = jointes.filter(j => j.chemin).map(j => j.chemin);
  const texte = messageAvecImages(champ.value.trim(), prets);
  if (!texte) return;
  // Une image encore en cours d'envoi partirait sans son chemin : on attend le tour suivant
  // plutot que d'envoyer un message qui parle d'une piece absente.
  if (jointes.some(j => !j.chemin && !j.rate)) {
    noteBarre("une image finit de partir…");
    return;
  }
  // Plus de test sur l'état de la liaison, et c'était un vrai défaut : la fonction sortait
  // AVANT d'appeler envoyerCmd, qui sait pourtant mettre en file et reconnecter. Liaison
  // coupée, on tapait un message, on faisait Entrée, et il ne se passait rien — sans un mot.
  // Le commentaire de cette ligne disait « jamais perdu » alors que le code le jetait.
  //
  // La liaison tombe pour des raisons banales : l'onglet passe en arrière-plan, le portable
  // se met en veille, l'agent redémarre. C'est exactement là qu'on ne veut pas perdre ce
  // qu'on vient d'écrire.
  histoAjouter(texte);
  enVol.push({ texte, quand: Date.now() });
  majEnVol();
  const parti = envoyerCmd({ cmd: "texte", texte });
  // Une lecture est en cours : le message part mais ne sera traité qu'après. On le garde
  // visible en grisé plutôt que de laisser un vide de plusieurs secondes.
  if (parti && lectureEnCours) {
    enAttenteLecture.push(texte);
    majEnAttente();
  }
  if (!parti) {
    // On le DIT plutôt que de laisser croire à un envoi : le message part à la reconnexion.
    noteBarre("hors ligne — le message part dès que la liaison revient");
  }
  jointes.length = 0;
  majJointes();
  champ.value = "";
  barreVide = true;
  majEnvoyer();
  ajusterHauteur();
  suivre = true;
  window.scrollTo(0, document.body.scrollHeight);
};

// Le point d'entree unique de tout ce qui arrive du serveur. Nomme, et hors de
// ws.onmessage, pour deux raisons : le routage est la partie la plus facile a casser en
// ajoutant un genre, et un harnais qui appelle `ajouter()` directement ne le traverse pas —
// c'est ainsi qu'un apercu montrait des selecteurs vides en croyant montrer l'application.
function recevoir(e, etat = false) {
    if (e.genre === "_histoire") {
      // L'état du micro se relit sur TOUTE l'histoire, y compris la partie déjà affichée :
      // c'est une resynchronisation, pas un affichage.
      e.evenements.forEach(ev => { if (ev.genre === "micro") microActif = ev.actif; });
      majMicro();
      enRejeu = true;
      try { e.evenements.filter(ev => !dejaVu(ev)).forEach(ajouter); }
      finally { enRejeu = false; }
      return;
    }
    if (etat) {
      // Un état rejoué à la connexion ne passe PAS par le compteur monotone. Le serveur
      // renvoie l'état dans l'ordre de son dictionnaire, pas dans l'ordre des numéros : le
      // quota (n° 116) arrivait avant les modèles (n° 9), pris alors pour du déjà-vu — comme
      // les efforts, les délais, et tout l'historique derrière. Listes vides et page muette
      // pour toute page ouverte après les premières minutes de session.
      if (typeof e.n === "number") {
        if (etatsRejoues.has(e.n)) return;
        if (ETATS_EN_LIGNE.has(e.genre) && e.n <= vuJusqua) return;   // déjà dans le flux
        etatsRejoues.add(e.n);
      }
    } else if (dejaVu(e)) return;
    if (e.genre === "erreur" && !enRejeu) {
      // Le serveur nomme le message qui a echoue quand il le connait : on le rend, avec la
      // raison. Une erreur sans rapport (un quota de transcription, par exemple) ne rend
      // rien, mais se lit quand meme dans la barre — le flux, on ne le regarde pas toujours.
      const i = e.commande === "texte"
        ? (e.message ? enVol.findIndex(m => m.texte === e.message) : (enVol.length ? 0 : -1))
        : -1;
      if (i >= 0) rendreMessage(i, e.texte); else noteBarre(e.texte, true);
    }
    if (e.genre === "modeles") { remplirModeles(e.liste || [], e.actuel); return; }
    if (e.genre === "efforts") { remplirEfforts(e.liste || [], e.actuel); return; }
    if (e.genre === "delais") {
      remplirDelais(e.paliers || [], e.actuel, e.plafond); return;
    }
    if (e.genre === "delai") {
      selDelai.value = String(e.secondes);
      majTitreDelai(e.secondes, e.plafond);
      return;
    }
    // La mesure de fenêtre arrive une seconde après le bilan du tour : elle complète la
    // ligne existante au lieu d'en créer une seconde, sinon un tour laisserait deux traces
    // pour un seul événement.
    if (e.genre === "tour_quota") { completerTour(e); return; }
    if (e.genre === "moteurs_stt") {
      inventaireMoteurs = e.liste || [];
      chaineMoteurs = e.chaine || [];
      moteurImpose = !!e.impose;
      const tete = inventaireMoteurs.find(m => m.cle === e.actif);
      if (e.direct != null) sttDirect = !!e.direct;
      majMoteur(tete ? tete.libelle : e.actif, false);
      majResteMoteur();
      const b = document.getElementById("choix-moteur");
      if (b && !b.hidden) dessinerChoix();
      return;
    }
    if (e.genre === "vider") {
      // Changer de conversation ne doit pas empiler deux conversations dans le même flux :
      // on ne saurait plus laquelle on lit, et les compteurs additionneraient les deux.
      // Le rejeu de la nouvelle arrive juste après.
      flux.textContent = "";
      actions = 0; jetons = 0;
      majCompteurs();
      // La barre aussi, et sans condition : ce qu'elle porte appartient à la conversation
      // qu'on quitte. Le garder l'enverrait à la suivante, qui n'a rien demandé — c'est la
      // même famille de bug que le texte déjà envoyé qui réapparaît.
      viderDictee();
      champ.value = "";
      ajusterHauteur();
      majEnvoyer();
      barreVide = true;
      noteBarre(null);
      return;
    }
    if (e.genre === "conversations") {
      convs = e.liste || [];
      convCourante = e.courante || null;
      convDossier = e.dossier || "";
      majConvs();
      const b = document.getElementById("choix-conv");
      if (b && !b.hidden) dessinerConvs();
      return;
    }
    if (e.genre === "consommation") {
      consoMoteurs = e.moteurs || [];
      majResteMoteur();
      const b = document.getElementById("choix-moteur");
      if (b && !b.hidden) dessinerChoix();
      return;
    }
    if (e.genre === "moteur_actif") {
      majMoteur(e.libelle, (e.tombes || []).length > 0);
      return;
    }
    if (e.genre === "transcrit") {
      if (e.actif) {
        if (e.direct != null) sttDirect = !!e.direct;
        // On réutilise la clé « stt » du cycle de vie : la pastille existe déjà, elle
        // n'attendait qu'un signal qu'un moteur batch ne donne jamais.
        if (!encours.has("stt")) marquer("stt", document.createElement("span"));
        battre("stt");
      } else {
        clearTimeout(echeances.stt);
        resoudre("stt", "ok");
      }
      return;
    }
    if (e.genre === "pupitre") { majPupitre(e); return; }
    // Le texte est complet, mais la voix le lit encore : on rearme le garde-fou plutot
    // que d'eteindre l'indicateur, sinon « parole » s'eteindrait en pleine phrase.
    if (e.genre === "parole_fin") { battre("voix"); outillerParole(e); return; }
    if (e.genre === "lecture") {
      lectureEnCours = e.actif ? e.id : null;
      majBoutonsLecture();
      // Filet de sécurité : si la lecture s'arrête et que le message n'a toujours pas été
      // pris en compte dix secondes plus tard, quelque chose s'est mal passé. Mieux vaut
      // effacer l'attente que la laisser affichée pour toujours — une indication fausse est
      // pire qu'une absence d'indication.
      if (!lectureEnCours && enAttenteLecture.length) {
        setTimeout(() => {
          if (!lectureEnCours && enAttenteLecture.length) {
            enAttenteLecture = [];
            majEnAttente();
          }
        }, 10000);
      }
      return;
    }
    if (e.genre === "micro") {
      microActif = e.actif;
      // Le micro change d'état : l'énoncé en cours n'en est plus un. Sans cette remise à
      // zéro, couper le micro en pleine parole laissait les ondes s'animer indéfiniment
      // alors que plus rien n'était entendu — une confirmation FAUSSE d'être écouté, donc
      // exactement le défaut que cet indicateur est censé éviter.
      paroleDetectee = false;
      btnMicro.classList.remove("attente");   // le serveur a repondu : plus d'attente
      majMicro();
    }
    if (e.genre === "travail") {
      if (!e.actif) toutClore("travail terminé");
      debutTravail = e.actif ? Date.now() : null;
      if (e.actif && !minuteur) minuteur = setInterval(majTravail, 1000);
      majTravail();
      return;
    }
    if (e.genre === "partiel") {
      // `final` doit être transmis : une transcription finale ne remplace pas la précédente,
      // elle s'ajoute derrière. Le passer en dur à `false` faisait disparaître le début de
      // toute phrase coupée par une pause — et une pause de deux secondes suffit.
      poserDictee(e.texte || "", !!e.final);
      ajouter(e);
      return;
    }
    // Déposé pour relecture : la dictée devient définitive dans la barre, à toi de jouer.
    if (e.genre === "dictee") {
      // Le texte du serveur est le texte CONSOLIDÉ du tour : il fait autorité sur les
      // segments accumulés côté page, qui peuvent avoir manqué un morceau.
      fermerDictee(e.texte || "");
      champ.focus();
      // Un mot sous la barre, au moment exact où le texte s'y dépose : c'est là que le regard
      // est. Le trouver dans le flux quinze lignes plus haut ne sert à rien.
      noteBarre({
        attente: "ajouté à ce qui attend déjà — relis, puis Entrée",
        occupe: "retenu pendant que Claude travaille — relis, puis Entrée",
        mode: "mode « retenir » armé — relis, puis Entrée",
        tour: "rattrapé — relis, puis Entrée",
      }[e.raison] || "retenu — relis, puis Entrée");
      ajouter(e);            // et une ligne, sinon rien ne dit qu'on a parlé pour rien
      return;
    }
    // Parti chez Claude : la barre n'a plus à porter le texte.
    // La ligne « toi » signifie « pris en compte » : c'est le seul signal qui l'atteste, et
    // donc le seul moment ou l'attente cesse d'etre vraie. La retirer sur « fin de lecture »
    // serait faux — le tour n'est pas encore passe par llm_node a cet instant.
    if (e.genre === "toi" && enAttenteLecture.length) {
      const i = enAttenteLecture.indexOf((e.texte || "").trim());
      enAttenteLecture.splice(i >= 0 ? i : 0, 1);
      majEnAttente();
    }
    if (e.genre === "toi" && !e.tape) {
      // Une dictée prise en compte entre aussi dans l'historique : « les dernières choses que
      // j'ai écrit » et « ce que j'ai dit » sont la même liste à l'usage — on remonte pour
      // renvoyer une phrase, sans se souvenir par quel canal elle est passée.
      histoAjouter(e.texte || "");
      viderDictee();
      noteBarre(null);
    }
    if (e.genre === "retenir") { retenir = !!e.actif; majRetenir(); return; }
    if (e.genre === "ecoute") {
      // `parle` marque le DÉBUT de la parole : c'est là qu'une nouvelle dictée s'ouvre, et
      // nulle part ailleurs. Ce repère est ce qui permet d'ignorer une transcription en
      // retard, qui appartient au tour précédent.
      if (e.parle) { ouvrirDictee(); noteBarre(null); }
      // `parle` marque le début de la parole, `actif` le début du silence : les deux bornes
      // du même énoncé. L'animation suit donc exactement ce que le VAD entend.
      if (e.parle) { paroleDetectee = true; majMicro(); }
      else if (e.actif) { paroleDetectee = false; majMicro(); }
      if (e.actif) lancerCompte(e.min, e.max, e.fin); else arreterCompte();
      return;
    }
    if (e.genre === "quota") { quota = e.fenetres || []; majCompteurs(); return; }
    if (e.genre === "etat") {
      etatRecu = true; clearTimeout(attenteAgent);
      const el = document.getElementById("etat");
      el.textContent = ETATS[e.vers] || e.vers;
      // « vif » fait respirer la pastille tant que la session n'est pas au repos : un coup
      // d'oeil suffit alors pour savoir si le système est vivant.
      el.className = "e-" + e.vers + (e.vers === "listening" ? "" : " vif");
      // L'infobulle suit, sinon le motif de la DERNIERE coupure resterait affiche sur une
      // liaison redevenue saine — on lisait « coupure réseau » en survolant une pastille
      // parfaitement verte. Liaison bonne : elle dit ce que le serveur raconte de lui-meme.
      el.title = motDuServeur();
      if (e.vers !== "speaking") resoudre("voix", "ok");
      return;
    }
    ajouter(e);
}

// --- qui transcrit, en ce moment -------------------------------------------------------
// C'est la première question qu'on se pose quand une transcription est mauvaise, et la
// réponse n'était nulle part. La pastille dit le moteur actif ; la liste dit ce que chaque
// palier gratuit donne, sans quoi choisir revient à tirer au sort.
const pastilleMoteur = document.getElementById("moteur");
let inventaireMoteurs = [], chaineMoteurs = [], moteurImpose = false;

function majMoteur(actif, replie) {
  if (!actif) { pastilleMoteur.className = ""; return; }
  pastilleMoteur.className = "montre" + (replie ? " replie" : "");
  pastilleMoteur.textContent = actif;
  // « sans direct » se lit d'un coup d'œil : c'est ce qui explique qu'aucun texte ne
  // s'écrive pendant qu'on parle.
  pastilleMoteur.textContent = actif + (sttDirect ? "" : " · sans direct");
  pastilleMoteur.title = (replie
    ? actif + " — repli : le moteur de tête ne répond plus"
    : actif + " — moteur de reconnaissance")
    + (sttDirect ? "" : " · ne transcrit qu'à la fin de la phrase, donc rien ne s'écrit "
                        + "pendant que tu parles et « retenir » n'a rien à relire")
    + " (clic pour choisir)";
}

// Mettre un moteur en tête sans jeter les autres : le reste garde son ordre derrière. Une
// fonction nommée plutôt qu'une ligne dans un gestionnaire de clic — c'est la seule vraie
// règle de cet écran, et elle mérite d'être vérifiable.
let consoMoteurs = [];

// « 2 h 04 », « 37 min » — jamais « 7440 ». La meme regle que cote Python (consommation.duree),
// volontairement dupliquee : le tableau doit rester lisible meme sur un etat rejoue d'une
// version anterieure, sans dependre d'un champ pre-formate qui pourrait manquer.
function dureeCourte(s) {
  if (s == null) return "—";
  s = Math.floor(s);
  if (s >= 3600) return Math.floor(s / 3600) + " h " + String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  if (s >= 60) return Math.floor(s / 60) + " min";
  return s + " s";
}

// Ce que la pastille dit au survol : le moteur en tête et ce qu'il lui reste. Sans ouvrir le
// panneau, c'est la seule occasion de voir venir un quota qui se vide.
function majResteMoteur() {
  const tete = consoMoteurs.find(m => m.cle === (chaineMoteurs[0] || ""));
  if (!tete) return;
  const reste = tete.epuise ? "palier épuisé (constaté)"
    : tete.palier_s
      ? dureeCourte(tete.consomme_s) + " utilisées sur " + dureeCourte(tete.palier_s)
        + " · il reste ~" + dureeCourte(tete.reste_s)
      : tete.gratuit;
  pastilleMoteur.title = tete.libelle + " — " + reste
    + "\nMesuré micro ouvert, réactualisé toutes les 20 s pendant que tu parles."
    + "\nEstimation locale. Cliquer pour tout voir.";
}

function ordreAvecTete(tete) {
  if (!tete) return "";
  const suite = chaineMoteurs.filter(c => c !== tete);
  return [tete, ...suite].join(",");
}

// Un panneau ancré à droite de son bouton sort par la gauche quand le bouton s'est déplacé —
// et il se déplace, parce que le titre de la conversation courante est généré par un modèle et
// que sa longueur pousse tout l'en-tête. Le CSS ne peut pas connaître la place disponible : on
// la mesure à l'ouverture. Mesuré : le panneau des moteurs sortait de 47 px en fenêtre de
// 860 px, donc une partie de la liste était inatteignable.
function placerPanneau(el) {
  if (!el || el.hidden) return;
  el.style.maxWidth = "";
  const r = el.getBoundingClientRect();
  const place = r.right - 12;                 // du bord gauche de l'écran au bord droit du panneau
  if (r.width > place) el.style.maxWidth = Math.max(240, place) + "px";
}

function dessinerChoix() {
  const b = document.getElementById("choix-moteur");
  const lignes = inventaireMoteurs.map(m => {
    const rang = m.rang == null ? "" : String(m.rang + 1);
    const etat = m.dispo
      ? (m.rang == null ? "hors chaîne" : "rang " + rang)
      : "pas de clé";
    const c = consoMoteurs.find(x => x.cle === m.cle) || {};
    // La jauge n'apparait que s'il y a un palier a jauger : une barre vide sur « illimite »
    // laisserait croire a un quota qui n'existe pas.
    const part = c.epuise ? 1 : (c.part || 0);
    const classe = c.epuise || part >= 1 ? " vide" : part >= 0.8 ? " tendu" : "";
    const jauge = c.palier_s
      ? `<div class="jauge${classe}"><i style="width:${Math.round(part * 100)}%"></i></div>` : "";
    // Le CONSOMMÉ en évidence, le restant en second. C'était l'inverse, et sur un palier de
    // 330 h une minute d'usage était invisible : on lisait « il reste 329 h 59 » après avoir
    // parlé, et on concluait à un compteur cassé. Le chiffre qu'on vient vérifier est ce qu'on
    // a dépensé, pas ce qui reste — surtout quand ce qui reste se compte en centaines d'heures.
    const vif = c.en_cours_s > 0 ? ` <span class="vif">· en cours</span>` : "";
    const reste = c.epuise
      ? `<span class="vide">épuisé</span><br>constaté`
      : c.palier_s
        ? `<b>${ech(dureeCourte(c.consomme_s))}</b>${vif}<br>`
          + `reste ${ech(dureeCourte(c.reste_s))}`
        : c.consomme_s ? `<b>${ech(dureeCourte(c.consomme_s))}</b>${vif}<br>utilisé` : `illimité`;
    return `<div class="m${m.dispo ? "" : " absent"}">`
      + `<span class="rang">${ech(rang)}</span>`
      + `<span><span class="nom">${ech(m.libelle)}</span> — `
      + `<span class="quoi">${ech(m.gratuit)}`
      + (m.streaming ? "" : " · sans streaming")
      + (m.note ? " · " + ech(m.note) : "") + `</span>`
      + (c.motif ? `<br><span class="quoi">refus : ${ech(String(c.motif).slice(0, 110))}</span>` : "")
      + jauge + `</span>`
      + `<span class="reste">${m.dispo ? reste : ech(etat)}</span></div>`;
  }).join("");
  const dispo = inventaireMoteurs.filter(m => m.dispo).map(m => m.cle);
  b.innerHTML = lignes + `<div class="pied">`
    + `<b>Ordre par défaut</b> : les quotas mensuels d'abord — ils reviennent, autant les `
    + `dépenser — puis les crédits uniques, puis le local, illimité mais lent.<br>`
    + `<b>Les restes sont une estimation locale</b> : on compte le temps micro ouvert sur `
    + `cette machine, depuis l'installation du compteur. Ce qui a été consommé ailleurs, ou `
    + `avant, n'y est pas. « épuisé — constaté » est en revanche un fait : le fournisseur a `
    + `refusé pour cause de quota.<br>`
    + (moteurImpose
        ? `<b>VOIX_STT est posé dans l'environnement</b> : il gagne sur tout choix fait ici.`
        : `<b>Choisir ici</b> vaut pour le prochain lancement : le moteur ne peut pas changer `
          + `en cours de session.`)
    + `<div class="tout-rien" style="margin-top:8px">`
    + `<button type="button" id="moteur-defaut">ordre par défaut</button>`
    + dispo.map(c => `<button type="button" data-tete="${ech(c)}">`
        + `${ech((inventaireMoteurs.find(m => m.cle === c) || {}).libelle)} en tête</button>`).join("")
    + `</div></div>`;

  b.querySelector("#moteur-defaut").onclick = () => envoyerCmd({ cmd: "moteur_stt", ordre: "" });
  for (const bouton of [...b.querySelectorAll("[data-tete]")]) {
    bouton.onclick = () => envoyerCmd({
      cmd: "moteur_stt", ordre: ordreAvecTete(bouton.getAttribute("data-tete")) });
  }
}

// --- les conversations de ce dossier ----------------------------------------------------
// Le défaut qu'on corrige : « vv » ouvrait une conversation neuve à chaque lancement, et la
// liste comptait les LANCEMENTS. Sur cette machine, 23 lignes pour 5 conversations réelles —
// d'où l'impression que les conversations se multiplient et qu'on perd le contexte. Ici une
// ligne = une conversation, et on la reconnaît à ce qu'on y a dit en dernier : entre quatre
// conversations sur le même projet, « 22:49 » et « 22:55 » ne distinguent rien.
let convs = [], convCourante = null, convDossier = "";
const btnConvs = document.getElementById("convs");

function ilYA(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "à l'instant";
  if (s < 5400) return "il y a " + Math.round(s / 60) + " min";
  if (s < 172800) return "il y a " + Math.round(s / 3600) + " h";
  return "il y a " + Math.round(s / 86400) + " j";
}

function majConvs() {
  const vraies = convs.filter(c => c.session_id && c.tours);
  const c = vraies.find(x => x.session_id === convCourante);
  // Le nom de la conversation courante d'abord, le compte ensuite : ce qu'on veut savoir d'un
  // coup d'oeil est « où suis-je », pas « combien y en a-t-il ».
  const nom = c ? (c.titre || c.projet || "sans nom") : "nouvelle conversation";
  btnConvs.innerHTML = `<span class="rond"></span><b>${ech(nom)}</b>`
    + (vraies.length > 1 ? ` <span>+${vraies.length - 1}</span>` : "");
  btnConvs.title = c
    ? `${c.titre ? c.titre + " · " : ""}${c.projet} — ${c.tours} tours`
      + `\nCliquer pour en reprendre une autre, ou en ouvrir une neuve.`
    : "conversation neuve — cliquer pour en reprendre une, ou en ouvrir une autre";
}

function dessinerConvs() {
  const b = document.getElementById("choix-conv");
  // Une conversation sans identifiant de session n'est pas reprenable : Claude Code n'en a
  // gardé aucune trace. L'afficher comme cliquable promettrait une reprise qui repartirait
  // de zéro — précisément le silence qu'on cherche à supprimer.
  const utiles = convs.filter(c => c.session_id && c.tours);
  const perdues = convs.length - utiles.length;

  // En HAUT, et toujours present — meme quand la liste est vide. C'est une action, pas une
  // entree de liste : la meler aux conversations existantes ferait cliquer dessus par erreur.
  let html = `<button type="button" class="c neuve" data-neuve="1">`
    + `<span class="haut"><span class="nom"><span class="plus">+</span> nouvelle conversation`
    + `</span></span>`
    + `<div class="dit">Repart d'un contexte vide, pour un autre sujet. `
    + `Les conversations en cours sont conservées et restent reprenables.</div></button>`;
  let section = null;
  for (const c of utiles) {
    const titre = c.ici ? "ce dossier" : (c.sous ? "sous-dossiers" : "ailleurs");
    if (titre !== section) { section = titre; html += `<div class="titre">${ech(titre)}</div>`; }
    const active = c.session_id === convCourante;
    const morte = c.etat === "en cours" && !active;   // tenue par un autre agent
    // La pastille dit l'état d'un coup d'œil, et son titre le dit en toutes lettres : une
    // icône qu'il faut deviner ne vaut pas mieux qu'une absence d'icône.
    const et = active ? ["vit", "celle que tu utilises en ce moment"]
      : morte ? ["prise", "ouverte dans une autre fenêtre — on ne peut pas y être à deux"]
      : c.etat === "interrompue" ? ["coupee", "interrompue — l'agent s'est arrêté sans se fermer"]
      : ["close", "fermée proprement, prête à reprendre"];
    html += `<button type="button" class="c${active ? " active" : ""}${morte ? " morte" : ""}" `
      + `data-sid="${ech(c.session_id)}"${morte ? " disabled" : ""} `
      + `title="${ech(et[1])}">`
      + `<span class="haut"><span class="nom">`
      + `<span class="etat ${et[0]}"></span>${ech(c.titre || c.projet || "?")}`
      + (active ? ` <span class="ici">· en cours</span>` : "")
      + (morte ? ` <span class="ici">· ouverte ailleurs</span>` : "")
      + `</span><span class="quand">${ech(ilYA(c.maj))}</span></span>`
      + `<div class="dit">${ech(c.apercu || "(rien n'y a encore été dit)")}</div>`
      + `<div class="meta">`
      + (c.titre ? `${ech(c.projet || "?")} · ` : "")
      + `${c.tours} tour${c.tours > 1 ? "s" : ""}`
      + (c.reprises > 1 ? `<span class="repr">↻ ${c.reprises}</span>` : "")
      + (c.sous ? ` · ./${ech(c.sous)}` : "") + `</div></button>`;
  }
  if (!utiles.length) {
    html += `<div class="pied">Aucune autre conversation dans ce dossier — celle-ci `
      + `apparaîtra ici dès son premier échange.</div>`;
    b.innerHTML = html;
    brancherChoixConv(b);
    return;
  }
  html += `<div class="pied">`
    + `Reprendre garde <b>tout le contexte</b> : Claude Code relit sa session sur disque, `
    + `ce n'est pas un résumé.<br>`
    + `<b>↻</b> = nombre de fois où la conversation a été reprise. Une conversation reste `
    + `<b>une seule</b> conversation, même relancée dix fois.<br>`
    + `« vv » reprend la dernière d'ici tout seul ; « vvneuf » en ouvre une neuve.`
    + (perdues ? `<br>${perdues} lancement(s) sans session enregistrée — rien à y reprendre.` : "")
    + `</div>`;
  b.innerHTML = html;
  brancherChoixConv(b);
}

// Les gestionnaires, en un seul endroit : le panneau se redessine dans deux branches (liste
// vide ou non) et brancher deux fois laissait la version vide sans aucun clic actif.
function brancherChoixConv(b) {
  for (const bouton of [...b.querySelectorAll("[data-sid]")]) {
    bouton.onclick = () => {
      envoyerCmd({ cmd: "reprendre", session_id: bouton.getAttribute("data-sid") });
      b.hidden = true;
    };
  }
  for (const bouton of [...b.querySelectorAll("[data-neuve]")]) {
    bouton.onclick = () => {
      envoyerCmd({ cmd: "nouvelle_conversation" });
      b.hidden = true;
    };
  }
}

btnConvs.onclick = ev => {
  ev.stopPropagation();
  const b = document.getElementById("choix-conv");
  b.hidden = !b.hidden;
  // Rafraîchi à l'ouverture plutôt qu'en continu : la liste ne bouge qu'entre deux
  // lancements, et relire l'index chaque seconde pour rien serait du travail pur.
  if (!b.hidden) { envoyerCmd({ cmd: "conversations" }); dessinerConvs(); placerPanneau(b); }
};
addEventListener("click", ev => {
  const b = document.getElementById("choix-conv");
  if (b && !b.hidden && !b.contains(ev.target) && ev.target !== btnConvs) b.hidden = true;
});

pastilleMoteur.onclick = ev => {
  ev.stopPropagation();
  const b = document.getElementById("choix-moteur");
  b.hidden = !b.hidden;
  if (!b.hidden) { dessinerChoix(); placerPanneau(b); }
};
addEventListener("click", ev => {
  const b = document.getElementById("choix-moteur");
  if (b && !b.hidden && !b.contains(ev.target) && ev.target !== pastilleMoteur) b.hidden = true;
});

// --- les conversations en parallèle -----------------------------------------------------
// Une seule écoute à la fois. Le reste continue de travailler : le micro est la seule
// ressource réellement exclusive, et couper le travail des autres n'aurait aucun sens.
const zonePupitre = document.getElementById("pupitre");
let monPid = null, lectureEnCours = null;

function majPupitre(e) {
  monPid = e.moi;
  const s = e.sessions || [];
  // Seul, il n'y a rien à arbitrer : afficher un sélecteur d'une entrée serait du bruit.
  if (s.length < 2) { zonePupitre.innerHTML = ""; return; }
  const moi = s.find(x => x.pid === monPid);
  const bouts = s.map(x => {
    const classes = ["sess", x.micro ? "ecoute" : "muette"];
    if (x.pid === monPid) classes.push("moi");
    if (x.parle) classes.push("parle");
    const detail = [x.chemin || "", x.micro ? "écoute" : "muette",
                    x.parle ? "lit une réponse" : ""].filter(Boolean).join(" — ");
    const nom = ech(x.projet);
    // Les autres sont des liens : leur tableau vit sur son propre port.
    return x.pid === monPid
      ? `<span class="${classes.join(" ")}" title="${ech(detail)}">${nom}</span>`
      : `<a class="${classes.join(" ")}" href="http://127.0.0.1:${x.port}/" target="_blank"`
        + ` title="${ech(detail)} — ouvrir son tableau">${nom}</a>`;
  });
  if (moi && !moi.micro) {
    bouts.push(`<button id="prendre-micro" type="button"`
      + ` title="couper l'écoute des autres et écouter ici">écouter ici</button>`);
  }
  zonePupitre.innerHTML = bouts.join("");
  const b = document.getElementById("prendre-micro");
  if (b) b.onclick = () => envoyerCmd({ cmd: "prendre_micro" });
}

// --- couper ou relire UNE réponse -------------------------------------------------------
// Accrochés à la ligne concernée : « couper la parole » ne dit pas laquelle, et relire la
// dernière n'est pas relire celle-ci.
const lignesVoix = new Map();   // id de réponse -> sa ligne dans le flux

function outillerParole(e) {
  const ligne = lignesVoix.get(e.id);
  if (!ligne || ligne.dejaOutille) return;
  ligne.dejaOutille = true;
  const corps = ligne.querySelector(".corps");
  if (!corps) return;
  const zone = document.createElement("span");
  zone.className = "lecture";

  const couper = document.createElement("button");
  couper.type = "button";
  couper.textContent = "couper";
  couper.title = "arrêter cette lecture";
  couper.onclick = () => envoyerCmd({ cmd: "couper_lecture" });

  const relire = document.createElement("button");
  relire.type = "button";
  relire.textContent = "relire";
  relire.title = "relire cette réponse — utile si la lecture a été coupée";
  relire.onclick = () => envoyerCmd({ cmd: "relire", id: e.id });

  zone.append(couper, relire);
  corps.appendChild(zone);
  ligne.zoneLecture = zone;
  majBoutonsLecture();
}

// « couper » n'a de sens que sur la lecture qui joue : ailleurs, il ne ferait rien.
function majBoutonsLecture() {
  // Le bouton de la barre suit le MEME etat que ceux du flux : une seule source, donc pas
  // moyen qu'ils se contredisent.
  const bc = document.getElementById("couper-lecture");
  if (bc) bc.hidden = !lectureEnCours;
  for (const [id, ligne] of lignesVoix) {
    const z = ligne.zoneLecture;
    if (!z) continue;
    const joue = lectureEnCours === id;
    z.children[0].style.display = joue ? "" : "none";
    z.children[1].classList.toggle("vif", !joue);
  }
}

const ETATS = { listening: "écoute", thinking: "réfléchit", speaking: "parle", initializing: "démarre" };

// --- l'etat de la liaison, et ce qu'on en dit --------------------------------------------
// Ce bloc remplace un compteur d'echecs et un delai fixe de 1,2 s. Ce que ca ratait, dans
// l'ordre ou on s'en apercoit :
//
//  1. Revenir sur l'onglet du telephone. Le systeme gele la page et tue la socket ; au retour
//     la page decouvrait la coupure, la comptait comme un ECHEC, et au troisieme affichait
//     « deconnecte » — alors que le reseau allait tres bien et qu'il suffisait de rebrancher.
//     Une coupure subie en arriere-plan ne compte donc plus, et le retour rebranche TOUT DE
//     SUITE au lieu d'attendre le prochain reveil de minuterie.
//  2. La socket zombie. Un reseau mobile qui bascule laisse une socket « ouverte » dont plus
//     rien ne sort : aucune fermeture, donc aucune reconnexion, donc une page morte qui a
//     l'air vivante. Le pouls du serveur donne la mesure qui manquait.
//  3. « deconnecte » tout court. Ni pourquoi, ni depuis quand, ni si quelque chose est tente.
//     L'etat porte maintenant la tentative en cours et le compte a rebours ; l'infobulle
//     porte le code de fermeture et ce que le serveur disait de lui-meme en dernier.
let echecs = 0;            // reconnexions ratees d'affilee — les vraies, pas les mises en veille
let dernierPouls = 0;      // Date.now() du dernier signe de vie du serveur
let serveur = null;        // le dernier _pouls recu, tel quel
let causeCoupure = "";     // ce qu'on sait de la derniere fermeture
let minuterieRebranche = null, minuterieCompte = null, prochaineTentative = 0;

// 0,8 s puis on s'ecarte, jusqu'a 20 s. Reessayer toutes les 1,2 s pendant qu'un portable est
// hors couverture ne reconnecte rien et vide la batterie ; l'ecart laisse aussi le temps a un
// agent qui redemarre de rouvrir son port.
const ATTENTES = [800, 1500, 3000, 5000, 8000, 12000, 20000];
const attenteRebranche = () => ATTENTES[Math.min(echecs, ATTENTES.length - 1)];

// Ce que le serveur a dit de lui-meme en dernier, en une ligne d'infobulle.
function motDuServeur() {
  if (!serveur) return "aucun signe du serveur pour l'instant";
  const bouts = [serveur.pret ? "agent prêt" : "agent pas encore branché"];
  if (serveur.travail) bouts.push("un tour est en cours");
  if (serveur.en_attente) bouts.push(`${serveur.en_attente} commande(s) en attente`);
  if (serveur.depuis != null) bouts.push(`en vie depuis ${Math.round(serveur.depuis)} s`);
  if (serveur.evenements != null) bouts.push(`${serveur.evenements} événements`);
  if (serveur.clients != null) bouts.push(`${serveur.clients} page(s) branchée(s)`);
  const vu = dernierPouls ? Math.round((Date.now() - dernierPouls) / 1000) : null;
  if (vu != null) bouts.push(`dernier signe il y a ${vu} s`);
  return bouts.join(" · ");
}

// L'affichage de la liaison quand elle n'est PAS etablie. Appelee a chaque seconde du compte
// a rebours : dire « reconnexion... » sans fin ne distingue pas une reprise d'une panne.
function direLiaison() {
  const el = document.getElementById("etat");
  if (!el) return;
  if (!navigator.onLine) {
    el.textContent = "hors ligne";
    el.className = "e-listening";
    champ.placeholder = "hors ligne — le message partira au retour du réseau";
    el.title = "l'appareil n'a pas de réseau. " + motDuServeur();
    return;
  }
  const reste = Math.max(0, Math.ceil((prochaineTentative - Date.now()) / 1000));
  // « deconnecte » reste reserve a l'echec repete : une coupure d'une seconde annoncee comme
  // une panne fait chercher un bug la ou il n'y a qu'un aller-retour.
  const perdu = echecs >= 4;
  el.textContent = perdu
    ? `déconnecté — essai ${echecs + 1}${reste ? ` dans ${reste} s` : "…"}`
    : (reste > 1 ? `reconnexion dans ${reste} s…` : "reconnexion…");
  el.className = "e-listening" + (perdu ? "" : " vif");
  champ.placeholder = perdu
    ? "déconnecté — l'agent tourne toujours ? le message part à la reconnexion"
    : "reconnexion…";
  el.title = (causeCoupure ? causeCoupure + ". " : "") + motDuServeur();
}

function arreterRebranche() {
  if (minuterieRebranche) { clearTimeout(minuterieRebranche); minuterieRebranche = null; }
  if (minuterieCompte) { clearInterval(minuterieCompte); minuterieCompte = null; }
}

function programmerRebranche() {
  arreterRebranche();
  const delai = attenteRebranche();
  prochaineTentative = Date.now() + delai;
  reconnexionPrevue = true;
  direLiaison();
  // La seconde qui s'affiche EST la seconde qui s'ecoule : un compte a rebours fige ressemble
  // a une page plantee, ce qui est precisement le doute qu'on cherche a lever.
  minuterieCompte = setInterval(direLiaison, 1000);
  minuterieRebranche = setTimeout(() => {
    arreterRebranche();
    reconnexionPrevue = false;
    brancher();
  }, delai);
}

// Rebrancher SANS attendre, et sans compter ca comme un echec : on revient sur la page, le
// reseau revient, l'appareil se reveille. Dans ces trois cas l'attente n'apporte rien —
// c'est meme exactement le moment ou l'utilisateur regarde l'ecran.
// `force` court-circuite le garde-fou « la socket est ouverte, donc tout va bien » : c'est
// precisement cette croyance qui laissait une page morte se croire vivante.
function rebrancherMaintenant(pourquoi, force) {
  // Liaison saine : rien a faire. Tentative deja en vol (CONNECTING) : la laisser aboutir —
  // sans ce second cas, trois clics pendant une coupure ouvraient et refermaient trois
  // sockets d'affilee en remettant le compte a rebours a zero a chaque fois, ce qui martele
  // un serveur qui est justement en train de redemarrer.
  if (socket && socket.readyState === 0 && !force) return;
  if (socket && socket.readyState === 1 && !perimee() && !force) return;
  causeCoupure = pourquoi;
  echecs = 0;
  arreterRebranche();
  // Pas de compte a rebours : la tentative part maintenant. Sans cette remise a zero,
  // l'echeance d'un report precedent serait encore affichee, et on lirait « reconnexion
  // dans 12 s » sur une tentative deja en cours.
  prochaineTentative = 0;
  reconnexionPrevue = false;
  direLiaison();
  brancher();
}

// Une socket « ouverte » dont plus rien ne sort. Le serveur envoie un pouls toutes les 25 s :
// passe 70 s sans rien, la liaison est morte meme si le navigateur ne l'a pas encore admis.
function perimee() {
  return dernierPouls > 0 && Date.now() - dernierPouls > 70000;
}

// Poser la question au serveur au lieu de croire le navigateur.
//
// Le defaut, exactement : on envoie un message, on verrouille son telephone, on revient. La
// socket a ete tuee par le systeme, mais `readyState` vaut toujours OPEN — iOS ne previent
// pas. La page se croit donc branchee, `send()` ne leve rien, le message part dans le vide,
// et il faut quitter la conversation et y revenir pour que quoi que ce soit reparte. Regarder
// `readyState` ne pouvait pas trouver ca : il ment. Une reponse, elle, ne ment pas.
let sonde = null;
// 2,5 s : un aller-retour local en prend trois centiemes, et meme un reseau mobile mediocre
// reste tres en dessous. Au-dela, ce n'est pas de la lenteur, c'est une socket morte.
let DELAI_SONDE = 2500;
function sonder(pourquoi) {
  // Deja fermee, ou muette depuis plus longtemps que le pouls du serveur ne l'autorise : pas
  // la peine de demander, on sait. Rebrancher tout de suite vaut mieux que 2,5 s d'attente
  // supplementaire au moment precis ou quelqu'un revient regarder son ecran.
  if (!socket || socket.readyState !== 1 || perimee()) {
    rebrancherMaintenant(pourquoi, true);
    return;
  }
  const avant = dernierPouls;
  try { socket.send(JSON.stringify({ cmd: "ping" })); }
  catch (_) { rebrancherMaintenant(pourquoi, true); return; }
  clearTimeout(sonde);
  sonde = setTimeout(() => {
    if (dernierPouls === avant) {
      rebrancherMaintenant(pourquoi + " — le serveur n'a pas répondu à la vérification", true);
    }
  }, DELAI_SONDE);
}

if (typeof addEventListener === "function") {
  // Le retour sur la page. C'est LE cas du telephone : l'onglet a ete gele, la socket coupee
  // en silence, et sans ce gestionnaire la page attendait le prochain reveil de minuterie —
  // qui, gelee elle aussi, pouvait ne jamais arriver. On ne se contente pas de regarder si
  // elle est fermee : on VERIFIE qu'elle repond encore.
  addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    sonder("retour sur la page après une mise en veille");
  });
  // Restauration depuis le cache arriere/avant : la page revient telle quelle, socket morte
  // comprise, et aucun evenement de fermeture n'a ete delivre.
  addEventListener("pageshow", e => {
    if (e && e.persisted) rebrancherMaintenant("page restaurée depuis le cache du navigateur");
  });
  addEventListener("online", () => rebrancherMaintenant("le réseau est revenu"));
  addEventListener("offline", () => { causeCoupure = "réseau perdu"; direLiaison(); });
}

function brancher() {
  // Fermer l'ancienne avant d'ouvrir : deux sockets vivantes recevaient les mêmes
  // événements, et la page les affichait deux fois.
  if (socket && socket.readyState <= 1) {
    try { socket.onclose = null; socket.close(); } catch (_) {}
  }
  // L'URL se construit a partir du chemin de la page, pas en absolu : servie derriere un
  // proxy (« /talk/ »), un « /flux » absolu viserait le mauvais serveur. Et wss:// suit
  // automatiquement si la page est servie en https.
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://")
    + location.host + (location.pathname || "").replace(/\/$/, "") + "/flux");
  socket = ws;
  __diagSocket(ws);
  // Le bouton suit l'état réel de la liaison : proposer « envoyer » sur une socket morte
  // ferait disparaître le message sans rien dire.
  ws.onopen = () => {
    echecs = 0;
    causeCoupure = "";
    dernierPouls = Date.now();
    arreterRebranche();
    reconnexionPrevue = false;
    // La verite du travail en cours revient avec l'etat rejoue, juste apres. En attendant on
    // leve le gel : si l'agent ne travaille plus, l'etat rejoue eteindra la pastille ; s'il
    // travaille, le compteur repart d'un chiffre juste.
    liaisonPerdue = false;
    if (debutTravail != null && !minuteur) minuteur = setInterval(majTravail, 1000);
    majTravail();
    // Le serveur ne publie l'état qu'au premier changement d'état de l'agent :
    // avant tout échange, la pastille restait sur « connexion… » alors que la
    // liaison était ouverte — le témoin mentait. Dès l'ouverture, on affiche « prêt » ;
    // le premier état réel le remplacera.
    // La liaison est ouverte, mais l'agent n'est peut-être pas encore là : son serveur
    // écoute plusieurs secondes avant que la session soit prête. Dire « prêt » ici était
    // un mensonge — on l'a cru, et le premier message partait dans le vide. L'état réel
    // arrive en rejeu dès qu'il existe ; d'ici là, on dit ce qu'on sait.
    const et = document.getElementById("etat");
    if (et) et.title = motDuServeur();
    if (et && !etatRecu) {
      et.textContent = "l'agent démarre…"; et.className = "e-initializing vif";
      clearTimeout(attenteAgent);
      attenteAgent = setTimeout(() => {
        if (!etatRecu) {
          et.textContent = "l'agent ne répond pas"; et.className = "e-listening";
          noteBarre("l'agent n'a pas donné signe de vie en 30 s — voir son journal", true);
        }
      }, 30000);
    }
    majEnvoyer();
    champ.placeholder = "écrire au lieu de parler — touche /";
    // Les clics faits pendant la coupure partent maintenant, dans l'ordre.
    viderFile();
  };
  ws.onmessage = m => {
    const d = JSON.parse(m.data);
    // Tout ce qui arrive est un signe de vie, pouls ou pas : c'est cette date qui distingue
    // une liaison silencieuse parce que rien ne se passe d'une liaison silencieuse parce
    // qu'elle est morte.
    dernierPouls = Date.now();
    if (d.genre === "_pouls") { serveur = d; return; }
    if (d.genre === "_bonjour") {
      serveur = d;
      // Le rejeu RALLUME les indicateurs du passe, et c'est la source la plus visible de
      // « ça tourne alors que rien ne tourne ». L'historique renvoyé contient les lignes
      // telles qu'elles ont été publiées : un vieux « voix » y rallume la pastille parole,
      // et rien derrière ne l'éteint — l'état de l'agent, lui, est envoyé AVANT l'historique,
      // donc il est déjà passé quand la ligne le rallume. Résultat : une session au repos
      // affichait « parole » et faisait défiler les mots du bandeau, après CHAQUE
      // reconnexion — donc souvent, sur un téléphone.
      //
      // Ici on sait ce que le serveur sait : s'il ne travaille pas et ne parle pas, rien ne
      // peut être en cours, quoi qu'ait rallumé le rejeu. S'il travaille ou parle vraiment,
      // on ne touche à rien : l'indicateur dit alors la vérité.
      if (!d.travail && d.etat !== "speaking" && d.etat !== "thinking") {
        toutClore("rien n'était en cours à la reconnexion");
        // Le bouton « couper la lecture » pulse tant qu'une lecture est declaree en cours, et
        // sa seule extinction est l'evenement de fin. Perdre la liaison PENDANT une lecture
        // le laissait donc allume, a clignoter, sur une page ou plus personne ne parle — avec
        // un bouton qui ne couperait rien si on le pressait.
        if (lectureEnCours) { lectureEnCours = null; majBoutonsLecture(); }
        if (enAttenteLecture.length) { enAttenteLecture = []; majEnAttente(); }
      }
      // Le bonjour arrive APRES tout le rejeu. Donc a cet instant precis, un message encore
      // « en vol » n'a pas seulement perdu son accuse : il n'est jamais arrive — s'il etait
      // passe, son echo serait dans l'historique qu'on vient de rejouer, et l'aurait retire.
      // C'est le seul moment ou on peut le renvoyer sans risquer de le poster deux fois, et
      // sans lui, le message tape juste avant une mise en veille etait simplement perdu.
      const perdus = renvoyerEnVol();
      if (perdus) noteBarre(`rebranché — ${perdus} message${perdus > 1 ? "s" : ""} renvoyé${perdus > 1 ? "s" : ""}`, true);
      // Ce qu'on vient de retrouver, dit une fois. Le silence d'apres-reconnexion laissait
      // croire que la page repartait de zero alors qu'elle rejouait cent lignes.
      else if (d.rejoue) {
        noteBarre(d.travail
          ? `rebranché — ${d.rejoue} lignes retrouvées, un tour est en cours`
          : `rebranché — ${d.rejoue} lignes retrouvées`);
      }
      return;
    }
    // L'état arrive dans une enveloppe pour être distingué du flux, mais il traverse le même
    // routage : un second chemin aurait fini par diverger.
    if (d.genre === "_etat") { recevoir(d.evenement, true); return; }
    recevoir(d);
  };
  ws.onclose = (ev) => {
    // Une socket périmée qui se referme après qu'une nouvelle est en place ne doit ni
    // relancer un branchement, ni faire clignoter l'état.
    if (socket !== ws) return;
    majEnvoyer();
    arreterCompte();
    toutClore("connexion perdue");
    // Le compteur du tour s'arrete ici : il mesurait depuis l'horloge locale, donc il
    // continuait de monter meme sur un agent mort. Le minuteur aussi — une page au repos
    // n'a rien a faire tourner.
    liaisonPerdue = true;
    if (minuteur) { clearInterval(minuteur); minuteur = null; }
    majTravail();
    // La page était-elle en arrière-plan ? Alors ce n'est pas une panne, c'est le système qui
    // a rangé l'onglet. Le compter comme un échec faisait afficher « déconnecté » au retour,
    // avec un délai de reprise de plus en plus long, pour un réseau parfaitement sain.
    const enVeille = document.visibilityState === "hidden";
    const code = ev && ev.code ? ev.code : 0;
    causeCoupure = enVeille
      ? "liaison fermée pendant que la page était en arrière-plan"
      : ((ev && ev.reason) || ({
          1001: "le serveur ou l'onglet s'est retiré",
          1005: "fermée sans motif",
          1006: "coupure réseau — aucune fermeture propre",
          1011: "erreur interne du serveur",
          1012: "le serveur redémarre",
        }[code] || `fermée (code ${code || "?"})`));
    if (!enVeille) echecs++;
    if (enVeille) {
      // Inutile de rebrancher maintenant : les minuteries d'un onglet caché sont bridées, et
      // le retour sur la page le fera immédiatement. On garde juste l'état juste.
      reconnexionPrevue = true;
      direLiaison();
      return;
    }
    programmerRebranche();
  };
}
// Sur téléphone, l'en-tête complet prend un quart de l'écran. Dès qu'on lit la conversation,
// il se replie à ses deux premières lignes (titre, état, micro, arrêter, modèle) et redevient
// entier en haut de page. Hystérésis : replier retire ~110 px au document, et sur une page
// courte le défilement retomberait sous le seuil — l'en-tête battrait. On ne replie donc
// qu'avec de la marge, et on ne déplie qu'au sommet. La feuille de bureau ignore la classe.
const __entete = document.querySelector("header");
let __replie = false;
function __plierEntete() {
  const marge = document.documentElement.scrollHeight - innerHeight;
  if (!__replie && scrollY > 160 && marge > 320) __replie = true;
  else if (__replie && scrollY < 8) __replie = false;
  else return;
  __entete.classList.toggle("compact", __replie);
}
if (__entete && typeof addEventListener === "function") {
  addEventListener("scroll", __plierEntete, { passive: true });
}

brancher();

addEventListener("scroll", () => {
  suivre = window.innerHeight + window.scrollY >= document.body.scrollHeight - 60;
  document.getElementById("bas").style.display = suivre ? "none" : "block";
});
document.getElementById("bas").onclick = () => {
  suivre = true; window.scrollTo(0, document.body.scrollHeight);
};
</script></body></html>
"""

# Le lien de retour n'existe que si quelqu'un a dit ou retourner. Lancee a la main dans un
# terminal, la page n'a pas de « liste des sessions » ou revenir ; servie par le tableau de
# bord de la maison, elle en a une, et c'est lui qui la nomme.
_RETOUR = os.environ.get("VOIX_UI_RETOUR", "").strip()
if _RETOUR:
    _ECHAPPE = _RETOUR.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    PAGE = PAGE.replace("<!--RETOUR-->",
                        f'<a id="retour" href="{_ECHAPPE}" '
                        f'title="revenir a la liste des sessions">&#8592;</a>')
