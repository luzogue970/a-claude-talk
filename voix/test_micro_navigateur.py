"""Le micro du telephone, dans un VRAI navigateur : je dicte, je quitte, je reviens, je redicte.

Les cas de test_front_cas.js doublent le micro, l'enregistreur et le reseau ; ils disent ce que
la page DECIDE, pas ce que le navigateur FAIT. Ce parcours-ci roule dans Chrome, avec un
micro factice, contre un vrai serveur du tableau : l'autorisation est demandee pour de vrai,
MediaRecorder enregistre pour de vrai, le fichier part pour de vrai sur /audio, ffmpeg le
decode pour de vrai, et les traces de la page arrivent pour de vrai dans le journal.

Le parcours tel que je le vis :
  1. j'appuie sur « dicter ici » : ça enregistre ;
  2. je quitte l'application : ce que j'ai dit part tout seul ;
  3. je reviens, j'appuie : ça enregistre DE NOUVEAU — c'est le defaut qu'on chasse ;
  4. j'appuie : ça part, et le PC l'a bien recu deux fois.

    ../.venv/bin/python voix/test_micro_navigateur.py

Sans Chrome, le test s'abstient au lieu d'echouer.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp

from tableau import Tableau

ok = True
CHROMES = ["chromium", "google-chrome", "chromium-browser", "google-chrome-stable"]


def dire(bon: bool, texte: str) -> None:
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def chrome():
    for c in CHROMES:
        chemin = shutil.which(c)
        if chemin:
            return chemin
    return None


class Cdp:
    """Le strict necessaire du protocole DevTools, sur aiohttp : pas une dependance de plus."""

    def __init__(self, session, ws):
        self.session, self.ws, self.n = session, ws, 0
        self.evenements = []

    async def __call__(self, methode, **params):
        self.n += 1
        await self.ws.send_str(json.dumps({"id": self.n, "method": methode, "params": params}))
        while True:
            msg = await asyncio.wait_for(self.ws.receive(), 15)
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError(f"CDP : {msg.type}")
            d = json.loads(msg.data)
            if d.get("id") == self.n:
                if "error" in d:
                    raise RuntimeError(f"{methode} : {d['error']}")
                return d.get("result", {})
            self.evenements.append(d)

    async def js(self, expression, geste=False):
        r = await self("Runtime.evaluate", expression=expression, awaitPromise=True,
                       returnByValue=True, userGesture=geste)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text") or r["exceptionDetails"])
        return r.get("result", {}).get("value")

    async def attendre(self, expression, delai=8.0, pas=0.1):
        fin = time.monotonic() + delai
        while time.monotonic() < fin:
            if await self.js(expression):
                return True
            await asyncio.sleep(pas)
        return False


async def parcours():
    binaire = chrome()
    if not binaire:
        print("  Chrome introuvable — parcours ignore")
        return
    recus = []

    async def commande(nom, donnees):
        if nom == "vocal":
            recus.append(round(donnees["secondes"], 1))
            return {"texte": "bonjour", "moteur": "essai", "secondes": round(donnees["secondes"], 1)}
        return None

    t = Tableau(port=7894, ouvrir=False, on_commande=commande)
    journal = logging.getLogger("voix.tableau")
    journal.setLevel(logging.INFO)
    h = t.journal_handler()
    journal.addHandler(h)
    url = await t.demarrer()
    profil = tempfile.mkdtemp(prefix="voix-micro-chrome-")
    proc = subprocess.Popen(
        [binaire, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
         "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
         "--autoplay-policy=no-user-gesture-required", "--remote-debugging-port=0",
         # Sans ces deux-la, rien ne se charge : Chrome attend le trousseau du bureau
         # (gnome-keyring) avant la PREMIERE navigation, et sur une machine sans session
         # graphique ouverte il attend pour toujours. La page ne repondait pas, la requete
         # n'atteignait meme pas le serveur, et ça ressemblait a une panne reseau.
         "--password-store=basic", "--use-mock-keychain",
         f"--user-data-dir={profil}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        fichier = Path(profil) / "DevToolsActivePort"
        for _ in range(100):
            if fichier.exists() and fichier.read_text().strip():
                break
            await asyncio.sleep(0.1)
        port = int(fichier.read_text().split()[0])
        async with aiohttp.ClientSession() as s:
            async with s.put(f"http://127.0.0.1:{port}/json/new") as r:
                cible = await r.json()
            async with s.ws_connect(cible["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                cdp = Cdp(s, ws)
                await cdp("Runtime.enable")
                await cdp("Page.enable")
                await cdp("Page.navigate", url=url + "/")
                print("\n=== dans Chrome : je dicte, je quitte, je reviens, je redicte ===")
                dire(await cdp.attendre("document.readyState === 'complete' && !!document.getElementById('dicter-ici')"),
                     "la page du tableau est servie et porte le bouton « dicter ici »")
                dire(await cdp.attendre("typeof socket !== 'undefined' && socket && socket.readyState === 1 && liaisonVivante()"),
                     "la liaison avec le serveur est etablie")
                dire(await cdp.js("isSecureContext && peutEnregistrerIci"),
                     "ce navigateur peut enregistrer ici")

                # 1. J'appuie : ça enregistre, avec un vrai flux et un vrai enregistreur.
                await cdp.js("document.getElementById('dicter-ici').click(); true", geste=True)
                dire(await cdp.attendre("vocalEnCours === true && enregistreur && enregistreur.state === 'recording'"),
                     "j'appuie : ça enregistre")
                dire(await cdp.js("fluxMicro.getAudioTracks().every(p => p.readyState === 'live' && !p.muted)"),
                     "sur une piste vivante et non muette")
                await asyncio.sleep(1.5)

                # 2. Je quitte l'application : l'enregistrement s'arrete et part tout seul.
                # Chrome sans fenetre ne cache pas une page par lui-meme : on lui fait vivre
                # l'evenement que le telephone envoie, avec l'etat qui va avec.
                # `bubbles: true` n'est pas un detail : la page ecoute sur la fenetre, et
                # l'evenement natif remonte depuis le document. Un evenement fabrique sans
                # cette option ne quitte jamais le document, et le test croirait a tort que
                # la page ne reagit pas.
                await cdp.js("""
                  Object.defineProperty(document, 'visibilityState', { get: () => 'hidden', configurable: true });
                  document.dispatchEvent(new Event('visibilitychange', { bubbles: true })); true""")
                dire(await cdp.attendre("vocalEnCours === false"), "je quitte : l'enregistrement s'arrete")
                fin = time.monotonic() + 8
                while time.monotonic() < fin and len(recus) < 1:
                    await asyncio.sleep(0.1)
                dire(len(recus) == 1 and recus[0] >= 0.3,
                     f"et ce que j'ai dit est arrive sur le PC, decode par ffmpeg : {recus!r} s")
                dire(await cdp.attendre("/transcrit par essai/.test(document.getElementById('note-barre').textContent)"),
                     "la page dit que c'est transcrit")

                # 3. Je reviens, j'appuie : ça enregistre DE NOUVEAU. C'est LE point.
                await cdp.js("""
                  Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
                  document.dispatchEvent(new Event('visibilitychange', { bubbles: true })); true""")
                await asyncio.sleep(0.3)
                dire(await cdp.js("vocalEnCours === false && ouvertureVocal === false && fluxMicro === null"),
                     "au retour, rien ne traine : ni flux, ni ouverture, ni enregistrement")
                await cdp.js("document.getElementById('dicter-ici').click(); true", geste=True)
                ok2 = await cdp.attendre("vocalEnCours === true && enregistreur && enregistreur.state === 'recording'")
                if not ok2:
                    print("    DIAG :", await cdp.js(
                        "JSON.stringify({vocalEnCours, ouvertureVocal, vocalEnvoi, "
                        "attente: !!vocalEnAttente, echecs: echecsMicro, flux: !!fluxMicro, "
                        "enr: enregistreur && enregistreur.state, "
                        "note: (document.getElementById('note-barre').textContent||'').slice(0,160), "
                        "liaison: liaisonVivante(), sock: socket && socket.readyState})"))
                    print("    TRACES :", [e["texte"] for e in t.histoire
                                           if e["genre"] == "log" and "page · micro" in e["texte"]][-8:])
                dire(ok2, "je reviens, j'appuie : ça enregistre de nouveau — sans recharger la page")
                dire(await cdp.js("fluxMicro.getAudioTracks().every(p => p.readyState === 'live' && !p.muted)"),
                     "sur une piste de nouveau vivante")
                await asyncio.sleep(1.5)

                # 4. J'appuie : ça part, et le PC l'a bien recu deux fois.
                await cdp.js("document.getElementById('dicter-ici').click(); true", geste=True)
                dire(await cdp.attendre("vocalEnCours === false"), "j'appuie : ça s'arrete")
                fin = time.monotonic() + 8
                while time.monotonic() < fin and len(recus) < 2:
                    await asyncio.sleep(0.1)
                dire(len(recus) == 2, f"et le PC a recu les deux enregistrements : {recus!r} s")
                dire(await cdp.js("echecsMicro === 0"), "aucun echec compte sur tout le parcours")

                # Et la page a raconte tout ça a l'agent, dans l'ordre.
                await asyncio.sleep(0.3)
                traces = [e["texte"] for e in t.histoire if e["genre"] == "log" and "page · micro" in e["texte"]]
                etapes = [tr.split("micro · ", 1)[1].split(" —")[0] for tr in traces]
                dire(etapes.count("enregistre") == 2 and etapes.count("arrêt") == 2
                     and "page cachée" in etapes and "page visible" in etapes,
                     f"le journal de l'agent raconte le parcours : {etapes}")
    finally:
        journal.removeHandler(h)
        proc.terminate()
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profil, ignore_errors=True)
        await t.arreter()


async def principal():
    await parcours()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
