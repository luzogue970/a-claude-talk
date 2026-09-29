"""Le relais audio : un enregistrement venu d'un autre appareil devient une phrase entendue.

Trois pieces, et chacune a un test parce que chacune a un moyen precis de casser :

- le decodeur : ffmpeg doit rendre du 16 kHz mono 16 bits quel que soit ce qui entre. On lui
  envoie du 44,1 kHz stereo, et on compte les echantillons.
- la route /audio : multipart, champ « audio », taille, type, et ce qu'elle rend quand l'agent
  n'est pas la, quand le fichier est vide, quand la transcription ne comprend rien.
- l'envoi d'un tour venu du telephone : il n'existe AUCUN tour audio LiveKit derriere, donc
  commit_user_turn n'aurait rien a commettre — le texte doit partir par generate_reply, et
  seulement lui.

Sans reseau, sans moteur de reconnaissance : le gestionnaire de commande est un talon qui
rend ce qu'on lui dit de rendre. Ce qui est verifie, c'est le cablage.
"""
import asyncio, io, os, struct, sys, types, wave

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("VOIX_WORKDIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

import tableau as tab

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def un_wav(secondes: float = 1.0, taux: int = 44100, voies: int = 2) -> bytes:
    """Un la (440 Hz) : du son, pas du silence, pour qu'un decodeur qui tronque se voie."""
    import math
    n = int(secondes * taux)
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(voies)
        w.setsampwidth(2)
        w.setframerate(taux)
        trames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / taux))
            trames += struct.pack("<h", v) * voies
        w.writeframes(bytes(trames))
    return tampon.getvalue()


async def le_decodeur_normalise():
    print("\n=== le decodeur rend toujours du 16 kHz mono 16 bits ===")
    import tempfile, pathlib
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(un_wav(1.0, 44100, 2))
        chemin = pathlib.Path(f.name)
    try:
        pcm = await tab._decoder_pcm(chemin)
    finally:
        chemin.unlink(missing_ok=True)
    dire(pcm is not None, "ffmpeg a decode le fichier")
    if pcm:
        echantillons = len(pcm) // 2
        dire(abs(echantillons - 16000) < 200,
             f"une seconde de 44,1 kHz stereo devient ~16000 echantillons mono ({echantillons})")
        crete = max(abs(struct.unpack("<h", pcm[i:i + 2])[0]) for i in range(0, len(pcm), 2))
        dire(crete > 5000, f"et le son y est encore, pas un silence ({crete} de crete)")

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(b"ceci n'est pas de l'audio" * 100)
        chemin = pathlib.Path(f.name)
    try:
        pcm = await tab._decoder_pcm(chemin)
    finally:
        chemin.unlink(missing_ok=True)
    dire(pcm is None, "un fichier qui n'est pas de l'audio rend None, sans lever")


async def la_route_audio():
    print("\n=== /audio : ce qui entre, ce qui sort ===")
    recues = []

    async def commande(nom, donnees):
        recues.append((nom, donnees))
        if donnees.get("secondes", 0) > 3:
            return {"erreur": "rien compris dans l'enregistrement", "moteur": "talon"}
        return {"texte": "corrige la barre du bas", "moteur": "talon"}

    t = tab.Tableau(port=7893, ouvrir=False, on_commande=commande)
    url = await t.demarrer()
    base = url.rstrip("/")
    try:
        async with aiohttp.ClientSession() as s:
            # Le cas nominal.
            form = aiohttp.FormData()
            form.add_field("audio", un_wav(1.0), filename="vocal.wav", content_type="audio/wav")
            async with s.post(base + "/audio", data=form) as r:
                d = await r.json()
                dire(r.status == 200, f"un wav d'une seconde est accepte ({r.status})")
                dire(d.get("texte") == "corrige la barre du bas",
                     f"et la reponse porte le texte transcrit : {d.get('texte')!r}")
            dire(len(recues) == 1 and recues[0][0] == "vocal", "la commande « vocal » a ete appelee")
            if recues:
                donnees = recues[0][1]
                dire(donnees.get("taux") == 16000, "avec du 16 kHz")
                dire(abs(len(donnees.get("pcm", b"")) // 2 - 16000) < 200,
                     f"et une seconde d'echantillons ({len(donnees.get('pcm', b'')) // 2})")
                dire(0.9 < donnees.get("secondes", 0) < 1.1,
                     f"la duree est mesuree sur le PCM decode ({donnees.get('secondes'):.2f} s)")

            # La transcription ne comprend rien : 422, et la raison.
            form = aiohttp.FormData()
            form.add_field("audio", un_wav(4.0), filename="vocal.wav", content_type="audio/wav")
            async with s.post(base + "/audio", data=form) as r:
                d = await r.json()
                dire(r.status == 422 and "rien compris" in d.get("erreur", ""),
                     f"un enregistrement incompris rend 422 et le dit ({r.status}: {d.get('erreur')})")

            # Un type refuse, un envoi vide, un champ absent.
            form = aiohttp.FormData()
            form.add_field("audio", b"<html>", filename="x.html", content_type="text/html")
            async with s.post(base + "/audio", data=form) as r:
                dire(r.status == 415, f"un type qui n'est pas de l'audio est refuse ({r.status})")
            form = aiohttp.FormData()
            form.add_field("audio", b"", filename="vide.wav", content_type="audio/wav")
            async with s.post(base + "/audio", data=form) as r:
                dire(r.status == 400, f"un envoi vide est refuse ({r.status})")
            form = aiohttp.FormData()
            form.add_field("image", un_wav(0.5), filename="v.wav", content_type="audio/wav")
            async with s.post(base + "/audio", data=form) as r:
                dire(r.status == 400, f"sans champ « audio », refuse ({r.status})")
            async with s.post(base + "/audio", data=b"brut") as r:
                dire(r.status == 400, f"sans multipart, refuse ({r.status})")
    finally:
        fin = getattr(t, "arreter", None) or getattr(t, "stop", None)
        if fin:
            await fin()
        elif getattr(t, "_runner", None):
            await t._runner.cleanup()

    # Sans agent branche : on le dit, on ne plante pas.
    t2 = tab.Tableau(port=7894, ouvrir=False)
    url2 = await t2.demarrer()
    try:
        async with aiohttp.ClientSession() as s:
            form = aiohttp.FormData()
            form.add_field("audio", un_wav(0.5), filename="v.wav", content_type="audio/wav")
            async with s.post(url2.rstrip("/") + "/audio", data=form) as r:
                dire(r.status == 503, f"agent pas encore branche : 503 plutot qu'un silence ({r.status})")
    finally:
        fin = getattr(t2, "arreter", None) or getattr(t2, "stop", None)
        if fin:
            await fin()
        elif getattr(t2, "_runner", None):
            await t2._runner.cleanup()


def un_tour_venu_du_telephone():
    print("\n=== envoyer un tour venu du telephone : par le texte, jamais par le tour audio ===")
    from agent import Voix

    class Sess:
        def __init__(self):
            self.repondu, self.commis, self.oublies = [], 0, 0
        def generate_reply(self, **kw):
            self.repondu.append(kw)
        def commit_user_turn(self):
            self.commis += 1
        def clear_user_turn(self):
            self.oublies += 1

    def stand_in(vocal: bool, dit: str):
        vus = []
        o = types.SimpleNamespace(
            _dit=dit, _tour_vocal=vocal, _dictee_ouverte=True, sess=Sess(), vus=vus,
            fermer_fenetre=lambda publier=True: None,
            _voir=lambda genre, **d: vus.append((genre, d)),
        )
        o._oublier_tour = lambda: o.sess.clear_user_turn()
        return o

    o = stand_in(True, "corrige la barre du bas")
    Voix._envoyer_maintenant(o, "test")
    dire(o.sess.repondu == [{"user_input": "corrige la barre du bas", "input_modality": "text"}],
         "venu du telephone, le texte part par generate_reply")
    dire(o.sess.commis == 0, "et rien n'est commis cote audio : il n'y a pas de tour audio")
    dire(o.sess.oublies == 1, "le tour audio LiveKit eventuel est jete, pas envoye en double")
    dire(o._dit == "" and not o._tour_vocal and not o._dictee_ouverte,
         "l'enonce est consomme et le drapeau retombe")
    dire(any(g == "ecoute" and d.get("actif") is False for g, d in o.vus),
         "et la page apprend que l'ecoute est finie")

    o = stand_in(False, "phrase dite dans le micro du PC")
    Voix._envoyer_maintenant(o, "test")
    dire(o.sess.commis == 1 and not o.sess.repondu,
         "venu du micro du PC, le tour audio est commis, comme avant")

    o = stand_in(True, "")
    Voix._envoyer_maintenant(o, "test")
    dire(not o.sess.repondu and any(g == "log" for g, _ in o.vus),
         "un vocal sans texte n'envoie rien, et le dit")


async def le_contexte_du_job_est_rejoue():
    """Le piege, reproduit, puis desamorce.

    Les moteurs prennent leur session HTTP dans une variable de contexte que le job LiveKit
    pose avant d'appeler le point d'entree. Une requete arrivant par la route web tourne dans
    une tache creee par aiohttp. Ici on construit le PIRE cas — le serveur demarre depuis un
    contexte vide, donc aucun heritage possible — et on verifie que la transcription passe
    quand meme, parce qu'elle est rejouee dans le contexte capture au demarrage du job.

    Ce test parle a Deepgram pour de vrai : c'est le seul moyen de savoir. Sans reseau, la
    seconde tentative echoue pour une raison reseau — qui n'est PAS le contexte, et c'est ce
    qui est affirme."""
    print("\n=== la transcription tourne dans le contexte du job, d'ou qu'elle vienne ===")
    import contextvars
    from livekit import rtc
    from livekit.agents.utils import http_context
    import config, moteurs_stt
    if not config.DEEPGRAM_KEY:
        print("  (pas de cle Deepgram ici : maillon non verifie)")
        return
    http_context._new_session_ctx()               # ce que fait le job avant l'entrypoint
    contexte_job = contextvars.copy_context()      # ce que l'agent capture au demarrage
    moteur = moteurs_stt.construire("deepgram", None)
    resultats = {}

    async def commande(nom, donnees):
        pcm = donnees["pcm"]
        trame = rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1,
                               samples_per_channel=len(pcm) // 2)
        try:
            await asyncio.wait_for(moteur.recognize(trame), timeout=60)
            resultats["sans"] = "ok"
        except Exception as e:
            resultats["sans"] = f"{type(e).__name__}: {e}"[:220]
        try:
            ev = await asyncio.wait_for(
                asyncio.create_task(moteur.recognize(trame), context=contexte_job), timeout=60)
            resultats["avec"] = "ok:" + repr((ev.alternatives[0].text if ev.alternatives else ""))
        except Exception as e:
            resultats["avec"] = f"{type(e).__name__}: {e}"[:220]
        return {"texte": "x"}

    t = tab.Tableau(port=7895, ouvrir=False, on_commande=commande)
    # Le pire cas : demarre depuis un contexte VIDE, les gestionnaires n'heritent de rien.
    url = await asyncio.create_task(t.demarrer(), context=contextvars.Context())
    try:
        async with aiohttp.ClientSession() as s:
            form = aiohttp.FormData()
            form.add_field("audio", un_wav(1.0, 16000, 1), filename="la.wav", content_type="audio/wav")
            async with s.post(url.rstrip("/") + "/audio", data=form) as r:
                dire(r.status == 200, f"la route repond ({r.status})")
    finally:
        fin = getattr(t, "arreter", None) or getattr(t, "stop", None)
        if fin:
            await fin()
        elif getattr(t, "_runner", None):
            await t._runner.cleanup()
        await http_context._close_http_ctx()

    sans, avec = resultats.get("sans", "?"), resultats.get("avec", "?")
    dire("outside of a job context" in sans,
         "hors contexte, le moteur refuse — le piege est reel, pas suppose")
    dire("outside of a job context" not in avec,
         f"dans le contexte rejoue, il ne refuse plus : {avec}")
    dire(avec.startswith("ok:"),
         "et Deepgram a bien repondu (un la de 440 Hz : rien a transcrire, et c'est normal)")


async def la_route_parler():
    """L'autre sens : le PC synthetise, le telephone ecoute.

    Ce qui est verifie sans reseau : le refus propre quand il n'y a rien a dire, et surtout
    l'echappement XML. Le SSML est du XML : une reponse qui contient « a < b && c » casse le
    document et rend la page muette — silencieusement, ce qui est le pire cas. Avec une cle,
    on parle a Azure pour de vrai et on verifie le MP3 et le cache."""
    print("\n=== /parler : la voix du PC, servie au telephone ===")
    import config
    t = tab.Tableau(port=7898, ouvrir=False)
    url = (await t.demarrer()).rstrip("/")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url + "/parler", json={"texte": "   "}) as r:
                dire(r.status == 400, f"rien a dire : refuse proprement ({r.status})")
            async with s.post(url + "/parler", data=b"pas du json") as r:
                dire(r.status == 400, f"corps illisible : refuse proprement ({r.status})")

            if not config.AZURE_KEY:
                async with s.post(url + "/parler", json={"texte": "bonjour"}) as r:
                    dire(r.status == 503, f"sans cle Azure : 503, la page se rabattra ({r.status})")
                print("  (pas de cle Azure ici : la synthese elle-meme n'est pas verifiee)")
                return

            # Le texte qui casse le SSML. S'il passe, l'echappement tient.
            piege = 'si a < b && c > d, alors "go" & <fin>'
            async with s.post(url + "/parler", json={"texte": piege}) as r:
                corps = await r.read()
                dire(r.status == 200 and r.headers.get("Content-Type", "").startswith("audio/"),
                     f"un texte plein de chevrons et d'esperluettes passe ({r.status})")
                dire(len(corps) > 1000, f"et rend un vrai MP3 ({len(corps)} octets)")

            # Le cache : relire ne doit pas refacturer.
            phrase = "La barre du bas tient desormais sur une ligne."
            async with s.post(url + "/parler", json={"texte": phrase}) as r:
                premier = await r.read()
                dire(r.headers.get("X-Cache") == "non", "la premiere synthese va chez Azure")
            async with s.post(url + "/parler", json={"texte": phrase}) as r:
                second = await r.read()
                dire(r.headers.get("X-Cache") == "oui", "la seconde sort du cache")
                dire(premier == second, "et rend exactement le meme enregistrement")
            dire(r.headers.get("X-Voix") == "azure", "la reponse dit quelle voix a parle")

            # Preparer puis servir : le detour qui fait marcher iPhone. L'element audio va
            # chercher le son lui-meme, et il exige des plages d'octets.
            async with s.post(url + "/parler/preparer", json={"texte": phrase}) as r:
                d = await r.json()
                dire(r.status == 200 and len(d.get("cle", "")) == 64,
                     f"preparer rend une cle ({r.status}, cache={d.get('cache')})")
                cle = d.get("cle", "")
            async with s.get(url + "/parler/" + cle) as r:
                entier = await r.read()
                dire(r.status == 200 and r.headers.get("Accept-Ranges") == "bytes",
                     f"la cle se sert en entier, et annonce les plages ({r.status})")
            async with s.get(url + "/parler/" + cle, headers={"Range": "bytes=0-1"}) as r:
                deux = await r.read()
                dire(r.status == 206 and len(deux) == 2
                     and r.headers.get("Content-Range") == f"bytes 0-1/{len(entier)}",
                     f"la sonde d'iOS « bytes=0-1 » recoit un 206 juste ({r.status}, "
                     f"{r.headers.get('Content-Range')})")
            async with s.get(url + "/parler/" + cle, headers={"Range": "bytes=100-"}) as r:
                fin = await r.read()
                dire(r.status == 206 and fin == entier[100:], "une plage ouverte rend la fin du fichier")
            async with s.get(url + "/parler/" + cle, headers={"Range": "bytes=99999999-"}) as r:
                dire(r.status == 416, f"une plage hors du fichier rend 416 ({r.status})")
            async with s.get(url + "/parler/" + "0" * 64) as r:
                dire(r.status == 404, f"une cle inconnue rend 404, la page refera la synthese ({r.status})")
    finally:
        fin = getattr(t, "arreter", None) or getattr(t, "stop", None)
        if fin:
            await fin()
        elif getattr(t, "_runner", None):
            await t._runner.cleanup()


async def main():
    await le_decodeur_normalise()
    await la_route_parler()
    await la_route_audio()
    await le_contexte_du_job_est_rejoue()
    un_tour_venu_du_telephone()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


asyncio.run(main())
