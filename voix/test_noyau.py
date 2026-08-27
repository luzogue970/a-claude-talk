"""Headless smoke test: does the worker see the tools, and does the porte-parole speak them?
No audio, no LiveKit — just the two pieces the old system got wrong."""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("VOIX_WORKER_MODEL", "claude-haiku-4-5")
os.environ.setdefault("VOIX_WORKER_EFFORT", "low")
os.environ.setdefault("VOIX_WORKDIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from worker import Worker
from porte_parole import PorteParole


async def main():
    demandes = []

    async def on_permission(action, libelle):
        demandes.append(action)
        print(f"  [permission demandee] {action}  -> refusee par le test")
        return False

    t0 = time.monotonic()
    pp = PorteParole(); await pp.start()
    w = Worker(on_permission=on_permission); await w.start()
    print(f"sessions pretes en {time.monotonic()-t0:.1f}s (worker={config.WORKER_MODEL}, "
          f"porte-parole={config.SPEAKER_MODEL})")

    t1 = time.monotonic()
    await w.envoyer("Lis le fichier .gitignore de ce projet et dis-moi ce qu'il protege.")
    genre = None
    while genre != "fin":
        genre, charge = await asyncio.wait_for(w.events.get(), timeout=180)
        if genre == "outil":
            print(f"  [outil vu] {charge[0]} -> {charge[1]}")
    journal = charge
    print(f"tour termine en {time.monotonic()-t1:.1f}s, cout {journal.cout_usd}")
    print(f"journal        : {journal.lignes()}")
    print(f"resume court   : {journal.resume_court()}")
    print(f"texte ecrit    : {(journal.textes[-1] if journal.textes else '')[:220]}")

    t2 = time.monotonic(); premiere = None; morceaux = []
    async for bout in pp.dire_flux(journal):
        if premiere is None:
            premiere = time.monotonic() - t2
        morceaux.append(bout)
    parle = "".join(morceaux).strip()
    print(f"\n--- PORTE-PAROLE (1re phrase a {premiere:.1f}s, total {time.monotonic()-t2:.1f}s) ---")
    print(parle)

    await w.stop()
    if pp.client: await pp.client.disconnect()

asyncio.run(main())
