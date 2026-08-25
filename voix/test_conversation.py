"""Enchaine plusieurs tours dans la chaine complete, sans micro.

Ce qu'il faut prouver : le mot de reveil ouvre, le tour suivant n'en a plus besoin, le
contexte est conserve entre les tours, et une formulation de sortie ferme.
"""
import asyncio, os, sys, time, urllib.request, wave, pathlib
sys.path.insert(0, os.path.dirname(__file__))
import config
from assistant import Assistant, TAUX

DOSSIER = pathlib.Path("/tmp/claude-1000/-home-mathieulp-Documents-mathieu-obsidian-kaizen/eda31a1b-e94c-4fca-94ee-75513d4a62a0/scratchpad/audio")

TOURS = [
    ("Hey Claude, quelle est la taille de la Terre ?", "ouvre la conversation"),
    ("Et son volume, en kilomètres cubes ?", "sans mot de reveil, et suite du contexte"),
    ("Merci c'est bon.", "ferme la conversation"),
    ("Et sa masse alors ?", "doit etre ignore : conversation fermee"),
]


def synthetiser(texte, cible):
    if cible.exists():
        return
    ssml = (f"<speak version='1.0' xml:lang='fr-FR'><voice name='{config.AZURE_VOICE}'>"
            f"{texte}</voice></speak>")
    req = urllib.request.Request(
        f"https://{config.AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={"Ocp-Apim-Subscription-Key": config.AZURE_KEY,
                 "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        cible.write_bytes(r.read())


async def main():
    DOSSIER.mkdir(parents=True, exist_ok=True)
    a = Assistant(config.EVEIL_PLAFOND_CENTIMES, seuil_sortie=10 ** 9)
    await a.demarrer()
    # On neutralise la lecture audio : ce test verifie la logique, pas les haut-parleurs.
    import assistant
    assistant.jouer = lambda wav: asyncio.sleep(0)
    assistant.synthetiser = lambda texte: asyncio.sleep(0, result=b"")

    for i, (phrase, attendu) in enumerate(TOURS):
        f = DOSSIER / f"conv{i}.wav"
        synthetiser(phrase, f)
        with wave.open(str(f)) as w:
            pcm = w.readframes(w.getnframes())
        duree = len(pcm) / (TAUX * 2)
        print(f"\n[{i}] « {phrase} »   ({attendu})")
        print(f"    conversation avant : {a.conversation}")
        t0 = time.monotonic()
        await a.traiter(pcm, duree)
        print(f"    conversation apres : {a.conversation}   ({time.monotonic()-t0:.1f}s)")

    print(f"\n  plafond : {a.plafond.resume()}")
    await a.notif_conv.fermer()
    await asyncio.sleep(3)   # laisse la remise a zero du contexte se terminer
    try:
        if a.client:
            await a.client.disconnect()
    except Exception:
        pass

asyncio.run(main())
