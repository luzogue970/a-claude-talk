"""Voice front end: LiveKit Agents drives the conversation, Claude Code does the work.

Why LiveKit rather than the hand-rolled loop it replaces:

- End-of-turn detection that understands French *semantically*: a trailing "donc, euh" is
  not a finished thought.
- Adaptive interruption that tells a real barge-in from an "mmh" of agreement, and
  false-interruption recovery that resumes the sentence when a noise cut it off and the
  transcript came back empty.
- Real echo cancellation. This is the one that matters on a laptop with no headset: console
  mode runs the WebRTC AudioProcessingModule with echo_cancellation, noise_suppression,
  high_pass_filter and auto_gain_control, and feeds it the playback as a reverse stream with
  a stream delay recomputed on every input callback. The old loop hand-measured that delay
  ("the sink monitor goes silent 460 ms before the microphone stops hearing the speakers")
  and then just closed the microphone for up to 28 seconds, which is why barge-in could
  never work. Here the microphone stays open and the agent's own voice is subtracted.

Run it:  python voix/agent.py console --input-device "..." --output-device "..."
"""

import asyncio
import logging
import re
import time
from pathlib import Path

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    Agent,
    AgentSession,
    InterruptionOptions,
    JobContext,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.inference import TurnDetector
from livekit.plugins import azure, silero

import config
from porte_parole import PorteParole
from worker import Worker

log = logging.getLogger("voix")

ARRET = re.compile(r"\b(stop|arr[êe]te|laisse tomber|annule|ta gueule)\b", re.I)
STATUT = re.compile(r"\b(o[uù] (tu )?en es|t'en es o[uù]|[çc]a avance|[çc]a donne quoi|tu fais quoi)\b", re.I)
OUI = re.compile(r"\b(oui|ok|d'accord|vas[- ]y|valide|fais[- ]le|autorise|feu vert)\b", re.I)
NON = re.compile(r"\b(non|pas [çc]a|refuse|laisse|surtout pas|n'y touche pas)\b", re.I)

# A long run needs to say something occasionally, or it feels dead. Rarely, though:
# narration every few seconds is what makes voice agents exhausting.
JALON_APRES_S = 60.0
JALON_INTERVALLE_S = 75.0


class _LLMInerte(llm.LLM):
    """AgentSession refuses to generate a reply when no LLM is attached, even though
    llm_node here is fully overridden and never calls one. This satisfies that check and
    fails loudly if it is ever actually reached."""

    class _Flux(llm.LLMStream):
        async def _run(self) -> None:
            raise RuntimeError("llm_node est surchargé : ce LLM ne doit jamais être appelé")

    @property
    def model(self) -> str:
        return "claude-code-via-agent-sdk"

    def chat(self, *, chat_ctx, tools=None,
             conn_options=DEFAULT_API_CONNECT_OPTIONS, **kwargs) -> llm.LLMStream:
        return _LLMInerte._Flux(self, chat_ctx=chat_ctx, tools=tools or [],
                                conn_options=conn_options)


class Voix(Agent):
    def __init__(self, worker: Worker, porte_parole: PorteParole):
        # instructions are unused: llm_node is fully overridden and Claude Code carries
        # its own system prompt.
        super().__init__(instructions="", llm=_LLMInerte())
        self.worker = worker
        self.porte_parole = porte_parole
        self.permission_en_cours: asyncio.Future | None = None
        self._debut_tour: float | None = None
        self._dernier_jalon = 0.0

    # --- ce que la voix fait d'une phrase entendue ----------------------------
    async def llm_node(self, chat_ctx: llm.ChatContext, tools, model_settings):
        texte = ""
        for item in reversed(chat_ctx.items):
            if getattr(item, "role", None) == "user":
                texte = (item.text_content or "").strip()
                break
        if not texte:
            return

        # A pending permission owns the next utterance: it is an answer, not a new task.
        if self.permission_en_cours and not self.permission_en_cours.done():
            if OUI.search(texte):
                self.permission_en_cours.set_result(True)
                yield "d'accord, j'y vais."
                return
            if NON.search(texte):
                self.permission_en_cours.set_result(False)
                yield "très bien, je ne le fais pas."
                return
            yield "je n'ai pas compris : c'est oui ou c'est non ?"
            return

        # Answered locally, with zero latency — the whole point of having the journal.
        if ARRET.search(texte):
            await self.worker.interrompre()
            yield "ok, j'arrête."
            return
        if STATUT.search(texte) and self.worker.occupe:
            yield self.worker.journal.resume_court() + "."
            return

        deja_occupe = self.worker.occupe
        await self.worker.envoyer(texte)
        self._debut_tour = self._debut_tour or time.monotonic()
        # Deliberately short: the real answer arrives later through session.say(), so this
        # turn must not block for the twenty minutes Claude might take.
        yield "noté, j'ajoute ça." if deja_occupe else "c'est parti."

    # --- ce que la voix dit de son propre chef --------------------------------
    async def pomper_evenements(self):
        while True:
            genre, charge = await self.worker.events.get()
            if genre == "fin":
                self._debut_tour = None
                # say() takes an async iterable, so the voice starts on the first sentence
                # the porte-parole produces instead of waiting for the whole rewrite.
                await self.session.say(
                    self.porte_parole.dire_flux(charge), allow_interruptions=True
                )
            elif genre == "outil":
                await self._peut_etre_un_jalon()

    async def _peut_etre_un_jalon(self):
        maintenant = time.monotonic()
        if not self._debut_tour or maintenant - self._debut_tour < JALON_APRES_S:
            return
        if maintenant - self._dernier_jalon < JALON_INTERVALLE_S:
            return
        self._dernier_jalon = maintenant
        lignes = self.worker.journal.lignes()
        if lignes:
            await self.session.say(f"toujours dessus, je viens de {lignes[-1]}.",
                                   allow_interruptions=True)

    async def demander_permission(self, action: str, libelle: str) -> bool:
        """Awaited by the worker's can_use_tool, so the session really waits for an answer."""
        boucle = asyncio.get_running_loop()
        self.permission_en_cours = boucle.create_future()
        await self.session.say(f"je veux {action}. Je le fais ?", allow_interruptions=True)
        try:
            return await asyncio.wait_for(self.permission_en_cours, timeout=120)
        except asyncio.TimeoutError:
            return False


def _stt():
    if config.STT_ENGINE == "azure" and config.AZURE_KEY:
        # phrase_list biases the acoustic layer. Without it Azure fr-FR turned "MQL" into
        # "kubedka" and "un fichier point MD" into "un point MD".
        return azure.STT(
            speech_key=config.AZURE_KEY,
            speech_region=config.AZURE_REGION,
            language=config.LANGUAGE,
            phrase_list=config.phrase_list(),
            explicit_punctuation=True,
        )
    raise RuntimeError(
        "STT indisponible. Soit VOIX_STT=azure avec AZURE_SPEECH_KEY dans "
        "~/.config/claude-talk/secrets.env, soit brancher faster-whisper ici pour "
        "garder l'audio sur la machine (voir README, section « reste a faire »)."
    )


def _tts():
    if config.TTS_ENGINE == "azure" and config.AZURE_KEY:
        return azure.TTS(
            speech_key=config.AZURE_KEY,
            speech_region=config.AZURE_REGION,
            voice=config.AZURE_VOICE,
            language=config.LANGUAGE,
        )
    raise RuntimeError(
        "TTS indisponible. Soit VOIX_TTS=azure avec AZURE_SPEECH_KEY, soit brancher "
        "les voix Piper fr_FR-* de tts/voices/ ici (voir README)."
    )


async def entrypoint(ctx: JobContext):
    porte_parole = PorteParole()
    await porte_parole.start()

    agent: Voix | None = None

    async def on_permission(action: str, libelle: str) -> bool:
        return await agent.demander_permission(action, libelle) if agent else False

    worker = Worker(on_permission=on_permission)
    await worker.start()
    agent = Voix(worker, porte_parole)

    session = AgentSession(
        stt=_stt(),
        tts=_tts(),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            # v1-mini runs entirely on this machine. The v1 detector, and the adaptive
            # interruption detector below, are LiveKit Cloud inference: they need
            # LIVEKIT_API_KEY and they send audio off the machine.
            turn_detection=TurnDetector(version="v1-mini"),
            interruption=InterruptionOptions(
                enabled=True,
                # "adaptive" would tell a real barge-in from an "mmh" of agreement, but it
                # is cloud-only. Stating "vad" explicitly documents the choice and stops
                # LiveKit from trying, failing, and warning on every start.
                mode="vad",
                # Tuned for laptop speakers rather than a headset. AEC removes most of the
                # agent's own voice but not all of it, so one short blip must not count as
                # a barge-in; two words of real speech must be heard. Interrupting takes a
                # beat longer, which beats the agent cutting itself off — or worse,
                # transcribing itself and feeding that back as a new instruction.
                min_duration=0.6,
                min_words=2,
                # If a noise interrupts and the transcript comes back empty, resume the
                # sentence instead of leaving the debrief half-said.
                resume_false_interruption=True,
                false_interruption_timeout=2.0,
            ),
        ),
    )

    async def fermer(*_):
        # Two claude subprocesses are alive at this point. Without this they outlive the
        # Ctrl+C, and the "coroutine AgentServer.aclose was never awaited" warning is the
        # symptom of tearing down while they are still attached.
        await worker.stop()
        if porte_parole.client:
            await porte_parole.client.disconnect()

    ctx.add_shutdown_callback(fermer)

    await session.start(agent=agent, room=ctx.room)
    # Console mode wires its own audio, but a room job needs this or the tracks never
    # attach and the agent listens to nothing.
    await ctx.connect()

    asyncio.create_task(agent.pomper_evenements())
    await session.say(
        f"prêt, on est dans {Path(config.WORKDIR).name}. Qu'est-ce qu'on fait ?",
        allow_interruptions=True,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
