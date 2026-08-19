"""Integration test of the whole voice loop, minus the microphone and the speaker.

Console mode needs a TTY, so it cannot be driven from a pipe. AgentSession.run() is the
supported way to push a user turn through the real session, so this exercises exactly what
the voice path exercises: llm_node routing, the worker, the event pump, the porte-parole.
Speech is captured instead of synthesised.
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("VOIX_WORKER_MODEL", "claude-haiku-4-5")
os.environ.setdefault("VOIX_WORKER_EFFORT", "low")
os.environ.setdefault("VOIX_WORKDIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from livekit.agents import AgentSession  # noqa: E402

import config  # noqa: E402
from agent import Voix  # noqa: E402
from porte_parole import PorteParole  # noqa: E402
from worker import Worker  # noqa: E402

dit: list[str] = []


async def main():
    pp = PorteParole()
    await pp.start()

    agent: Voix | None = None

    async def on_permission(action, libelle):
        print(f"  [permission] « je veux {action}. Je le fais ? » -> le test repond oui")
        return True

    worker = Worker(on_permission=on_permission)
    await worker.start()
    agent = Voix(worker, pp)

    # No stt/tts: the run below uses the text modality, so the audio ends are not needed.
    session = AgentSession(
        allow_interruptions=True,
        min_interruption_duration=0.4,
        false_interruption_timeout=2.0,
        resume_false_interruption=True,
    )
    await session.start(agent=agent)

    async def capture(texte, **kwargs):
        """Stand in for the speaker; accepts a str or the porte-parole's async generator."""
        if isinstance(texte, str):
            morceaux = [texte]
        else:
            morceaux = [m async for m in texte]
        phrase = "".join(morceaux).strip()
        dit.append(phrase)
        print(f"\n  [VOIX] {phrase}\n")
        return None

    session.say = capture  # type: ignore[method-assign]
    pompe = asyncio.create_task(agent.pomper_evenements())

    async def tour(entree: str, attendre_fin: bool):
        t0 = time.monotonic()
        avant = len(dit)
        res = await session.run(user_input=entree)
        ack = " | ".join(
            ev.item.text_content for ev in res.events
            if getattr(ev, "type", "") == "message" and getattr(getattr(ev, "item", None), "role", "") == "assistant"
        ) if hasattr(res, "events") else "(?)"
        print(f'> "{entree}"\n  ack immediat ({time.monotonic()-t0:.2f}s) : {ack}')
        if attendre_fin:
            for _ in range(600):
                if len(dit) > avant:
                    print(f"  debrief parle apres {time.monotonic()-t0:.1f}s")
                    return
                await asyncio.sleep(0.25)
            print("  !! aucun debrief parle")

    print("=== 1. une tache qui utilise un outil ===")
    await tour("Compte les fichiers Python dans le dossier voix et dis-moi combien il y en a.", True)

    print("=== 2. commande locale instantanee : statut pendant le travail ===")
    await worker.envoyer("Relis le fichier config.py et resume-le en une phrase.")
    await asyncio.sleep(1.5)
    await tour("t'en es où ?", False)

    print("=== 3. commande locale instantanee : arret ===")
    await tour("stop", False)

    pompe.cancel()
    await worker.stop()
    if pp.client:
        await pp.client.disconnect()
    print(f"\n=== {len(dit)} prise(s) de parole capturee(s) ===")


asyncio.run(main())
