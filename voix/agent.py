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
    EndpointingOptions,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents import stt as stt_api
from livekit.agents.inference import TurnDetector
from livekit.plugins import azure, silero

try:
    from livekit.plugins import deepgram
except ImportError:   # paquet absent d'un venv reconstruit : on doit rester audible
    deepgram = None

import stt_local

import config
import journal
from intentions import modele_demande, reconnaitre
from porte_parole import PorteParole
from quota import Quota
from tableau import Tableau
from worker import Worker

log = logging.getLogger("voix")


def journal_lignes(j) -> list[str]:
    try:
        return j.lignes()
    except Exception:
        return []

OUI = re.compile(r"\b(oui|ok|d'accord|vas[- ]y|valide|fais[- ]le|autorise|feu vert)\b", re.I)
NON = re.compile(r"\b(non|pas [çc]a|refuse|laisse|surtout pas|n'y touche pas)\b", re.I)

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
    def __init__(self, worker: Worker, porte_parole: PorteParole,
                 tableau: Tableau | None = None, quota: Quota | None = None,
                 conversation=None):
        # instructions are unused: llm_node is fully overridden and Claude Code carries
        # its own system prompt.
        super().__init__(instructions="", llm=_LLMInerte())
        self.worker = worker
        self.porte_parole = porte_parole
        self.tableau = tableau
        self.quota = quota
        self.conv = conversation
        self.permission_en_cours: asyncio.Future | None = None
        self._debut_tour: float | None = None
        self._dernier_debrief: str | None = None
        # « Retenir » : la dictée se dépose dans la barre de saisie au lieu de partir chez
        # Claude, pour pouvoir corriger une transcription avant de l'envoyer.
        self.retenir = False
        # Retenue d'UN seul tour, décidée pendant le décompte : « celui-là, garde-le ». Elle
        # ne change pas le mode, elle rattrape le message en vol. Sans ça il faudrait armer
        # « retenir » avant de parler, donc savoir à l'avance qu'on allait se tromper.
        self._retenir_ce_tour = False
        # Les pourcentages de fenêtre au départ du tour, pour pouvoir dire à l'arrivée de
        # combien ils ont bougé.
        self._quota_depart: dict[str, float] = {}
        # La session, gardée directement. Voir la propriété `sess` : c'est la correction du
        # « micro coupé qui ne se coupe pas du premier clic ».
        self._session_directe = None
        # Marque le tour suivant comme tapé plutôt que dicté. Posé par la commande texte du
        # tableau, lu et effacé par llm_node — pour qu'UNE seule ligne « toi » soit publiée
        # par tour, quel que soit le canal.
        self._tape_en_attente = False

    def _voir(self, genre: str, **donnees):
        if self.tableau:
            self.tableau.publier(genre, **donnees)

    def attacher_session(self, session):
        self._session_directe = session

    @property
    def sess(self):
        """La session, utilisable AUSSI hors activité.

        `Agent.session` passe par `_get_activity_or_raise()`, qui lève `RuntimeError` dès que
        `_activity` est `None` — c'est-à-dire pendant une transition, une interruption, une
        fermeture. Or le tableau de bord et la pompe d'événements appellent ces méthodes
        depuis l'EXTÉRIEUR d'un tour de parole.

        C'était le bug : cliquer « micro coupé » au mauvais moment levait une RuntimeError,
        qui remontait jusqu'à la boucle WebSocket et la tuait — d'où « déconnecté » à gauche,
        un micro resté ouvert, et un second clic nécessaire une fois la reconnexion faite.
        Le sens de la lecture explique aussi pourquoi seul *couper* échouait : rouvrir passait
        par l'objet session capturé dans l'entrypoint, qui ne contrôle rien.

        On garde `self.session` en second : si la référence directe manque, le comportement
        d'origine reprend, contrôle d'activité compris.
        """
        return self._session_directe or self.session

    def marquer_tape(self):
        """Le prochain tour vient du clavier, pas du micro."""
        self._tape_en_attente = True

    def _parler(self, texte: str):
        """Every short line the voice says goes to the page too, or the transcript on
        screen has holes exactly where the conversation happened."""
        self._voir("voix", texte=texte)
        return texte

    # --- ce que la voix fait d'une phrase entendue ----------------------------
    async def llm_node(self, chat_ctx: llm.ChatContext, tools, model_settings):
        texte = ""
        for item in reversed(chat_ctx.items):
            if getattr(item, "role", None) == "user":
                texte = (item.text_content or "").strip()
                break
        if not texte:
            return

        tape, self._tape_en_attente = self._tape_en_attente, False

        # Une ligne « toi » signifie « ce message a été pris en compte », et rien d'autre.
        # Elle est donc publiée par chaque branche qui consomme réellement l'énoncé, jamais
        # en amont : une dictée retenue dans la barre n'a pas été prise en compte, et
        # l'afficher comme telle rendait le flux menteur — impossible de savoir, en le
        # relisant, ce qui était vraiment parti.
        #
        # Elle n'est pas non plus publiée depuis l'événement de transcription : les
        # transcriptions finales tombent plusieurs fois par tour dès qu'on marque une pause,
        # ce qui donnait trois lignes pour un seul message.
        def vu():
            self._voir("toi", texte=texte, tape=tape)

        # A pending permission owns the next utterance: it is an answer, not a new task.
        if self.permission_en_cours and not self.permission_en_cours.done():
            vu()
            if OUI.search(texte):
                self.permission_en_cours.set_result(True)
                yield self._parler("d'accord, j'y vais.")
                return
            if NON.search(texte):
                self.permission_en_cours.set_result(False)
                yield self._parler("très bien, je ne le fais pas.")
                return
            yield self._parler("je n'ai pas compris : c'est oui ou c'est non ?")
            return

        # Local orders, answered with zero latency and never forwarded to Claude. The
        # detection is published so a wrong hijack is visible as such instead of looking
        # like Claude behaving oddly.
        intention, pourquoi = reconnaitre(texte)
        if intention:
            vu()
            self._voir("ordre", texte=f"{intention} — {pourquoi}")
            if self.conv:
                self.conv.ordre_local(f"{intention} ({pourquoi})")
            reponse = await self._executer(intention, texte)
            if reponse:
                yield self._parler(reponse)
            return

        # Mode « retenir » : le texte se dépose dans la barre, il ne part pas. Placé APRÈS les
        # permissions et les ordres locaux, délibérément : « oui » à une demande de permission
        # et « coupe le micro » ne sont pas des tâches à relire, et les parquer dans une boîte
        # pour ensuite appuyer sur Entrée n'aurait aucun sens.
        rattrape, self._retenir_ce_tour = self._retenir_ce_tour, False
        # Retenue automatique pendant que Claude travaille. Sans elle, parler pendant une
        # tâche empile un second message : le worker l'accepte, répond « noté, j'ajoute ça »,
        # et il part sans qu'on ait rien relu. Or c'est justement le moment où l'on parle pour
        # réagir à ce qu'on voit passer — donc celui où une phrase mal transcrite coûte le
        # plus cher.
        #
        # Placée APRÈS les permissions et les ordres locaux : « arrête » et « oui » doivent
        # continuer de passer, ce sont des réactions, pas des tâches à relire.
        auto = config.RETENIR_SI_OCCUPE and self.worker.occupe
        if (self.retenir or rattrape or auto) and not tape:
            # Pas de ligne « toi » : rien n'a été envoyé. Le texte attend dans la barre, et la
            # ligne « retenu » dit pourquoi — sinon on croit avoir parlé pour rien.
            self._voir("dictee", texte=texte,
                       auto=bool(auto and not (self.retenir or rattrape)))
            return

        vu()
        deja_occupe = self.worker.occupe
        # Relevé au départ, pas à l'arrivée : sans point de comparaison il n'y a pas d'écart.
        # Un tour ajouté à un tour déjà en cours ne réinitialise pas le départ, sinon la
        # mesure ne couvrirait qu'une partie du travail.
        if self.quota and not deja_occupe:
            self._quota_depart = self.quota.instantane()
        if self.conv:
            self.conv.tour_utilisateur(texte)
        await self.worker.envoyer(texte)
        self._debut_tour = self._debut_tour or time.monotonic()
        # Deliberately short: the real answer arrives later through session.say(), so this
        # turn must not block for the twenty minutes Claude might take.
        yield self._parler("noté, j'ajoute ça." if deja_occupe else "c'est parti.")

    # --- ce que la voix dit de son propre chef --------------------------------
    async def pomper_evenements(self):
        while True:
            genre, charge = await self.worker.events.get()
            if genre == "fin":
                self._debut_tour = None
                # Hors du chemin de la voix : la mesure demande un aller-retour HTTP, et le
                # débrief n'a pas à l'attendre. Le tableau complète la ligne du tour quand le
                # chiffre arrive, une seconde plus tard.
                if self.quota:
                    asyncio.create_task(self._mesurer_quota())
                # say() takes an async iterable, so the voice starts on the first sentence
                # the porte-parole produces instead of waiting for the whole rewrite.
                await self.sess.say(self._debrief(charge), allow_interruptions=True)

    async def _mesurer_quota(self):
        """De combien les fenêtres ont bougé pendant le tour qui vient de finir."""
        depart, self._quota_depart = self._quota_depart, {}
        try:
            ecarts = await self.quota.mesurer(depart)
        except Exception:
            log.debug("mesure de quota impossible", exc_info=True)
            return
        if ecarts:
            self._voir("tour_quota", ecarts=ecarts,
                       texte=self.quota.dire_ecarts(ecarts))

    async def _executer(self, intention: str, texte: str = "") -> str | None:
        """Runs a local order. Returning None means: say nothing at all."""
        if intention == "modele":
            # Voice switches are temporary by design: "pour cette tâche" means this task,
            # and the base model comes back on its own once the turn ends.
            cle = modele_demande(texte)
            if not cle:
                # "change de modèle" tout court n'indique rien : deviner serait pire que
                # demander, surtout quand la bascule invalide le cache de prompt.
                dispo = ", ".join(l.split(" —")[0] for _, l in config.MODELES.values())
                return f"sur quel modèle ? {dispo}."
            libelle = await self.worker.changer_modele(cle, temporaire=True)
            if not libelle:
                return "je ne connais pas ce modèle."
            retour = " juste pour cette tâche" if cle != config.cle_du_modele(
                config.WORKER_MODEL) else ""
            return f"d'accord, je passe sur {libelle}{retour}."
        if intention == "micro":
            return self.couper_micro()
        if intention == "silence":
            # Shut up without stopping the work. Confirming out loud would defeat it.
            self.sess.interrupt()
            return None
        if intention == "arret":
            # No session.interrupt() here: it would cancel the very speech this returns,
            # which is how "arrête tout" ended up silent. Your own voice already interrupted
            # whatever was being said — that is what barge-in is for.
            await self.worker.interrompre()
            return "ok, j'arrête."
        if intention == "statut":
            if self.worker.occupe:
                return self.worker.journal.resume_court() + "."
            return "il n'y a rien en cours."
        if intention == "repete":
            return self._dernier_debrief or "je ne t'ai encore rien dit."
        if intention == "quota":
            return self.quota.resume_parle() if self.quota else "je n'ai pas le quota."
        return None

    def couper_micro(self) -> str:
        """Cuts the input and returns what to say about it.

        Entrée seulement. Couper le micro ne touche PAS à la parole en cours, et c'est
        délibéré : « arrête de m'écouter » et « arrête de parler » sont deux demandes
        différentes, et les confondre coupait la phrase en train d'être dite. Il existe déjà
        deux chemins pour faire taire la voix — l'ordre « chut » et le bouton d'arrêt — donc
        les lier ici ne rendait aucun service et retirait un choix.

        Le message est dit après la coupure, jamais avant : la coupure doit être immédiate, et
        comme elle ne concerne que l'entrée, la confirmation s'entend quand même. Elle nomme
        toujours le chemin du retour : micro fermé, on ne peut plus demander à le rouvrir."""
        self.sess.input.set_audio_enabled(False)
        self._voir("micro", actif=False)
        suite = "la réflexion continue" if self.worker.occupe else "j'attends tes instructions"
        return f"le micro est bien coupé, {suite}. Touche m ou le bouton pour le rouvrir."

    async def _debrief(self, journal_):
        """Feeds TTS and the page from the same stream, so what you read is what you hear."""
        morceaux = []
        async for bout in self.porte_parole.dire_flux(journal_):
            morceaux.append(bout)
            self._voir("voix", texte=bout, suite=True)
            yield bout
        # Kept so "répète" can say it again without paying for a second rewrite.
        self._dernier_debrief = "".join(morceaux).strip() or None
        if self.conv:
            self.conv.tour_claude(self._dernier_debrief or "", outils=journal_lignes(journal_),
                                  jetons=getattr(journal_, "jetons", None),
                                  duree=getattr(journal_, "duree_s", None))

    async def demander_permission(self, action: str, libelle: str) -> bool:
        """Awaited by the worker's can_use_tool, so the session really waits for an answer."""
        boucle = asyncio.get_running_loop()
        self.permission_en_cours = boucle.create_future()
        self._voir("permission", texte=f"je veux {action}")
        await self.sess.say(self._parler(f"je veux {action}. Je le fais ?"),
                               allow_interruptions=True)
        try:
            accord = await asyncio.wait_for(self.permission_en_cours, timeout=120)
        except asyncio.TimeoutError:
            accord = False
        self._voir("permission", texte=f"je veux {action}",
                   decision="autorisé" if accord else "refusé")
        return accord


def _stt_azure():
    # phrase_list biases the acoustic layer. Without it Azure fr-FR turned "MQL" into
    # "kubedka" and "un fichier point MD" into "un point MD".
    return azure.STT(
        speech_key=config.AZURE_KEY,
        speech_region=config.AZURE_REGION,
        language=config.LANGUAGE,
        phrase_list=config.phrase_list(),
        explicit_punctuation=True,
    )


def _stt_deepgram():
    """Le repli de même calibre qu'Azure.

    keyterm est à nova-3 ce que phrase_list est à Azure : sans lui la bascule ferait perdre
    le vocabulaire du projet au pire moment, et « MQL » redeviendrait « kubedka » juste
    parce qu'Azure a manqué de crédit."""
    extra = {}
    if config.DEEPGRAM_KEYTERM:
        # Nova-3 plafonne le nombre de termes ; on garde les plus utiles.
        extra["keyterm"] = config.phrase_list()[:50]
    return deepgram.STT(
        api_key=config.DEEPGRAM_KEY,
        model=config.DEEPGRAM_MODELE,
        language=config.LANGUAGE,
        punctuate=True,
        **extra,
    )


def _stt(vad):
    """Azure, puis Deepgram, puis le moteur local.

    FallbackAdapter bascule tout seul quand le premier moteur échoue — c'est exactement le
    trou qui a fait perdre une session entière : le quota Azure s'épuise, chaque phrase
    rate, et rien ne prend le relais.

    Deepgram est là parce que le filet local, lui, se sent : 4 à 5 s par phrase transforme
    une conversation en échange de télégrammes. Entre Azure et Deepgram la bascule est
    inaudible — deux moteurs en streaming, résultats intermédiaires, même ordre de latence.
    Le local reste en queue pour le jour où les deux services sont coupés ensemble : lent
    vaut mieux que sourd.

    config.chaine_stt() décide, ici on assemble seulement — pour que le panneau de
    démarrage et l'agent ne puissent pas raconter deux histoires différentes."""
    fabriques = {
        "azure": _stt_azure,
        "deepgram": _stt_deepgram,
        "local": lambda: stt_local.local(vad),
    }
    chaine = config.chaine_stt()
    if "deepgram" in chaine and deepgram is None:
        chaine = [n for n in chaine if n != "deepgram"] or ["local"]
        log.warning("clé Deepgram présente mais livekit-plugins-deepgram n'est pas installé "
                    "(pip install livekit-plugins-deepgram) : repli retiré de la chaîne")
    if chaine == ["local"] and config.STT_ENGINE == "auto":
        log.warning("aucune clé cloud (Azure ni Deepgram) : reconnaissance locale seule, "
                    "compter 4 à 5 s par phrase")
    log.info("reconnaissance vocale : %s", " → ".join(chaine))

    moteurs = [fabriques[nom]() for nom in chaine]
    if len(moteurs) == 1:
        return moteurs[0]
    return stt_api.FallbackAdapter(
        moteurs,
        # Court : inutile d'attendre dix secondes un service qui répond 400 en 200 ms.
        attempt_timeout=6.0,
        max_retry_per_stt=1,
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
    tableau = Tableau(port=config.UI_PORT, ouvrir=config.UI_OUVRIR)
    url = await tableau.demarrer()
    # LiveKit's own log lines land on the page too, so "tout ce qui se passe" really is
    # everything and not just the parts I remembered to publish.
    logging.getLogger().addHandler(tableau.journal_handler())
    log.info("tableau de bord : %s", url)
    print(f"  tableau {url}")

    quota = Quota(tableau)
    await quota.rafraichir()

    # Une seule session à la fois, en principe. Deux agents ne se cassent pas mutuellement —
    # le tableau change de port tout seul — mais ils se disputent le micro et consomment la
    # même fenêtre de rate limit. C'est exactement ce qui s'était produit : un zombie qui
    # mangeait le quota sans que personne l'écoute. On avertit sans bloquer : le blocage
    # empêcherait aussi les cas légitimes, et un avertissement nommant le PID est ce qui
    # permet d'agir.
    deja = journal.actives(config.WORKDIR)
    if deja:
        detail = ", ".join(f"pid {d['pid']} dans {d.get('projet') or '?'}" for d in deja)
        alerte = (f"{len(deja)} session déjà active ({detail}) — elles se partagent le micro "
                  f"et le quota. « vvstop » ferme les autres.")
        log.warning("%s", alerte)
        tableau.publier("erreur", texte=alerte)

    conv = journal.Conversation(
        projet=Path(config.WORKDIR).name,
        modele=config.WORKER_MODEL,
        effort=config.WORKER_EFFORT,
        reprise=config.REPRENDRE or None,
        chemin=config.WORKDIR,
    )
    log.info("conversation enregistrée dans %s", conv.fichier)
    print(f"  journal {conv.fichier}")

    porte_parole = PorteParole()
    await porte_parole.start()

    agent: Voix | None = None

    async def on_permission(action: str, libelle: str) -> bool:
        return await agent.demander_permission(action, libelle) if agent else False

    worker = Worker(on_permission=on_permission, tableau=tableau, conversation=conv)
    await worker.start()
    agent = Voix(worker, porte_parole, tableau, quota, conv)

    valeurs = config.resume()
    alerte = valeurs.pop("_alerte", "")
    valeurs["quota"] = quota.resume()
    valeurs["reconnaissance"] = (
        f"{config.STT_ENGINE} · {config.LANGUAGE} · {len(config.phrase_list())} termes biaisés"
        + (f" · repli local {config.STT_LOCAL_MODELE}"
           if config.STT_ENGINE == "auto" else ""))
    valeurs["journal"] = conv.fichier.name
    if config.REPRENDRE:
        valeurs["reprise de"] = config.REPRENDRE
    valeurs["_alerte"] = alerte
    tableau.publier("config", valeurs=valeurs)
    tableau.publier("modeles",
                    liste=[{"cle": k, "libelle": l} for k, (_, l) in config.MODELES.items()],
                    actuel=config.cle_du_modele(config.WORKER_MODEL))
    # Reprendre une conversation rendait le contexte complet à Claude, mais la page restait
    # VIDE : le tableau vit en mémoire du processus, et un nouveau processus part de rien. On
    # relit donc ce que Claude Code a écrit sur disque et on le republie, pour retrouver les
    # soixante tours d'avant en ouvrant la page.
    async def _rejouer_historique(sid: str):
        try:
            dossier = journal.dossier_de_session(sid) or config.WORKDIR
            evenements, ecartes = await asyncio.to_thread(
                journal.rejouer_session, sid, dossier)
        except Exception:
            log.warning("historique de %s illisible", sid, exc_info=True)
            return
        if not evenements:
            tableau.publier("reprise",
                            texte=f"aucun historique lisible pour la session {sid}")
            return
        tableau.publier("reprise", texte=(
            f"reprise de {sid} — {len(evenements)} lignes rechargées"
            + (f", {ecartes} plus anciennes écartées" if ecartes else "")))
        for e in evenements:
            tableau.publier(e.pop("genre"), passe=True, **e)
        tableau.publier("reprise", texte="fin de l'historique — la suite est en direct")
        log.info("historique rejoué : %d lignes", len(evenements))

    if config.REPRENDRE:
        # En tâche de fond : lire huit cents messages ne doit pas retarder le premier mot.
        asyncio.create_task(_rejouer_historique(config.REPRENDRE))

    tableau.publier("delais",
                    paliers=[{"s": p} for p in config.ECOUTE_PALIERS],
                    actuel=config.ECOUTE_MIN, plafond=config.plafond_ecoute())
    tableau.publier("efforts",
                    liste=[{"cle": k, "libelle": l} for k, (_, l) in config.EFFORTS.items()],
                    actuel=config.WORKER_EFFORT)

    vad = silero.VAD.load()
    moteur_stt = _stt(vad)

    # Une bascule de moteur doit s'annoncer. Sans ça, la session où le quota Azure s'est
    # épuisé n'a laissé qu'un mur d'erreurs identiques et aucune ligne disant ce qui prenait
    # le relais — impossible de comprendre pourquoi tout était devenu lent.
    if isinstance(moteur_stt, stt_api.FallbackAdapter):
        # Le label est le chemin complet du module (livekit.plugins.azure.stt.STT), pas le
        # nom du plugin : on cherche donc le segment, pas une égalité.
        NOMS = (("azure", "Azure"), ("deepgram", "Deepgram"),
                ("stream_adapter", "le moteur local"), ("whisper", "le moteur local"))

        def _nom_moteur(m) -> str:
            brut = (getattr(m, "label", "") or type(m).__name__).lower()
            for motif, joli in NOMS:
                if motif in brut:
                    return joli
            return brut

        @moteur_stt.on("stt_availability_changed")
        def _bascule(ev):
            nom = _nom_moteur(ev.stt)
            if ev.available:
                texte = f"reconnaissance : {nom} est de nouveau disponible"
            else:
                jolis = {"azure": "Azure", "deepgram": "Deepgram",
                         "local": "le moteur local"}
                restants = [jolis[n] for n in config.chaine_stt() if jolis.get(n) != nom]
                suite = restants[0] if restants else "plus rien"
                texte = (f"reconnaissance : {nom} est tombé, bascule sur {suite} "
                         f"(nouvelle tentative en arrière-plan)")
            log.warning("%s", texte)
            tableau.publier("erreur" if not ev.available else "log",
                            niveau="WARNING", source="stt", texte=texte)

    session = AgentSession(
        stt=moteur_stt,
        tts=_tts(),
        vad=vad,
        turn_handling=TurnHandlingOptions(
            # v1-mini runs entirely on this machine. The v1 detector, and the adaptive
            # interruption detector below, are LiveKit Cloud inference: they need
            # LIVEKIT_API_KEY and they send audio off the machine.
            turn_detection=TurnDetector(version=config.DETECTEUR_TOUR),
            # Le vrai correctif au problème « il m'envoie avant que j'aie fini » : le défaut
            # de 0,5 s faisait d'une pause pour réfléchir une fin de phrase, et la suite
            # repartait comme un second message par-dessus le premier.
            endpointing=EndpointingOptions(
                mode="fixed",
                min_delay=config.ECOUTE_MIN,
                max_delay=config.plafond_ecoute(),
            ),
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
                min_duration=config.INTERRUPT_MIN_DUREE,
                min_words=config.INTERRUPT_MIN_MOTS,
                # If a noise interrupts and the transcript comes back empty, resume the
                # sentence instead of leaving the debrief half-said.
                resume_false_interruption=True,
                false_interruption_timeout=config.FAUX_INTERRUPT_S,
            ),
        ),
    )

    async def fermer(*_):
        conv.clore("session terminée")
        # Two claude subprocesses are alive at this point. Without this they outlive the
        # Ctrl+C, and the "coroutine AgentServer.aclose was never awaited" warning is the
        # symptom of tearing down while they are still attached.
        await worker.stop()
        if porte_parole.client:
            await porte_parole.client.disconnect()
        await quota.fermer()
        await tableau.arreter()

    ctx.add_shutdown_callback(fermer)

    # Everything the session hears or decides, mirrored to the page.
    @session.on("user_input_transcribed")
    def _entendu(ev):
        # Du direct uniquement : ces événements alimentent le texte qui s'écrit dans la barre
        # à mesure qu'il est reconnu. La ligne définitive du flux est publiée par llm_node,
        # qui est le seul endroit connaissant le texte consolidé du tour.
        # Les résultats intermédiaires arrivent entiers et grandissants : ils remplacent.
        tableau.publier("partiel", texte=ev.transcript, final=bool(ev.is_final))

    @session.on("agent_state_changed")
    def _etat(ev):
        tableau.publier("etat", vers=str(ev.new_state))

    # Le décompte avant envoi. La fenêtre est entièrement déterminée : à partir de la fin de
    # la parole, le tour partira soit à ECOUTE_MIN, soit à ECOUTE_MAX si le détecteur juge la
    # phrase inachevée. La page peut donc afficher un compte à rebours exact plutôt qu'une
    # animation vague — et savoir quand se taire n'est plus une devinette.
    @session.on("user_state_changed")
    def _etat_utilisateur(ev):
        if str(ev.new_state) == "speaking":
            tableau.publier("ecoute", actif=False, parle=True)
        elif str(ev.old_state) == "speaking":
            # Le silence commence ici, pas avant : c'est la seule transition qui compte.
            # Lu depuis la session, pas depuis la constante : après un réglage, la
            # constante ne dit plus la vérité et le décompte mentirait.
            reglage = session._opts.endpointing
            tableau.publier("ecoute", actif=True,
                            min=reglage.get("min_delay", config.ECOUTE_MIN),
                            max=reglage.get("max_delay", config.plafond_ecoute()))

    # Le message est réellement parti quand il entre dans le contexte de conversation. Les
    # transcriptions finales, elles, tombent plusieurs fois par tour dès qu'on marque une
    # pause : les prendre pour un envoi afficherait trois lignes « toi » pour un seul message.
    @session.on("conversation_item_added")
    def _tour_ajoute(ev):
        if str(getattr(ev.item, "role", "")) == "user":
            tableau.publier("ecoute", actif=False)

    vus: set[str] = set()

    @session.on("error")
    def _erreur(ev):
        brut = str(getattr(ev, "error", ev))
        # Le quota Azure produisait une erreur toutes les quinze secondes : le tableau
        # devenait illisible et le vrai message se perdait dedans. On résume, une fois.
        if "Quota exceeded" in brut:
            cle = "quota-stt"
            suivant = [n for n in config.chaine_stt() if n != "azure"]
            clair = ("quota Azure de transcription épuisé — le palier gratuit F0 s'arrête à "
                     "5 h/mois, passer en S0 le supprime. Bascule sur "
                     + ({"deepgram": "Deepgram", "local": "le moteur local (lent)"}
                        .get(suivant[0], suivant[0]) if suivant else "rien"))
        elif "stt" in brut.lower():
            cle, clair = "stt", f"reconnaissance vocale en échec : {brut[:180]}"
        else:
            cle, clair = brut[:60], brut[:400]
        if cle in vus:
            return
        vus.add(cle)
        log.warning("%s", clair)
        tableau.publier("erreur", texte=clair)

    await session.start(agent=agent, room=ctx.room)
    # Console mode wires its own audio, but a room job needs this or the tracks never
    # attach and the agent listens to nothing.
    await ctx.connect()

    # The page can now act, not just watch. Assigned after start() because the command
    # needs the live session, and the server is already up by then.
    async def commande(nom: str, donnees: dict):
        if nom == "micro":
            actif = bool(donnees.get("actif", True))
            try:
                if actif:
                    session.input.set_audio_enabled(True)
                    tableau.publier("micro", actif=bool(session.input.audio_enabled))
                else:
                    # Same path as the spoken command, minus the confirmation: you are looking
                    # at the page, the red button says it.
                    agent.couper_micro()
            except Exception:
                # Republier l'état RÉEL avant de laisser remonter l'échec. Sans ça le bouton
                # garde une valeur fausse, et le clic suivant renvoie donc la même demande —
                # que set_audio_enabled traite comme un non-événement, puisqu'il commence par
                # « si c'est déjà cet état, ne rien faire ». D'où l'impression que le micro
                # « ne se coupe pas tout de suite » et qu'il faut recliquer.
                try:
                    tableau.publier("micro", actif=bool(session.input.audio_enabled))
                except Exception:
                    pass
                raise
        elif nom == "modele":
            cle = str(donnees.get("cle") or "")
            # From the page the choice is deliberate, so it sticks until changed again.
            libelle = await worker.changer_modele(cle, temporaire=False)
            if libelle:
                tableau.publier("ordre", texte=f"modèle changé depuis le tableau : {libelle}")
        elif nom == "texte":
            # Écrire au lieu de parler, sans créer un second chemin.
            #
            # generate_reply() ajoute le message au contexte et déclenche llm_node : le
            # texte tapé traverse donc EXACTEMENT la même suite qu'une phrase entendue —
            # réponse à une permission en attente, ordre local, envoi au worker, débrief
            # parlé. Un chemin parallèle aurait fini par dériver de l'autre, et c'est déjà
            # ce qui rendait l'ancienne version incohérente.
            #
            # Volontairement indépendant de l'état du micro : c'est micro coupé que taper
            # est le plus utile, et ça donne un mode de travail entièrement silencieux.
            propos = str(donnees.get("texte") or "").strip()
            if not propos:
                return
            # llm_node publie la ligne « toi » pour tous les canaux ; on lui dit seulement
            # que celui-ci vient du clavier.
            agent.marquer_tape()
            session.generate_reply(user_input=propos, input_modality="text")
        elif nom == "effort":
            cle = str(donnees.get("cle") or "")
            libelle = await worker.changer_effort(cle)
            if libelle:
                tableau.publier("ordre", texte=f"effort réglé sur {libelle}")
            elif worker.occupe:
                # Refus explicite plutôt que clic sans effet : reconstruire le client en
                # pleine tâche tuerait le travail en cours.
                tableau.publier("log", niveau="WARNING", source="effort",
                                texte="tâche en cours : arrête-la avant de changer l'effort "
                                      "(le niveau est figé à la construction de la session)")
                tableau.publier("effort", cle=config.cle_de_effort(worker.effort),
                                niveau=worker.effort,
                                libelle=config.EFFORTS.get(worker.effort, ("", ""))[1])
        elif nom == "retenir":
            agent.retenir = bool(donnees.get("actif"))
            tableau.publier("retenir", actif=agent.retenir)
            log.info("dictée %s", "retenue dans la barre" if agent.retenir else "envoyée directement")
        elif nom == "delai":
            # Le plancher d'envoi, réglable en cours de session par une API publique
            # (update_options) — pas besoin de reconstruire quoi que ce soit.
            try:
                plancher = float(donnees.get("secondes") or 0)
            except (TypeError, ValueError):
                return
            if not 0.5 <= plancher <= 120:
                return
            plafond = config.plafond_ecoute(plancher)
            session.update_options(endpointing_opts=EndpointingOptions(
                min_delay=plancher, max_delay=plafond))
            tableau.publier("delai", secondes=plancher, plafond=plafond)
            tableau.publier("ordre",
                            texte=f"envoi après {plancher:g} s de silence "
                                  f"(jusqu'à {plafond:g} s si la phrase semble inachevée)")
        elif nom == "retenir_tour":
            # Rattraper le message pendant son décompte. On n'arme PAS le mode : c'est ce
            # tour-là qu'on veut relire, pas tous les suivants.
            agent._retenir_ce_tour = True
            tableau.publier("ordre", texte="ce message sera retenu dans la barre")
        elif nom == "envoyer":
            # Contourner l'attente sans la raccourcir pour tout le monde : la fenêtre reste
            # longue pour pouvoir réfléchir à voix haute, et ce bouton dit « j'ai fini ».
            try:
                session.commit_user_turn()
            except Exception as exc:
                # Rien à envoyer, ou tour déjà parti : ça n'a rien de grave, mais il faut le
                # dire plutôt que de laisser un clic sans effet visible.
                log.info("envoi immédiat sans effet : %s", exc)
                tableau.publier("log", niveau="INFO", source="tour",
                                texte="rien à envoyer pour l'instant")
            else:
                tableau.publier("ecoute", actif=False)
        elif nom == "arreter":
            # The reason this button exists: with the microphone cut you can no longer say
            # "stop", so the page has to carry the stop.
            session.interrupt()
            await worker.interrompre()
            tableau.publier("arret", texte="arrêt demandé depuis le tableau")

    # Sans ça, tout appel venant du tableau passerait par Agent.session et son contrôle
    # d'activité — voir Voix.sess.
    agent.attacher_session(session)
    tableau.on_commande = commande
    tableau.publier("micro", actif=session.input.audio_enabled)

    asyncio.create_task(agent.pomper_evenements())
    asyncio.create_task(quota.boucle())
    if config.STT_ENGINE in ("auto", "local"):
        asyncio.create_task(stt_local.precharger())
    await session.say(
        (f"on reprend la conversation dans {Path(config.WORKDIR).name}."
         if config.REPRENDRE else
         f"prêt, on est dans {Path(config.WORKDIR).name}. Qu'est-ce qu'on fait ?"),
        allow_interruptions=True,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
