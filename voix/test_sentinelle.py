#!/usr/bin/env python3
"""La sentinelle d'ecoute : prouver la panne, puis prouver qu'on la rattrape.

Deux niveaux, et le premier est le plus important. Un test qui ne verifie que notre propre
code prouverait seulement que notre code fait ce qu'on a ecrit — pas que le probleme existe.
On commence donc par LIRE la source de LiveKit installee, et par montrer la ligne exacte qui
arrete la reconnaissance pour toujours :

    # node ended without error (audio input closed): stop
    return

Si une version future de LiveKit rattrape cette fin-la toute seule, ce test deviendra rouge —
et ce sera une bonne nouvelle : il faudra alors se demander si la sentinelle sert encore,
plutot que de la garder par habitude. C'est la seule façon de ne pas empiler des rustines sur
des bugs deja corriges en amont.

Le reste tourne sur des faux objets, et c'est volontaire : reproduire une WebSocket qui se
ferme proprement chez un fournisseur demanderait le reseau, une cle, et de la chance. Ce qui
se verifie ici est la DECISION — quand reparer, quand se taire, et quand ne surtout pas
marteler.
"""

import asyncio
import inspect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import sentinelle

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


# --- de quoi simuler une session LiveKit, au maillon pres ---------------------------------

class FausseReco:
    def __init__(self, pipeline):
        self._stt = object()            # le « noeud » de reconnaissance
        self._stt_pipeline = pipeline
        self.recreations = 0
        self.casse = False

    def _update_stt(self, noeud, **_):
        if self.casse:
            raise RuntimeError("reconnaissance indisponible")
        self.recreations += 1
        self._stt_pipeline = FaussePipeline(morte=False)


# Les taches qui dorment, pour les annuler proprement a la fin : une tache en attente a la
# sortie fait crier asyncio, et ce bruit-la masquerait un vrai avertissement.
DORMEURS = []


class FaussePipeline:
    """Une pompe vivante dort ; une pompe morte est une tache DEJA terminee.

    « Deja » compte : annuler une tache ne la termine qu'au tour de boucle suivant, et un test
    qui l'oublierait verifierait un etat intermediaire plutot que la panne.
    """

    def __init__(self, morte: bool):
        if morte:
            fini = asyncio.get_running_loop().create_future()
            fini.set_result(None)
            self._pump_task = fini
            return

        async def dormir():
            await asyncio.sleep(3600)

        self._pump_task = asyncio.ensure_future(dormir())
        DORMEURS.append(self._pump_task)


class FausseSession:
    def __init__(self, pipeline):
        self._activity = type("A", (), {"_audio_recognition": FausseReco(pipeline)})()

    @property
    def reco(self):
        return self._activity._audio_recognition


def neuve(morte=False, micro=True):
    """Une sentinelle branchee sur une fausse session, et la liste de ce qu'elle a dit."""
    session = FausseSession(FaussePipeline(morte=morte))
    dits = []
    s = sentinelle.Sentinelle(session, micro_ouvert=lambda: micro,
                              dire=lambda t, grave=False: dits.append((t, grave)))
    return s, session, dits


async def principal():
    # --- 1. la panne est reelle, et elle est dans la source installee ----------------------
    print("=== la fin silencieuse existe bel et bien dans LiveKit ===")
    from livekit.agents.voice import audio_recognition
    from livekit.agents.stt import fallback_adapter

    src = inspect.getsource(audio_recognition._STTPipeline._stt_pump)
    dire("node ended without error" in src,
         "la pompe s'arrete explicitement sur une fin SANS erreur")
    # Et elle ne rattrape QUE les APIError : tout le reste, y compris une fin propre, est
    # definitif. C'est ce « seulement » qui fait la panne.
    dire("except APIError" in src and src.count("except ") == 1,
         "et elle ne rattrape que les APIError — une fin propre n'est pas rattrapee")

    run = inspect.getsource(fallback_adapter.FallbackRecognizeStream._run)
    # Le repli sort par un `return` des que le flux principal se termine sans exception : il
    # ne relance rien. C'est le maillon qui transforme « un fournisseur a ferme sa socket »
    # en « plus personne ne transcrit ».
    dire("async for ev in main_stream" in run and "\n                    return\n" in run,
         "le repli SORT quand son flux se termine sans erreur, au lieu de rouvrir")

    # La reparation qu'on utilise doit exister, et accepter un noeud seul.
    sig = inspect.signature(audio_recognition.AudioRecognition._update_stt)
    dire("pipeline" in sig.parameters and sig.parameters["pipeline"].default is None,
         "_update_stt(noeud) fabrique bien une pipeline neuve — c'est notre reparation")

    # --- 2. elle voit une pompe morte -----------------------------------------------------
    print("\n=== une pompe arretee est vue, et relancee ===")
    s, session, dits = neuve(morte=True)
    dire(s.pompe_morte(), "une tache de pompe terminee est reconnue comme morte")
    raison = s.verifier()
    dire(raison is not None, f"la veille decide de reparer ({raison})")
    dire(session.reco.recreations == 1, "la pipeline de reconnaissance a ete recreee")
    dire(not s.pompe_morte(), "et l'oreille est de nouveau vivante")
    dire(any(grave for _, grave in dits), "la reparation est annoncee, pas silencieuse")

    # --- 3. elle ne repare pas ce qui n'est pas casse --------------------------------------
    print("\n=== une oreille saine n'est pas touchee ===")
    s, session, dits = neuve()
    dire(s.verifier() is None, "rien a signaler")
    dire(session.reco.recreations == 0, "aucune recreation inutile")
    dire(not dits, "et rien de dit : une alerte sans panne detruit la confiance dans l'alerte")

    # --- 4. micro ferme : du repos, pas une panne -------------------------------------------
    print("\n=== micro ferme, une pompe arretee n'est pas une panne ===")
    s, session, dits = neuve(morte=True, micro=False)
    dire(s.verifier() is None, "la veille se tait")
    dire(session.reco.recreations == 0,
         "et ne rouvre pas une liaison vers le fournisseur pour rien")

    # --- 5. le filet qui ne depend d'aucune structure interne -------------------------------
    print("\n=== j'ai parle, rien n'est revenu ===")
    s, session, dits = neuve()
    s.parole_commence()
    s.parole_finie()
    dire(s.verifier() is None, "juste apres la phrase, on laisse le temps de transcrire")
    s.parole_finie_a = time.monotonic() - (sentinelle.SEUIL_MUET - 1)
    dire(s.verifier() is None,
         f"a {sentinelle.SEUIL_MUET - 1:.0f} s, c'est encore de la lenteur legitime")
    s.parole_finie_a = time.monotonic() - (sentinelle.SEUIL_MUET + 1)
    raison = s.verifier()
    dire(raison is not None and "rien n'est revenu" in raison,
         f"au-dela, c'est une panne et on la nomme ({raison})")
    dire(session.reco.recreations == 1, "l'oreille est relancee sur ce seul signal")
    dire(any("redis ta phrase" in t for t, _ in dits),
         "et on dit quoi faire : redire la phrase, puisqu'elle est perdue")

    # Un partiel suffit a prouver que la chaine repond : le chrono repart.
    s, session, dits = neuve()
    s.parole_finie()
    s.parole_finie_a = time.monotonic() - (sentinelle.SEUIL_MUET + 1)
    s.transcrit()
    dire(s.verifier() is None and session.reco.recreations == 0,
         "du texte recu annule l'attente — meme un partiel")

    # Une longue phrase dictee au moteur local : il ne rend rien avant la fin, et met deux a
    # trois fois le temps reel. Crier a la panne a vingt secondes la-dessus serait une fausse
    # alerte systematique — et une alerte a laquelle on cesse de croire ne protege plus rien.
    s, session, dits = neuve()
    s.parole_commence()
    s.parole_debut_a = time.monotonic() - 30          # trente secondes de parole
    s.parole_finie()
    dire(s.duree_parole >= 29, "la duree de la phrase est mesuree")
    dire(s.seuil() > sentinelle.SEUIL_MUET + 80,
         f"et le delai accorde suit la longueur dite ({s.seuil():.0f} s)")
    s.parole_finie_a = time.monotonic() - (sentinelle.SEUIL_MUET + 5)
    dire(s.verifier() is None and session.reco.recreations == 0,
         "une transcription longue a le temps d'arriver, sans fausse alerte")
    s.parole_finie_a = time.monotonic() - (s.seuil() + 1)
    dire(s.verifier() is not None, "mais passe CE delai-la, c'est bien une panne")

    # Reparler aussi : une pause de reflexion n'est pas une panne.
    s, session, dits = neuve()
    s.parole_finie()
    s.parole_finie_a = time.monotonic() - (sentinelle.SEUIL_MUET + 1)
    s.parole_commence()
    dire(s.verifier() is None and session.reco.recreations == 0,
         "reparler annule l'attente : on n'a pas parle dans le vide, on a repris")

    # --- 6. on ne martele pas ---------------------------------------------------------------
    print("\n=== deux reparations ne se suivent pas de pres ===")
    s, session, dits = neuve(morte=True)
    s.verifier()
    session.reco._stt_pipeline = FaussePipeline(morte=True)     # elle retombe aussitot
    dire(s.verifier() is None, "la seconde tentative est refusee pendant le repos")
    dire(session.reco.recreations == 1,
         f"une seule recreation en moins de {sentinelle.REPOS:.0f} s")
    s.reparee_a -= sentinelle.REPOS + 1
    dire(s.verifier() is not None and session.reco.recreations == 2,
         "le repos passe, elle reessaie")

    # --- 7. l'echec se dit, il ne se cache pas ----------------------------------------------
    print("\n=== quand la reparation echoue, on le dit ===")
    s, session, dits = neuve(morte=True)
    session.reco.casse = True
    dire(s.verifier() is None, "aucune reparation n'est revendiquee")
    dire(any("relancer l'application" in t for t, _ in dits),
         "et le seul recours restant est nomme explicitement")

    # Structure interne illisible — une future version de LiveKit, par exemple : on degrade
    # vers le filet, on ne tombe pas et on ne repare pas a l'aveugle.
    print("\n=== une session qu'on ne sait pas lire ne fait pas tomber la veille ===")
    s = sentinelle.Sentinelle(object(), micro_ouvert=lambda: True)
    dire(s.pompe_morte() is False, "inconnue vaut « pas de panne constatee »")
    dire(s.verifier() is None, "et la veille passe son tour sans lever")

    # --- 8. la boucle survit a une exception -------------------------------------------------
    print("\n=== la boucle ne meurt pas ===")
    # Les traces attendues ne doivent pas se melanger au compte rendu : on les coupe le temps
    # de ce test, et uniquement celui-la.
    sentinelle.log.disabled = True
    s, session, dits = neuve()
    tours = {"n": 0}

    def exploser():
        tours["n"] += 1
        raise RuntimeError("panne de la veille elle-meme")

    s.verifier = exploser
    s.periode = 0.01
    t = s.demarrer()
    await asyncio.sleep(0.1)
    t.cancel()
    sentinelle.log.disabled = False
    dire(tours["n"] >= 3,
         f"elle a continue malgre les exceptions ({tours['n']} tours)")

    for d in DORMEURS:
        d.cancel()

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
