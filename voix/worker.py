"""The one Claude Code session that owns all the state.

The old system talked to Claude through its GUI: xdotool typed into the panel and a
speaker-output probe stood in for "is it done yet". Everything painful descended from
that blindness. Here the session is a library call, so the voice layer sees the real
events: which tools ran, what they touched, when a permission is needed, when the turn
actually ended. The spoken debrief is built from that journal, not from guessing at the
last paragraph of text.
"""

import asyncio
import os
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
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

import config

# Second filter, under the CLI's own. Read-only tools never ask: a spoken approval for
# every `grep` would make the voice channel unusable. Under acceptEdits the CLI already lets
# in-project writes through without consulting the callback, so what actually reaches it is
# the boundary crossings — and those are worth a question.
AUTO_ALLOW = {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite", "NotebookRead"}


def _cible(name: str, args: dict) -> str:
    """A short, speakable description of one tool call. Paths become basenames because
    nobody wants to hear a full path read out, and commands keep only their verb."""
    if path := args.get("file_path") or args.get("path") or args.get("notebook_path"):
        return Path(str(path)).name
    if cmd := args.get("command"):
        return str(cmd).strip().split()[0] if str(cmd).strip() else ""
    if pattern := args.get("pattern") or args.get("query"):
        return str(pattern)[:40]
    if url := args.get("url"):
        return str(url).split("/")[2] if "://" in str(url) else str(url)[:40]
    if desc := args.get("description"):
        return str(desc)[:60]
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


class Worker:
    """A long-lived Claude Code session plus an event queue the voice layer drains."""

    def __init__(self, on_permission=None, tableau=None, conversation=None):
        self.client: ClaudeSDKClient | None = None
        self.events: asyncio.Queue = asyncio.Queue()
        self.journal = Journal()
        self.occupe = False
        self._on_permission = on_permission
        self._tableau = tableau
        self._conv = conversation
        self.session_id: str | None = None
        self._pompe: asyncio.Task | None = None
        self.modele = config.WORKER_MODEL
        self._modele_base = config.WORKER_MODEL
        self.effort = config.WORKER_EFFORT
        self._rendre_apres_tour = False

    def _voir(self, genre: str, **donnees):
        if self._tableau:
            self._tableau.publier(genre, **donnees)

    async def _can_use_tool(self, name: str, args: dict, ctx) -> PermissionResultAllow | PermissionResultDeny:
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

    def _options(self, effort: str | None = None, reprendre: str | None = None):
        """Les options du client, en un seul endroit.

        Extrait de start() parce que changer de niveau d'effort oblige a reconstruire le
        client : le SDK n'expose pas de set_effort. Deux constructions divergentes auraient
        fini par ne plus se ressembler, et la difference se serait vue comme un changement de
        comportement inexplicable apres un simple reglage."""
        return ClaudeAgentOptions(
            model=self.modele,
            effort=effort or self.effort,
            cwd=config.WORKDIR,
            cli_path=config.claude_binary(),
            permission_mode=config.PERMISSION,
            # Reprendre rend le contexte COMPLET de la session precedente : Claude Code
            # ecrit ses sessions sur disque, on ne rejoue pas un resume approximatif.
            resume=reprendre or config.REPRENDRE or None,
            # Only bypassPermissions really asks nothing; every other mode still routes
            # boundary crossings through the callback, which is how an out-of-project shell
            # command gets asked out loud instead of silently denied.
            can_use_tool=None if config.PERMISSION == "bypassPermissions" else self._can_use_tool,
            # Deltas feed the dashboard: thinking and the written answer appear as they are
            # produced instead of landing in one block at the end.
            include_partial_messages=True,
            # The reply is spoken, so shape it at the source instead of stripping markdown
            # afterwards. The porte-parole still rewrites, but starting from prose costs
            # it far less work than starting from a table.
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": (
                    "Tes réponses finales sont lues à voix haute par une synthèse vocale. "
                    "Écris-les en prose continue : pas de markdown, pas de liste, pas de tableau, "
                    "pas de bloc de code, pas de chemin de fichier complet. Deux à quatre phrases, "
                    "sauf demande explicite. Écris le français avec ses accents. "
                    "Le détail technique reste dans tes outils et tes fichiers, pas dans la réponse parlée."
                ),
            },
        )

    async def start(self):
        self.client = ClaudeSDKClient(self._options())
        await self.client.connect()
        self._pompe = asyncio.create_task(self._drainer())

    async def _drainer(self):
        """Turn SDK messages into voice-layer events and dashboard lines.

        Runs for the life of the session. The journal it fills is what the porte-parole
        reads, and what answers "t'en es ou" without calling a model."""
        assert self.client
        async for message in self.client.receive_messages():
            sid = getattr(message, "session_id", None)
            if sid and not self.session_id:
                self.session_id = sid
                self._voir("session", id=sid)
                if self._conv:
                    self._conv.note_session(sid)
            if isinstance(message, StreamEvent):
                self._delta(message)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        args = block.input or {}
                        entree = (block.name, _cible(block.name, args))
                        self.journal.outils.append(entree)
                        # L'identifiant voyage jusqu'a la page : c'est lui qui permet de
                        # rattacher le resultat a l'action et d'afficher un verdict plutot
                        # qu'une ligne dont on ne sait jamais si elle s'est terminee.
                        self._voir("outil", nom=block.name, cible=entree[1], args=args,
                                   id=block.id)
                        await self.events.put(("outil", entree))
                    elif isinstance(block, TextBlock) and block.text.strip():
                        self.journal.textes.append(block.text.strip())
                    elif isinstance(block, ThinkingBlock):
                        pass  # already streamed as deltas
            elif isinstance(message, UserMessage):
                # Tool results come back echoed on the user turn.
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
                        self._voir("resultat", texte=texte[:1500],
                                   id=block.tool_use_id, echec=bool(block.is_error))
            elif isinstance(message, ResultMessage):
                self.journal.cout_usd = message.total_cost_usd
                self.journal.duree_s = round((message.duration_ms or 0) / 1000, 1)
                self.journal.jetons = _jetons(message.model_usage)
                for refus in message.permission_denials or []:
                    self.journal.refus.append(str(getattr(refus, "tool_name", refus)))
                self.occupe = False
                self._voir("travail", actif=False)
                await self._rendre_le_modele()
                self._voir("tour", actions=len(self.journal.outils),
                           duree=self.journal.duree_s, jetons=self.journal.jetons)
                await self.events.put(("fin", self.journal))

    def _delta(self, message: StreamEvent):
        event = message.event if isinstance(message.event, dict) else {}
        if event.get("type") != "content_block_delta":
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

    async def envoyer(self, texte: str):
        """Queued server-side if a turn is already running, so he can speak mid-work."""
        assert self.client
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

    async def changer_modele(self, cle: str, temporaire: bool = True) -> str:
        """Switch model mid-session. Returns the label to say.

        The cost worth knowing: a model switch invalidates the prompt cache, so the next turn
        re-reads the conversation at full price. That is fine for an explicit choice, and the
        reason this is not done automatically per task."""
        assert self.client
        entree = config.MODELES.get(cle)
        if not entree:
            return ""
        identifiant, libelle = entree
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

        Le coût, à savoir : comme un changement de modèle, ça invalide le cache de prompt. Le
        tour suivant relit la conversation au tarif plein."""
        entree = config.EFFORTS.get(cle)
        if not entree or not self.client:
            return ""
        niveau, libelle = entree
        if niveau == self.effort:
            return libelle
        if self.occupe:
            return ""

        ancien, ancienne_pompe = self.client, self._pompe
        nouveau = ClaudeSDKClient(self._options(effort=niveau, reprendre=self.session_id))
        await nouveau.connect()
        # Bascule seulement maintenant : jusqu'ici l'ancien client servait encore.
        self.client = nouveau
        self.effort = niveau
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
        assert self.client
        await self.client.interrupt()
        self.occupe = False
        self._voir("travail", actif=False)

    async def stop(self):
        if self._pompe:
            self._pompe.cancel()
        if self.client:
            await self.client.disconnect()
