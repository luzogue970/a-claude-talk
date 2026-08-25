"""Banc d'essai des moteurs de reconnaissance : le meme audio, tous les moteurs.

Pourquoi ce fichier existe : les editeurs publient tous des bancs ou ils gagnent. Ce qui
decide ici n'est pas un taux d'erreur moyen sur de l'anglais lu, c'est la performance sur
DU FRANCAIS TECHNIQUE DICTE PAR TOI, avec le vocabulaire de tes projets. Personne d'autre ne
peut mesurer ca.

    ../.venv/bin/python voix/banc_stt.py                  # phrases de reference, via Azure TTS
    ../.venv/bin/python voix/banc_stt.py mon-audio.wav    # ta voix, ce qui compte vraiment
    ../.venv/bin/python voix/banc_stt.py --moteurs gladia,speechmatics

Ce qui est mesure, et pourquoi :

- **le texte rendu**, mot pour mot. C'est le seul juge.
- **la distance au texte attendu** (Levenshtein sur les mots, soit un WER). Sur un fichier
  fourni sans transcription de reference, la colonne est vide : on ne peut pas noter sans
  verite terrain, et inventer une note serait pire que ne pas noter.
- **la latence**, du premier octet envoye au texte final. Un moteur excellent qui met huit
  secondes ne sert a rien dans une conversation.

Le banc consomme du quota reel sur chaque moteur teste. Trois phrases coutent quelques
secondes d'audio : negligeable, mais ce n'est pas gratuit.
"""

import asyncio
import os
import sys
import time
import urllib.request
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import moteurs_stt

# Des phrases qui ressemblent a ce qu'on dicte vraiment : du francais courant, du jargon, des
# noms de fichiers, des sigles. C'est la ou les moteurs se separent — pas sur « bonjour ».
PHRASES = [
    "renomme la variable qui gère le silence dans config point py",
    "regarde pourquoi le décompte n'utilise pas le réglage en vigueur et corrige-le",
    "ajoute un test qui vérifie que la chaîne de repli garde le vocabulaire du projet",
]


def _synthetiser(texte: str, vers: Path) -> bool:
    """Fabrique l'audio de reference avec Azure TTS.

    Meme voix pour tous les moteurs : sans ca on comparerait des enregistrements differents
    et le classement ne voudrait rien dire.
    """
    if not config.AZURE_KEY:
        return False
    ssml = (f"<speak version='1.0' xml:lang='{config.LANGUAGE}'>"
            f"<voice name='{config.AZURE_VOICE}'>{texte}</voice></speak>")
    req = urllib.request.Request(
        f"https://{config.AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={"Ocp-Apim-Subscription-Key": config.AZURE_KEY,
                 "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            vers.write_bytes(r.read())
        return True
    except Exception as e:
        print(f"  synthèse impossible : {e}")
        return False


def _lire_wav(chemin: Path):
    """Le WAV en trames LiveKit de 100 ms, mono 16 bits.

    Decoupe plutot qu'un bloc unique, pour deux raisons : les moteurs streaming REFUSENT une
    trame unique (« does not support single frame recognition »), et pousser par tranches
    mesure une latence proche de l'usage reel au lieu d'un temps de traitement en lot.
    """
    from livekit import rtc
    with wave.open(str(chemin)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{chemin.name} : il faut du mono 16 bits")
        taux = w.getframerate()
        brut = w.readframes(w.getnframes())
    # Une seconde de silence a la fin. Sans elle, le VAD ne voit jamais de fin de parole et
    # StreamAdapter ne ferme aucun segment — le moteur local rendait une chaine vide en
    # 0,18 s, ce qui se lisait comme « il n'a rien compris » alors qu'on ne lui avait rien
    # demande. Azure aussi finalise plus surement. Et c'est fidele a l'usage : on se tait.
    brut += b"\x00" * (taux * 2)

    par_tranche = (taux // 10) * 2          # 100 ms, 2 octets par echantillon
    trames = []
    for i in range(0, len(brut), par_tranche):
        bloc = brut[i:i + par_tranche]
        trames.append(rtc.AudioFrame(data=bloc, sample_rate=taux, num_channels=1,
                                     samples_per_channel=len(bloc) // 2))
    return trames, taux


def _distance_mots(attendu: str, obtenu: str) -> float:
    """Taux d'erreur sur les mots (WER), en pourcentage.

    Levenshtein sur les mots plutot que sur les caracteres : une lettre en trop compte moins
    qu'un mot faux, et c'est le mot qui change le sens d'une instruction.
    """
    def normaliser(t):
        import re
        t = t.lower().replace("’", "'")
        t = re.sub(r"[^\w'\s]", " ", t)
        return t.split()

    a, b = normaliser(attendu), normaliser(obtenu)
    if not a:
        return 0.0
    d = list(range(len(b) + 1))
    for i, ma in enumerate(a, 1):
        prec, d[0] = d[0], i
        for j, mb in enumerate(b, 1):
            prec, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prec + (ma != mb))
    return 100.0 * d[len(b)] / len(a)


async def _essayer(cle: str, trames, attendu: str | None):
    """Un moteur, un audio. Renvoie (texte, secondes, wer, erreur)."""
    from livekit.agents import stt as stt_api
    from livekit.plugins import silero
    vad = silero.VAD.load()          # requis par les moteurs batch (StreamAdapter)
    debut = time.monotonic()
    try:
        moteur = moteurs_stt.construire(cle, vad)
        if moteur.capabilities.streaming:
            texte = await _par_flux(moteur, trames)
        else:
            # Batch : une seule trame concatenee, c'est ce que ces moteurs attendent.
            from livekit.agents import utils
            ev = await moteur.recognize([utils.audio.combine_frames(trames)])
            texte = " ".join(a.text for a in ev.alternatives).strip()
        duree = time.monotonic() - debut
        return texte, duree, (_distance_mots(attendu, texte) if attendu else None), None
    except Exception as e:
        return None, time.monotonic() - debut, None, f"{type(e).__name__}: {str(e)[:90]}"


async def _par_flux(moteur, trames) -> str:
    """Pousse l'audio par tranches et recolte les transcriptions finales.

    On garde les FINAL_TRANSCRIPT dans l'ordre : un moteur streaming peut decouper une phrase
    en plusieurs segments, et n'en garder qu'un donnerait un texte tronque qu'on prendrait
    pour une mauvaise transcription.
    """
    from livekit.agents import stt as stt_api
    flux = moteur.stream()
    morceaux: list[str] = []

    async def recolter():
        async for ev in flux:
            if ev.type == stt_api.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
                t = ev.alternatives[0].text.strip()
                if t:
                    morceaux.append(t)

    tache = asyncio.create_task(recolter())
    for t in trames:
        flux.push_frame(t)
    flux.end_input()
    try:
        await asyncio.wait_for(tache, timeout=25)
    except asyncio.TimeoutError:
        tache.cancel()
        if not morceaux:
            # Un vide sans explication se lit comme « mauvaise qualite ». Le dire.
            raise TimeoutError("aucune transcription en 25 s")
    finally:
        await flux.aclose()
    if not morceaux:
        # C'est le cas d'Azure a quota epuise : le flux se ferme aussitot, sans transcription
        # et sans exception. Sans ce garde-fou le banc notait 100 % d'erreur et accusait la
        # qualite du moteur, alors que le probleme etait le credit.
        raise RuntimeError("le moteur s'est fermé sans rien transcrire "
                           "(quota épuisé, clé invalide ou langue refusée ?)")
    return " ".join(morceaux).strip()


async def principal(argv):
    fichiers: list[tuple[Path, str | None]] = []
    voulus = None
    args = []
    saute = False
    for i, a in enumerate(argv):
        if saute:
            saute = False
            continue
        if a == "--moteurs" and i + 1 < len(argv):
            voulus = [c.strip() for c in argv[i + 1].split(",")]
            saute = True          # la valeur n'est pas un fichier
        elif not a.startswith("--"):
            args.append(a)

    dossier = Path(os.environ.get("TMPDIR", "/tmp")) / "banc-stt"
    dossier.mkdir(parents=True, exist_ok=True)

    if args:
        # Un fichier fourni : pas de verite terrain, donc pas de note. On lit quand meme.
        for a in args:
            p = Path(a)
            if p.is_file():
                fichiers.append((p, None))
            else:
                print(f"  introuvable : {a}")
    else:
        print("  synthèse des phrases de référence (Azure TTS)…")
        for n, phrase in enumerate(PHRASES, 1):
            f = dossier / f"ref{n}.wav"
            if not f.exists() and not _synthetiser(phrase, f):
                print("  pas de clé Azure : donne un fichier WAV en argument.")
                return 1
            fichiers.append((f, phrase))

    candidats = [c for c in (voulus or [m.cle for m in moteurs_stt.MOTEURS])
                 if c in moteurs_stt.PAR_CLE]
    dispos = [c for c in candidats if moteurs_stt.PAR_CLE[c].dispo]
    absents = [c for c in candidats if c not in dispos]
    print(f"\n  {len(dispos)} moteur(s) testable(s) : "
          + ", ".join(moteurs_stt.PAR_CLE[c].libelle for c in dispos))
    if absents:
        print("  sans clé, donc non testés : "
              + ", ".join(moteurs_stt.PAR_CLE[c].libelle for c in absents))

    resultats: dict[str, list] = {c: [] for c in dispos}
    echecs: dict[str, list] = {}
    for f, attendu in fichiers:
        trames, taux = _lire_wav(f)
        secondes = sum(t.samples_per_channel for t in trames) / taux - 1.0
        print(f"\n  ── {f.name} ({secondes:.1f} s d'audio) ──")
        if attendu:
            print(f"     attendu : {attendu}")
        for cle in dispos:
            texte, duree, wer, err = await _essayer(cle, trames, attendu)
            nom = moteurs_stt.PAR_CLE[cle].libelle
            if err:
                print(f"     {nom:<22} ÉCHEC  {err}")
                echecs.setdefault(cle, []).append(err)
                continue
            note = f"{wer:5.1f} % WER" if wer is not None else "    —     "
            print(f"     {nom:<22} {duree:5.2f} s  {note}  « {texte} »")
            resultats[cle].append((duree, wer))

    print("\n  ── bilan ──")
    lignes = []
    for cle, mesures in resultats.items():
        if not mesures:
            continue
        lat = sum(d for d, _ in mesures) / len(mesures)
        wers = [w for _, w in mesures if w is not None]
        lignes.append((sum(wers) / len(wers) if wers else None, lat, cle))
    # Trie par qualite, puis par latence. Sans verite terrain, la latence decide seule.
    lignes.sort(key=lambda x: (x[0] if x[0] is not None else 0, x[1]))
    for wer, lat, cle in lignes:
        m = moteurs_stt.PAR_CLE[cle]
        note = f"{wer:5.1f} % WER" if wer is not None else "    —     "
        print(f"     {m.libelle:<22} {note}  {lat:5.2f} s   {m.gratuit}")
    for cle, msgs in echecs.items():
        if not resultats.get(cle):
            # Aucun essai reussi : le moteur n'est pas mauvais, il n'a pas repondu. Le
            # distinguer, sinon on le condamne pour une raison qui n'est pas la sienne.
            m = moteurs_stt.PAR_CLE[cle]
            print(f"     {m.libelle:<22} inutilisable — {msgs[0]}")
    if lignes:
        gagnant = moteurs_stt.PAR_CLE[lignes[0][2]]
        print(f"\n  Le meilleur ici : {gagnant.libelle}.")
        print(f"  Pour le mettre en tête :  set -x VOIX_STT "
              + ",".join(c for _, _, c in lignes))
    print("\n  Un banc sur trois phrases synthétiques indique une tendance, pas une vérité.")
    print("  Enregistre TA voix (mono 16 bits) et relance : c'est elle qui compte.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal(sys.argv[1:])))
