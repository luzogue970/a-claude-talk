"""La sentinelle d'ecoute : verifier que l'oreille ecoute VRAIMENT, et la reparer sinon.

Le cas vecu, mot pour mot : « il me laisse parler pendant un moment, puis au final je me rends
compte que ça ne marche pas ». Jamais au demarrage — toujours en milieu de session, apres
etre parti et revenu. Le micro est ouvert, la page dit « j'ecoute », et plus un mot n'arrive.
Il faut relancer l'application.

La cause est dans la chaine de LiveKit, et elle est silencieuse par construction. Dans
`_STTPipeline._stt_pump`, la boucle qui lit les evenements de reconnaissance se termine ainsi :

    except APIError:
        ...  recreer le flux apres une pause
    # node ended without error (audio input closed): stop
    return

Autrement dit : une panne qui se signale est rattrapee, une fin PROPRE arrete la pompe pour
toujours. Or une fin propre arrive sans que rien ne soit casse — un fournisseur ferme sa
WebSocket apres une periode sans audio (c'est-a-dire exactement pendant qu'on code en
silence, micro coupe par la veille), et le `FallbackAdapter` lui-meme sort de `_run` par un
`return` des que le flux principal se termine sans exception. Aucune erreur n'est levee,
aucun evenement n'est emis, la session reste « en bonne sante » : la pompe est morte, les
trames continuent d'etre poussees dans un canal que plus personne ne lit, et on parle dans le
vide jusqu'a ce qu'on le remarque soi-meme.

Ce module regarde donc deux choses, parce qu'aucune des deux seule ne suffit :

1. **L'etat de la pompe**, directement. C'est le controle preventif : il voit la panne
   AVANT qu'on ait parle pour rien. Il touche a des attributs prives de LiveKit — c'est
   assume, et c'est pour ça que `etat()` retourne `None` plutot que de lever quand la
   structure interne change. Une sentinelle qui tombe en panne ne doit pas emporter l'agent ;
   le controle (2) reste, lui, entierement sur nos propres signaux.

2. **La parole sans transcription.** Le detecteur d'activite vocale tourne EN LOCAL : il ne
   depend d'aucun reseau, d'aucun quota, d'aucune cle. Quand il dit « quelqu'un a parle » et
   qu'aucune transcription n'arrive dans le delai ou meme le moteur le plus lent aurait rendu
   son texte, le constat est sans appel, quelle qu'en soit la cause. C'est le filet qui
   rattrape les pannes qu'on n'a pas prevues — y compris celles que (1) ne saura pas voir.

Reparer veut dire recreer la pipeline de reconnaissance sur la session vivante, ce que fait
`AudioRecognition._update_stt(node)` : le `FallbackAdapter` rouvre alors un flux, en
reessayant TOUS les moteurs (quand ils sont tous marques indisponibles, il les rejoue quand
meme). C'est l'equivalent exact de relancer l'application, sans la relancer.

Et surtout : ça se DIT. Une reparation muette ferait croire que rien n'est arrive, donc
qu'il n'y avait rien a corriger. La seule chose pire que parler dans le vide, c'est ne pas
savoir qu'on vient de le faire.
"""

import asyncio
import logging
import time

log = logging.getLogger("voix.sentinelle")

# Toutes les deux secondes. La question « la pompe est-elle vivante » est une lecture
# d'attribut, elle ne coute rien ; ce qui coute, c'est de parler trente secondes dans le vide.
PERIODE = 2.0

# Le delai au-dela duquel « j'ai parle et rien n'est revenu » devient une panne et non une
# lenteur. Le moteur local — le plus lent de la chaine — rend une phrase en 4 a 7 s, et un
# repli d'un moteur a l'autre ajoute son propre delai d'essai. Vingt secondes laissent donc
# passer la pire lenteur legitime sans laisser passer une panne.
SEUIL_MUET = 20.0

# Mais la lenteur legitime depend de la LONGUEUR de ce qu'on a dit. Les moteurs en direct
# rendent du texte pendant qu'on parle — chaque partiel remet le compteur a zero, donc la
# duree ne change rien pour eux. Le moteur local, lui, ne rend rien avant la fin et met deux a
# trois fois le temps reel : une phrase de trente secondes lui en prend soixante-dix. Sans
# cette marge, une sentinelle reglee pour le cas rapide crierait a la panne sur chaque longue
# phrase dictee au moteur local — et une alerte qui se trompe est pire qu'une absence
# d'alerte, parce qu'on cesse de la croire.
MARGE_PAR_SECONDE = 3.0

# Deux reparations ne se suivent pas de pres. Si la premiere n'a pas suffi, c'est que la
# cause n'est pas celle qu'on repare, et marteler ne ferait qu'empiler des flux a moitie
# ouverts chez le fournisseur.
REPOS = 15.0


def _pompe(session):
    """La pompe de reconnaissance de la session, ou None si on ne peut pas la voir.

    Volontairement tolerant : chaque maillon de ce chemin est prive, donc susceptible de
    disparaitre a la prochaine version de LiveKit. On degrade vers le controle par la parole,
    on ne tombe pas.
    """
    try:
        activite = getattr(session, "_activity", None)
        if activite is None:
            return None
        reco = getattr(activite, "_audio_recognition", None)
        if reco is None:
            return None
        return reco, getattr(reco, "_stt_pipeline", None)
    except Exception:          # pragma: no cover — structure interne inattendue
        log.debug("pompe de reconnaissance illisible", exc_info=True)
        return None


class Sentinelle:
    """Veille sur l'oreille, et la repare quand elle s'est tue sans le dire.

    `dire` recoit une phrase destinee a l'humain, pas au journal : c'est elle qui transforme
    une reparation invisible en information utilisable. `micro_ouvert` dit si on est cense
    ecouter — une pompe arretee micro ferme n'est pas une panne, c'est du repos.
    """

    def __init__(self, session, *, micro_ouvert, dire=None, periode: float = PERIODE):
        self.session = session
        self.micro_ouvert = micro_ouvert
        self.dire = dire or (lambda texte, grave=False: None)
        self.periode = periode
        # Quand le detecteur local a entendu une parole se terminer, et quand une
        # transcription est arrivee pour de bon. L'ecart entre les deux est la mesure.
        self.parole_finie_a: float | None = None
        self.parole_debut_a: float | None = None
        self.duree_parole: float = 0.0
        self.derniere_transcription: float = time.monotonic()
        self.reparee_a: float = 0.0
        self.reparations: int = 0
        self.derniere_raison: str = ""
        self._tache: asyncio.Task | None = None

    # --- ce que l'agent lui signale -------------------------------------------------------

    def parole_commence(self):
        """Le detecteur local entend quelqu'un. Une attente en cours est annulee : on reparle,
        donc le silence precedent n'etait pas une panne mais une pause."""
        self.parole_finie_a = None
        self.parole_debut_a = time.monotonic()

    def parole_finie(self):
        """La parole s'arrete : le chronometre de la transcription attendue demarre ici."""
        maintenant = time.monotonic()
        self.duree_parole = (maintenant - self.parole_debut_a
                             if self.parole_debut_a is not None else 0.0)
        self.parole_finie_a = maintenant

    def transcrit(self):
        """Du texte est arrive. C'est la preuve que la chaine entiere fonctionne."""
        self.derniere_transcription = time.monotonic()
        self.parole_finie_a = None

    # --- ce qu'elle constate --------------------------------------------------------------

    def pompe_morte(self) -> bool:
        """Vrai quand la pompe de reconnaissance ne lit plus rien et ne reviendra pas.

        Inconnue vaut non : on ne repare pas sur une lecture qu'on n'a pas su faire. Le
        controle par la parole, lui, ne depend d'aucune structure interne.
        """
        vu = _pompe(self.session)
        if vu is None:
            return False
        _reco, pipeline = vu
        if pipeline is None:
            # La session tourne sans reconnaissance du tout. Ce n'est pas un etat transitoire :
            # `_update_stt(None)` est le seul chemin qui mene la, et l'agent ne l'emprunte pas.
            return True
        tache = getattr(pipeline, "_pump_task", None)
        return bool(tache is not None and tache.done())

    def muette(self) -> float:
        """Depuis combien de secondes on a parle sans que rien ne revienne. 0 si tout va bien."""
        if self.parole_finie_a is None:
            return 0.0
        return time.monotonic() - self.parole_finie_a

    def seuil(self) -> float:
        """Le temps qu'on accorde a CETTE phrase-la avant de parler de panne."""
        return SEUIL_MUET + MARGE_PAR_SECONDE * self.duree_parole

    # --- ce qu'elle fait ------------------------------------------------------------------

    def reparer(self, raison: str) -> bool:
        """Recreer la pipeline de reconnaissance sur la session vivante.

        Retourne vrai si la reparation a pu etre tentee. Un echec se dit aussi : c'est le seul
        cas ou relancer l'application reste necessaire, et le savoir vaut mieux que d'insister
        au micro.
        """
        maintenant = time.monotonic()
        if maintenant - self.reparee_a < REPOS:
            return False
        self.reparee_a = maintenant
        vu = _pompe(self.session)
        if vu is None:
            self.dire("la reconnaissance vocale ne répond plus et je ne sais pas la relancer "
                      "d'ici — il faut relancer l'application", True)
            return False
        reco, _pipeline = vu
        noeud = getattr(reco, "_stt", None)
        if noeud is None:
            self.dire("la reconnaissance vocale ne répond plus et aucun moteur n'est branché "
                      "— il faut relancer l'application", True)
            return False
        try:
            # pipeline laisse a None : c'est ce qui en fabrique une neuve. Le repli rouvre
            # alors un flux en reessayant tous les moteurs, y compris ceux qu'il avait
            # marques tombes.
            reco._update_stt(noeud)
        except Exception:
            log.exception("la reparation de la reconnaissance a echoue")
            self.dire("la reconnaissance vocale est tombée et n'a pas pu être relancée — "
                      "il faut relancer l'application", True)
            return False
        self.reparations += 1
        self.derniere_raison = raison
        self.parole_finie_a = None
        self.duree_parole = 0.0
        self.derniere_transcription = maintenant
        log.warning("oreille relancee (%s) — reparation n°%d", raison, self.reparations)
        self.dire(f"l'écoute s'était arrêtée ({raison}) — je viens de la relancer, "
                  f"redis ta phrase", True)
        return True

    def verifier(self) -> str | None:
        """Un tour de veille. Retourne la raison de la reparation, ou None.

        Micro ferme, on ne verifie rien : une pompe au repos n'est pas une panne, et la
        reparer rouvrirait une liaison vers le fournisseur pour rien.
        """
        try:
            if not self.micro_ouvert():
                return None
        except Exception:        # pragma: no cover
            log.debug("etat du micro illisible", exc_info=True)
            return None

        if self.pompe_morte():
            raison = "la liaison avec le moteur s'était fermée en silence"
            return raison if self.reparer(raison) else None

        attente = self.muette()
        if attente >= self.seuil():
            raison = f"tu as parlé et rien n'est revenu depuis {int(attente)} s"
            return raison if self.reparer(raison) else None
        return None

    def etat(self) -> dict:
        """Ce que le tableau de bord montre. Lisible meme quand tout va bien : savoir que la
        veille est armee fait partie de la confiance qu'on peut lui accorder."""
        return {
            "vivante": not self.pompe_morte(),
            "muette": round(self.muette(), 1),
            "seuil": round(self.seuil(), 1),
            "reparations": self.reparations,
            "raison": self.derniere_raison,
        }

    async def veiller(self):
        """La boucle. Ne meurt jamais sur une exception : une sentinelle qui s'arrete est
        pire qu'une sentinelle absente, parce qu'on compte dessus."""
        while True:
            await asyncio.sleep(self.periode)
            try:
                self.verifier()
            except Exception:    # pragma: no cover
                log.exception("tour de veille en echec")

    def demarrer(self) -> asyncio.Task:
        self._tache = asyncio.create_task(self.veiller())
        return self._tache
