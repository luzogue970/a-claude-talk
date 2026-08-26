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
from quota import Quota  # noqa: E402
from tableau import Tableau  # noqa: E402
from worker import Worker  # noqa: E402

dit: list[str] = []
demandes: list[str] = []

# Cette suite observait beaucoup et ne verifiait rien : elle affichait des compteurs, sortait
# avec 0, et le lanceur la comptait verte pour zero verification. C'est la suite la PLUS
# couteuse — un vrai Claude, un vrai quota — et c'etait la seule a ne rien prouver. Un feu
# vert paye au prix d'un appel API et qui ne teste rien est un mauvais echange.
ok = ko = 0


def dire(vrai, quoi):
    global ok, ko
    if vrai:
        ok += 1
        print(f"  ok    {quoi}")
    else:
        ko += 1
        print(f"  ECHEC {quoi}")


async def main():
    pp = PorteParole()
    await pp.start()

    agent: Voix | None = None

    async def on_permission(action, libelle):
        demandes.append(action)
        print(f"  [permission] « je veux {action}. Je le fais ? » -> le test repond oui")
        return True

    tableau = Tableau(port=7799, ouvrir=False)
    worker = Worker(on_permission=on_permission, tableau=tableau)
    await worker.start()
    q = Quota(tableau)
    await q.rafraichir()
    agent = Voix(worker, pp, tableau, q)

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

    print(f"=== 3. mode auto ({config.PERMISSION}) : ecriture sans demander ===")
    essai = os.path.join(config.WORKDIR, "_essai_auto.md")
    if os.path.isfile(essai):
        os.remove(essai)
    await tour("Cree un fichier _essai_auto.md contenant juste le mot bonjour.", True)
    # Attendre que le WORKER ait fini, pas un delai fixe. `tour()` rend la main des que la
    # voix a dit « c'est parti » — le travail continue derriere. Dix secondes suffisaient la
    # plupart du temps, et l'assertion echouait le reste du temps sur une machine chargee :
    # un test qui depend de la vitesse du moment ne mesure pas ce qu'il pretend mesurer, et
    # un rouge intermittent finit par etre ignore, ce qui coute plus cher que pas de test.
    for _ in range(240):                        # deux minutes de patience, verifiees chaque 0,5 s
        if os.path.isfile(essai) and not worker.occupe:
            break
        await asyncio.sleep(0.5)
    dire(os.path.isfile(essai),
         "un tour de travail va jusqu'a ecrire le fichier demande")
    dire(not demandes,
         f"aucune permission demandee en mode {config.PERMISSION} ({len(demandes)} vue(s))")
    outils = [n for n, _ in worker.journal.outils]
    dire(any(n in ("Write", "Edit", "Bash") for n in outils),
         f"et l'outil d'ecriture apparait dans le journal : {outils}")
    if os.path.isfile(essai):
        os.remove(essai)

    print("=== 4. les ordres locaux, sur des formulations libres ===")
    for phrase in ("utilise un modele plus rapide pour cette tache", "change de modele",
                   "tu peux couper le micro s'il te plait", "t'en es ou",
                   "j'ai consomme combien de tokens", "repete", "chut", "arrete tout"):
        await tour(phrase, False)

    from collections import Counter
    compte = Counter(e["genre"] for e in tableau.histoire)

    print("\n=== ce que la boucle a reellement produit ===")
    # Une ligne « toi » signifie « ce message a ete pris en compte ». Sans elle, en relisant
    # le flux, impossible de savoir ce qui est vraiment parti.
    dire(compte.get("toi", 0) >= 1, "des lignes « toi » marquent les messages pris en compte")
    # Les ordres locaux ne doivent JAMAIS atteindre Claude : c'est tout leur interet.
    dire(compte.get("ordre", 0) >= 5,
         f"les ordres locaux sont detectes et annonces ({compte.get('ordre', 0)} fois)")
    dire(dit, "la voix a bien quelque chose a dire a chaque tour")
    # Le mode manuel change QUI decide de l'envoi : si la constante est a False sans qu'on
    # l'ait voulu, tout le decompte visible retombe silencieusement sur une estimation.
    dire(config.TOUR_MANUEL,
         "le tour est en mode manuel : c'est l'agent qui commet, donc le decompte affiche decide")
    dire(agent.pourquoi_retenir() is None,
         "au repos, aucune raison de retenir — sinon plus rien ne partirait jamais")
    print(f"  ·     {len(compte)} genres publiés : {', '.join(sorted(compte))}")
    print(f"\n=== tableau : {len(tableau.histoire)} evenements ===")
    for genre, n in compte.most_common():
        print(f"   {genre:12} {n}")
    await q.fermer()
    await tableau.arreter()

    pompe.cancel()
    await worker.stop()
    if pp.client:
        await pp.client.disconnect()
    print(f"\n=== {len(dit)} prise(s) de parole capturee(s) ===")
    print(f"\n  {ok} ok, {ko} echec(s)")
    return 1 if ko else 0


sys.exit(asyncio.run(main()))
