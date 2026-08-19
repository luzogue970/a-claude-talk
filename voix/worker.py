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
    TextBlock,
    ToolUseBlock,
)

import config

# Read-only tools run without asking: a spoken approval for every `grep` would make the
# voice channel unusable. Anything that writes, runs, or leaves the machine gets asked.
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

    def __init__(self, on_permission=None):
        self.client: ClaudeSDKClient | None = None
        self.events: asyncio.Queue = asyncio.Queue()
        self.journal = Journal()
        self.occupe = False
        self._on_permission = on_permission
        self._pompe: asyncio.Task | None = None

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

    async def start(self):
        options = ClaudeAgentOptions(
            model=config.WORKER_MODEL,
            effort=config.WORKER_EFFORT,
            cwd=config.WORKDIR,
            cli_path=config.claude_binary(),
            permission_mode="default",
            can_use_tool=self._can_use_tool,
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
        self.client = ClaudeSDKClient(options)
        await self.client.connect()
        self._pompe = asyncio.create_task(self._drainer())

    async def _drainer(self):
        """Turn SDK messages into voice-layer events. Runs for the life of the session."""
        assert self.client
        async for message in self.client.receive_messages():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        entree = (block.name, _cible(block.name, block.input or {}))
                        self.journal.outils.append(entree)
                        await self.events.put(("outil", entree))
                    elif isinstance(block, TextBlock) and block.text.strip():
                        self.journal.textes.append(block.text.strip())
            elif isinstance(message, ResultMessage):
                self.journal.cout_usd = message.total_cost_usd
                self.journal.duree_s = (message.duration_ms or 0) / 1000
                for refus in message.permission_denials or []:
                    self.journal.refus.append(str(getattr(refus, "tool_name", refus)))
                self.occupe = False
                await self.events.put(("fin", self.journal))

    async def envoyer(self, texte: str):
        """Queued server-side if a turn is already running, so he can speak mid-work."""
        assert self.client
        if not self.occupe:
            self.journal = Journal(question=texte)
            self.occupe = True
        else:
            self.journal.question = f"{self.journal.question} / {texte}".strip(" /")
        await self.client.query(texte)

    async def interrompre(self):
        assert self.client
        await self.client.interrupt()
        self.occupe = False

    async def stop(self):
        if self._pompe:
            self._pompe.cancel()
        if self.client:
            await self.client.disconnect()
