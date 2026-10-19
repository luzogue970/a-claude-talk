"""The one Claude Code session that owns all the state.

The old system talked to Claude through its GUI: xdotool typed into the panel and a
speaker-output probe stood in for "is it done yet". Everything painful descended from
that blindness. Here the session is a library call, so the voice layer sees the real
events: which tools ran, what they touched, when a permission is needed, when the turn
actually ended. The spoken debrief is built from that journal, not from guessing at the
last paragraph of text.
"""

import asyncio
import json
import re
import time
import logging
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

import config

log = logging.getLogger("voix.worker")

# Le SDK previent, a chaque construction de client, que `can_use_tool` ne sera pas consulte en
# bypassPermissions. C'est exact pour les permissions, et c'est le mode qu'on a choisi — mais
# on fournit le rappel pour AskUserQuestion, qui lui y passe quand meme. L'avertissement est
# donc juste dans sa lettre et faux dans ce qu'il laisse croire ici, et il tombe a chaque
# changement de modele ou d'effort. On le tait, en disant pourquoi plutot qu'en le subissant.
warnings.filterwarnings(
    "ignore", category=getattr(__import__("claude_agent_sdk.types", fromlist=["types"]),
                               "CanUseToolShadowedWarning", Warning))

# Second filter, under the CLI's own. Read-only tools never ask: a spoken approval for
# every `grep` would make the voice channel unusable. Under acceptEdits the CLI already lets
# in-project writes through without consulting the callback, so what actually reaches it is
# the boundary crossings — and those are worth a question.
AUTO_ALLOW = {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite", "NotebookRead"}


# Les preludes d'une commande shell : ils ne disent rien de ce qu'elle FAIT. Deux familles,
# et la difference compte. `cd /un/dossier` emporte son argument — sans ça on resume
# « cd /un/dossier && npm test » par « dossier », ce qui est encore pire que par « cd ».
# `sudo npm test` n'en emporte aucun : ce qui suit EST la commande.
_PRELUDES_AVEC_ARG = {"cd", "source", ".", "pushd"}
_PRELUDES_SEULS = {"sudo", "env", "time", "nohup", "exec", "command"}
_LONGUEUR = 64          # au-dela, on tronque : la ligne doit rester une ligne

_ASSIGNATION = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")


def _verbe_shell(commande: str) -> str:
    """Ce qu'une commande shell fait vraiment, en quelques mots.

    On coupe sur les ENCHAINEMENTS (`&&`, `||`, `;`) et on garde le premier morceau qui ne
    soit pas un prelude. Pas sur les tubes : `grep -rn X voix/ | head -20` dit son intention
    entiere, la couper la mutilerait. Prendre le dernier morceau serait tentant — c'est
    souvent lui qui compte — mais une chaine se termine aussi bien par un `| tail -3`.
    """
    for morceau in re.split(r"&&|\|\||;|\n", commande):
        mots = morceau.strip().split()
        while mots:
            tete = mots[0]
            if tete in _PRELUDES_AVEC_ARG:
                del mots[:2]                       # le prelude ET ce qu'il vise
            elif tete in _PRELUDES_SEULS or _ASSIGNATION.match(tete) or tete == "export":
                mots.pop(0)
            else:
                break
        if mots:
            # Le binaire sans son chemin : « .venv/bin/python » se lit « python ».
            mots[0] = mots[0].rsplit("/", 1)[-1]
            return " ".join(mots)
    return commande.strip()


def _cible(name: str, args: dict) -> str:
    """Ce qu'un appel d'outil fait, en une ligne lisible ET prononcable.

    L'ancienne version rendait « cd » pour toute commande commencant par un changement de
    dossier, et un motif brut pour les recherches. Au tableau comme a l'oral, ça donnait une
    suite de « Bash cd », « Grep def », « Bash cd » : on voyait que ça travaillait, jamais sur
    quoi. Or le CLI FOURNIT une description en clair pour les commandes shell et les
    delegations — elle etait la, en derniere position, donc jamais atteinte.

    L'ordre compte, et il va du plus parlant au plus brut : la description ecrite par Claude,
    puis ce que l'outil vise, puis un repli. La longueur est bornee : cette ligne partage
    l'ecran avec la conversation, et elle est lue a voix haute dans le bilan du tour.
    """
    def court(texte: str) -> str:
        texte = " ".join(str(texte).split())
        return texte if len(texte) <= _LONGUEUR else texte[:_LONGUEUR - 1].rstrip() + "…"

    # 1. La description, quand le CLI en fournit une — commandes shell, delegations. C'est
    #    une phrase ecrite pour etre lue, on ne fera jamais mieux nous-memes.
    if desc := args.get("description"):
        return court(desc)

    # 2. Une recherche : le motif ET l'endroit. « def _cible » seul ne dit pas ou l'on
    #    cherche, et c'est souvent l'endroit qui renseigne sur l'intention.
    if name in ("Grep", "Glob") and (motif := args.get("pattern")):
        ou = args.get("path") or args.get("glob") or ""
        ou = Path(str(ou)).name if ou and "/" in str(ou) else str(ou)
        return court(f"{motif} dans {ou}" if ou and ou != "." else str(motif))

    # 3. Un fichier : son nom, jamais son chemin complet — personne ne veut entendre
    #    s'epeler trois niveaux de dossiers. La portion lue, quand elle est precisee, dit
    #    la difference entre relire un fichier et en verifier deux lignes.
    if chemin := args.get("file_path") or args.get("path") or args.get("notebook_path"):
        nom = Path(str(chemin)).name
        if (debut := args.get("offset")) and (combien := args.get("limit")):
            return court(f"{nom}, lignes {debut} à {int(debut) + int(combien)}")
        return court(nom)

    # 4. Une commande shell sans description : on cherche ce qu'elle fait vraiment.
    if cmd := args.get("command"):
        return court(_verbe_shell(str(cmd)))

    if requete := args.get("query"):
        return court(requete)
    if motif := args.get("pattern"):
        return court(motif)
    if url := args.get("url"):
        brut = str(url)
        return court(brut.split("/")[2] if "://" in brut else brut)
    if prompt := args.get("prompt"):
        return court(prompt)
    return ""


def _jetons(model_usage) -> int | None:
    """Every token the turn moved, cache included. On a company seat the dollar figure is
    noise; what is scarce is the rate-limit window, and that is driven by tokens."""
    if not isinstance(model_usage, dict):
        return None
    total = 0
    for detail in model_usage.values():
        if not isinstance(detail, dict):
            continue
        for champ in ("inputTokens", "outputTokens",
                      "cacheReadInputTokens", "cacheCreationInputTokens"):
            total += int(detail.get(champ) or 0)
    return total or None


VERBES = {
    "Read": "lu", "Glob": "cherché", "Grep": "cherché dans", "Edit": "modifié",
    "Write": "écrit", "NotebookEdit": "modifié le notebook", "Bash": "lancé",
    "WebFetch": "consulté", "WebSearch": "cherché sur le web", "Task": "délégué",
    "TodoWrite": "mis à jour la liste de tâches",
}


# Ce qu'une conversation pese, et a partir de quand il faut le dire.
#
# Mesure sur cette machine le 5 octobre 2026, et c'est elle qui a motive tout ce qui suit :
# six conversations au-dessus de 850 000 jetons, deux collees au plafond du million, environ
# 1 190 $ d'API a elles toutes. Rien ne l'avait signale — le chiffre etait pourtant a
# l'ecran, mais sans echelle pour le lire, et un nombre sans echelle ne se lit pas.
#
# Le cout n'est pas une image, c'est de l'arithmetique : une conversation relit son contexte
# ENTIER a chaque aller-retour, et un tour avec dix outils en fait dix. A 500 000 jetons,
# l'aller-retour coute 0,25 $ en lecture de cache Opus ; le meme tour, en debut de
# conversation, en coute deux centimes.
SEUILS_CONTEXTE = (200_000, 400_000, 600_000, 800_000)

# Lecture de cache Opus 5 : 10 % du prix d'entree, soit 0,50 $ le million. C'est le tarif qui
# s'applique a la quasi-totalite d'un contexte relu — le neuf, lui, est marginal.
PRIX_LECTURE_CACHE_PAR_JETON = 0.50 / 1_000_000


@dataclass
class Journal:
    """What actually happened this turn — the input the spoken debrief reads."""

    question: str = ""
    outils: list[tuple[str, str]] = field(default_factory=list)
    textes: list[str] = field(default_factory=list)
    refus: list[str] = field(default_factory=list)
    cout_usd: float | None = None
    duree_s: float | None = None
    jetons: int | None = None
    # Le detail, cumule au fil du tour et publie en direct. `jetons` reste le total que le
    # SDK donne a la fin, qui fait foi pour le bilan ; ceux-ci servent a MONTRER que ça
    # avance pendant que ça avance.
    # Ce que la conversation PESE au dernier echange — pas un cumul. Voir _delta.
    contexte: int = 0
    jetons_sortie: int = 0
    echanges: int = 0          # combien de fois le modele a repris la parole dans ce tour
    # --- comment le tour s'est termine -------------------------------------------------
    # Le SDK le DIT, dans le message de resultat, et on le jetait : seuls le cout, la duree
    # et les jetons etaient lus. Un tour coupe a la limite de tours, arrete par une erreur
    # d'API ou tronque a la limite de jetons rendait donc exactement la meme ligne qu'un tour
    # fini normalement — « il tourne longtemps puis s'arrete sans raison » est la description
    # exacte de ce trou. Ces cinq champs sont la reponse, telle que le CLI la donne.
    fin: str = "success"            # success | error_during_execution | error_max_turns |
                                    # error_max_budget_usd | error_max_structured_output_retries
    tours: int | None = None        # num_turns : combien d'allers-retours ce tour a coute
    stop: str | None = None         # stop_reason de la derniere reponse : max_tokens, refusal…
    erreurs: list[str] = field(default_factory=list)
    api_statut: int | None = None   # le code HTTP quand c'est l'API qui a coupe

    def pourquoi_arrete(self) -> str | None:
        """Une phrase disant pourquoi le tour s'est arrete, ou None s'il a fini normalement.

        Dite a voix haute ET affichee : c'est la seule information qui explique un travail qui
        s'interrompt, et la deviner coute plus cher que de la lire."""
        detail = (self.erreurs or [None])[0]
        if self.fin == "error_max_turns":
            combien = f" ({self.tours} tours)" if self.tours else ""
            return (f"je me suis arrêté avant d'avoir fini : la limite de tours est "
                    f"atteinte{combien}")
        if self.fin == "error_max_budget_usd":
            return "je me suis arrêté avant d'avoir fini : le plafond de dépense est atteint"
        if self.fin == "error_max_structured_output_retries":
            return ("je me suis arrêté avant d'avoir fini : trop d'essais pour produire une "
                    "sortie structurée")
        if self.fin == "error_during_execution" or self.api_statut:
            bout = f" — {detail}" if detail else ""
            if self.api_statut:
                bout = f" — l'API a répondu {self.api_statut}{bout}"
            return f"je me suis arrêté avant d'avoir fini : une erreur a coupé l'exécution{bout}"
        if self.stop == "max_tokens":
            return ("ma réponse a été coupée net : elle a atteint la limite de longueur, "
                    "donc la fin manque")
        if self.stop == "refusal":
            return "j'ai refusé de poursuivre cette réponse"
        return None

    def lignes(self) -> list[str]:
        out = []
        for name, cible in self.outils:
            verbe = VERBES.get(name, name)
            out.append(f"{verbe} {cible}".strip())
        return out

    def resume_court(self) -> str:
        """Answered instantly, locally, when he asks "t'en es où" mid-run. It is spoken, so
        the agreement has to be right — "1 actions" grates when you hear it."""
        if not self.outils:
            return "pas encore d'action sur les fichiers"
        lignes = self.lignes()
        if len(lignes) == 1:
            return f"une seule action pour l'instant : {lignes[0]}"
        return (f"{len(lignes)} actions pour l'instant, "
                f"les dernières : " + ", ".join(lignes[-3:]))


# Les outils qui travaillent en fond SANS le dire dans leurs arguments. `Agent` et
# `Workflow` ne posent pas `run_in_background` : le premier rend « Async agent launched
# successfully … agentId: … », le second « Workflow launched in background. Task ID: … ».
# Mesure du 13 octobre 2026 : un workflow de six agents a tourne vingt minutes sur la
# conversation insnap sans qu'une seule pastille s'allume — la page le donnait pour fini.
FOND_IMPLICITE = ("Agent", "Workflow")
# Ce qu'on inscrit au journal d'un tour que personne n'a demande : le bilan parle doit
# pouvoir dire d'ou il sort, sinon il repond a une question qui n'a pas ete posee.
RELANCE_DE_FOND = "(une tâche de fond s'est terminée)"

# Les outils qui DELEGUENT. Pendant qu'ils tournent, le SDK fait remonter les appels
# d'outils du delegue dans le MEME flux, avec `parent_tool_use_id` renseigne — c'est
# documente comme « enough for a progress heartbeat ». On ne regardait pas ce champ : les
# actions du delegue s'affichaient comme celles du tour principal, melangees aux siennes,
# et le delegue lui-meme n'existait nulle part. « Qu'est-ce que fabrique l'agent en
# arriere-plan » n'etait donc pas une impression : l'information arrivait, et on la jetait.
OUTILS_DELEGUES = ("Agent", "Task")
# Ce qu'on garde du travail d'un agent. Au-dela, on oublie le debut : la page montre ce
# qu'il fait MAINTENANT et le chemin recent, pas un journal complet qui repartirait en
# entier vers le telephone a chaque nouvel outil.
ACTIONS_GARDEES = 30

# Ce que le retour immediat d'un outil de fond dit de l'identifiant de la tache. C'est CET
# identifiant que portera la notification de fin, jamais celui de l'outil.
_MARQUE_FOND = re.compile(r"\(ID: ([A-Za-z0-9_-]+)\)|agentId: ([A-Za-z0-9_-]+)"
                          r"|Task ID: ([A-Za-z0-9_-]+)")
_DOSSIER_FOND = re.compile(r"Transcript dir: (\S+)")

_ETATS_AGENT = {"result": "fini", "failed": "échoué"}


def agents_du_journal(texte: str) -> list[dict]:
    """Les agents d'un workflow, lus dans son `journal.jsonl` : qui tourne, qui a fini.

    Le CLI y ecrit une ligne par evenement — `started` avec le libelle et la phase de
    l'agent, puis `result` ou `failed`. On ne lit que les lignes TERMINEES : la derniere
    peut etre en cours d'ecriture, et un JSON coupe au milieu n'est pas une erreur, c'est
    une ligne qu'on relira au prochain passage.
    """
    agents: dict[str, dict] = {}
    par_cle: dict[str, str] = {}
    for ligne in texte.split("\n")[:-1]:
        try:
            e = json.loads(ligne)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        genre = e.get("type")
        if genre == "started":
            ident = str(e.get("agentId") or e.get("key") or len(agents))
            par_cle[str(e.get("key"))] = ident
            agents[ident] = {"id": ident, "nom": str(e.get("label") or "agent"),
                             "phase": str(e.get("phase") or ""), "etat": "en cours"}
        elif genre in _ETATS_AGENT:
            ident = str(e.get("agentId") or par_cle.get(str(e.get("key"))) or "")
            if ident in agents:
                agents[ident]["etat"] = _ETATS_AGENT[genre]
    return list(agents.values())


class Worker:
    """A long-lived Claude Code session plus an event queue the voice layer drains."""

    # Cadence de relecture du journal d'un workflow. Deux secondes : un agent qui finit se
    # voit sans attendre, et relire quelques lignes coute moins qu'un battement de la page.
    PAS_SUIVI = 2.0

    def __init__(self, on_permission=None, on_question=None, tableau=None,
                 conversation=None):
        self.client: ClaudeSDKClient | None = None
        self.events: asyncio.Queue = asyncio.Queue()
        self.journal = Journal()
        self.occupe = False
        # Un arret demande : tant qu'il n'a pas rendu son bilan, les messages qui trainent
        # encore dans le tube ne doivent pas rallumer le travail qu'on vient d'eteindre.
        self.arret_demande = False
        # Ce qui tourne encore quand le tour est fini. Survit d'un tour a l'autre : c'est
        # tout l'interet, la notification de fin arrive apres.
        self._fond: dict[str, dict] = {}
        self._attente_fond: dict[str, tuple[str, str]] = {}
        # Un lecteur par workflow : il relit le journal de l'orchestration pour dire
        # quels agents tournent. S'arrete avec la tache, jamais avant.
        self._suivis: dict[str, asyncio.Task] = {}
        # Les delegations en cours, par identifiant de l'outil qui les a lancees. Comme
        # `_fond`, ça survit au tour : un agent lance en arriere-plan travaille encore
        # quand la reponse est deja partie.
        self._agents: dict[str, dict] = {}
        self._on_permission = on_permission
        self._on_question = on_question
        self._tableau = tableau
        self._conv = conversation
        self.session_id: str | None = None
        # La conversation qu'on a decide de reprendre, resolue une fois au demarrage. Gardee
        # a part de session_id, qui est ce que le SDK finit par nous rendre : si la reprise
        # echouait, les deux differeraient et c'est precisement ce qu'il faut pouvoir dire.
        self.reprise: dict | None = None
        # Vrai quand la pompe d'evenements s'est arretee : plus rien n'arrivera de ce
        # client-la, et seule une reconstruction remet la conversation en marche.
        self.pompe_morte = False
        # Les paliers de contexte deja annonces. Vides a l'ouverture et apres chaque
        # compactage : franchir 400 k une seconde fois, apres s'en etre allege, est une
        # nouvelle quand le redire a chaque tour n'en est pas une.
        self._seuils_dits: set[int] = set()
        self._pompe: asyncio.Task | None = None
        self.modele = config.WORKER_MODEL
        self._modele_base = config.WORKER_MODEL
        self.effort = config.WORKER_EFFORT
        self._rendre_apres_tour = False
        # Le dernier couple modele+effort refuse, pour que la voix le dise au lieu de
        # repondre « je ne connais pas ce modele » a un modele qu'elle connait tres bien.
        self.refus_credits = ""

    def _voir(self, genre: str, **donnees):
        if self._tableau:
            self._tableau.publier(genre, **donnees)

    # ─── le travail qui continue apres la reponse ────────────────────────────────
    #
    # Une commande lancee en arriere-plan et un sous-agent ne s'arretent pas avec le tour :
    # Claude dit « je lance ca, je te dis quand c'est bon », le tour se termine, et plus rien
    # a l'ecran ne distingue cette fin-la d'une vraie. On repond alors a une conversation qui
    # n'a pas fini de travailler — et le message part au milieu d'autre chose.
    #
    # On tient donc la liste de ce qui tourne encore. Elle s'ouvre a l'appel de l'outil et ne
    # se referme que sur la notification de fin, qui arrive un tour plus tard ou jamais.

    def _fond_publier(self):
        self._voir("fond", taches=[
            {"id": c, "nom": t["nom"], "cible": t["cible"], "depuis": t["depuis"],
             "agents": t.get("agents") or []}
            for c, t in self._fond.items()
        ])

    def _fond_ouvrir(self, cle: str, nom: str, cible: str, journal: str = ""):
        if cle in self._fond:
            return
        self._fond[cle] = {"nom": nom, "cible": cible, "depuis": time.time()}
        if journal:
            try:
                self._suivis[cle] = asyncio.get_running_loop().create_task(
                    self._fond_suivre(cle, Path(journal) / "journal.jsonl"))
            except RuntimeError:
                pass   # hors boucle : on saura QUE ca tourne, pas qui
        self._fond_publier()

    def _fond_fermer(self, *cles: str) -> bool:
        parti = False
        for cle in cles:
            if cle and self._fond.pop(cle, None) is not None:
                parti = True
            if suivi := self._suivis.pop(cle, None):
                suivi.cancel()
            # La fiche du delegue s'eteint avec sa tache. Sans ça, un agent lance en fond
            # resterait « en cours » pour toujours : son resultat d'outil est arrive au
            # lancement, pas a la fin.
            for tid, a in list(self._agents.items()):
                if cle and a.get("fond") == cle:
                    self._agents.pop(tid, None)
                    self._agents_publier()
        if parti:
            self._fond_publier()
        return parti

    async def _fond_suivre(self, cle: str, journal: Path):
        """Relire le journal d'un workflow tant que la tache est ouverte.

        Savoir QU'un workflow tourne suffit a ne pas repondre trop tot ; savoir QUELS agents
        tournent, et lesquels ont fini, c'est ce qu'on venait voir sur la page. Le journal
        est petit — une ligne par agent — et se relit entier : plus simple qu'un curseur,
        et une ligne coupee au milieu n'est jamais perdue, seulement relue.
        """
        dernier: list[dict] | None = None
        limite = time.monotonic() + 6 * 3600
        while cle in self._fond and time.monotonic() < limite:
            try:
                agents = agents_du_journal(journal.read_text(encoding="utf-8"))
            except OSError:
                agents = []
            if agents != dernier:
                dernier = agents
                self._fond[cle]["agents"] = agents
                self._fond_publier()
            await asyncio.sleep(self.PAS_SUIVI)

    # ─── ce que font les delegues, pendant qu'ils le font ────────────────────────
    #
    # Un `Agent` rend la main cinq ou dix minutes plus tard. Entre les deux, la page
    # montrait une ligne « Agent · explorer les tests » suivie du silence, puis une volee
    # d'actions impossibles a distinguer de celles du tour principal. Deux manques en un :
    # on ne savait pas QUI travaillait, et on ne savait pas SUR QUOI.

    def _agents_publier(self):
        self._voir("agents", liste=[
            {"id": i, "nom": a["nom"], "sorte": a["sorte"], "depuis": a["depuis"],
             "combien": a["combien"], "fait": a["actions"][-1] if a["actions"] else "",
             "actions": list(a["actions"]), "fond": bool(a["fond"])}
            for i, a in self._agents.items()
        ])

    def _agent_ouvrir(self, tool_id: str, args: dict, cible: str):
        sorte = str(args.get("subagent_type") or "").strip()
        self._agents[tool_id] = {
            "nom": cible or sorte or "agent", "sorte": sorte,
            "depuis": time.time(), "actions": [], "combien": 0, "fond": "",
        }
        self._agents_publier()

    def _agent_agir(self, parent: str, nom: str, cible: str) -> bool:
        """Une action remontee d'un delegue. Rend vrai quand elle lui a ete attribuee.

        Fausse pour tout le reste, y compris pour un petit-fils — un agent lance par un
        agent : son parent n'est pas une de nos fiches, et on prefere une action sans
        proprietaire a une action rangee sous le mauvais."""
        a = self._agents.get(parent)
        if a is None:
            return False
        a["actions"].append(" ".join(
            m for m in (VERBES.get(nom, nom), cible) if m).strip())
        del a["actions"][:-ACTIONS_GARDEES]
        a["combien"] += 1
        self._agents_publier()
        return True

    def _agent_rendu(self, tool_id: str, fond: str = ""):
        """Le retour de l'outil de delegation : l'agent a fini, ou il continue en fond."""
        a = self._agents.get(tool_id)
        if a is None:
            return
        if fond:
            a["fond"] = fond    # il continue : c'est la notification de fin qui l'eteindra
        else:
            self._agents.pop(tool_id, None)
        self._agents_publier()

    def _agents_balayer(self):
        """A la fin du tour, ce qui n'est pas en fond ne tourne plus.

        Un tour interrompu, une erreur d'API, un resultat d'outil jamais recu : la fiche
        resterait allumee indefiniment, et une pastille qui ment sur ce qui tourne est pire
        que pas de pastille du tout."""
        restants = {i: a for i, a in self._agents.items() if a["fond"]}
        if len(restants) != len(self._agents):
            self._agents = restants
            self._agents_publier()

    def _fond_resultat(self, tool_id: str, texte: str) -> str:
        """Le retour immediat d'un outil lance en fond : il porte l'identifiant de la tache.

        Rend l'identifiant de la tache ouverte, ou une chaine vide quand rien ne continue :
        l'appelant s'en sert pour savoir si la fiche du delegue doit rester allumee.

        Les deux identifiants ne sont pas les memes. L'outil a le sien (`toolu_...`), la
        tache de fond en recoit un autre a son demarrage — « (ID: bdjie0kr9) » pour une
        commande, « agentId: ... » pour un sous-agent. C'est le SECOND que portera la
        notification de fin : sans ce rattachement, la ligne ne s'eteindrait jamais.
        """
        attendu = self._attente_fond.pop(tool_id, None)
        if not attendu:
            return ""
        nom, cible = attendu
        m = _MARQUE_FOND.search(texte or "")
        if m:
            cle = next(g for g in m.groups() if g).strip()
        elif nom in FOND_IMPLICITE:
            # Un agent lance au premier plan rend son rapport, pas un identifiant : il a
            # deja fini, il n'y a rien a suivre.
            return ""
        else:
            cle = tool_id
        d = _DOSSIER_FOND.search(texte or "")
        self._fond_ouvrir(cle, nom, cible, journal=d.group(1) if d else "")
        return cle

    def _fond_notifie(self, texte: str):
        """Une notification de fin, qui arrive un tour plus tard — ou jamais."""
        for m in re.finditer(r"<task-id>([^<]+)</task-id>", texte or ""):
            self._fond_fermer(m.group(1).strip())

    async def _can_use_tool(self, name: str, args: dict, ctx) -> PermissionResultAllow | PermissionResultDeny:
        # AskUserQuestion passe par ici, et c'est le seul outil qui y passe QUOI QU'IL ARRIVE :
        # meme en bypassPermissions, ou le CLI approuve tout sans consulter le rappel, celui-la
        # le consulte quand meme — il declare `requiresUserInteraction`, donc il n'a personne
        # d'autre a qui demander. Verifie, pas suppose.
        #
        # Ce n'est pas une permission : c'est une question, et la reponse attendue est une
        # phrase, pas un oui. On la rend au CLI dans `updated_input["response"]` — c'est le
        # champ qu'il lit pour fabriquer « The user responded: … ». Les trois autres formes
        # essayees (answers seul, answers avec l'entree, rien) rendent « The user did not
        # answer the questions » ou cassent la validation du schema.
        if name == "AskUserQuestion":
            return await self._question(args)
        if name in AUTO_ALLOW or self._on_permission is None:
            return PermissionResultAllow()
        libelle = getattr(ctx, "display_name", None) or getattr(ctx, "title", None) or name
        cible = _cible(name, args)
        # The voice layer decides; it may take seconds to ask and hear an answer, and the
        # session waits on this coroutine, which is exactly the behaviour we want.
        accord = await self._on_permission(f"{VERBES.get(name, name)} {cible}".strip(), libelle)
        if accord:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Refusé à la voix. Propose autre chose.", interrupt=False)

    async def _question(self, args: dict) -> PermissionResultAllow:
        """Claude demande quelque chose a l'utilisateur. On va le lui demander pour de vrai.

        Sans ce chemin, l'outil rendait « The user did not answer the questions » et Claude
        enchainait en expliquant que la session etait non-interactive — alors qu'il y a
        quelqu'un, qui ecoute, et dont c'est exactement le role."""
        questions = args.get("questions") or []
        if self._on_question is None or not questions:
            return PermissionResultAllow()
        reponse = await self._on_question(questions)
        if not reponse:
            # Pas de reponse : on laisse l'outil dire lui-meme qu'il n'en a pas eu, plutot
            # que d'inventer un « l'utilisateur n'a pas repondu » qui se lirait comme un refus.
            return PermissionResultAllow()
        return PermissionResultAllow(updated_input={**args, "response": reponse})

    def _options(self, effort: str | None = None, reprendre: str | None = None):
        """Les options du client, en un seul endroit.

        Extrait de start() parce que changer de niveau d'effort oblige a reconstruire le
        client : le SDK n'expose pas de set_effort. Deux constructions divergentes auraient
        fini par ne plus se ressembler, et la difference se serait vue comme un changement de
        comportement inexplicable apres un simple reglage."""
        niveau = effort or self.effort
        return ClaudeAgentOptions(
            model=self.modele,
            # Traduit, jamais brut : « ultracode » n'existe pas cote SDK, ou l'effort est un
            # Literal de cinq valeurs. Le CLI le definit comme xhigh plus l'orchestration,
            # et c'est exactement ce qu'on envoie — xhigh ici, l'orchestration plus bas.
            effort=config.effort_sdk(niveau),
            cwd=config.WORKDIR,
            cli_path=config.claude_binary(),
            permission_mode=config.PERMISSION,
            # Reprendre rend le contexte COMPLET de la session precedente : Claude Code
            # ecrit ses sessions sur disque, on ne rejoue pas un resume approximatif.
            # Ce qu'on recoit, rien d'autre : retomber ici sur config.REPRENDRE ferait
            # rouvrir la session demandee au lancement a chaque « nouvelle conversation ».
            # L'argument est toujours resolu par l'appelant, _a_reprendre inclus.
            resume=reprendre or None,
            # Only bypassPermissions really asks nothing; every other mode still routes
            # boundary crossings through the callback, which is how an out-of-project shell
            # command gets asked out loud instead of silently denied.
            # Toujours fourni, y compris en bypassPermissions ou le SDK previent qu'il ne
            # sera pas consulte. C'est vrai pour les permissions — c'est le mode choisi — et
            # faux pour AskUserQuestion, qui y passe quand meme : le poser a None privait
            # donc la conversation de toute question, sans rien dire.
            can_use_tool=self._can_use_tool,
            # Vides par defaut : sans eux le CLI ne borne rien, et en mettre un chiffre
            # arbitraire introduirait la coupure qu'on cherche justement a expliquer. Quand
            # ils sont poses, l'arret porte un nom (error_max_turns, error_max_budget_usd)
            # que le bilan du tour dit a voix haute — un plafond choisi vaut mieux qu'un
            # arret muet, et c'est tout l'interet de les exposer.
            max_turns=config.MAX_TOURS,
            max_budget_usd=config.MAX_DEPENSE,
            # Sans ça, un `Read` sur une photo coupe la conversation en plein travail : le
            # resultat d'outil voyage en base64 dans UNE ligne du flux, et le defaut du SDK
            # plafonne une ligne a un mega-octet. Voir config.MAX_TAMPON.
            max_buffer_size=config.MAX_TAMPON,
            # Deltas feed the dashboard: thinking and the written answer appear as they are
            # produced instead of landing in one block at the end.
            include_partial_messages=True,
            # The reply is spoken, so shape it at the source instead of stripping markdown
            # afterwards. The porte-parole still rewrites, but starting from prose costs
            # it far less work than starting from a table.
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                # Le second paragraphe vise un symptome precis : des tours qui durent
                # quinze minutes sans que personne ne sache sur quoi. Ici, l'attente n'est
                # pas la meme qu'en terminal — on a P'RLE, et on attend une reponse parlee ;
                # un silence de dix minutes ne se distingue pas d'une panne. Il ne demande
                # pas de bacler : il demande de revenir parler quand le travail est long,
                # plutot que de continuer indefiniment en silence. Un tour qui rend la parole
                # se relance d'un mot ; un tour qui tourne sans fin se coupe, et c'est la
                # coupure qui fait perdre le contexte.
                "append": self._consignes(niveau),
            },
        )

    # Le mot-cle « ultracode » tape dans une session Claude Code fait deux choses : il monte
    # l'effort a xhigh et il ouvre l'outil Workflow pour ce tour. Le second passe par un
    # system-reminder que le CLI injecte lui-meme — et qu'il n'injecte PAS en mode SDK :
    # verifie, le mot-cle place dans le message n'y declenche rien du tout. L'outil Workflow,
    # lui, est bien la : il est dans la liste des outils de la session. Ce qui manque n'est
    # donc pas la capacite mais l'autorisation, et c'est precisement ce que ce bloc donne.
    #
    # Il ne desserre que l'exploration. La consigne orale, elle, tient toujours : un tour qui
    # lance huit agents doit rendre trois phrases, pas un rapport.
    ULTRACODE = (
        "\n\n"
        "Le mode ultracode est actif : l'utilisateur a explicitement demandé l'orchestration "
        "multi-agents pour cette session. Tu peux donc utiliser l'outil Workflow de toi-même, "
        "sans redemander l'autorisation, dès qu'une tâche se décompose en travaux indépendants "
        "— explorer plusieurs pistes de front, relire un diff sous plusieurs angles, couvrir "
        "un large périmètre. Le paragraphe précédent te demandait de ne pas explorer "
        "indéfiniment : en ultracode cette retenue ne s'applique plus à la PROFONDEUR du "
        "travail, va au fond des choses. Elle s'applique toujours à la PAROLE : quel que soit "
        "le nombre d'agents lancés, la réponse dite à voix haute reste de deux à quatre "
        "phrases, et un travail long rend la parole en chemin au lieu de disparaître."
    )

    def _consignes(self, niveau: str) -> str:
        """Ce qu'on ajoute au prompt systeme de Claude Code."""
        return self._CONSIGNES + (self.ULTRACODE if config.est_ultracode(niveau) else "")

    # La consigne d'ecriture, et le changement qui compte : la reponse finale est LUE A
    # L'ECRAN, pas prononcee. Elle l'etait avant — contrainte a la prose continue, sans
    # markdown, pour que la synthese vocale sonne juste. Mais la synthese ne lit pas ce
    # texte : un second modele, le porte-parole, en fabrique une version parlee a partir de
    # lui. On payait donc le prix d'une contrainte orale sur le seul contenu qui se lit avec
    # les yeux — un bloc gris sans relief, sur un telephone, au milieu d'un flux. Chacun son
    # metier : l'ecrit se met en forme, le porte-parole parle.
    _CONSIGNES = (
                    "Tes réponses finales s'affichent à l'écran et se LISENT. Mets-les en forme "
                    "comme une réponse normale : gras pour ce qui compte, listes à puces quand il "
                    "y a plusieurs points, titres si la réponse a des parties, code entre accents "
                    "graves. L'écran est souvent celui d'un téléphone : des phrases courtes, des "
                    "paragraphes de deux ou trois lignes, pas de tableau large. "
                    "Écris le français avec ses accents."
                    "\n\n"
                    "Reste BREF — l'équivalent de deux à quatre phrases pour un tour ordinaire, "
                    "plus seulement si on te demande un développement. La mise en forme sert la "
                    "lecture, elle n'autorise pas le rapport : une réponse en huit sections pour "
                    "une question simple est aussi pénible à lire qu'un pavé."
                    "\n\n"
                    # Mesuré sur l'usage réel : les réponses de fin de tour racontaient tout
                    # le travail — ce qui avait été lu, essayé, écarté, puis corrigé. Or
                    # celui qui écoute n'a qu'une décision à prendre : relancer, ou passer à
                    # la suite. Le récit du chemin ne l'aide pas à la prendre, et il la
                    # retarde de deux minutes de lecture à chaque tour.
                    "Quand le tour est un travail sur le code, la réponse tient en trois "
                    "choses, dans cet ordre, en sautant ce qui ne s'applique pas : "
                    "**ce qui est fait** — en une phrase, pas la liste des fichiers touchés ni "
                    "le récit des étapes ; **ce qui ne l'est pas, et pourquoi** — un blocage, "
                    "un choix laissé ouvert, une partie écartée ; **ce que tu ferais ensuite**, "
                    "quand une suite s'impose vraiment. Rien d'autre : pas de résumé de ce que "
                    "tu as essayé, pas de récapitulatif de la conversation, pas de "
                    "réexplication de ce qui est déjà à l'écran. Quatre ou cinq lignes pour un "
                    "tour ordinaire ; on développe seulement si on te le demande."
                    "\n\n"
                    "Tu n'as pas à écrire pour la voix : une autre instance relit ton tour et en "
                    "fabrique la version parlée, qui est dite séparément. N'ajoute donc aucun "
                    "résumé oral, et ne t'interdis rien de ce qui rend un texte lisible."
                    "\n\n"
                    "Cette conversation est orale : quelqu'un attend en écoutant. Va au bout de "
                    "ce qu'on te demande, mais rends la parole dès que tu as de quoi la rendre. "
                    "Concrètement : si la tâche demande plus d'une dizaine de minutes ou "
                    "beaucoup d'allers-retours, fais la première partie utile, puis réponds en "
                    "disant ce qui est fait, ce qui reste et ce que tu ferais ensuite — on te "
                    "relancera d'un mot. N'explore pas indéfiniment pour être exhaustif : quand "
                    "tu as répondu à la question posée, arrête-toi. Si tu tournes en rond ou "
                    "qu'une piste ne donne rien, dis-le au lieu de continuer à chercher — un "
                    "constat d'échec est une réponse utile, un silence de dix minutes ne l'est pas."
    )

    def _sid_vivant(self) -> str | None:
        """La session a reprendre quand on reconstruit le client en cours de route.

        `session_id` est ce que le SDK a fini par nous rendre — et il ne nous le rend qu'au
        PREMIER message. Entre la connexion et la premiere reponse il vaut None, et c'est
        une fenetre reelle : au lancement, l'ajustement automatique de l'effort tombe
        pendant cette fenetre, puisqu'il precede l'envoi de la premiere question. Reconstruire
        avec None ouvrait alors une conversation neuve et jetait la reprise du demarrage,
        juste a temps pour que le contexte manque au moment ou on en avait besoin.

        On retombe donc sur la session qu'on avait DEMANDEE, qui reste valable tant que
        personne n'a dit « nouvelle conversation » — ce cas-la remet `reprise` a None.
        """
        return self.session_id or (self.reprise or {}).get("session_id") or None

    def _a_reprendre(self) -> str | None:
        """Quelle conversation reprendre au demarrage, et le dire.

        Trois sources, par ordre de priorite : ce qu'on a demande explicitement
        (`vvreprendre`), puis la derniere conversation de ce dossier, puis rien. Le choix est
        ANNONCE dans les deux cas — reprendre en silence laisserait croire a une conversation
        neuve, et ouvrir une neuve en silence est exactement ce qui faisait perdre le contexte
        sans que rien ne le signale.
        """
        if config.REPRENDRE:
            self._voir("log", niveau="INFO", source="session",
                       texte=f"reprise demandée : {config.REPRENDRE[:8]}")
            self.reprise = {"session_id": config.REPRENDRE}
            return config.REPRENDRE
        if not config.REPRISE_AUTO:
            self._voir("log", niveau="INFO", source="session",
                       texte="nouvelle conversation (demandée)")
            return None
        try:
            import journal
            c = journal.derniere_conversation(config.WORKDIR)
        except Exception:
            log.debug("reprise automatique impossible", exc_info=True)
            c = None
        if not c:
            self._voir("log", niveau="INFO", source="session",
                       texte="nouvelle conversation — rien à reprendre dans ce dossier")
            return None
        self.reprise = c
        self._voir("log", niveau="INFO", source="session",
                   texte=(f"reprise de la conversation de {c.get('projet') or 'ce dossier'} — "
                          f"{c.get('tours', 0)} tours, "
                          f"{c.get('reprises', 1)} lancement(s) · {c['session_id'][:8]}"))
        # Son poids AVANT la premiere question, parce que c'est le seul moment ou on peut
        # encore choisir d'en ouvrir une neuve. Apres, la question est posee et payee.
        try:
            poids = journal.poids_session(c["session_id"])
        except Exception:
            log.debug("poids de la session illisible", exc_info=True)
            poids = 0
        if poids >= SEUILS_CONTEXTE[0]:
            self.journal.contexte = poids
            self._seuils_dits.update(s for s in SEUILS_CONTEXTE if poids >= s)
            self._voir("erreur" if poids >= 600_000 else "log",
                       niveau="WARNING", source="contexte",
                       texte=(f"{self.poids()}. « compacte » la résume sans la quitter, "
                              "« nouvelle conversation » repart à zéro."))
        return c["session_id"]

    async def start(self):
        sid = self._a_reprendre()
        if sid:
            # Laisser la conversation precedente finir d'ecrire son transcript. Relancer dans
            # la seconde qui suit un Ctrl-C tombait sur un fichier encore en cours d'ecriture,
            # et Claude Code ouvre alors une conversation NEUVE sans rien dire.
            try:
                import journal
                await asyncio.to_thread(journal.attendre_transcript, sid)
            except Exception:
                log.debug("attente du transcript impossible", exc_info=True)
        self.client = ClaudeSDKClient(self._options(reprendre=sid))
        await self.client.connect()
        self._pompe = asyncio.create_task(self._drainer())

    async def _drainer(self):
        """Turn SDK messages into voice-layer events and dashboard lines.

        Runs for the life of the session. The journal it fills is what the porte-parole
        reads, and what answers "t'en es ou" without calling a model.

        Elle ne meurt JAMAIS en silence, et c'est tout l'objet de l'enveloppe ci-dessous.
        Le degat repare, observe sur une conversation d'insnap : la pompe s'est arretee au
        debut d'un tour, `occupe` est reste vrai pour toujours, et la page a affiche « au
        travail » pendant seize minutes pendant que Claude Code, lui, finissait tranquillement
        son tour sur disque. Plus aucun evenement, plus aucun moyen d'envoyer quoi que ce
        soit, et rien a l'ecran pour dire ce qui se passait. Une tache creee par
        `create_task` qui leve emporte son exception avec elle : personne ne la lit, et la
        conversation est morte sans un mot.

        Deux choses sont donc garanties ici, quoi qu'il arrive en chemin : le tour se
        TERMINE — `occupe` retombe, la couche vocale reçoit sa fin, elle n'attend pas un
        bilan qui ne viendra pas — et la panne se DIT, a l'ecran, avec de quoi repartir.
        """
        try:
            await self._drainer_boucle()
        except asyncio.CancelledError:
            raise                      # remplacement de client : normal, rien a signaler
        except Exception as exc:
            log.exception("la pompe d'evenements est tombee")
            # Nommer ce cas-la plutot que de recracher l'exception : « Failed to decode
            # JSON » laisse croire a un flux corrompu alors que c'est un plafond de taille,
            # et on cherche la panne du mauvais cote pendant une demi-heure.
            if "buffer size" in str(exc):
                raison = (f"un message dépassait le plafond de taille "
                          f"({config.MAX_TAMPON // 1024 // 1024} Mo) — "
                          "une image ou un résultat d'outil trop gros")
            else:
                raison = f"la liaison avec Claude Code a lâché ({exc})"
            self._clore_sur_panne(raison)
        else:
            # Fin normale du flux : le CLI a ferme sa sortie. Dit quand meme, parce qu'un
            # tour en cours ne se finira plus et que la page doit cesser de l'attendre.
            self._clore_sur_panne("Claude Code a fermé le flux")

    def _clore_sur_panne(self, pourquoi: str):
        """Rendre la main apres une pompe tombee, et le dire.

        Le pire etat possible est celui qu'on vient de quitter : bloque, sans explication et
        sans bouton. On annonce donc la panne ET la sortie, parce que « débloque » remonte
        une conversation intacte — le contexte est sur disque, pas dans ce processus.
        """
        self._voir("erreur", niveau="ERROR", source="session",
                   texte=(f"{pourquoi} — le tour en cours ne rendra pas de bilan. "
                          "« débloque » relance la liaison en gardant la conversation."))
        self.pompe_morte = True
        if self.occupe:
            self.occupe = False
            self._voir("travail", actif=False)
            self.journal.fin = "error_during_execution"
            self.journal.erreurs = [pourquoi]
            # La couche vocale attend cette fin pour reprendre la parole. Sans elle, le
            # micro reste ferme et plus rien n'entre : bloque une seconde fois.
            try:
                self.events.put_nowait(("fin", self.journal))
            except Exception:
                log.debug("file d'evenements pleine a la cloture", exc_info=True)

    async def _drainer_boucle(self):
        assert self.client
        async for message in self.client.receive_messages():
            sid = getattr(message, "session_id", None)
            if sid and not self.session_id:
                self.session_id = sid
                # Verifier que la reprise a PRIS. Claude Code ne rale pas quand il ne trouve
                # pas la session demandee : il en ouvre une neuve, et la conversation repart
                # de zero sans le moindre message. C'est exactement le silence qui faisait
                # croire a des conversations qui se dedoublent toutes seules.
                voulu = (self.reprise or {}).get("session_id") or None
                repris = bool(voulu) and sid == voulu
                if voulu and not repris:
                    self._voir("erreur", niveau="WARNING", source="session",
                               texte=(f"la reprise de {voulu[:8]} n'a PAS pris — Claude Code a "
                                      f"ouvert une conversation neuve ({sid[:8]}). Le contexte "
                                      f"précédent n'est pas perdu : « vvreprendre "
                                      f"{voulu} » le retrouve."))
                    log.warning("reprise refusee : demande %s, obtenu %s", voulu, sid)
                self._voir("session", id=sid, repris=repris,
                           tours=(self.reprise or {}).get("tours") if repris else 0)
                if self._conv:
                    self._conv.note_session(sid)
            # Un tour peut repartir SANS qu'on ait rien envoye. Quand une tache de fond se
            # termine, Claude Code glisse lui-meme une `<task-notification>` et redonne la
            # parole au modele : mesure prise le 15 octobre 2026 sur la conversation insnap
            # — reponse a 15:44:03, fin de tache a 15:44:33, nouveau Bash a 15:44:36. Le
            # travail n'etant allume que par `envoyer()`, tout ce tour-la se deroulait avec
            # l'indicateur eteint : la page montrait Claude en train de reflechir pendant
            # que le tableau affichait « prête » — et la notification de fin etait deja
            # partie. La conversation etait annoncee finie alors qu'elle ne l'etait pas.
            if (isinstance(message, AssistantMessage) and not self.occupe
                    and not self.arret_demande and not self.pompe_morte):
                self.journal = Journal(question=RELANCE_DE_FOND)
                self.occupe = True
                self._voir("travail", actif=True)
            if isinstance(message, StreamEvent):
                self._delta(message)
            elif isinstance(message, AssistantMessage):
                # Renseigne UNIQUEMENT quand le message vient d'un delegue : c'est le
                # seul moyen de distinguer son travail de celui du tour principal.
                parent = getattr(message, "parent_tool_use_id", None) or ""
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        args = block.input or {}
                        entree = (block.name, _cible(block.name, args))
                        # Les actions d'un delegue vont a SA fiche, pas au journal du tour.
                        # Le journal nourrit le bilan parle : « trente-trois actions » quand
                        # trente viennent d'un agent qu'on a deja annonce dit la meme chose
                        # deux fois, et noie les trois qui etaient les siennes.
                        delegue = self._agent_agir(parent, block.name, entree[1])
                        if not delegue:
                            self.journal.outils.append(entree)
                        # L'identifiant voyage jusqu'a la page : c'est lui qui permet de
                        # rattacher le resultat a l'action et d'afficher un verdict plutot
                        # qu'une ligne dont on ne sait jamais si elle s'est terminee.
                        # `run_in_background` est la seule marque fiable : c'est le champ
                        # que Bash comme Agent utilisent pour dire « je rends la main tout
                        # de suite, le travail continue ». On ne peut pas encore ouvrir la
                        # ligne — l'identifiant de la tache n'existera qu'au retour.
                        en_fond = bool(args.get("run_in_background"))
                        if en_fond or block.name in FOND_IMPLICITE:
                            self._attente_fond[block.id] = (block.name, entree[1])
                        if block.name in OUTILS_DELEGUES and not delegue:
                            self._agent_ouvrir(block.id, args, entree[1])
                        self._voir("outil", nom=block.name, cible=entree[1], args=args,
                                   id=block.id, fond=en_fond, parent=parent)
                        await self.events.put(("outil", entree))
                    elif isinstance(block, TextBlock) and block.text.strip():
                        self.journal.textes.append(block.text.strip())
                    elif isinstance(block, ThinkingBlock):
                        pass  # already streamed as deltas
            elif isinstance(message, UserMessage):
                # Tool results come back echoed on the user turn.
                # Une notification de fin de tache arrive sur le tour utilisateur, et pas
                # toujours en blocs : quand le contenu est une simple chaine, la boucle qui
                # suit ne la voyait pas passer et la ligne restait allumee pour toujours.
                if isinstance(message.content, str):
                    self._fond_notifie(message.content)
                contenu = message.content if isinstance(message.content, list) else []
                for block in contenu:
                    if isinstance(block, ToolResultBlock):
                        brut = block.content
                        if isinstance(brut, list):
                            brut = " ".join(
                                b.get("text", "") for b in brut if isinstance(b, dict)
                            )
                        texte = str(brut or "").strip()
                        # Publie meme sans texte : un outil qui ne renvoie rien reussit
                        # quand meme, et sans cet evenement son indicateur tournerait
                        # indefiniment sur la page.
                        cle = self._fond_resultat(block.tool_use_id, texte)
                        # L'outil de delegation a rendu : soit l'agent a fini et sa fiche
                        # s'eteint, soit il continue en fond sous la cle qu'on vient d'ouvrir.
                        self._agent_rendu(block.tool_use_id, cle)
                        self._voir("resultat", texte=texte[:1500], id=block.tool_use_id,
                                   echec=bool(block.is_error),
                                   parent=getattr(message, "parent_tool_use_id", None) or "")
                    elif isinstance(block, TextBlock):
                        # Les notifications de fin de tache arrivent par la, sur le tour
                        # utilisateur, sans passer par le moindre outil.
                        self._fond_notifie(block.text)
            elif isinstance(message, SystemMessage):
                self._systeme(message)
            elif isinstance(message, ResultMessage):
                self.journal.cout_usd = message.total_cost_usd
                self.journal.duree_s = round((message.duration_ms or 0) / 1000, 1)
                self.journal.jetons = _jetons(message.model_usage)
                self.journal.fin = message.subtype or "success"
                self.journal.tours = message.num_turns
                self.journal.stop = message.stop_reason
                self.journal.erreurs = [str(e) for e in (message.errors or []) if e]
                self.journal.api_statut = message.api_error_status
                for refus in message.permission_denials or []:
                    self.journal.refus.append(str(getattr(refus, "tool_name", refus)))
                self.occupe = False
                self.arret_demande = False
                self._agents_balayer()
                # L'extrait voyage avec l'etat : c'est ce qu'une notification de fin de
                # tour peut montrer sans ouvrir la page.
                reponse = " ".join(self.journal.textes[-1].split()) if self.journal.textes else ""
                self._voir("travail", actif=False, reponse=reponse[:200])
                await self._rendre_le_modele()
                # La raison part avec le bilan, pas dans une ligne separee : cherchee, elle
                # l'est au moment ou l'on regarde pourquoi le tour s'est termine comme ca.
                self._voir("tour", actions=len(self.journal.outils),
                           duree=self.journal.duree_s, jetons=self.journal.jetons,
                           tours=self.journal.tours, fin=self.journal.fin,
                           stop=self.journal.stop, erreurs=self.journal.erreurs[:3],
                           pourquoi=self.journal.pourquoi_arrete())
                if self.journal.fin != "success":
                    # Aussi dans le journal technique : le bilan d'un tour se relit sur la
                    # page, mais un arret anormal se cherche dans les logs.
                    log.warning("tour termine en %s (%s tours) : %s", self.journal.fin,
                                self.journal.tours, "; ".join(self.journal.erreurs) or "—")
                await self.events.put(("fin", self.journal))

    def _delta(self, message: StreamEvent):
        event = message.event if isinstance(message.event, dict) else {}
        genre = event.get("type")

        # Les jetons, au fil du tour. Deux evenements les portent, et ils ne disent pas la
        # meme chose :
        #
        # « message_start » ouvre CHAQUE reponse du modele — et c'est la premiere preuve
        # qu'un message est arrive chez Claude. Jusqu'ici, entre l'envoi et la premiere
        # pensee affichee, il pouvait s'ecouler plusieurs secondes de silence total pendant
        # lesquelles rien ne distinguait « ça monte » de « c'est perdu ».
        #
        # « message_delta » clot la reponse et porte sa consommation reelle. Un tour avec des
        # outils en enchaine plusieurs : on CUMULE, sinon le compteur repartirait de zero a
        # chaque aller-retour.
        if genre == "message_start":
            usage = (event.get("message") or {}).get("usage") or {}
            # REMPLACE, n'additionne pas. L'entree d'un echange est le CONTEXTE que le modele
            # relit — la conversation entiere, largement servie par le cache. L'additionner a
            # chaque aller-retour donnait des chiffres enormes et faux : trois echanges de
            # 24 k affichaient 72 k, puis 294 k sur un tour un peu long, alors que la
            # conversation n'a jamais pese que 98 k. On montrait un cumul de relectures en le
            # presentant comme une consommation. Ce qu'on veut lire, c'est ce que la
            # conversation pese MAINTENANT : la derniere valeur, donc.
            self.journal.contexte = (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0))
            self.journal.echanges += 1
            self._publier_jetons(recu=True)
            return
        if genre == "message_delta":
            usage = event.get("usage") or {}
            # La sortie, elle, s'additionne vraiment : chaque echange PRODUIT du texte, et
            # rien n'est relu. C'est la seule des deux mesures qui soit cumulative.
            self.journal.jetons_sortie += int(usage.get("output_tokens") or 0)
            self._publier_jetons()
            return

        if genre != "content_block_delta":
            return
        delta = event.get("delta") or {}
        # Un delta vide n'apporte rien et coute une place dans la memoire de la page : mesure
        # sur une session reelle, 65 evenements « pensee » sur 65 avaient un texte vide.
        if delta.get("type") == "thinking_delta":
            bout = delta.get("thinking") or ""
            if bout:
                self._voir("pensee", texte=bout, suite=True)
        elif delta.get("type") == "text_delta":
            bout = delta.get("text") or ""
            if bout:
                self._voir("texte", texte=bout, suite=True)

    def _systeme(self, message: SystemMessage):
        """Ce que le CLI raconte de lui-meme. On n'en retient que le compactage.

        Il etait jusqu'ici jete en silence, et c'est dommage : c'est le seul endroit qui
        dise ce qu'une conversation pesait AVANT d'etre resumee. Sans ça, un compactage
        automatique — celui que le CLI declenche tout seul en arrivant au plafond — passait
        pour un trou de memoire inexplicable : Claude oubliait soudain la moitie de la
        conversation, et rien a l'ecran ne disait pourquoi.
        """
        if message.subtype == "status" and (message.data or {}).get("status") == "compacting":
            self._voir("log", niveau="INFO", source="contexte",
                       texte="compactage en cours — la conversation se résume elle-même")
            return
        if message.subtype != "compact_boundary":
            return
        meta = (message.data or {}).get("compact_metadata") or {}
        avant = int(meta.get("pre_tokens") or 0)
        auto = (meta.get("trigger") or "") != "manual"
        self._seuils_dits.clear()
        self.journal.contexte = 0
        self._voir("contexte", genre_compactage="auto" if auto else "manuel", avant=avant)
        self._voir("log", niveau="INFO", source="contexte",
                   texte=(f"conversation compactée{' automatiquement' if auto else ''} — "
                          f"elle pesait {avant // 1000} k jetons. Le contexte repart léger ; "
                          "le détail d'avant n'est plus relu, le résumé le remplace."))

    def poids(self) -> str | None:
        """Une phrase sur ce que la conversation pese, ou None quand ça ne vaut pas la peine.

        Dire un nombre de jetons ne sert a rien : personne ne sait si 400 000 est beaucoup.
        Dire ce qu'il COUTE par aller-retour, si — et c'est la seule facon de faire sentir
        que la meme question posee ici coute dix fois ce qu'elle couterait ailleurs.
        """
        c = self.journal.contexte
        if c < SEUILS_CONTEXTE[0]:
            return None
        return (f"cette conversation pèse {c // 1000} k jetons — "
                f"{c * PRIX_LECTURE_CACHE_PAR_JETON:.2f} $ par aller-retour, "
                f"et un tour avec outils en fait plusieurs")

    def _veiller_au_contexte(self):
        """Prevenir UNE fois par palier franchi, jamais a chaque tour.

        Le piege evite : une alerte a chaque echange devient un decor, on cesse de la lire,
        et le jour ou elle compte elle ne se distingue plus du reste. Un palier franchi est
        un evenement ; le meme palier re-franchi n'en est pas un.
        """
        c = self.journal.contexte
        for seuil in SEUILS_CONTEXTE:
            if c >= seuil and seuil not in self._seuils_dits:
                self._seuils_dits.add(seuil)
                self._voir("erreur" if seuil >= 600_000 else "log",
                           niveau="WARNING", source="contexte",
                           texte=(f"{self.poids()}. « compacte » la résume sans la quitter, "
                                  "« nouvelle conversation » repart à zéro."))

    async def compacter(self) -> str:
        """Demander au CLI de resumer la conversation, sans la quitter.

        Pourquoi ne pas laisser faire l'automatique : il existe, mais il attend le PLAFOND.
        Avec une fenetre d'un million de jetons, ça veut dire traverser toute la zone ou
        chaque aller-retour coute un demi-dollar avant que quoi que ce soit ne se passe —
        mesure du 5 octobre 2026 : six conversations au-dessus de 850 k, deux collees au
        million, et la facture qui va avec. Compacter quand on le DECIDE, typiquement entre
        deux sujets, coute un resume et rend une conversation legere.

        Rien ne se perd de ce qui compte : le resume est ecrit par le modele qui vient de
        faire le travail, et la session garde son identifiant — donc son historique sur
        disque reste relisible en entier.
        """
        if not self.client:
            return ""
        if self.occupe:
            return "une tâche est en cours — arrête-la d'abord."
        avant = self.journal.contexte
        await self.envoyer("/compact")
        return (f"je compacte{f' — {avant // 1000} k jetons avant' if avant else ''}."
                if avant else "je compacte.")

    def _publier_jetons(self, recu: bool = False):
        """Ce que le tour a consomme jusqu'ici. Des chiffres mesures, jamais estimes.

        `recu` marque le tout premier signe de vie de l'API sur ce tour : la page s'en sert
        pour remplacer « envoyé » par « reçu », ce qui est la question qu'on se pose en
        regardant l'ecran apres avoir parle."""
        self._voir("jetons",
                   contexte=self.journal.contexte,
                   sortie=self.journal.jetons_sortie,
                   echanges=self.journal.echanges,
                   recu=recu or None)
        self._veiller_au_contexte()

    async def envoyer(self, texte: str):
        """Queued server-side if a turn is already running, so he can speak mid-work.

        Si la liaison est morte, on relance AVANT d'envoyer plutot que de laisser la phrase
        tomber dans un tube que personne ne lit : c'est ce silence-la qu'on corrige, pas
        seulement l'affichage.
        """
        assert self.client
        if self.pompe_morte:
            self._voir("log", niveau="INFO", source="session",
                       texte="liaison morte — on la relance avant d'envoyer")
            if not await self.debloquer():
                return
        self.arret_demande = False
        if not self.occupe:
            self.journal = Journal(question=texte)
            self.occupe = True
            # The page shows work in progress instead of the voice announcing it. Spoken
            # milestones were removed: "toujours dessus, je viens de lancer cat" is noise
            # you cannot skim, where a header indicator is glanceable and silent.
            self._voir("travail", actif=True)
        else:
            self.journal.question = f"{self.journal.question} / {texte}".strip(" /")
        await self.client.query(texte)

    def _refuser_le_couple(self, refus: str) -> str:
        """Dit non au couple modele+effort, une fois, en clair, et renvoie "".

        Le refus s'ecrit ici plutot que chez chacun des appelants parce qu'ils sont trois —
        la voix, la page de conversation et la carte du tableau de bord — et qu'un refus
        silencieux ressemble a un bouton casse. Le message reste aussi en memoire : le
        chemin vocal le dit a haute voix, les deux autres le lisent dans le journal.
        """
        self.refus_credits = refus
        self._voir("log", niveau="WARNING", source="crédits", texte=refus)
        return ""

    async def changer_modele(self, cle: str, temporaire: bool = True) -> str:
        """Switch model mid-session. Returns the label to say.

        The cost worth knowing: a model switch invalidates the prompt cache, so the next turn
        re-reads the conversation at full price. That is fine for an explicit choice, and the
        reason this is not done automatically per task."""
        assert self.client
        self.refus_credits = ""
        entree = config.MODELES.get(cle)
        if not entree:
            return ""
        identifiant, libelle = entree
        refus = config.refus_du_couple(cle, self.effort)
        if refus:
            return self._refuser_le_couple(refus)
        await self.client.set_model(identifiant)
        self.modele = identifiant
        self._rendre_apres_tour = temporaire and identifiant != self._modele_base
        self._voir("modele", modele=identifiant, cle=cle, libelle=libelle,
                   temporaire=self._rendre_apres_tour)
        return libelle

    async def changer_effort(self, cle: str) -> str:
        """Change le niveau d'effort. Renvoie le libellé à dire, ou "" si refusé.

        Le SDK n'a pas de set_effort : le niveau est figé à la construction du client. Il faut
        donc en reconstruire un — et la conversation ne doit rien perdre au passage, d'où la
        reprise sur l'identifiant de session. Claude Code écrit ses sessions sur disque, donc
        la reprise rend le contexte COMPLET, pas un résumé approximatif.

        Deux garde-fous appris à la dure :

        - **Jamais en pleine tâche.** Reconstruire pendant un tour tuerait le travail en
          cours sans rien pour le dire. On refuse, l'appelant explique.
        - **Construire, puis basculer, puis lâcher l'ancien.** L'ordre inverse laissait une
          fenêtre de deux secondes et demie où `self.client` était déjà mort et où toute
          phrase arrivant là se perdait.

        Le coût, mesuré plutôt que supposé — ce commentaire affirmait l'inverse. Reprendre la
        session PRÉSERVE le cache de prompt : sur une conversation de 26 000 jetons, le tour
        qui suit une reconstruction en relit 26 366 en cache et n'en crée que 451, contre 162
        sans reconstruction. Ce qui se paie est donc la latence, et elle seule : une demi-
        seconde, avant que Claude ne commence. C'est ce qui rend l'ajustement automatique de
        l'effort viable à chaque tour — voir complexite.py."""
        self.refus_credits = ""
        entree = config.EFFORTS.get(cle)
        if not entree or not self.client:
            return ""
        niveau, libelle = entree
        refus = config.refus_du_couple(config.cle_du_modele(self.modele), cle)
        if refus:
            return self._refuser_le_couple(refus)
        # On compare, on reconstruit et on retient sur la CLE, jamais sur le niveau SDK.
        # Invisible sur cinq niveaux ou les deux coincident, faux sur le sixieme : ultracode
        # s'envoie en « xhigh », donc le retenir ainsi effacait l'information qu'on y etait —
        # les consignes ultracode ne partaient pas, et le selecteur retombait tout seul sur
        # « très élevé ». Tout le reste du code lit self.effort comme une cle.
        if cle == self.effort:
            return libelle
        if self.occupe:
            return ""

        ancien, ancienne_pompe = self.client, self._pompe
        nouveau = ClaudeSDKClient(self._options(effort=cle, reprendre=self._sid_vivant()))
        await nouveau.connect()
        # Bascule seulement maintenant : jusqu'ici l'ancien client servait encore.
        self.client = nouveau
        self.effort = cle
        self._pompe = asyncio.create_task(self._drainer())

        if ancienne_pompe:
            ancienne_pompe.cancel()
        try:
            await ancien.disconnect()
        except Exception:
            # L'ancien client est déjà remplacé : son adieu qui rate ne concerne plus personne.
            pass
        self._voir("effort", cle=cle, niveau=niveau, libelle=libelle)
        return libelle

    async def changer_conversation(self, sid: str) -> str:
        """Basculer sur une AUTRE conversation, sans quitter l'application.

        Meme mecanique que le changement d'effort — le SDK fige la session a la construction,
        donc il faut reconstruire — et les memes deux garde-fous, pour les memes raisons :
        jamais en pleine tache, et on construit avant de lacher l'ancien client.

        La difference tient a ce qu'on verifie AVANT : reprendre une session que Claude Code
        ne connait pas ne leve rien, ça ouvre une conversation vide. On compare donc
        l'identifiant obtenu a celui demande, et on le DIT quand ils different — sinon on croit
        avoir change de conversation alors qu'on vient d'en creer une.
        """
        sid = (sid or "").strip()
        if not sid or not self.client:
            return ""
        if sid == self.session_id:
            return "c'est déjà la conversation en cours."
        if self.occupe:
            return ""

        ancien, ancienne_pompe = self.client, self._pompe
        try:
            nouveau = ClaudeSDKClient(self._options(reprendre=sid))
            await nouveau.connect()
        except Exception as exc:
            log.warning("bascule de conversation refusee : %s", exc)
            self._voir("erreur", niveau="WARNING", source="session",
                       texte=f"impossible de reprendre {sid[:8]} : {exc}")
            return ""
        self.client = nouveau
        # Remis a zero pour que le drainer recolte l'identifiant du NOUVEAU client et puisse
        # verifier la reprise. Le garder ferait passer la verification pour reussie.
        self.session_id = None
        self.reprise = {"session_id": sid}
        self.journal = Journal()
        self._pompe = asyncio.create_task(self._drainer())

        if ancienne_pompe:
            ancienne_pompe.cancel()
        try:
            await ancien.disconnect()
        except Exception:
            pass
        return f"conversation {sid[:8]} reprise."

    async def nouvelle_conversation(self) -> str:
        """Ouvrir une conversation NEUVE, sans toucher aux autres.

        Meme mecanique que changer_conversation — reconstruire le client, puisque le SDK fige
        la session a la construction — mais avec `resume=None` : Claude Code cree alors une
        session vierge. Les precedentes ne sont ni fermees ni supprimees ; elles restent
        reprenables depuis le selecteur.

        Le cas d'usage : changer de sujet dans le meme dossier. Reprendre par defaut est ce
        qu'on veut en revenant continuer, mais pas quand on attaque autre chose — le contexte
        precedent devient alors du bruit qu'on paie a chaque tour.
        """
        if not self.client:
            return ""
        if self.occupe:
            return ""
        ancien, ancienne_pompe = self.client, self._pompe
        try:
            nouveau = ClaudeSDKClient(self._options(reprendre=None))
            await nouveau.connect()
        except Exception as exc:
            log.warning("nouvelle conversation impossible : %s", exc)
            self._voir("erreur", niveau="WARNING", source="session",
                       texte=f"impossible d'ouvrir une conversation neuve : {exc}")
            return ""
        self.client = nouveau
        # Remis a zero pour que le drainer recolte l'identifiant du NOUVEAU client. Et
        # `reprise` a None : sans ça la verification de reprise croirait qu'on voulait
        # reprendre l'ancienne et signalerait un echec qui n'existe pas.
        self.session_id = None
        self.reprise = None
        self.journal = Journal()
        self._pompe = asyncio.create_task(self._drainer())

        if ancienne_pompe:
            ancienne_pompe.cancel()
        try:
            await ancien.disconnect()
        except Exception:
            pass
        return "nouvelle conversation ouverte."

    async def _rendre_le_modele(self):
        if not self._rendre_apres_tour or not self.client:
            return
        self._rendre_apres_tour = False
        await self.client.set_model(self._modele_base)
        self.modele = self._modele_base
        cle = config.cle_du_modele(self._modele_base)
        self._voir("modele", modele=self._modele_base, cle=cle,
                   libelle=config.MODELES.get(cle, ("", self._modele_base))[1],
                   temporaire=False)

    async def interrompre(self):
        """Arreter le tour en cours. Rend la main AVANT de demander l'arret.

        L'ordre compte, et il a coute une conversation entiere. L'ancien ordre — demander
        au client, puis relacher l'etat — supposait que `interrupt()` reponde. Quand la
        liaison est justement ce qui est casse, il n'y repond jamais : le bouton « arrêter »
        reste en attente pour toujours, et il ne reste plus aucun bouton. Or c'est
        exactement dans ce cas-la qu'on appuie dessus.

        On relache donc d'abord, on demande ensuite, et la demande est bornee : si le CLI ne
        repond pas en trois secondes, il est injoignable et c'est « débloque » qu'il faut.
        """
        self.occupe = False
        self.arret_demande = True
        self._voir("travail", actif=False)
        if not self.client:
            return
        try:
            await asyncio.wait_for(self.client.interrupt(), 3.0)
        except asyncio.TimeoutError:
            self.pompe_morte = True
            self._voir("erreur", niveau="WARNING", source="session",
                       texte=("Claude Code ne répond plus à l'arrêt — « débloque » relance "
                              "la liaison en gardant la conversation."))
            log.warning("interrupt sans reponse : liaison probablement morte")
        except Exception as exc:
            log.warning("interrupt refuse : %s", exc)

    async def debloquer(self) -> str:
        """Reconstruire le client sur la MEME conversation, quand la liaison est morte.

        La porte de sortie, et la raison pour laquelle elle existe : une pompe tombée laisse
        un processus vivant mais sourd — la page affiche la conversation, le bouton d'arrêt
        ne fait rien, et le seul remède connu était de tout relancer depuis un clavier qu'on
        n'a pas forcément sous la main.

        Rien n'est perdu en reconstruisant : Claude Code écrit ses sessions sur disque, donc
        la reprise rend le contexte COMPLET. Ce qui disparaît est le tour en cours, qui de
        toute façon n'allait plus rien rendre.
        """
        sid = self._sid_vivant()
        ancien, ancienne_pompe = self.client, self._pompe
        try:
            nouveau = ClaudeSDKClient(self._options(reprendre=sid))
            await nouveau.connect()
        except Exception as exc:
            log.warning("deblocage impossible : %s", exc)
            self._voir("erreur", niveau="ERROR", source="session",
                       texte=f"impossible de relancer la liaison : {exc}")
            return ""
        self.client = nouveau
        self.pompe_morte = False
        self.occupe = False
        self.journal = Journal()
        self._pompe = asyncio.create_task(self._drainer())
        self._voir("travail", actif=False)

        if ancienne_pompe:
            ancienne_pompe.cancel()
        if ancien:
            # Sans borne : un client mort ne dit pas toujours au revoir, et on ne va pas
            # attendre son adieu pour rendre la conversation a son proprietaire.
            try:
                await asyncio.wait_for(ancien.disconnect(), 3.0)
            except Exception:
                log.debug("l'ancien client n'a pas pu etre ferme", exc_info=True)
        return ("liaison relancée" + (f" sur {sid[:8]}" if sid else " sur une conversation neuve")
                + " — le contexte est gardé.")

    async def stop(self):
        if self._pompe:
            self._pompe.cancel()
        for suivi in self._suivis.values():
            suivi.cancel()
        self._suivis.clear()
        if self.client:
            await self.client.disconnect()
