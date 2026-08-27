"""Reconnaissance vocale locale, comme filet quand Azure lâche.

Ce module existe parce qu'Azure a rendu l'âme en pleine session : le palier gratuit donne
5 heures de transcription par mois, et une fois épuisées le service répond
`400 Quota exceeded` sur chaque requête. Le tableau s'est rempli d'erreurs, l'agent n'a plus
rien entendu, et il n'y avait aucun repli.

Mesuré sur ce portable (i5-1335U, 12 fils, sans GPU), en int8 :

    base   1,5-1,9 s pour 2 s d'audio   qualité moyenne sur le français
    small  4,2-4,9 s pour 2 s d'audio   qualité correcte

C'est plus lent qu'Azure, et il faut l'assumer : `small` ajoute quelques secondes avant que
l'agent réagisse. En échange, aucun quota, aucune facture, et l'audio ne quitte pas la
machine — ce qui pour du code d'entreprise n'est pas un détail.

Le modèle est chargé à la première phrase, pas au démarrage : quand Azure fonctionne, ce
module ne coûte rien du tout.
"""

import asyncio
import logging

import numpy as np
from livekit.agents import APIConnectOptions, stt, utils

import config

log = logging.getLogger("voix.stt")


class WhisperLocal(stt.STT):
    """Non-streaming STT. LiveKit's StreamAdapter turns it into a streaming one using the
    VAD, so segmentation stays the framework's job."""

    def __init__(self, taille: str | None = None, langue: str = "fr"):
        super().__init__(capabilities=stt.STTCapabilities(streaming=False,
                                                          interim_results=False))
        self.taille = taille or config.STT_LOCAL_MODELE
        self.langue = langue
        self._modele = None
        self._verrou = asyncio.Lock()

    async def _charger(self):
        """Lazy, et sous verrou : deux phrases simultanées ne doivent pas charger deux fois
        un modèle de plusieurs centaines de mégaoctets."""
        async with self._verrou:
            if self._modele is not None:
                return self._modele
            from faster_whisper import WhisperModel

            log.info("chargement du modèle local « %s » (int8, CPU)…", self.taille)
            boucle = asyncio.get_running_loop()
            self._modele = await boucle.run_in_executor(
                None,
                lambda: WhisperModel(self.taille, device="cpu", compute_type="int8",
                                     cpu_threads=config.STT_LOCAL_THREADS),
            )
            log.info("modèle local prêt")
            return self._modele

    async def _recognize_impl(self, buffer, *, language=None,
                              conn_options: APIConnectOptions = None) -> stt.SpeechEvent:
        modele = await self._charger()
        trame = utils.audio.combine_frames(buffer)
        echantillons = np.frombuffer(trame.data, dtype=np.int16)
        audio = echantillons.astype(np.float32) / 32768.0

        def transcrire():
            segments, _ = modele.transcribe(
                audio, language=self.langue, beam_size=1,
                condition_on_previous_text=False,
                # Les mots du projet, comme la phrase_list d'Azure : whisper s'appuie
                # dessus pour ne pas transformer un sigle maison en « kubedka ».
                initial_prompt=", ".join(config.phrase_list()[:40]),
            )
            return "".join(s.text for s in segments).strip()

        boucle = asyncio.get_running_loop()
        texte = await boucle.run_in_executor(None, transcrire)
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=self.langue, text=texte)],
        )


_dernier: WhisperLocal | None = None


def local(vad) -> stt.STT:
    """Le moteur local, rendu streaming par le VAD."""
    global _dernier
    _dernier = WhisperLocal()
    return stt.StreamAdapter(stt=_dernier, vad=vad)


async def precharger():
    """Charge le modèle en tâche de fond au démarrage.

    Sans ça, la première phrase après une bascule paie les 3 à 5 s de chargement en plus de
    sa propre transcription — c'est-à-dire précisément au moment où le service cloud vient
    de lâcher et où on a le moins envie d'attendre."""
    if _dernier is None:
        return
    try:
        await _dernier._charger()
    except Exception:
        log.exception("préchargement du modèle local en échec")
