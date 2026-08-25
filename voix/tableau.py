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
import time
import webbrowser
from collections import deque
from datetime import datetime

from aiohttp import WSMsgType, web

log = logging.getLogger("voix.tableau")

# Assez pour contenir un historique rejoue (jusqu'a 400 lignes) SANS chasser la session en
# cours : une page ouverte en cours de route doit montrer les deux.
MEMOIRE = 1600


class Tableau:
    def __init__(self, port: int = 7788, ouvrir: bool = True, on_commande=None):
        self.port = port
        self.ouvrir = ouvrir
        # Awaited coroutine (nom, donnees). The page is a control surface, not just a log:
        # cutting the microphone from there is the fastest stop button available, which
        # matters more now that nothing asks permission.
        self.on_commande = on_commande
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

    # --- publication ---------------------------------------------------------
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
        if not self.clients:
            return
        charge = json.dumps(evenement, ensure_ascii=False, default=str)
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
    async def _page(self, _req):
        return web.Response(text=PAGE, content_type="text/html")

    async def _ecrivain(self, ws, file: asyncio.Queue):
        while True:
            charge = await file.get()
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
            await ws.send_str(json.dumps(
                {"genre": "_histoire", "evenements": list(self.histoire)},
                ensure_ascii=False, default=str,
            ))
            async for message in ws:
                if message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                if message.type is WSMsgType.TEXT and self.on_commande:
                    try:
                        ordre = json.loads(message.data)
                    except (ValueError, TypeError):
                        continue
                    nom = ordre.pop("cmd", None)
                    if nom:
                        # Chaque commande est isolee. Sans ce garde-fou, UNE exception dans
                        # UNE commande sortait de cette boucle, passait par le finally qui
                        # retire le client, et tuait la WebSocket : la page affichait
                        # « deconnecte », la commande n'avait pas eu lieu, et il fallait
                        # recliquer une fois la reconnexion faite. Reproduit, puis corrige.
                        try:
                            await self.on_commande(nom, ordre)
                        except Exception:
                            log.exception("commande « %s » en echec", nom)
                            self.publier("erreur",
                                         texte=f"la commande « {nom} » a echoue — "
                                               f"details dans les logs")
        finally:
            ecrivain.cancel()
            self.clients.pop(ws, None)
        return ws

    async def demarrer(self):
        app = web.Application()
        app.router.add_get("/", self._page)
        app.router.add_get("/flux", self._flux)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        # Loopback only: this stream carries the content of your code.
        # Un agent laisse tourne garde le port : on le dit et on prend le suivant, plutot
        # que de faire echouer toute la session sur un « address already in use ».
        demande = self.port
        for essai in range(demande, demande + 10):
            try:
                await web.TCPSite(self._runner, "127.0.0.1", essai).start()
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
<title>claude-talk</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2032%2032%27%3E%3Cpath%20d%3D%27M15.13%2011.89Q15.38%207.50%2016.00%202.20Q16.62%207.50%2016.87%2011.89Z%20M17.30%2012.01Q19.12%209.54%2021.40%206.65Q20.03%2010.07%2018.81%2012.88Z%20M19.12%2013.19Q23.05%2011.21%2027.95%209.10Q23.67%2012.29%2019.99%2014.70Z%20M20.11%2015.13Q23.15%2015.47%2026.80%2016.00Q23.15%2016.53%2020.11%2016.87Z%20M19.99%2017.30Q23.67%2019.71%2027.95%2022.90Q23.05%2020.79%2019.12%2018.81Z%20M18.81%2019.12Q20.03%2021.93%2021.40%2025.35Q19.12%2022.46%2017.30%2019.99Z%20M16.87%2020.11Q16.62%2024.50%2016.00%2029.80Q15.38%2024.50%2015.13%2020.11Z%20M14.70%2019.99Q12.88%2022.46%2010.60%2025.35Q11.97%2021.93%2013.19%2019.12Z%20M12.88%2018.81Q8.95%2020.79%204.05%2022.90Q8.33%2019.71%2012.01%2017.30Z%20M11.89%2016.87Q8.85%2016.53%205.20%2016.00Q8.85%2015.47%2011.89%2015.13Z%20M12.01%2014.70Q8.33%2012.29%204.05%209.10Q8.95%2011.21%2012.88%2013.19Z%20M13.19%2012.88Q11.97%2010.07%2010.60%206.65Q12.88%209.54%2014.70%2012.01Z%27%20fill%3D%27%2358a6ff%27%2F%3E%3C%2Fsvg%3E">
<style>
:root{
  --fond:#0e1116; --carte:#161b22; --bord:#272e37; --texte:#d7dde5; --faible:#8b949e;
  --toi:#58a6ff; --voix:#3fb950; --pensee:#a371f7; --outil:#d29922; --erreur:#f85149;
  --permission:#ff7b72; --tour:#39c5cf;
}
*{box-sizing:border-box}
body{margin:0;background:var(--fond);color:var(--texte);
  font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:#0e1116ee;backdrop-filter:blur(8px);
  border-bottom:1px solid var(--bord);padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
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
#cogitation{position:absolute;bottom:100%;left:16px;margin-bottom:8px;
  display:flex;gap:8px;align-items:center;background:var(--carte);
  border:1px solid var(--bord);border-radius:999px;padding:5px 13px 5px 10px;
  font-size:12.5px;color:var(--pensee)}
#cogitation[hidden]{display:none}
#cogitation .marque{color:var(--pensee)}
#mot{font-weight:600;letter-spacing:.01em}
.points i{font-style:normal;animation:clignote 1.4s ease-in-out infinite}
.points i:nth-child(2){animation-delay:.18s}
.points i:nth-child(3){animation-delay:.36s}
@keyframes clignote{0%,100%{opacity:.18}45%{opacity:1}}
#etat{font-weight:600;padding:2px 10px;border-radius:999px;border:1px solid var(--bord);font-size:12px}
.e-listening{color:var(--toi);border-color:var(--toi)}
.e-thinking{color:var(--pensee);border-color:var(--pensee)}
.e-speaking{color:var(--voix);border-color:var(--voix)}
#compteurs{margin-left:auto;display:flex;gap:8px;align-items:center;
  color:var(--faible);font-size:12px;font-variant-numeric:tabular-nums}
.q{border:1px solid var(--bord);border-radius:999px;padding:2px 9px;white-space:nowrap}
.q b{font-weight:650;color:var(--texte)}
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
#filtres{display:flex;gap:6px;flex-wrap:wrap}
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
main{padding:14px 16px 118px;max-width:1100px;margin:0 auto}

/* Écrire au lieu de parler. Utile quand le micro est coupé, quand le mot est trop
   technique pour être dicté proprement, ou quand quelqu'un dort à côté. */
#saisie-barre{position:fixed;bottom:0;left:0;right:0;z-index:6;
  background:#0e1116f2;backdrop-filter:blur(8px);border-top:1px solid var(--bord);
  padding:10px 16px;display:flex;justify-content:center}
/* align-items:flex-end : quand le champ grandit, les boutons restent alignes sur sa
   derniere ligne au lieu de flotter au milieu d'une grande boite. */
#saisie-barre form{display:flex;gap:8px;width:100%;max-width:1100px;align-items:flex-end}

/* Un textarea, pas un input : une longue dictee doit rester ENTIEREMENT visible. Sur une
   seule ligne, le texte defilait hors du champ et on ne voyait plus ce qu'on dictait.
   La hauteur est calculee en JS (scrollHeight) ; le CSS ne fait que l'animer. */
#saisie{flex:1;min-width:0;background:var(--carte);color:var(--texte);
  border:1px solid var(--bord);border-radius:19px;padding:8px 15px;font:inherit;
  font-size:13px;line-height:1.5;outline:none;resize:none;overflow-y:auto;
  display:block;height:36px;max-height:40vh;
  transition:height .12s ease-out,border-radius .12s ease-out,border-color .12s}
/* Une seule ligne : la pastille arrondie du reste de l'interface. Plusieurs lignes : un coin
   plus sobre, sinon la boite ressemble a une gelule etiree. */
#saisie.une-ligne{border-radius:999px}
#saisie:focus{border-color:var(--toi)}
#saisie::placeholder{color:#5a636e}
/* Au bout de combien de silence le message part. À côté de la barre, parce que c'est un
   réglage de la dictée, pas de la session. */
#delai{background:var(--carte);color:var(--texte);border:1px solid var(--bord);
  border-radius:999px;padding:8px 10px;font:inherit;font-size:12.5px;cursor:pointer}
#delai:hover{border-color:#4b5563}

/* Une explication accessible sans manger le bouton. Le « i » est un élément SÉPARÉ : mis
   dans le bouton, il aurait volé les clics destinés à la bascule, et « retenir » est fait
   pour être basculé, pas pour être lu. */
.avec-info{position:relative;display:inline-flex;align-items:center;gap:4px}
.info{width:17px;height:17px;flex:none;border-radius:50%;border:1px solid var(--bord);
  background:transparent;color:var(--faible);font:inherit;font-size:10.5px;font-weight:700;
  font-style:italic;line-height:1;cursor:help;padding:0;display:inline-flex;
  align-items:center;justify-content:center}
.info:hover,.info.ouvert{border-color:var(--toi);color:#9ecbff}
.bulle{position:absolute;bottom:calc(100% + 9px);right:0;z-index:30;width:290px;
  background:var(--carte);border:1px solid var(--bord);border-radius:10px;
  padding:11px 13px;font-size:11.5px;line-height:1.5;color:var(--faible);
  box-shadow:0 12px 32px #00000080}
.bulle[hidden]{display:none}
.bulle b{color:var(--texte)}
.bulle .astuce{color:#8a949f;border-top:1px solid var(--bord);display:block;
  padding-top:8px;margin-top:2px}

/* Le bascule dictée : envoyer tout seul, ou garder dans la barre pour corriger. */
#retenir{background:transparent;border:1px solid var(--bord);border-radius:999px;
  padding:8px 14px;font:inherit;font-size:12.5px;cursor:pointer;color:var(--faible);
  white-space:nowrap;display:inline-flex;gap:7px;align-items:center}
#retenir:hover{border-color:#4b5563;color:var(--texte)}
#retenir.on{border-color:var(--toi);color:#9ecbff;background:#132133}
#retenir::before{content:"";width:8px;height:8px;border-radius:50%;
  background:currentColor;opacity:.35}
#retenir.on::before{opacity:1}
/* Pendant qu'on dicte, la barre montre que ce texte n'est pas encore le tien. */
#saisie.dictee{border-color:var(--toi);color:#9ecbff;font-style:italic}
#envoyer{background:transparent;color:var(--faible);border:1px solid var(--bord);
  border-radius:999px;padding:8px 16px;font:inherit;font-size:13px;cursor:pointer;
  white-space:nowrap}
#envoyer:not(:disabled):hover{border-color:var(--toi);color:#9ecbff}
#envoyer:disabled{opacity:.3;cursor:default}
.g-toi .tape{color:var(--faible);font-size:11px;margin-right:5px}
.ev{display:grid;grid-template-columns:62px 92px 14px 1fr;gap:12px;padding:7px 0;
  border-bottom:1px solid #1c2129;align-items:baseline}
.t{color:#5a636e;font-size:11px;font-variant-numeric:tabular-nums;text-align:right}
.badge{font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.04em}
.corps{min-width:0;overflow-wrap:anywhere;white-space:pre-wrap}
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
/* L'écart de fenêtre arrive après la ligne du tour : on le distingue pour qu'on voie qu'il
   s'agit d'une mesure rapportée, pas d'un chiffre connu au moment du bilan. */
.fenetre{color:#e3b341;margin-left:2px}
.fenetre.nul{color:#7d8590}
.g-erreur .badge{color:var(--erreur)} .g-erreur .corps{color:#ffb3ad}
/* Le passe rejoue est lisible mais visiblement passe : sans ca, relire soixante tours
   d'historique donne l'impression que tout vient de se produire. */
.ev.passe{opacity:.55}
.ev.passe .pip{display:none}
.g-reprise{border-bottom:1px solid var(--tour)}
.g-reprise .badge{color:var(--tour)}
.g-reprise .corps{color:#9fe6ec;font-size:12px;letter-spacing:.02em}
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
#etat.vif{animation:pulse 1.4s ease-in-out infinite}

#bas{position:fixed;bottom:62px;right:16px;background:var(--carte);border:1px solid var(--bord);
  color:var(--faible);border-radius:999px;padding:6px 14px;font-size:12px;cursor:pointer;display:none;font-family:inherit}
</style></head><body>
<header>
  <h1>
    <svg class="marque" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M15.13 11.89Q15.38 7.50 16.00 2.20Q16.62 7.50 16.87 11.89Z M17.30 12.01Q19.12 9.54 21.40 6.65Q20.03 10.07 18.81 12.88Z M19.12 13.19Q23.05 11.21 27.95 9.10Q23.67 12.29 19.99 14.70Z M20.11 15.13Q23.15 15.47 26.80 16.00Q23.15 16.53 20.11 16.87Z M19.99 17.30Q23.67 19.71 27.95 22.90Q23.05 20.79 19.12 18.81Z M18.81 19.12Q20.03 21.93 21.40 25.35Q19.12 22.46 17.30 19.99Z M16.87 20.11Q16.62 24.50 16.00 29.80Q15.38 24.50 15.13 20.11Z M14.70 19.99Q12.88 22.46 10.60 25.35Q11.97 21.93 13.19 19.12Z M12.88 18.81Q8.95 20.79 4.05 22.90Q8.33 19.71 12.01 17.30Z M11.89 16.87Q8.85 16.53 5.20 16.00Q8.85 15.47 11.89 15.13Z M12.01 14.70Q8.33 12.29 4.05 9.10Q8.95 11.21 12.88 13.19Z M13.19 12.88Q11.97 10.07 10.60 6.65Q12.88 9.54 14.70 12.01Z"/>
    </svg>
    claude-talk
  </h1>
  <span id="etat" class="e-listening">connexion…</span>
  <span id="alerte-entete" class="alerte" style="display:none"></span>
  <button id="micro" title="couper le micro (touche m)">🎤 micro</button>
  <button id="arreter" title="arrêter le travail en cours (touche s)" disabled>⏹ arrêter</button>
  <select id="modele" title="modèle utilisé pour le travail"></select>
  <select id="effort" title="niveau d'effort de réflexion"></select>
  <span id="compte" title="temps avant envoi automatique">
    <span id="reste"></span>
    <button id="retenir-vite" type="button"
            title="garder CE message dans la barre au lieu de l'envoyer">retenir</button>
    <button id="envoi-vite" type="button" title="envoyer tout de suite (Entrée)">envoyer</button>
  </span>
  <span id="travail"></span>
  <div id="activite"></div>
  <div id="filtres"></div>
  <span id="compteurs"></span>
</header>
<main id="flux"></main>
<button id="bas">↓ suivre</button>
<div id="saisie-barre">
  <div id="cogitation" hidden>
    <span class="spin"></span>
    <span id="mot"></span><span class="points"><i>.</i><i>.</i><i>.</i></span>
  </div>
  <form id="composer" autocomplete="off">
    <textarea id="saisie" rows="1" class="une-ligne"
              placeholder="écrire au lieu de parler — touche /"
              aria-label="message à envoyer" maxlength="4000"></textarea>
    <select id="delai" title="au bout de combien de silence le message part"></select>
    <span class="avec-info">
      <button id="retenir" type="button"
              title="retenir la dictée dans la barre au lieu de l'envoyer (touche r)">retenir</button>
      <button id="retenir-info" class="info" type="button"
              aria-label="à quoi sert « retenir »">i</button>
      <div id="retenir-aide" class="bulle" hidden>
        <b>Retenir</b> — ce que tu dictes se dépose dans la barre au lieu de partir chez
        Claude. Tu relis, tu corriges, tu envoies quand tu veux.
        <br><br>
        Utile quand la transcription se trompe sur un mot technique, ou quand tu penses à
        voix haute avant de savoir ce que tu veux demander.
        <br><br>
        Les ordres immédiats — « coupe le micro », « stop » — continuent de passer : ce
        ne sont pas des messages à relire.
        <br><br>
        <span class="astuce">Le décompte a son propre bouton « retenir » : il rattrape un
        seul message, sans changer ce réglage.</span>
      </div>
    </span>
    <button id="envoyer" type="submit" disabled>envoyer</button>
  </form>
</div>
<script>
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
    { g: "micro",      lib: "micro",       quoi: "ouverture et coupure du micro" },
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
function dejaVu(e) {
  if (typeof e.n !== "number") return false;   // événement d'une version sans numéro
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
function majCompteurs() {
  const pastilles = quota.map(f => {
    const c = f.pct >= 90 ? "chaud" : f.pct >= 70 ? "tiede" : "";
    const reinit = f.reset ? ` — réinit. ${ech(f.reset)}` : "";
    return `<span class="q ${c}" title="${ech(f.cle)}${reinit}">${ech(f.cle)} <b>${f.pct.toFixed(0)} %</b></span>`;
  }).join("");
  const gauche = `${actions} action${actions > 1 ? "s" : ""}`
    + (jetons ? ` · ${fmtJetons(jetons)} jetons` : "");
  document.getElementById("compteurs").innerHTML = `<span>${gauche}</span>${pastilles}`;
}

const ech = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

// La configuration n'est pas une ligne du flux : c'est l'en-tete de la session. Elle reste
// en haut, non filtrable, parce qu'en bypassPermissions c'est le seul endroit qui dit
// noir sur blanc que plus rien ne sera demande.
function carteConfig(e) {
  const champs = Object.entries(e.valeurs || {}).filter(([k]) => !k.startsWith("_"));
  const alerte = e.valeurs?._alerte
    ? `<span class="alerte">⚠ ${ech(e.valeurs._alerte)}</span>` : "";
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
      if (e.jetons != null) bouts.push(`${fmtJetons(e.jetons)} jetons`);
      if (e.texte) bouts.push(e.texte);
      return bouts.join(" · ") || "terminé";
    }
    case "log":
      return `<span style="opacity:.7">${ech(e.source)}</span> ${ech(e.texte)}`;
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
const GARDE = { stt: 4000, pensee: 30000 };
const echeances = {};
function battre(nom) {
  clearTimeout(echeances[nom]);
  echeances[nom] = setTimeout(() => resoudre(nom, "coupe", "sans suite"), GARDE[nom]);
}

const zoneActivite = document.getElementById("activite");
const LIB_ACT = { stt: "transcription", pensee: "réflexion", voix: "parole" };
function majActivite() {
  const outils = [...encours.keys()].filter(c => c.startsWith("outil:")).length;
  const bouts = [];
  for (const nom of ["stt", "pensee", "voix"]) {
    if (encours.has(nom)) bouts.push(`<span class="act ${nom}">${LIB_ACT[nom]}</span>`);
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
    ligne.className = `ev g-${e.genre}` + (e.passe ? " passe" : "");
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
  if (e.genre === "toi") { clearTimeout(echeances.stt); resoudre("stt", "ok"); }
  if (e.genre === "resultat" && e.id) {
    resoudre("outil:" + e.id, e.echec ? "echec" : "ok",
             e.echec ? "l'outil a renvoyé une erreur" : "");
  }
  if (e.genre === "pensee") battre("pensee");
  // La réflexion s'arrête dès qu'elle produit quelque chose : du texte, ou un outil.
  if (e.genre === "texte" || e.genre === "outil") {
    clearTimeout(echeances.pensee); resoudre("pensee", "ok");
  }
  if (e.genre === "tour") toutClore("tour terminé");
  if (e.genre === "arret") toutClore("interrompu");

  // Les tours rejoues ne captent pas la mesure de fenetre : elle appartient a un tour vif.
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

// Rebrancher tout de suite, sans doublonner les tentatives.
function reconnecter() {
  if (socket && socket.readyState <= 1) return;   // vivante ou en cours d'ouverture
  if (reconnexionPrevue) return;
  reconnexionPrevue = true;
  brancher();
}

// Une veille du portable ou un onglet longtemps en arriere-plan tue la socket sans que la
// page l'apprenne. On regarde donc au moment ou l'onglet redevient visible, plutot que de
// laisser le prochain clic faire la decouverte.
addEventListener("visibilitychange", () => {
  if (!document.hidden) reconnecter();
});
addEventListener("online", reconnecter);
const btnMicro = document.getElementById("micro");
function majMicro() {
  btnMicro.textContent = microActif ? "🎤 micro" : "🔇 micro coupé";
  btnMicro.className = microActif ? "" : "coupe";
  btnMicro.title = (microActif ? "couper" : "rouvrir") + " le micro (touche m)";
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

// Le travail en cours est montre, pas annonce. La narration parlee (« toujours dessus, je
// viens de lancer cat ») a ete retiree : on ne peut pas survoler du son, alors qu'un
// compteur dans l'en-tete se lit d'un coup d'oeil et ne coupe pas la parole.
// Le selecteur reflete l'etat du worker, il ne le devine pas : un changement vocal doit
// mettre la liste a jour, et une bascule temporaire doit se voir comme temporaire.
const selModele = document.getElementById("modele");
function remplirModeles(liste, actuel) {
  selModele.innerHTML = liste.map(m =>
    `<option value="${ech(m.cle)}">${ech(m.libelle)}</option>`).join("");
  if (actuel) selModele.value = actuel;
}
selModele.onchange = () => { envoyerCmd({ cmd: "modele", cle: selModele.value }); };

// Le niveau d'effort, à côté du modèle. Les cinq niveaux du SDK, pas plus : low, medium,
// high, xhigh, max. Contrairement au modèle, il ne peut pas changer en pleine tâche — il est
// figé à la construction de la session — donc le sélecteur se verrouille pendant le travail
// plutôt que d'accepter un clic qui n'aurait aucun effet.
const selEffort = document.getElementById("effort");
function remplirEfforts(liste, actuel) {
  selEffort.innerHTML = liste.map(e =>
    `<option value="${ech(e.cle)}">${ech(e.libelle)}</option>`).join("");
  if (actuel) selEffort.value = actuel;
}
selEffort.onchange = () => { envoyerCmd({ cmd: "effort", cle: selEffort.value }); };

const btnStop = document.getElementById("arreter");
const pilTravail = document.getElementById("travail");
let debutTravail = null, minuteur = null;
function majTravail() {
  if (debutTravail == null) {
    pilTravail.style.display = "none";
    btnStop.disabled = true;
    if (minuteur) { clearInterval(minuteur); minuteur = null; }
    selEffort.disabled = false;
    selEffort.title = "niveau d'effort de réflexion";
    majCogitation();
    return;
  }
  const s = Math.round((Date.now() - debutTravail) / 1000);
  pilTravail.textContent = `travaille ${s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s"}`;
  pilTravail.style.display = "";
  btnStop.disabled = false;
  selEffort.disabled = true;
  selEffort.title = "tâche en cours — l'effort ne peut changer qu'entre deux tâches";
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
  const occupe = debutTravail != null || encours.size > 0;
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

function lancerCompte(min, max) {
  finMin = Date.now() + min * 1000;
  finMax = Date.now() + max * 1000;
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

function majEnvoyer() {
  const pret = champ.value.trim().length > 0
    && socket && socket.readyState === WebSocket.OPEN;
  btnEnvoyer.disabled = !pret;
}
// --- la dictée s'écrit dans la barre ----------------------------------------------------
// Le principe qui évite tous les conflits : on retient le contenu du champ au DÉBUT de
// l'énoncé, et le texte reconnu s'ajoute à cette base. Champ vide, la dictée le remplit ;
// champ déjà rempli d'une correction, elle s'ajoute à la suite. Rien n'est jamais écrasé,
// parce qu'écraser une correction qu'on vient de taper serait la pire des trahisons.
let base = null;              // contenu du champ avant l'énoncé en cours
let retenir = false;

function poserDictee(texte, definitif) {
  if (base === null) base = champ.value ? champ.value.trimEnd() + " " : "";
  champ.value = base + texte;
  champ.classList.toggle("dictee", !definitif);
  majEnvoyer();
  // La dictée écrit sans passer par oninput : sans cet appel le champ ne grandirait que
  // quand on tape, c'est-à-dire jamais pendant qu'on parle.
  ajusterHauteur();
  if (definitif) { base = null; champ.classList.remove("dictee"); }
}

// Fin de tour en mode envoi direct : le texte est parti chez Claude, la barre se vide.
function viderDictee() {
  // trimEnd : la base porte l'espace de jonction ajouté pour la concaténation. Rendre un
  // brouillon doit rendre exactement le brouillon, pas le brouillon plus une espace.
  if (base !== null) { champ.value = base.trimEnd(); majEnvoyer(); }
  base = null;
  champ.classList.remove("dictee");
  ajusterHauteur();   // revenu vide, le champ reprend sa taille d'origine
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
  btnRetenir.className = retenir ? "on" : "";
  btnRetenir.textContent = retenir ? "retenu" : "retenir";
  btnRetenir.title = (retenir
    ? "la dictée reste dans la barre — tu relis, tu corriges, tu envoies"
    : "la dictée part dès que tu as fini de parler") + " (touche r)";
}
function basculerRetenir() {
  retenir = !retenir;
  majRetenir();
  envoyerCmd({ cmd: "retenir", actif: retenir });
}
btnRetenir.onclick = basculerRetenir;
majRetenir();

// L'explication de « retenir ». Au survol pour la lire d'un coup d'oeil, au clic pour
// qu'elle reste — sans jamais déclencher la bascule, qui est le rôle du bouton d'à côté.
const btnInfo = document.getElementById("retenir-info");
const bulle = document.getElementById("retenir-aide");
let bulleEpinglee = false;

function montrerBulle(oui) {
  bulle.hidden = !oui;
  btnInfo.classList.toggle("ouvert", oui);
}
btnInfo.addEventListener("mouseenter", () => montrerBulle(true));
btnInfo.addEventListener("mouseleave", () => { if (!bulleEpinglee) montrerBulle(false); });
btnInfo.addEventListener("click", ev => {
  // Sans ça le clic remonterait jusqu'au gestionnaire global, qui refermerait aussitôt.
  ev.stopPropagation();
  bulleEpinglee = !bulleEpinglee;
  montrerBulle(bulleEpinglee);
});
// Épinglée, elle se referme comme tout le reste : un clic ailleurs, ou Échap.
addEventListener("click", ev => {
  if (bulleEpinglee && !bulle.contains(ev.target) && ev.target !== btnInfo) {
    bulleEpinglee = false;
    montrerBulle(false);
  }
});
addEventListener("keydown", ev => {
  if (ev.key === "Escape" && bulleEpinglee) {
    bulleEpinglee = false;
    montrerBulle(false);
  }
});

// Taper reprend la main. L'événement input ne se déclenche que pour une vraie frappe — une
// valeur posée par le script ne le déclenche pas — donc ceci ne peut venir que de toi, et ce
// que tu écris devient la nouvelle base de la dictée suivante.
champ.oninput = () => {
  base = null;
  champ.classList.remove("dictee");
  majEnvoyer();
  ajusterHauteur();
};

// --- le champ grandit avec ce qu'on y met ------------------------------------------------
// Sur une seule ligne, une longue dictée sortait du champ : on ne voyait plus ce qui était en
// train d'être reconnu, donc on ne pouvait plus le corriger. Le champ suit maintenant son
// contenu, jusqu'à 40 % de la hauteur de fenêtre — au-delà il défile à l'intérieur, parce
// qu'un champ qui mange l'écran cache le flux qu'on est venu lire.
const HAUTEUR_MINI = 36;
const barreSaisie = document.getElementById("saisie-barre");
const btnBas = document.getElementById("bas");
const zoneFlux = document.querySelector("main");

function ajusterHauteur() {
  // « auto » d'abord : sans ça scrollHeight reste bloqué sur la hauteur précédente et le
  // champ ne redescend jamais quand on efface.
  champ.style.height = "auto";
  const plafond = Math.round((window.innerHeight || 800) * 0.4);
  const voulue = Math.min(Math.max(champ.scrollHeight || HAUTEUR_MINI, HAUTEUR_MINI), plafond);
  champ.style.height = voulue + "px";
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

addEventListener("resize", ajusterHauteur);
// Échap rend le clavier aux raccourcis, sans envoyer. Attaché ici et pas plus haut : le
// gestionnaire global s'exécute au chargement, et y toucher « champ » avant sa déclaration
// tuait tout le script — page blanche, sans rien dans le flux pour le dire.
champ.addEventListener("keydown", ev => {
  if (ev.key === "Escape") champ.blur();
});

composer.onsubmit = ev => {
  ev.preventDefault();
  const texte = champ.value.trim();
  if (!texte || !socket || socket.readyState !== WebSocket.OPEN) return;
  envoyerCmd({ cmd: "texte", texte });   // parti, ou en file : jamais perdu
  champ.value = "";
  majEnvoyer();
  ajusterHauteur();
  suivre = true;
  window.scrollTo(0, document.body.scrollHeight);
};

const ETATS = { listening: "écoute", thinking: "réfléchit", speaking: "parle", initializing: "démarre" };
let echecs = 0;   // reconnexions ratées d'affilée
function brancher() {
  // Fermer l'ancienne avant d'ouvrir : deux sockets vivantes recevaient les mêmes
  // événements, et la page les affichait deux fois.
  if (socket && socket.readyState <= 1) {
    try { socket.onclose = null; socket.close(); } catch (_) {}
  }
  const ws = new WebSocket(`ws://${location.host}/flux`);
  socket = ws;
  // Le bouton suit l'état réel de la liaison : proposer « envoyer » sur une socket morte
  // ferait disparaître le message sans rien dire.
  ws.onopen = () => {
    echecs = 0;
    reconnexionPrevue = false;
    majEnvoyer();
    champ.placeholder = "écrire au lieu de parler — touche /";
    // Les clics faits pendant la coupure partent maintenant, dans l'ordre.
    viderFile();
  };
  ws.onmessage = m => {
    const e = JSON.parse(m.data);
    if (e.genre === "_histoire") {
      // L'état du micro se relit sur TOUTE l'histoire, y compris la partie déjà affichée :
      // c'est une resynchronisation, pas un affichage.
      e.evenements.forEach(ev => { if (ev.genre === "micro") microActif = ev.actif; });
      majMicro();
      e.evenements.filter(ev => !dejaVu(ev)).forEach(ajouter);
      return;
    }
    if (dejaVu(e)) return;
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
    if (e.genre === "micro") {
      microActif = e.actif;
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
      poserDictee(e.texte || "", false);
      ajouter(e);
      return;
    }
    // Déposé pour relecture : la dictée devient définitive dans la barre, à toi de jouer.
    if (e.genre === "dictee") { poserDictee(e.texte || "", true); champ.focus(); return; }
    // Parti chez Claude : la barre n'a plus à porter le texte.
    if (e.genre === "toi" && !e.tape) { viderDictee(); }
    if (e.genre === "retenir") { retenir = !!e.actif; majRetenir(); return; }
    if (e.genre === "ecoute") {
      if (e.actif) lancerCompte(e.min, e.max); else arreterCompte();
      return;
    }
    if (e.genre === "quota") { quota = e.fenetres || []; majCompteurs(); return; }
    if (e.genre === "etat") {
      const el = document.getElementById("etat");
      el.textContent = ETATS[e.vers] || e.vers;
      // « vif » fait respirer la pastille tant que la session n'est pas au repos : un coup
      // d'oeil suffit alors pour savoir si le système est vivant.
      el.className = "e-" + e.vers + (e.vers === "listening" ? "" : " vif");
      if (e.vers !== "speaking") resoudre("voix", "ok");
      return;
    }
    ajouter(e);
  };
  ws.onclose = () => {
    // Une socket périmée qui se referme après qu'une nouvelle est en place ne doit ni
    // relancer un branchement, ni faire clignoter l'état.
    if (socket !== ws) return;
    majEnvoyer();
    arreterCompte();
    toutClore("connexion perdue");
    // Une coupure d'une seconde ne doit pas s'annoncer comme une panne. La page se
    // rebranche toute seule en 1,2 s, et afficher « déconnecté » pendant ce temps donnait
    // l'impression d'un bug là où il n'y avait qu'un aller-retour. « Déconnecté » est
    // réservé au cas où la reconnexion échoue vraiment, plusieurs fois de suite.
    echecs++;
    const perdu = echecs >= 3;
    const el = document.getElementById("etat");
    el.textContent = perdu ? "déconnecté" : "reconnexion…";
    el.className = "e-listening" + (perdu ? "" : " vif");
    champ.placeholder = perdu
      ? "déconnecté — l'agent ne tourne plus ?"
      : "reconnexion…";
    reconnexionPrevue = true;
    setTimeout(() => { reconnexionPrevue = false; brancher(); }, 1200);
  };
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
