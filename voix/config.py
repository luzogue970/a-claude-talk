"""Resolved paths, credentials and vocabulary. Nothing here is hardcoded to a machine."""

import glob
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = Path.home() / ".config" / "claude-talk" / "secrets.env"


def _load_secrets():
    """Secrets live outside the repo, so a clone can never carry a key."""
    if not SECRETS.is_file():
        return
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_secrets()


def claude_binary():
    """The CLI is not on PATH on every machine: the VS Code extension ships its own,
    and the SDK wheel bundles one. Prefer PATH, then the newest extension build."""
    found = shutil.which("claude")
    if found:
        return found
    builds = sorted(glob.glob(str(
        Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"
    )))
    return builds[-1] if builds else None


# --- audio -------------------------------------------------------------------
# The design assumes headphones: with them there is no echo, so the microphone can
# stay open and barge-in works. On speakers the agent hears itself and interrupts
# itself, which is exactly the failure the old gating tried to paper over.
INPUT_DEVICE = os.environ.get("VOIX_INPUT_DEVICE", "")
OUTPUT_DEVICE = os.environ.get("VOIX_OUTPUT_DEVICE", "")

# --- moteurs -----------------------------------------------------------------
STT_ENGINE = os.environ.get("VOIX_STT", "azure")      # azure | local
TTS_ENGINE = os.environ.get("VOIX_TTS", "azure")      # azure | local
LANGUAGE = os.environ.get("VOIX_LANGUAGE", "fr-FR")
AZURE_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_REGION = os.environ.get("AZURE_SPEECH_REGION", "francecentral")
AZURE_VOICE = os.environ.get("AZURE_SPEECH_VOICE", "fr-FR-DeniseNeural")

# --- Claude ------------------------------------------------------------------
WORKER_MODEL = os.environ.get("VOIX_WORKER_MODEL", "claude-opus-5")
WORKER_EFFORT = os.environ.get("VOIX_WORKER_EFFORT", "xhigh")
SPEAKER_MODEL = os.environ.get("VOIX_SPEAKER_MODEL", "claude-haiku-4-5")
WORKDIR = os.environ.get("VOIX_WORKDIR", os.getcwd())

# --- vocabulaire -------------------------------------------------------------
# Azure fr-FR alone turns "un fichier point MD" into "un point MD" and "MQL" into
# "kubedka". A phrase list biases the acoustic layer; the project name and branch
# are appended at runtime, the way the native /voice does it.
PHRASE_LIST = [
    "MQL", "GLIMPS", "gliphish", "Claude Code", "LiveKit", "Pipecat",
    "markdown", "gitignore", "commit", "rebase", "pull request", "merge request",
    "refactor", "linter", "pytest", "venv", "async", "await", "JSON", "YAML",
    "TypeScript", "Python", "Docker", "Kubernetes", "CI", "pipeline",
    "point py", "point md", "point json", "point sh", "point ts",
]


def phrase_list(extra=()):
    """Project name and git branch as recognition hints, plus anything caller adds."""
    hints = list(PHRASE_LIST)
    hints.append(Path(WORKDIR).name)
    head = Path(WORKDIR) / ".git" / "HEAD"
    if head.is_file():
        ref = head.read_text(encoding="utf-8", errors="replace").strip()
        if ref.startswith("ref: refs/heads/"):
            hints.append(ref.rsplit("/", 1)[-1])
    hints.extend(extra)
    return [h for h in dict.fromkeys(hints) if h]
