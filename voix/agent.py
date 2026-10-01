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
import contextvars
import logging
import os
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
from livekit import rtc
from livekit.agents import stt as stt_api
from livekit.agents.inference import TurnDetector
from livekit.plugins import azure, silero

try:
    from livekit.plugins import deepgram
except ImportError:   # paquet absent d'un venv reconstruit : on doit rester audible
    deepgram = None

import consommation
import moteurs_stt
import pupitre
import sentinelle
import stt_local

import complexite
import config
import journal

from intentions import modele_demande, reconnaitre
from porte_parole import PorteParole
from quota import Quota
from tableau import Tableau
from worker import Worker

log = logging.getLogger("voix")
# Les plugins de reconnaissance s'importent ICI, sur le fil principal, et pas au moment de
# construire le moteur. LiveKit refuse d'enregistrer un plugin hors du fil principal, or
# l'agent tourne dans un fil de travail : un import paresseux arrivait donc toujours trop tard
# et le moteur etait retire de la chaine. Mesure sur une vraie session : la chaine s'annonçait
# « AssemblyAI → Deepgram → Speechmatics → Gladia → local → Soniox → Azure » et il ne restait
# que Deepgram et le local, les cinq autres ecartes par un mur d'avertissements au demarrage.
_charges, _refuses = moteurs_stt.precharger()
for _cle, _pourquoi in _refuses:
    log.warning("moteur « %s » indisponible : %s", _cle, _pourquoi)


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
        self._amorcer_etat()

    def _amorcer_etat(self) -> None:
        """Tout l'etat interne, en un seul endroit.

        Extrait de __init__ pour une raison concrete : les tests construisent des Voix
        partielles avec `Voix.__new__` — un motif legitime, monter un vrai Agent LiveKit
        demanderait une session complete — et chaque champ nouveau les cassait une par une,
        avec une AttributeError loin de sa cause. Un seul endroit a tenir a jour, et les
        talons appellent la meme fonction que la vraie construction.

        Ne touche PAS aux dependances injectees (worker, tableau, quota) : un test qui veut un
        faux worker doit pouvoir le poser sans qu'on le lui reecrive.
        """
        self.permission_en_cours: asyncio.Future | None = None
        # Une question de Claude, en attente d'une phrase. Distincte de la
        # permission : celle-ci se repond par oui ou non, celle-la par ce qu'on veut.
        self.question_en_cours: asyncio.Future | None = None
        self._debut_tour: float | None = None
        self._dernier_debrief: str | None = None
        # « Retenir » : la dictée se dépose dans la barre de saisie au lieu de partir chez
        # Claude, pour pouvoir corriger une transcription avant de l'envoyer.
        self.retenir = False
        # Retenue d'UN seul tour, décidée pendant le décompte : « celui-là, garde-le ». Elle
        # ne change pas le mode, elle rattrape le message en vol. Sans ça il faudrait armer
        # « retenir » avant de parler, donc savoir à l'avance qu'on allait se tromper.
        self._retenir_ce_tour = False
        # --- la fenetre avant envoi, quand c'est nous qui la tenons (config.TOUR_MANUEL) ---
        # Un seul detenteur : la tache qui dort jusqu'a l'echeance EST la decision d'envoyer.
        # La page ne fait que lire l'echeance publiee. Avant, la page comptait de son cote et
        # LiveKit decidait du sien : deux horloges pour une decision, et le bouton « retenir »
        # arrivait parfois apres coup.
        self._fenetre: asyncio.Task | None = None
        self._fin_fenetre: float = 0.0          # time.monotonic() de l'echeance
        # Ce qui a ete transcrit pour le tour en cours. Sert a deux choses : ne pas armer de
        # fenetre quand il n'y a rien a envoyer, et pouvoir reconnaitre un ordre local AVANT
        # de decider d'attendre ou de retenir.
        self._dit: str = ""
        # Un enonce est-il en cours ? Le pendant exact de `dicteeOuverte` cote page, et son
        # absence ici etait un vrai defaut : la page se protegeait des transcriptions tardives
        # pour l'AFFICHAGE, mais l'agent les accumulait quand meme et republiait ensuite un
        # « dictee » que la page obeit — court-circuitant sa propre protection.
        #
        # Le symptome exact : on envoie un message, le moteur rend une derniere finale pour la
        # queue de l'audio (tous segmentent), elle atterrit dans le tour SUIVANT, et comme
        # Claude vient de se mettre au travail la retenue d'office la depose dans la barre.
        # On voyait donc reapparaitre un morceau du message qu'on venait d'envoyer, etiquete
        # « retenu » alors qu'on n'avait rien retenu du tout.
        self._dictee_ouverte: bool = False
        self._dernier_tour_utilisateur: str | None = None
        # De quoi republier la consommation quand elle change, sans que Voix connaisse le
        # tableau : elle sait juste qu'il y a quelqu'un a prevenir.
        self._sur_conso = None
        # Un texte retenu attend dans la barre, et il n'est jamais parti.
        #
        # Sans ce drapeau, le scenario suivant perdait du texte en silence : on dicte, c'est
        # retenu (la barre garde la phrase), on reparle, ce second tour PART, et la page vide
        # la barre — la premiere phrase disparait sans avoir jamais ete envoyee ni signalee.
        # C'est la version exacte du « texte deja envoye qui reapparait, et parfois seulement
        # une partie ».
        #
        # La retenue devient donc COLLANTE : tant que quelque chose attend une relecture,
        # rien ne part tout seul par-dessus. C'est aussi ce qu'on a promis en affichant
        # « relis, puis Entree » — une promesse que la version precedente ne tenait pas.
        self._retenu_en_attente = False
        # Quand la parole a ete entendue pour la derniere fois. Sert a la veille : micro
        # ouvert, l'audio part en continu vers le nuage, donc un micro oublie ouvert coute
        # exactement comme un micro qu'on utilise.
        self._derniere_parole: float = time.monotonic()
        # Les pourcentages de fenêtre au départ du tour, pour pouvoir dire à l'arrivée de
        # combien ils ont bougé.
        self._quota_depart: dict[str, float] = {}
        # La session, gardée directement. Voir la propriété `sess` : c'est la correction du
        # « micro coupé qui ne se coupe pas du premier clic ».
        self._session_directe = None
        # Deux choses differentes, delibérément separees : ce que TU veux (le bouton) et ce
        # que le bail autorise (une seule conversation ecoute a la fois). Ecouter = les deux.
        # Les confondre ferait qu'une prise de micro rouvrirait un micro que tu avais coupe.
        self.micro_voulu = True
        self.inscription = None
        # La lecture en cours, pour pouvoir la couper précisément. Et les réponses déjà dites,
        # pour pouvoir les relire : le texte existe, une seconde synthèse ne coûte que des
        # caractères, alors que refaire le tour coûterait tout le travail.
        self._lecture = None
        self._paroles: dict[str, str] = {}
        self._id_parole: str | None = None
        # Marque le tour suivant comme tapé plutôt que dicté. Posé par la commande texte du
        # tableau, lu et effacé par llm_node — pour qu'UNE seule ligne « toi » soit publiée
        # par tour, quel que soit le canal.
        self._tape_en_attente = False
        # Les photos posees dans le composeur et pas encore parties. La page les declare a
        # chaque changement, l'envoi les consomme.
        #
        # Pourquoi ici et pas seulement dans le navigateur : une image jointe ne partait
        # qu'avec un message TAPE. Joindre une capture puis PARLER — le geste naturel sur un
        # telephone — envoyait la phrase seule, la vignette restait collee au composeur, et
        # Claude repondait qu'il ne voyait rien. On croyait l'envoi casse alors que la photo
        # n'avait simplement jamais ete citee.
        self._jointes: list[str] = []
        # Un niveau d'effort choisi a la main rend l'ajustement automatique silencieux : une
        # decision prise doit tenir, sinon le reglage n'en est pas un.
        self._effort_manuel = False
        # L'enonce en cours vient du micro d'un AUTRE appareil : il n'y a pas de tour audio
        # LiveKit derriere, donc rien a commettre — le texte part par generate_reply. Vrai
        # tant que `_dit` porte du texte venu du telephone, faux des qu'il est consomme.
        self._tour_vocal = False

    def _voir(self, genre: str, **donnees):
        if self.tableau:
            self.tableau.publier(genre, **donnees)

    # --- la fenetre avant envoi -----------------------------------------------------------

    def pourquoi_retenir(self) -> str | None:
        """La SEULE fonction qui decide si ce tour doit etre retenu, et qui dit pourquoi.

        Trois raisons, dans cet ordre de priorite. « tour » est le rattrapage explicite d'un
        message pendant son decompte ; il gagne sur tout le reste parce que c'est un geste
        volontaire et immediat. « mode » est l'interrupteur global. « occupe » est la retenue
        d'office pendant que Claude travaille : c'est le moment ou l'on parle pour reagir a
        ce qu'on voit passer, donc celui ou une phrase mal transcrite coute le plus cher.

        Renvoyer la RAISON et pas un booleen n'est pas un detail : le tableau affiche « retenu
        parce que Claude travaille » plutot que « retenu », et la difference entre les deux est
        toute la difference entre comprendre et subir.
        """
        if self._retenir_ce_tour:
            return "tour"
        if self._retenu_en_attente:
            return "attente"
        if self.retenir:
            return "mode"
        if config.RETENIR_SI_OCCUPE and self.worker.occupe:
            return "occupe"
        return None

    # Un ordre local est court par nature : « stop », « chut », « coupe le micro ». La
    # detection, elle, accepte aussi des combinaisons — « arrêter » + « ça » suffit a declarer
    # un arret. Or « explique-moi pourquoi il faut arrêter de faire ça » contient les deux, et
    # n'est pas un ordre : c'est une question. Elle existe deja, cette confusion, mais tant
    # qu'elle passait par la fenetre on pouvait la rattraper avec « retenir ». Un raccourci
    # qui l'envoie SANS attendre lui retirerait ce filet.
    #
    # D'ou la limite de mots : ce qui est court part tout de suite, ce qui est long attend
    # comme n'importe quelle phrase. Le sens de l'erreur est le bon — au pire un « stop »
    # bavard patiente cinq secondes, jamais une question ne devient un ordre irrattrapable.
    ORDRE_MOTS_MAX = 5

    def _ordre_bref(self, texte: str) -> bool:
        return (len(texte.split()) <= self.ORDRE_MOTS_MAX
                and bool(reconnaitre(texte)[0]))

    def noter_transcription(self, texte: str, final: bool) -> bool:
        """Retenir une transcription finale — si elle appartient a l'enonce en cours.

        Renvoie True si elle a ete gardee. Le cas qui compte est le FALSE : une finale qui
        arrive apres le depart du tour appartient au tour precedent. Les moteurs segmentent
        tous — mesure sur AssemblyAI comme sur Deepgram — donc la queue d'une phrase arrive
        regulierement apres le commit. L'accumuler la ferait repartir dans le tour suivant, et
        comme Claude vient de se mettre au travail la retenue d'office la deposerait dans la
        barre : on voyait reapparaitre un morceau du message qu'on venait d'envoyer, etiquete
        « retenu » alors qu'on n'avait rien retenu.

        La page se protegeait deja de ça pour l'affichage, mais l'agent republiait ensuite un
        « dictee » qu'elle obeit — sa propre protection etait court-circuitee. Le garde-fou
        manquait ici, en amont.

        Methode plutot que code inline dans le gestionnaire d'evenement : c'est ce qui permet
        au test d'appeler le vrai chemin au lieu d'en recopier une version qui derive.
        """
        if not final:
            return False
        if not self._dictee_ouverte:
            log.debug("transcription tardive ignoree : %r", texte)
            return False
        # Accumule, jamais remplace : une phrase entrecoupee de pauses arrive en plusieurs
        # finales, et n'en garder que la derniere perdait tout le debut.
        self._dit = (self._dit + " " + texte).strip()
        return True

    def _delai_fenetre(self) -> float:
        """Le delai courant, lu depuis la session et non depuis la constante.

        Apres un reglage fait dans le tableau, la constante ne dit plus la verite et le
        decompte mentirait — c'est deja arrive."""
        try:
            return float(self.sess._opts.endpointing.get("min_delay", config.ECOUTE_MIN))
        except Exception:
            return config.ECOUTE_MIN

    def fermer_fenetre(self, publier: bool = True) -> None:
        """Annule la fenetre en cours. Ne commet rien : c'est une non-action, donc sure."""
        if self._fenetre and not self._fenetre.done():
            self._fenetre.cancel()
        self._fenetre, self._fin_fenetre = None, 0.0
        if publier:
            self._voir("ecoute", actif=False)

    def ouvrir_fenetre(self) -> None:
        """La parole vient de s'arreter : decide quoi faire de ce qui a ete dit.

        Un seul endroit tranche entre les quatre issues possibles, et dans cet ordre :

        1. **rien a envoyer** — aucune transcription : on n'arme rien. Armer sur un silence
           produisait un decompte qui s'achevait sur un tour vide.
        2. **une reponse a une permission, ou un ordre local** — ça part TOUT DE SUITE. Faire
           attendre « arrete » cinq secondes de silence est absurde : c'est une reaction, pas
           une phrase a relire. C'est le gain le plus sensible du passage en manuel.
        3. **une raison de retenir** — on ne commet pas. Le texte est deja dans la barre
           (les resultats intermediaires l'y ont mis au fil de la parole), donc « retenir »
           n'a rien a deplacer : il suffit de ne rien faire, et le tour en attente est vide.
        4. **le cas courant** — on arme la fenetre et on publie son echeance exacte.
        """
        self.fermer_fenetre(publier=False)
        texte = self._dit.strip()
        if not texte:
            return

        attend_permission = (self.permission_en_cours is not None
                             and not self.permission_en_cours.done())
        # Meme raison pour une question posee par Claude : il attend, la page le montre, et
        # faire mijoter la reponse cinq secondes de plus n'ameliore rien.
        attend_question = (self.question_en_cours is not None
                           and not self.question_en_cours.done())
        if attend_permission or attend_question or self._ordre_bref(texte):
            self._envoyer_maintenant("immédiat")
            return

        raison = self.pourquoi_retenir()
        if raison:
            # Le tour en attente est abandonne : sans ça il resterait dans LiveKit et
            # repartirait au prochain envoi, ce qui faisait reapparaitre du texte deja parti.
            self._oublier_tour()
            self._voir("dictee", texte=texte, raison=raison,
                       auto=(raison == "occupe"))
            self._dit = ""
            self._dictee_ouverte = False
            self._tour_vocal = False
            self._retenu_en_attente = True
            return

        delai = self._delai_fenetre()
        self._fin_fenetre = time.monotonic() + delai
        self._voir("ecoute", actif=True, delai=delai,
                   # L'echeance en horloge murale : la page compte en local, sans rien
                   # demander, et affiche le temps REEL qui reste avant l'envoi.
                   fin=(time.time() + delai) * 1000.0,
                   min=delai, max=config.plafond_ecoute(delai))
        self._fenetre = asyncio.create_task(self._attendre_puis_envoyer(delai))

    async def _attendre_puis_envoyer(self, delai: float) -> None:
        try:
            await asyncio.sleep(delai)
        except asyncio.CancelledError:
            return
        # Relu a l'echeance, pas au depart : « retenir » peut avoir ete arme PENDANT le
        # decompte, et c'est meme son usage principal.
        raison = self.pourquoi_retenir()
        if raison:
            self._oublier_tour()
            self._voir("dictee", texte=self._dit.strip(), raison=raison,
                       auto=(raison == "occupe"))
            self._dit = ""
            self._dictee_ouverte = False
            self._tour_vocal = False
            self._retenu_en_attente = True
            self.fermer_fenetre()
            return
        self._envoyer_maintenant("échéance")

    def _envoyer_maintenant(self, pourquoi: str) -> None:
        self.fermer_fenetre(publier=False)
        texte, vocal = self._dit.strip(), self._tour_vocal
        self._dit = ""
        self._dictee_ouverte = False
        self._tour_vocal = False
        if vocal:
            # Venu du telephone : il n'existe aucun tour audio a commettre, le texte est TOUT
            # ce qu'on a. Il part par le meme chemin que le clavier — llm_node, permissions,
            # ordres locaux, debrief — et le tour audio LiveKit eventuel est jete : si le
            # micro du PC a parle entre-temps, sa transcription est deja dans `texte`.
            self._oublier_tour()
            if texte:
                self.sess.generate_reply(user_input=texte, input_modality="text")
            else:
                self._voir("log", niveau="INFO", source="tour", texte="rien à envoyer pour l'instant")
            self._voir("ecoute", actif=False)
            return
        try:
            self.sess.commit_user_turn()
        except Exception as exc:
            # Rien a commettre, ou tour deja parti. Ce n'est pas grave, mais un envoi qui
            # n'arrive pas doit se voir : un clic sans effet visible est pire qu'une erreur.
            log.info("envoi (%s) sans effet : %s", pourquoi, exc)
            self._voir("log", niveau="INFO", source="tour",
                       texte="rien à envoyer pour l'instant")
        self._voir("ecoute", actif=False)

    async def _titrer(self, question: str) -> None:
        """Nommer la conversation d'apres son sujet, en arriere-plan."""
        try:
            from porte_parole import titrer
            titre = await titrer(question, self._dernier_debrief or "")
        except Exception:
            log.debug("titre indisponible", exc_info=True)
            return
        if titre and self.conv:
            self.conv.note_titre(titre)
            log.info("conversation intitulée « %s »", titre)
            self._voir("session", id=self.conv.session_id, titre=titre)

    def _oublier_tour(self) -> None:
        """Jette le tour audio en attente sans le commettre."""
        try:
            self.sess.clear_user_turn()
        except Exception:
            log.debug("aucun tour a oublier", exc_info=True)

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

    def _avec_jointes(self, texte: str) -> str:
        """La phrase, suivie des photos qui attendaient dans le composeur.

        Deux garde-fous, et chacun repare un silence observe :

        - le fichier doit EXISTER au moment de l'envoi. Un chemin mort partait sans un mot,
          Claude repondait qu'il ne trouvait rien, et on cherchait la panne du mauvais cote.
        - la consigne de LIRE est explicite. Un chemin pose dans le texte n'oblige a rien :
          il arrivait que la reponse soit ecrite sans que l'image ait jamais ete ouverte.
        """
        chemins, self._jointes = self._jointes, []
        if not chemins:
            return texte
        vivants, morts = [], []
        for c in chemins:
            (vivants if Path(c).is_file() else morts).append(c)
        if morts:
            self._voir("erreur", texte=("photo introuvable au moment de l'envoi : "
                                        + ", ".join(Path(c).name for c in morts)))
        if not vivants:
            return texte
        self._voir("log", niveau="INFO", source="image",
                   texte=f"{len(vivants)} image(s) jointe(s) au message")
        quoi = "ces images" if len(vivants) > 1 else "cette image"
        return ((texte or f"regarde {quoi}.")
                + f"\n\nimages jointes (ouvre chaque fichier avec l'outil Read avant de "
                  f"répondre — {quoi} fait partie de la demande) :\n"
                + "\n".join("- " + c for c in vivants))

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

        # Une question en attente capte la phrase entiere, telle quelle : contrairement a
        # une permission, il n'y a rien a interpreter — « plutot la deuxieme, mais garde
        # l'ancien fichier » est une reponse parfaitement valable, et la decouper en oui/non
        # la detruirait. Place AVANT les permissions parce qu'on ne peut pas avoir les deux
        # en vol, et avant les ordres locaux parce que « arrete » peut etre une reponse.
        if self.question_en_cours and not self.question_en_cours.done():
            vu()
            self.question_en_cours.set_result(texte)
            return

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

        # Les photos en attente rejoignent la phrase, quel que soit le canal qui l'a portee.
        # On le fait ICI, au point unique ou un enonce part vraiment : plus haut, une dictee
        # retenue ou un ordre local aurait consomme les images sans les envoyer. La ligne
        # « toi » garde la phrase dite, pas les chemins : c'est ce qu'on a dit qu'on relit.
        envoi = self._avec_jointes(texte)

        vu()
        deja_occupe = self.worker.occupe
        # Relevé au départ, pas à l'arrivée : sans point de comparaison il n'y a pas d'écart.
        # Un tour ajouté à un tour déjà en cours ne réinitialise pas le départ, sinon la
        # mesure ne couvrirait qu'une partie du travail.
        if self.quota and not deja_occupe:
            self._quota_depart = self.quota.instantane()
        if self.conv:
            self.conv.tour_utilisateur(texte)
            # Garde pour le titre : c'est la premiere DEMANDE qui dit le sujet, pas la reponse.
            self._dernier_tour_utilisateur = texte
        if not deja_occupe:
            await self._ajuster_effort(texte)
        await self.worker.envoyer(envoi)
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
                await self._lire(self._debrief(charge))

    async def _attendre_son_tour(self):
        """Attendre qu'aucune autre conversation ne soit en train de lire.

        Deux voix sur les mêmes haut-parleurs ne s'additionnent pas, elles s'annulent : on ne
        comprend ni l'une ni l'autre. Celle qui arrive en second attend son tour.

        Le plafond existe pour la même raison que le bail périmé : un agent tué en pleine
        lecture ne libère rien, et le silence définitif des autres serait un prix absurde.
        """
        if not self.inscription:
            return
        limite = time.monotonic() + 90
        annonce = False
        while time.monotonic() < limite:
            autre = self.inscription.parole_ailleurs()
            if autre is None:
                return
            if not annonce:
                annonce = True
                self._voir("attente", texte=f"une autre conversation lit sa réponse "
                                            f"(session {autre}) — j'attends mon tour")
            await asyncio.sleep(0.4)
        log.warning("attente de parole abandonnée après 90 s")

    async def _lire(self, source):
        """Lire une réponse : attendre son tour, prendre la parole, la rendre à la fin.

        La poignée est conservée pour que la page puisse couper CETTE lecture — et non
        « la parole en général » — et pour pouvoir la relancer si elle a été coupée.
        """
        await self._attendre_son_tour()
        if self.inscription:
            self.inscription.prendre_parole()
        poignee = await self.sess.say(source, allow_interruptions=True)
        self._lecture = poignee

        def rendre(_):
            if self.inscription:
                self.inscription.liberer_parole()
            if self._lecture is poignee:
                self._lecture = None
            self._voir("lecture", actif=False, id=self._id_parole)

        # Rendue par rappel, pas en attendant ici : la pompe doit rester libre de traiter
        # l'événement suivant pendant que la voix parle.
        poignee.add_done_callback(rendre)
        self._voir("lecture", actif=True, id=self._id_parole)
        return poignee

    def couper_lecture(self) -> bool:
        """Couper la lecture en cours, et elle seule."""
        if self._lecture is not None and not self._lecture.done():
            self._lecture.interrupt()
            return True
        return False

    async def relire(self, cle: str | None = None) -> bool:
        """Relire une réponse déjà dite. Sans clé, la dernière.

        Utile quand la lecture a été coupée — par une interruption, par un bruit, par un
        clic — parce que le texte existe toujours et qu'une deuxième synthèse ne coûte que
        des caractères, là où refaire le tour coûterait le travail entier.
        """
        texte = (self._paroles.get(cle) if cle else None) or self._dernier_debrief
        if not texte:
            return False
        await self._lire(texte)
        return True

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

    def appliquer_micro(self, publier: bool = True) -> bool:
        """Aligne l'entree audio sur « voulu ET autorisé ». Renvoie l'etat effectif.

        Un seul endroit calcule l'etat reel du micro. Deux endroits qui l'ecrivent finissent
        toujours par se contredire, et le symptome serait le pire possible : croire qu'on est
        ecoute alors qu'on ne l'est pas, ou l'inverse.
        """
        bail = self.inscription.detient_micro() if self.inscription else True
        effectif = self.micro_voulu and bail
        try:
            self.sess.input.set_audio_enabled(effectif)
        except Exception:
            log.debug("micro non applicable pour l'instant", exc_info=True)
            return effectif
        # Le quota ne se consomme que micro ouvert : c'est la seule mesure honnete de ce qui
        # part vers le fournisseur. La mesurer ici plutot qu'a cote garantit qu'elle suit
        # l'etat REEL du micro, pas l'intention.
        consommation.micro(effectif)
        # Couper le micro vaut « j'ai fini de parler ».
        #
        # En manuel, la fin de tour vient du VAD. Si le micro se coupe PENDANT la parole, ce
        # signal peut ne jamais arriver : le texte deja transcrit resterait alors dans la
        # barre sans decompte et sans envoi, et rien n'expliquerait pourquoi. La coupure
        # tranche donc elle-meme — ouvrir_fenetre decidera d'attendre, d'envoyer ou de
        # retenir, exactement comme un silence normal.
        if config.TOUR_MANUEL and not effectif and self._dit.strip():
            self.ouvrir_fenetre()
        # Fermer le micro solde le chrono sur disque : c'est le moment ou le chiffre change
        # pour de bon, donc celui ou la page doit l'apprendre.
        if not effectif and self._sur_conso:
            self._sur_conso()
        if publier:
            self._voir("micro", actif=effectif, voulu=self.micro_voulu, bail=bail)
        return effectif

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
        self.micro_voulu = False
        self.appliquer_micro()
        suite = "la réflexion continue" if self.worker.occupe else "j'attends tes instructions"
        return f"le micro est bien coupé, {suite}. Touche m ou le bouton pour le rouvrir."

    async def _debrief(self, journal_):
        """Feeds TTS and the page from the same stream, so what you read is what you hear."""
        morceaux = []
        # Un identifiant par réponse : sans lui, « relire » ne pourrait viser que la dernière,
        # alors que ce qu'on veut relire est souvent celle d'avant.
        cle = f"p{len(self._paroles) + 1}"
        self._id_parole = cle
        async for bout in self.porte_parole.dire_flux(journal_):
            morceaux.append(bout)
            self._voir("voix", texte=bout, suite=True, id=cle)
            yield bout
        # La raison d'un arret anormal est dite EN DUR, apres le debrief : passee par le
        # porte-parole elle aurait ete reformulee, et surtout elle pouvait sauter — c'est un
        # modele qui reecrit, et une phrase sur deux est deja ecartee par la verification des
        # faits. Or c'est la seule information qui explique un travail interrompu ; la perdre
        # ramene exactement au symptome de depart, « il s'arrete sans raison ».
        arret = getattr(journal_, "pourquoi_arrete", lambda: None)()
        if arret:
            phrase = (" " if morceaux else "") + arret + "."
            morceaux.append(phrase)
            self._voir("voix", texte=phrase, suite=True, id=cle)
            yield phrase
        # Kept so "répète" can say it again without paying for a second rewrite.
        self._dernier_debrief = "".join(morceaux).strip() or None
        if self._dernier_debrief:
            self._paroles[cle] = self._dernier_debrief
            # Bornée : garder tout l'historique parlé d'une session de deux jours n'aurait
            # aucun usage et grossirait sans fin.
            if len(self._paroles) > 40:
                for vieux in list(self._paroles)[:-40]:
                    self._paroles.pop(vieux, None)
            # La ligne du flux reçoit ses boutons quand le texte complet existe.
            self._voir("parole_fin", id=cle, mots=len(self._dernier_debrief.split()))
        if self.conv:
            # Le titre, apres le PREMIER echange et une seule fois. En tache de fond : un
            # appel de modele, meme sur Haiku, n'a pas a retarder la parole qui suit.
            if self.conv.tours <= 2 and not self.conv.titre:
                asyncio.create_task(self._titrer(self._dernier_tour_utilisateur or ""))
            self.conv.tour_claude(self._dernier_debrief or "", outils=journal_lignes(journal_),
                                  jetons=getattr(journal_, "jetons", None),
                                  duree=getattr(journal_, "duree_s", None))

    async def _ajuster_effort(self, texte: str) -> None:
        """Mettre l'effort au niveau que CETTE demande merite, avant de la transmettre.

        Deux garde-fous, et ils disent ce qu'on respecte. « ultracode » n'est jamais touche :
        il se demande a la main, il coute dix fois un tour normal, et l'automatique n'a pas a
        defaire un choix pareil. Un niveau choisi a la main non plus — c'est une decision, et
        une decision qui ne tient pas jusqu'au message suivant n'en est pas une.

        Le changement reconstruit le client, ce qui prend une demi-seconde et PRESERVE le
        cache de prompt (mesure : voir Worker.changer_effort). C'est ce qui le rend possible
        a chaque tour ; si la reconstruction coutait le contexte, il faudrait s'en abstenir.
        """
        if not config.EFFORT_AUTO or self._effort_manuel:
            return
        # Un worker qui n'expose ni niveau ni moyen d'en changer n'a rien a ajuster. Le cas
        # existe : les tests du tableau branchent un double qui ne joue que le strict
        # necessaire, et l'ajustement ne doit pas etre ce qui les fait tomber.
        avant = getattr(self.worker, "effort", None)
        if not avant or not callable(getattr(self.worker, "changer_effort", None)):
            return
        if avant in complexite.JAMAIS_AUTO:
            return
        choix = complexite.evaluer(texte)
        if choix.niveau == avant:
            return
        libelle = await self.worker.changer_effort(choix.niveau)
        if not libelle:
            return          # refuse : un tour tourne encore, on garde le niveau courant
        self._voir("effort", cle=choix.niveau, niveau=choix.niveau,
                   libelle=libelle, auto=True, pourquoi=choix.pourquoi,
                   de=complexite.LIBELLES.get(avant, avant),
                   vers=complexite.LIBELLES.get(choix.niveau, choix.niveau))

    async def poser_question(self, questions: list) -> str | None:
        """Claude a une question. On la dit, on attend la reponse, on la lui rend.

        C'est le pendant manquant de `demander_permission` : le worker savait demander
        l'autorisation de faire quelque chose, mais pas demander ce qu'on voulait. L'outil
        rendait donc « the user did not answer » et Claude expliquait poliment que la session
        etait non-interactive — devant quelqu'un qui l'ecoutait.

        La reponse est rendue en texte libre. Les options proposees sont dites, mais rien
        n'oblige a en choisir une : le CLI accepte une phrase, et a l'oral c'est le mode
        naturel — on repond « la deuxieme, mais garde l'ancien » bien plus souvent qu'on ne
        recite un intitule."""
        boucle = asyncio.get_running_loop()
        self.question_en_cours = boucle.create_future()

        morceaux, pour_la_page = [], []
        for q in questions:
            libelle_q = str(q.get("question") or "").strip()
            options = [str(o.get("label") or "").strip()
                       for o in (q.get("options") or []) if o.get("label")]
            if not libelle_q:
                continue
            morceaux.append(libelle_q + (" " + " ou ".join(options) + " ?" if options else ""))
            pour_la_page.append({"question": libelle_q, "options": options,
                                 "entete": str(q.get("header") or "").strip()})
        if not morceaux:
            self.question_en_cours = None
            return None

        dite = " ".join(morceaux)
        # Le meme identifiant sur les deux publications : c'est ce qui permet a la reponse
        # d'eteindre l'attente de SA question au lieu d'empiler une seconde ligne qui tourne.
        marque = f"q{id(self.question_en_cours):x}"
        self._voir("question", id=marque, texte=dite, questions=pour_la_page)
        await self.sess.say(self._parler(dite), allow_interruptions=True)
        try:
            # Large : une question peut arriver pendant qu'on regarde ailleurs, et le cout
            # d'attendre est nul alors que celui d'abandonner est un tour perdu. Au-dela,
            # l'outil dit lui-meme qu'il n'a pas eu de reponse et Claude reprend la main.
            reponse = await asyncio.wait_for(self.question_en_cours, timeout=300)
        except asyncio.TimeoutError:
            reponse = None
        finally:
            self.question_en_cours = None
        self._voir("question", id=marque, texte=dite, questions=pour_la_page,
                   reponse=reponse or "(pas de réponse)")
        return reponse

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


def _stt(vad):
    """La chaîne de reconnaissance, assemblée depuis la table des moteurs.

    L'ordre par défaut suit une logique de budget : les quotas MENSUELS d'abord — ils
    reviennent, autant les dépenser — puis les CRÉDITS uniques, qu'on garde pour quand les
    mensuels sont épuisés, puis le local, illimité mais lent.

    `FallbackAdapter` bascule tout seul quand un moteur échoue. C'est exactement le trou qui
    avait fait perdre une session entière : le quota Azure s'épuise, chaque phrase rate, et
    rien ne prend le relais.

    Un moteur dont la clé manque est retiré de la chaîne plutôt que de faire échouer la
    première phrase. Le local ferme toujours la marche : c'est le seul qui ne peut pas manquer
    de crédit, donc le seul qui garantit qu'on ne devienne jamais sourd.
    """
    chaine = moteurs_stt.chaine()
    log.info("reconnaissance vocale : %s", moteurs_stt.resume())
    if chaine == ["local"]:
        log.warning("aucune clé de reconnaissance : moteur local seul, "
                    "compter 4 à 5 s par phrase. Vois la section Prérequis du README.")

    moteurs = []
    for cle in chaine:
        try:
            moteurs.append(moteurs_stt.construire(cle, vad))
        except Exception:
            # Un moteur qui refuse de se construire ne doit pas emporter les autres.
            log.warning("moteur « %s » inutilisable, retiré de la chaîne", cle, exc_info=True)
    if not moteurs:
        moteurs = [moteurs_stt.construire("local", vad)]
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
        "des voix Piper fr_FR-* ici (voir README)."
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
        pluriel = "s" if len(deja) > 1 else ""
        alerte = (f"{len(deja)} autre{pluriel} conversation{pluriel} en cours ({detail}) — "
                  f"le quota est partagé. « vvstop » les ferme.")
        log.warning("%s", alerte)
        # Un AVERTISSEMENT, pas une erreur. Travailler sur deux projets a la fois est un
        # usage normal, pas une panne : le micro a son bail, personne ne se casse, et rien
        # n'a echoue. En rouge, cette ligne apprenait seulement a ignorer le rouge — et une
        # vraie erreur se serait perdue au milieu.
        tableau.publier("log", niveau="WARNING", source="session", texte=alerte)

    # Le registre des conversations parallèles, et le bail sur le micro. Le micro est la
    # seule ressource vraiment exclusive : deux agents qui écoutent transcrivent la même
    # phrase, l'envoient chacun à son Claude, et consomment deux fois la même fenêtre.
    #
    # Le bail se PREND au démarrage : lancer une conversation, c'est vouloir lui parler. Les
    # autres continuent leur travail, elles arrêtent seulement d'écouter.
    inscription = pupitre.Inscription(
        projet=Path(config.WORKDIR).name, chemin=config.WORKDIR, port=tableau.port)
    inscription.prendre_micro()

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

    async def on_question(questions: list) -> str | None:
        return await agent.poser_question(questions) if agent else None

    worker = Worker(on_permission=on_permission, on_question=on_question,
                    tableau=tableau, conversation=conv)
    await worker.start()
    agent = Voix(worker, porte_parole, tableau, quota, conv)

    valeurs = config.resume()
    alerte = valeurs.pop("_alerte", "")
    valeurs["quota"] = quota.resume()
    valeurs["reconnaissance"] = (
        f"{config.STT_ENGINE} · {config.LANGUAGE} · {len(config.phrase_list())} termes biaisés"
        + (f" · repli local {config.STT_LOCAL_MODELE}"
           if config.STT_ENGINE == "auto" else ""))
    # Le port REEL, pas celui demande : avec plusieurs conversations en parallele la bascule
    # de port devient la regle, et un panneau qui annonce une adresse ou personne ne repond
    # est pire que pas d'adresse du tout.
    valeurs["tableau"] = f"http://127.0.0.1:{tableau.port}"
    valeurs["reconnaissance"] = (f"{moteurs_stt.resume()} · {config.LANGUAGE} · "
                                 f"{len(config.phrase_list())} termes biaisés")
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
            f"↑ historique rechargé — {len(evenements)} lignes"
            + (f", {ecartes} plus anciennes écartées" if ecartes else "")))
        # Par lots, avec une pause : un rejeu complet fait plus de mille lignes, et tout
        # pousser d'un trait remplit la file du client jusqu'à ce qu'elle jette ses plus
        # anciens messages — on perdrait le début de la conversation qu'on vient de recharger.
        for i, e in enumerate(evenements):
            tableau.publier(e.pop("genre"), passe=True, **e)
            if i % 100 == 99:
                await asyncio.sleep(0.05)
        tableau.publier("reprise", texte="↓ ici commence le direct")
        log.info("historique rejoué : %d lignes", len(evenements))

    # La session qu'on a reellement reprise — demandee a la main OU choisie toute seule.
    # Ne tester que config.REPRENDRE laissait la page VIDE sur une reprise automatique : le
    # contexte de Claude etait complet, mais le flux ne montrait rien, ce qui donnait
    # exactement l'impression d'une conversation neuve qu'on cherchait a corriger.
    reprise_initiale = config.REPRENDRE or (worker.reprise or {}).get("session_id")
    if reprise_initiale:
        # En tâche de fond : lire huit cents messages ne doit pas retarder le premier mot.
        asyncio.create_task(_rejouer_historique(reprise_initiale))

    # Quel moteur transcrit, et lesquels sont disponibles. Publie tot : c'est la premiere
    # question qu'on se pose quand une transcription est mauvaise.
    tableau.publier("moteurs_stt", liste=moteurs_stt.inventaire(),
                    chaine=moteurs_stt.chaine(), actif=moteurs_stt.chaine()[0],
                    direct=moteurs_stt.tete().direct,
                    impose=bool(os.environ.get("VOIX_STT", "").strip()))
    # Un moteur sans resultats intermediaires ne rend son texte QU'A LA FIN, apres que le tour
    # est parti. Consequence concrete : rien ne s'ecrit pendant qu'on parle, le texte semble
    # apparaitre sans raison, et « retenir » ne sert a rien puisqu'il n'y a rien a relire au
    # moment de decider. Le dire au lancement plutot que de laisser chercher.
    if not moteurs_stt.tete().direct:
        tableau.publier("erreur", texte=(
            f"{moteurs_stt.tete().libelle} ne transcrit qu'à la fin de chaque phrase : "
            "aucun texte ne s'écrira pendant que tu parles, et « retenir » n'aura rien à "
            "relire. Une clé Speechmatics ou Gladia (gratuites, renouvelées) rétablit "
            "l'écriture en direct — clic sur la pastille du moteur."))
    tableau.publier("delais",
                    paliers=[{"s": p} for p in config.ECOUTE_PALIERS],
                    actuel=config.ECOUTE_MIN, plafond=config.plafond_ecoute())
    tableau.publier("efforts",
                    liste=[{"cle": k, "libelle": l} for k, (_, l) in config.EFFORTS.items()],
                    actuel=config.WORKER_EFFORT)

    vad = silero.VAD.load()
    moteur_stt = _stt(vad)
    # Le contexte du job LiveKit, capture ICI, la ou il est certain d'exister. Les moteurs de
    # reconnaissance prennent leur session HTTP dans une variable de contexte que le job
    # pose avant d'appeler ce point d'entree ; une requete arrivant par la route web tourne
    # dans une tache creee par aiohttp, et rien ne garantit qu'elle en herite. Verifie : hors
    # de ce contexte, Deepgram et Gladia refusent net (« outside of a job context »). On
    # rejoue donc ce contexte autour de chaque transcription de fichier. Si la tache en
    # heritait deja, c'est sans effet ; sinon, c'est ce qui la fait marcher.
    contexte_job = contextvars.copy_context()

    # Une bascule de moteur doit s'annoncer. Sans ça, la session où le quota Azure s'est
    # épuisé n'a laissé qu'un mur d'erreurs identiques et aucune ligne disant ce qui prenait
    # le relais — impossible de comprendre pourquoi tout était devenu lent.
    # Qui transcrit MAINTENANT. Partage entre la bascule de repli, le compteur de quota et
    # le gestionnaire d'erreurs : trois endroits qui doivent nommer le meme moteur, sinon le
    # temps est impute a l'un et l'epuisement constate sur l'autre.
    actif = {"cle": (moteurs_stt.chaine() or [None])[0]}
    consommation.moteur_actif(actif["cle"])

    def _publier_conso():
        tableau.publier("consommation", moteurs=consommation.etat(moteurs_stt.MOTEURS))

    _publier_conso()
    agent._sur_conso = _publier_conso

    async def suivre_conso():
        """Republier pendant que le micro est ouvert.

        Le compteur n'ecrit sur disque qu'a la fermeture du micro — ecrire a chaque trame
        serait absurde. Mais l'affichage ne se rafraichissait qu'au demarrage et lors d'une
        bascule de moteur : on parlait dix minutes et le chiffre ne bougeait pas d'une
        seconde. Le compteur mesurait bien, c'est l'ecart entre ce qui est MESURE et ce qui
        est MONTRE qui faisait croire a une panne.

        Toutes les vingt secondes, et seulement micro ouvert : au repos rien ne change, donc
        republier ne servirait qu'a remplir le flux.
        """
        while True:
            await asyncio.sleep(20)
            if consommation.en_cours()[0]:
                _publier_conso()

    if isinstance(moteur_stt, stt_api.FallbackAdapter):
        # Le label est le chemin complet du module (livekit.plugins.azure.stt.STT), pas le
        # nom du plugin : on cherche donc le segment, pas une égalité.
        tombes: set[str] = set()
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
                restants = [moteurs_stt.PAR_CLE[c].libelle
                            for c in moteurs_stt.chaine()
                            if moteurs_stt.PAR_CLE[c].libelle != nom]
                suite = restants[0] if restants else "plus rien"
                texte = (f"reconnaissance : {nom} est tombé, bascule sur {suite} "
                         f"(nouvelle tentative en arrière-plan)")
            log.warning("%s", texte)
            tableau.publier("erreur" if not ev.available else "log",
                            niveau="WARNING", source="stt", texte=texte)
            # Le moteur ACTIF, recalcule : c'est le premier de la chaine encore debout.
            # Sans ca le tableau continuerait d'annoncer celui du demarrage.
            tombes.discard(nom) if ev.available else tombes.add(nom)
            debout = [c for c in moteurs_stt.chaine()
                      if moteurs_stt.PAR_CLE[c].libelle not in tombes]
            actif["cle"] = debout[0] if debout else None
            # Decoupe la mesure a la bascule : sans ca, le temps consomme par Azure avant sa
            # chute serait impute au moteur qui prend le relais.
            consommation.moteur_actif(actif["cle"])
            if ev.available:
                for c, m in moteurs_stt.PAR_CLE.items():
                    if m.libelle == nom:
                        consommation.oublier_epuise(c)
            tableau.publier("moteur_actif",
                            cle=debout[0] if debout else None,
                            libelle=(moteurs_stt.PAR_CLE[debout[0]].libelle
                                     if debout else "aucun"),
                            tombes=sorted(tombes))
            _publier_conso()

    session = AgentSession(
        stt=moteur_stt,
        tts=_tts(),
        vad=vad,
        turn_handling=TurnHandlingOptions(
            # v1-mini runs entirely on this machine. The v1 detector, and the adaptive
            # interruption detector below, are LiveKit Cloud inference: they need
            # LIVEKIT_API_KEY and they send audio off the machine.
            # « manual » : le VAD continue de signaler debut et fin de parole, mais
            # LiveKit ne commet plus le tour — c'est Voix.ouvrir_fenetre qui decide. Voir
            # config.TOUR_MANUEL pour le pourquoi : une seule horloge, celle qu'on affiche.
            turn_detection=("manual" if config.TOUR_MANUEL
                            else TurnDetector(version=config.DETECTEUR_TOUR)),
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
        # Rendre le bail : sans ça, fermer la conversation qui écoutait laisserait les
        # autres sourdes jusqu'à ce que l'une d'elles constate la mort du détenteur.
        try:
            inscription.liberer()
        except Exception:
            log.debug("libération du pupitre", exc_info=True)
        await tableau.arreter()

    ctx.add_shutdown_callback(fermer)

    # La veille sur l'oreille elle-meme. Separee de la veille du micro (qui coupe un micro
    # oublie) : celle-ci ne coupe rien, elle verifie que ce qui est ouvert ecoute vraiment.
    # Voir sentinelle.py pour la panne qu'elle rattrape — une pompe de reconnaissance qui
    # s'arrete sans erreur et ne redemarre jamais, d'ou « il faut relancer l'application ».
    def _alerte_oreille(texte: str, grave: bool = False):
        tableau.publier("erreur" if grave else "log", niveau="WARNING", source="stt",
                        texte=texte)
        # Le dire a voix haute aussi : au moment ou ça arrive, on parle — donc on ne regarde
        # pas l'ecran. C'est tout le probleme qu'on corrige.
        if grave:
            try:
                session.say(texte, add_to_chat_ctx=False)
            except Exception:
                log.debug("annonce vocale de la sentinelle impossible", exc_info=True)

    oreille = sentinelle.Sentinelle(
        session,
        micro_ouvert=lambda: bool(session.input.audio_enabled),
        dire=_alerte_oreille,
    )
    agent.oreille = oreille

    # Everything the session hears or decides, mirrored to the page.
    @session.on("user_input_transcribed")
    def _entendu(ev):
        # Du direct uniquement : ces événements alimentent le texte qui s'écrit dans la barre
        # à mesure qu'il est reconnu. La ligne définitive du flux est publiée par llm_node,
        # qui est le seul endroit connaissant le texte consolidé du tour.
        # Les résultats intermédiaires arrivent entiers et grandissants : ils remplacent.
        tableau.publier("partiel", texte=ev.transcript, final=bool(ev.is_final))
        # Preuve que la chaine entiere fonctionne — meme un partiel la donne, et c'est tant
        # mieux : il arrive bien avant la finale, donc la sentinelle se tait plus tot.
        oreille.transcrit()
        if ev.is_final:
            if not agent.noter_transcription(ev.transcript, True):
                # Dire pourquoi plutot que de jeter en silence : c'est ce silence qui rendait
                # le defaut incomprehensible quand il se produisait.
                tableau.publier("log", niveau="DEBUG", source="stt",
                                texte=f"transcription en retard, déjà envoyée : "
                                      f"« {ev.transcript[:60]} »")
            tableau.publier("transcrit", actif=False)

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
            # On reparle : la fenetre se referme sans rien envoyer, et le texte deja transcrit
            # est CONSERVE. C'est ce qui rend les pauses de trois a cinq secondes possibles —
            # une pause pour reflechir ne coupe plus la phrase en deux messages.
            if config.TOUR_MANUEL:
                agent.fermer_fenetre(publier=False)
            # L'enonce s'ouvre ICI et nulle part ailleurs : c'est ce reperage qui permet
            # d'ignorer une transcription en retard, qui appartient au tour precedent.
            agent._dictee_ouverte = True
            agent._derniere_parole = time.monotonic()
            # Le detecteur tourne en local : quand il dit « quelqu'un parle », c'est vrai sans
            # dependre d'aucun reseau. C'est ce qui rend l'attente de transcription mesurable.
            oreille.parole_commence()
            tableau.publier("ecoute", actif=False, parle=True)
        elif str(ev.old_state) == "speaking":
            # La transcription commence ici. Avec un moteur sans texte en direct, c'est la
            # SEULE chose qui se passe pendant plusieurs secondes : sans ce signal, la page
            # semble figée puis du texte apparaît sans explication.
            tableau.publier("transcrit", actif=True, direct=moteurs_stt.tete().direct)
            # Et ici demarre le chronometre de la sentinelle : du texte doit arriver, sinon
            # cette phrase-la est partie dans le vide et il faut le dire.
            oreille.parole_finie()
            # Le silence commence ici, pas avant : c'est la seule transition qui compte.
            # Lu depuis la session, pas depuis la constante : après un réglage, la
            # constante ne dit plus la vérité et le décompte mentirait.
            if config.TOUR_MANUEL:
                # C'est ici que tout se decide, et ouvrir_fenetre publie elle-meme l'echeance
                # exacte : la page n'estime plus rien.
                agent.ouvrir_fenetre()
            else:
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
        # Un quota epuise n'est pas une panne : il ne reviendra pas avant le mois prochain.
        # Le constater ici, sur le moteur reellement actif, evite que le compteur continue
        # d'annoncer des heures restantes sur un palier vide — ce qu'Azure a fait pendant
        # des semaines.
        if consommation.ressemble_a_un_quota(brut) and actif["cle"]:
            consommation.constater_epuise(actif["cle"], brut)
            _publier_conso()
        if "Quota exceeded" in brut:
            cle = "quota-stt"
            suivant = [c for c in moteurs_stt.chaine() if c != "azure"]
            clair = ("quota Azure de transcription épuisé — le palier gratuit F0 s'arrête à "
                     "5 h/mois. Bascule sur "
                     + (moteurs_stt.PAR_CLE[suivant[0]].libelle if suivant else "rien"))
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
                    agent.micro_voulu = True
                    # Vouloir écouter ici, c'est vouloir écouter ICI : on reprend donc le
                    # bail. Sans ça, rouvrir le micro ne ferait rien de visible tant qu'une
                    # autre conversation le détient — un bouton sans effet, encore.
                    if inscription.prendre_micro():
                        publier_pupitre()
                    agent.appliquer_micro()
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
        elif nom == "nouvelle_conversation":
            if worker.occupe:
                tableau.publier("log", niveau="WARNING", source="session",
                                texte="pas maintenant : une tâche est en cours. "
                                      "« arrête » d'abord, ou attends la fin.")
            else:
                dit = await worker.nouvelle_conversation()
                if not dit:
                    tableau.publier("log", niveau="WARNING", source="session",
                                    texte="ouverture impossible — voir les erreurs ci-dessus")
                else:
                    # Clore l'ancien transcript AVANT d'en ouvrir un neuf : sans ça l'index
                    # le garde « en cours » indefiniment, et la liste affiche une conversation
                    # vivante qui ne l'est plus.
                    if agent.conv:
                        agent.conv.clore("nouvelle conversation ouverte")
                    # Un transcript neuf, sinon la conversation neuve s'ecrirait a la suite de
                    # l'ancienne dans le meme fichier et la liste les confondrait.
                    neuve = journal.Conversation(
                        projet=Path(config.WORKDIR).name,
                        modele=worker.modele, effort=worker.effort,
                        chemin=config.WORKDIR)
                    agent.conv = neuve
                    worker._conv = neuve
                    log.info("nouvelle conversation dans %s", neuve.fichier)
                    tableau.vider()
                    tableau.publier("vider")
                    tableau.publier("ordre", texte=dit + " Les précédentes restent reprenables.")
                    publier_conversations()
        elif nom == "conversations":
            # Rafraichi a l'ouverture du panneau plutot qu'en continu : la liste ne change
            # qu'entre deux lancements, et relire l'index a chaque seconde pour rien serait
            # du travail pur.
            publier_conversations()
        elif nom == "reprendre":
            sid = str(donnees.get("session_id") or "").strip()
            if worker.occupe:
                tableau.publier("log", niveau="WARNING", source="session",
                                texte="pas maintenant : une tâche est en cours. "
                                      "« arrête » d'abord, ou attends la fin.")
            else:
                dit = await worker.changer_conversation(sid)
                if dit:
                    # Vider AVANT de rejouer : sans ça les deux conversations s'empilent dans
                    # le meme flux et on ne sait plus laquelle on lit — ni a laquelle
                    # appartient un message qu'on relit trois jours plus tard.
                    tableau.vider()
                    tableau.publier("vider")
                    tableau.publier("ordre", texte=dit)
                    # Le contexte de Claude est complet des la bascule ; c'est la PAGE qui
                    # restait vide. On relit donc ce que Claude Code a ecrit sur disque, comme
                    # au demarrage d'une reprise — sinon reprendre depuis la page donnerait
                    # moins que reprendre depuis le terminal, pour la meme action.
                    asyncio.create_task(_rejouer_historique(sid))
                    publier_conversations()
                else:
                    tableau.publier("log", niveau="WARNING", source="session",
                                    texte="reprise impossible — voir les erreurs ci-dessus")
        elif nom == "jointes":
            # La page dit ce qui attend dans le composeur. On ne fait que le retenir : c'est
            # l'envoi qui consomme, pour que la photo suive la phrase quel que soit le canal
            # — clavier, micro du PC, micro du telephone.
            agent._jointes = [c for c in (donnees.get("chemins") or [])
                              if isinstance(c, str) and c][:12]
        elif nom == "barre_vide":
            # La barre a ete videe a la main : plus rien n'attend, la retenue collante tombe.
            agent._retenu_en_attente = False
            # Et l'etat le dit, pour la prochaine page qui se connectera : sinon elle
            # retrouverait la dictee que l'on vient justement de jeter.
            tableau.publier("dictee", texte="", raison="", auto=False)
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
            # Une photo sans phrase est un message a part entiere : c'est « regarde ca », et
            # c'est _avec_jointes qui met les mots. Sortir ici sur un champ vide revenait a
            # jeter l'envoi sans rien dire.
            if not propos and not agent._jointes:
                return
            # Un enonce vide ne traverse pas la session : on met la phrase que la photo dit
            # toute seule, et _avec_jointes se charge des chemins.
            propos = propos or "regarde cette image."
            # llm_node publie la ligne « toi » pour tous les canaux ; on lui dit seulement
            # que celui-ci vient du clavier.
            agent.marquer_tape()
            # La barre part : plus rien n'attend de relecture, la retenue collante se libere.
            agent._retenu_en_attente = False
            tableau.publier("dictee", texte="", raison="", auto=False)
            # Et l'enonce vocal en cours est consomme lui aussi : ce qu'on vient d'envoyer au
            # clavier contient deja la dictee relue. Sans ça, une finale arrivant apres
            # l'envoi repartirait dans le tour suivant — le meme defaut par l'autre porte,
            # celle des « actions un peu bizarres ».
            agent._dictee_ouverte = False
            agent._dit = ""
            agent._tour_vocal = False
            session.generate_reply(user_input=propos, input_modality="text")
        elif nom == "vocal":
            # Un enregistrement venu du micro d'un autre appareil, deja decode en PCM par le
            # serveur. Deux temps : le transcrire ici, puis le faire entrer dans la conversation
            # comme une PAROLE — pas comme du texte tape. La difference est tout le systeme de
            # retenue : decompte avant envoi, « retenir », retenue d'office pendant que Claude
            # travaille. Un texte tape part tout de suite ; une phrase dite se relit d'abord.
            pcm = donnees.get("pcm") or b""
            taux = int(donnees.get("taux") or 16000)
            if not pcm:
                return {"erreur": "audio vide"}
            trame = rtc.AudioFrame(data=pcm, sample_rate=taux, num_channels=1,
                                   samples_per_channel=len(pcm) // 2)
            # Moteur par moteur, A LA MAIN, et surtout pas via le repli automatique : Azure ne
            # sait pas transcrire un fichier (il leve NotImplementedError), et le repli
            # prendrait cet echec pour une panne — il marquerait Azure indisponible pour le
            # micro du PC aussi, qui n'a rien demande. Ici un moteur qui refuse est simplement
            # passe, et l'etat de la chaine n'est pas touche.
            moteurs = (moteur_stt._stt_instances
                       if isinstance(moteur_stt, stt_api.FallbackAdapter) else [moteur_stt])
            texte, qui, capables = "", "", 0

            def nom_moteur(m) -> str:
                # « livekit.plugins.deepgram.stt.STT » se lit « deepgram » : ce nom est dit
                # a l'ecran, pas dans un journal.
                brut = getattr(m, "label", "") or type(m).__module__ or ""
                if "plugins." in brut:
                    return brut.split("plugins.", 1)[1].split(".", 1)[0]
                return (brut.rsplit(".", 1)[-1] or type(m).__name__).lower()

            tableau.publier("transcrit", actif=True, direct=False, source="téléphone")
            try:
                for m in moteurs:
                    etiquette = nom_moteur(m)
                    try:
                        # Dans le contexte du job, quelle que soit la tache d'ou l'on vient.
                        ev = await asyncio.wait_for(
                            asyncio.create_task(m.recognize(trame), context=contexte_job),
                            timeout=45)
                        capables += 1
                    except NotImplementedError:
                        continue
                    except Exception:
                        log.warning("vocal : %s a échoué", etiquette, exc_info=True)
                        continue
                    alternatives = getattr(ev, "alternatives", None) or []
                    texte = (alternatives[0].text if alternatives else "").strip()
                    qui = etiquette
                    if texte:
                        break
            finally:
                tableau.publier("transcrit", actif=False)
            secondes = round(float(donnees.get("secondes") or 0), 1)
            if not capables and not texte:
                # Aucun moteur de la chaine ne sait lire un fichier : Azure, Speechmatics et
                # Soniox ne font que du direct. Le dire precisement, sinon « rien compris »
                # ferait chercher un probleme de micro la ou il manque une cle.
                tableau.publier("log", niveau="WARNING", source="vocal",
                                texte="aucun moteur ne sait transcrire un fichier — il faut "
                                      "une clé Deepgram ou Gladia pour les vocaux du téléphone")
                return {"erreur": "aucun moteur ne sait transcrire un fichier : ajoute une clé "
                                  "Deepgram ou Gladia", "secondes": secondes}
            if not texte:
                # Muet, ou simplement incompris ? Les deux se corrigent differemment — l'un
                # demande de liberer le micro, l'autre de reparler plus pres — et « rien
                # compris » les confondait. Un micro mort rend du zero, echantillon apres
                # echantillon : la mesure coute un parcours de tableau et tranche la question.
                try:
                    import array
                    ech = array.array("h")
                    ech.frombytes(pcm[:len(pcm) // 2 * 2])
                    pic = max((abs(v) for v in ech), default=0)
                except Exception:
                    pic = -1
                # 300 sur 32768, soit environ 1 % : en dessous, meme un souffle manque.
                if 0 <= pic < 300:
                    tableau.publier("log", niveau="WARNING", source="vocal",
                                    texte=f"enregistrement muet ({secondes} s, pic {pic}) — "
                                          "le micro du téléphone n'a rien capté")
                    return {"erreur": "l'enregistrement est muet : le micro n'a rien capté. "
                                      "Une autre application le tenait peut-être",
                            "moteur": qui, "secondes": secondes, "muet": True}
                tableau.publier("log", niveau="WARNING", source="vocal",
                                texte=f"rien compris dans un enregistrement de {secondes} s"
                                      + (f" ({qui})" if qui else ""))
                return {"erreur": "rien compris dans l'enregistrement", "moteur": qui,
                        "secondes": secondes}
            # Le meme enchainement que le VAD quand quelqu'un parle dans le micro du PC —
            # voir le gestionnaire user_state_changed : la fenetre en cours se referme sans
            # envoyer, l'enonce s'ouvre, la page ouvre sa dictee. Puis la transcription
            # arrive, finale d'un coup, et ouvrir_fenetre decide comme d'habitude.
            if config.TOUR_MANUEL:
                agent.fermer_fenetre(publier=False)
            agent._dictee_ouverte = True
            agent._tour_vocal = True
            agent._derniere_parole = time.monotonic()
            tableau.publier("ecoute", actif=False, parle=True, source="téléphone")
            oreille.transcrit()
            tableau.publier("partiel", texte=texte, final=True, source="téléphone", moteur=qui)
            agent.noter_transcription(texte, final=True)
            agent.ouvrir_fenetre()
            return {"texte": texte, "moteur": qui, "secondes": secondes}
        elif nom == "effort":
            cle = str(donnees.get("cle") or "")
            libelle = await worker.changer_effort(cle)
            if libelle:
                # Choisi a la main : l'ajustement automatique se tait desormais. Une decision
                # que la demande suivante defait n'en serait pas une.
                agent._effort_manuel = True
                tableau.publier("ordre",
                                texte=f"effort réglé sur {libelle} — l'ajustement "
                                      f"automatique est suspendu")
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
        elif nom == "couper_lecture":
            if agent.couper_lecture():
                tableau.publier("ordre", texte="lecture coupée")
            else:
                tableau.publier("log", niveau="INFO", source="lecture",
                                texte="aucune lecture en cours")
        elif nom == "relire":
            cle = donnees.get("id")
            if not await agent.relire(cle if isinstance(cle, str) else None):
                tableau.publier("log", niveau="INFO", source="lecture",
                                texte="ce texte n'est plus en mémoire")
        elif nom == "moteur_stt":
            # Le moteur ne peut pas changer a chaud : AgentSession.stt est en lecture seule.
            # On enregistre donc le choix, applique au prochain lancement — et on le DIT,
            # plutot que de laisser croire a un effet immediat.
            ordre = donnees.get("ordre")
            if ordre is not None and not isinstance(ordre, str):
                return
            moteurs_stt.enregistrer_preference(ordre or None)
            nouvelle = moteurs_stt.chaine(ordre or None)
            tableau.publier("moteurs_stt", liste=moteurs_stt.inventaire(),
                            chaine=nouvelle, actif=nouvelle[0],
                            impose=bool(os.environ.get("VOIX_STT", "").strip()),
                            enregistre=True)
            tableau.publier("ordre", texte=(
                "reconnaissance : " + " → ".join(moteurs_stt.PAR_CLE[c].libelle
                                                 for c in nouvelle)
                + " — au prochain lancement (le moteur ne change pas à chaud)"))
        elif nom == "prendre_micro":
            # Prendre le micro pour CETTE conversation. Les autres se taisent d'elles-mêmes
            # en une seconde, par leur propre surveillance.
            agent.micro_voulu = True
            inscription.prendre_micro()
            agent.appliquer_micro()
            publier_pupitre()
            tableau.publier("ordre", texte="micro pris pour cette conversation")
        elif nom == "ceder_micro":
            cible = donnees.get("pid")
            if isinstance(cible, int) and pupitre.ceder_a(cible):
                agent.appliquer_micro()
                publier_pupitre()
                tableau.publier("ordre", texte=f"micro cédé à la session {cible}")
            else:
                tableau.publier("log", niveau="WARNING", source="pupitre",
                                texte="cette conversation n'existe plus")
                publier_pupitre()
        elif nom == "retenir_tour":
            # Rattraper le message pendant son décompte. On n'arme PAS le mode : c'est ce
            # tour-là qu'on veut relire, pas tous les suivants.
            agent._retenir_ce_tour = True
            tableau.publier("ordre", texte="ce message sera retenu dans la barre")
            # Agir MAINTENANT : on a cliqué « retenir », on n'attend pas la fin d'un décompte
            # dont on vient précisément de décider qu'il ne devait pas aboutir.
            if config.TOUR_MANUEL and agent._fenetre:
                agent.fermer_fenetre(publier=False)
                agent._oublier_tour()
                tableau.publier("dictee", texte=agent._dit.strip(), raison="tour", auto=False)
                agent._dit = ""
                agent._dictee_ouverte = False
                agent._retenir_ce_tour = False
        elif nom == "envoyer":
            # Contourner l'attente sans la raccourcir pour tout le monde : la fenêtre reste
            # longue pour pouvoir réfléchir à voix haute, et ce bouton dit « j'ai fini ».
            # Le meme chemin que l'echeance : un seul endroit commet, donc un seul
            # comportement a comprendre et a tester.
            agent._envoyer_maintenant("bouton")
        elif nom == "arreter":
            # The reason this button exists: with the microphone cut you can no longer say
            # "stop", so the page has to carry the stop.
            session.interrupt()
            await worker.interrompre()
            tableau.publier("arret", texte="arrêt demandé depuis le tableau")

    # Sans ça, tout appel venant du tableau passerait par Agent.session et son contrôle
    # d'activité — voir Voix.sess.
    agent.attacher_session(session)
    agent.inscription = inscription
    tableau.on_commande = commande
    tableau.publier("micro", actif=session.input.audio_enabled)

    def publier_conversations():
        """Les conversations de ce dossier, choisissables.

        Une par conversation REELLE et non par lancement — c'est la difference qui faisait
        croire a des conversations qui se multiplient. Chacune porte de quoi la reconnaitre
        sans deviner : ses tours, en combien de reprises, quand on y a parle pour la derniere
        fois, et surtout la derniere phrase qu'on y a dite. Devant quatre conversations sur le
        meme projet, « 22:49 » et « 22:55 » ne distinguent rien ; une phrase, si.
        """
        try:
            liste = []
            for c in journal.conversations(config.WORKDIR, sous_arbre=True, limite=40):
                liste.append({
                    "session_id": c.get("session_id"),
                    "titre": c.get("titre"),
                    "projet": c.get("projet"),
                    "chemin": c.get("chemin"),
                    "tours": c.get("tours", 0),
                    "reprises": c.get("reprises", 1),
                    "debut": c.get("debut"),
                    "maj": c.get("maj"),
                    "etat": c.get("etat"),
                    "modele": c.get("modele"),
                    "ici": bool(c.get("ici")),
                    "sous": c.get("sous"),
                    "apercu": journal.dernier_echange(c.get("fichier") or ""),
                })
            # `session_id` n'existe qu'une fois le SDK revenu avec un premier message, et
            # cette liste est publiee au demarrage — donc AVANT. La page recevait donc
            # « courante : aucune », ne trouvait la conversation dans aucune entree, et
            # concluait « nouvelle conversation » alors qu'on venait d'en reprendre une.
            # A defaut du confirme, on donne le DEMANDE : c'est la meilleure reponse qu'on
            # ait a cet instant, et si la reprise echoue l'evenement « session » corrigera.
            tableau.publier("conversations", liste=liste,
                            courante=worker.session_id
                                     or (worker.reprise or {}).get("session_id"),
                            dossier=config.WORKDIR)
        except Exception:
            log.debug("liste des conversations indisponible", exc_info=True)

    def publier_pupitre():
        """Qui existe, qui écoute, et où aller pour les rejoindre."""
        tableau.publier("pupitre", moi=inscription.pid,
                        sessions=[
                            {"pid": s["pid"], "projet": s.get("projet") or "?",
                             "chemin": s.get("chemin") or "", "port": s.get("port"),
                             "micro": s.get("micro", False), "depuis": s.get("depuis")}
                            for s in pupitre.sessions()])

    async def veiller_micro():
        """Couper le micro apres un silence prolonge, et le dire.

        Le cout qu'on evite est reel et invisible : micro ouvert, l'audio part en CONTINU vers
        le moteur de reconnaissance — chaque trame, sans filtre. Une pause dejeuner micro
        ouvert consomme une heure de quota sans qu'une seule phrase ait ete dite, et rien ne
        le signale avant que le palier soit vide.

        Deux precautions pour que ça ne devienne pas une gene :

        - le compteur repart des qu'une parole est DETECTEE, donc reflechir a voix haute avec
          des pauses de trente secondes ne declenche rien ;
        - la coupure est annoncee et le chemin du retour est nomme. Un micro qui se ferme sans
          rien dire serait pire que le probleme : on parlerait dans le vide sans comprendre.
        """
        if not config.MICRO_VEILLE_S:
            return
        while True:
            await asyncio.sleep(15)
            if not agent.micro_voulu:
                continue
            silence = time.monotonic() - agent._derniere_parole
            if silence < config.MICRO_VEILLE_S:
                continue
            agent.micro_voulu = False
            agent.appliquer_micro()
            minutes = int(silence // 60)
            tableau.publier("ordre",
                            texte=(f"micro mis en veille après {minutes} min de silence — "
                                   f"il consommait du quota pour rien. "
                                   f"Touche m ou le bouton pour le rouvrir."))
            log.info("micro en veille apres %d min de silence", minutes)
            agent._derniere_parole = time.monotonic()

    async def surveiller_pupitre():
        """Suivre le bail, et le registre.

        Un fichier plutôt qu'un signal : l'état survit au redémarrage d'un agent, et un agent
        qui n'a pas encore démarré peut quand même être découvert par les autres. Une seconde
        de latence est invisible à l'usage et coûte moins qu'une surveillance d'inotify à
        maintenir.
        """
        dernier = None
        while True:
            try:
                bail = inscription.detient_micro()
                etat = (bail, tuple(sorted(
                    (s["pid"], s.get("micro", False)) for s in pupitre.sessions())))
                if etat != dernier:
                    dernier = etat
                    agent.appliquer_micro()
                    publier_pupitre()
                    if not bail:
                        log.info("micro cédé à une autre conversation")
            except Exception:
                log.debug("surveillance du pupitre", exc_info=True)
            await asyncio.sleep(1.0)

    publier_pupitre()
    # Apres la definition, forcement : appelee plus haut elle levait un NameError au
    # demarrage — une fonction imbriquee n'existe qu'une fois son « def » execute.
    publier_conversations()
    asyncio.create_task(surveiller_pupitre())
    asyncio.create_task(veiller_micro())
    oreille.demarrer()
    asyncio.create_task(suivre_conso())
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
    # PAS de basicConfig ici, et c'est la correction d'un vrai defaut d'affichage : LiveKit
    # installe son propre handler sur la racine (cli/log.py, setup_logging → root.addHandler)
    # et ne retire jamais ceux qui existent deja. Notre handler restait donc en place et CHAQUE
    # ligne s'imprimait deux fois — une fois brute, une fois au format colore de LiveKit. Le
    # demarrage faisait le double de sa longueur, et une trace d'erreur apparaissait deux fois
    # de suite, ce qui donne l'impression que le probleme s'est produit deux fois.
    #
    # LiveKit met la racine en DEBUG en mode console : nos lignes « voix » s'affichent donc,
    # et au bon format. Ce qui est journalise AVANT ce reglage — les avertissements de
    # prechargement des plugins — passe par le handler de dernier recours de Python, qui
    # imprime les WARNING sur stderr : rien n'est perdu.
    logging.getLogger("voix").setLevel(logging.INFO)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
