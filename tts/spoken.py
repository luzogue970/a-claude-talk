#!/usr/bin/env python3
"""Turn the last answer into something worth hearing, without letting it drift."""

import importlib.util
import json
import os
import re
import subprocess
import sys

TTS_DIR = os.path.expanduser("~/.claude/tts")
CLAUDE = "/usr/local/bin/claude"
MODEL = os.environ.get("SPOKEN_MODEL", "claude-haiku-4-5")
DIRECT_LIMIT = int(os.environ.get("SPOKEN_DIRECT_CHARS", "700"))
MIN_RATIO = float(os.environ.get("SPOKEN_MIN_RATIO", "0.4"))
MAX_RATIO = float(os.environ.get("SPOKEN_MAX_RATIO", "1.6"))
LOG = os.path.join(TTS_DIR, "voice.log")
HISTORY = os.path.join(TTS_DIR, "spoken-history.jsonl")
AUTOFILE = os.path.join(TTS_DIR, "auto-enabled")
HISTORY_TURNS = int(os.environ.get("SPOKEN_HISTORY_TURNS", "4"))
STRUCTURE = re.compile(r"^\s*(\|.*\||```|[-*+]\s|\d+[.)]\s|#{1,6}\s)", re.M)
MARKUP = re.compile(r"[|`*#_>\-]+|\s+")

INSTRUCTION = """Tu es la voix de Claude. On vient de te poser une question et tu y reponds
A L'ORAL, comme un collegue competent qui explique de vive voix. Tu ne lis pas un document.

Le ton :
- Tu t'adresses directement a la personne, tu la tutoies.
- Prose parlee : des phrases qui s'enchainent, des connecteurs naturels, pas d'enumeration.
- Commence par la reponse, pas par une annonce ("voici", "je vais t'expliquer").
- Pas de meta-commentaire sur ce que tu es en train de faire.

Le fond, non negociable :
- Garde TOUS les faits, chiffres, noms, conclusions et nuances de la reponse ecrite.
  Tu changes le registre, pas le contenu. Aucune omission.
- N'invente rien : aucun chiffre, aucune conclusion, aucune nuance absente de l'original.
- Les chemins de fichier, noms de symboles, commandes et blocs de code ne se prononcent pas :
  remplace-les par ce qu'ils sont ("le hook de fin de tour", "le script de lecture").
- Un tableau devient des phrases qui en portent le sens, jamais une lecture de cellules.
- Les termes anglais restent en anglais, la voix est multilingue.
- Ecris le francais avec ses accents.

La continuite, parce que c'est une conversation et pas une suite de lectures :
- Ce qui suit est ce que TU as deja dit a voix haute dans cet echange. Enchaine dessus.
- Ne re-explique pas ce que tu as deja explique : renvoie-y en une incise ("comme je te disais",
  "c'est le point qu'on vient de voir"), et va a ce qui est nouveau.
- Reprends le vocabulaire que tu as deja pose plutot que d'en introduire un autre pour la
  meme chose.
- Si la reponse ecrite reformule quelque chose de deja dit, abrege-le franchement.

Renvoie uniquement ce que la voix doit dire.

Ce que tu as deja dit a voix haute (du plus ancien au plus recent) :
<historique>
{history}
</historique>

La question qu'on vient de te poser :
<question>
{question}
</question>

La reponse ecrite a rendre a l'oral :
<reponse>
{answer}
</reponse>"""


PHASEFILE = os.path.join(TTS_DIR, "phase")


def note(message):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{message}\n")


def read_history():
    """The last spoken turns, so the voice continues a conversation instead of restarting one."""
    if not os.path.isfile(HISTORY):
        return []
    turns = []
    with open(HISTORY, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return turns[-HISTORY_TURNS:]


def append_history(question, spoken):
    turns = read_history()
    turns.append({"question": question[:400], "spoken": spoken})
    with open(HISTORY, "w", encoding="utf-8") as fh:
        for turn in turns[-HISTORY_TURNS:]:
            fh.write(json.dumps(turn, ensure_ascii=False) + "\n")


def render_history(turns):
    if not turns:
        return "(rien encore, c'est le debut de l'echange)"
    return "\n\n".join(f"Lui : {t['question']}\nToi : {t['spoken']}" for t in turns)


def set_phase(name):
    """The watcher reads this to show what is happening between answer and playback."""
    if name:
        with open(PHASEFILE, "w", encoding="utf-8") as fh:
            fh.write(name)
    elif os.path.exists(PHASEFILE):
        os.remove(PHASEFILE)


def load_extract():
    spec = importlib.util.spec_from_file_location("extract", os.path.join(TTS_DIR, "extract.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def last_texts(path):
    answer = question = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("isSidechain"):
                continue
            blocks = entry.get("message", {}).get("content")
            if isinstance(blocks, str):
                blocks = [{"type": "text", "text": blocks}]
            if not isinstance(blocks, list):
                continue
            text = "\n".join(b.get("text", "") for b in blocks
                             if isinstance(b, dict) and b.get("type") == "text").strip()
            if not text:
                continue
            if entry.get("type") == "assistant" and not entry.get("isApiErrorMessage"):
                answer = text
            elif entry.get("type") == "user" and not text.startswith("<"):
                question = text
    return question, answer


def rewrite(question, answer, history):
    prompt = INSTRUCTION.format(question=(question or "(inconnue)")[:2000], answer=answer,
                                history=render_history(history))
    result = subprocess.run(
        [CLAUDE, "-p", "--model", MODEL, "--output-format", "json", prompt],
        capture_output=True, text=True, cwd=TTS_DIR, timeout=120,
        env={**os.environ, "CLAUDE_TTS_CHILD": "1"},
    )
    if result.returncode != 0:
        note(f"spoken: {MODEL} exit {result.returncode}, repli mecanique")
        return ""
    try:
        return json.loads(result.stdout).get("result", "").strip()
    except json.JSONDecodeError:
        return result.stdout.strip()


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 1
    path = payload.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 1

    extract = load_extract()
    question, answer = last_texts(path)
    if not answer:
        return 1

    flat = extract.flatten(answer)
    if not flat.strip():
        return 1

    voice_mode = os.path.exists(AUTOFILE)
    structured = bool(STRUCTURE.search(answer))
    # In voice mode every answer goes through the spoken rewrite: it is what turns a written
    # answer into a turn of conversation, and what carries the thread from one turn to the next.
    if not voice_mode and len(flat) <= DIRECT_LIMIT and not structured:
        note(f"spoken: lecture directe ({len(flat)} car., hors mode vocal)")
        sys.stdout.write(flat)
        return 0

    history = read_history()
    set_phase("reecriture")
    try:
        spoken = rewrite(question, answer, history)
    finally:
        set_phase("")
    # Measure against the answer's content, not its flattening: flatten() drops tables
    # wholesale, so a table-heavy answer would make any faithful rewrite look like a 5x
    # expansion and get rejected.
    content = len(MARKUP.sub(" ", answer).strip())
    ratio = len(spoken) / max(content, 1)
    # Continuing a thread legitimately shortens: what was already said gets abridged.
    floor = MIN_RATIO * 0.75 if history else MIN_RATIO
    # A short answer expands a lot just by becoming a spoken sentence, so cap in absolute
    # terms too: the ratio alone rejects legitimate rewrites of anything under a paragraph.
    too_short = len(spoken) < content * floor
    too_long = len(spoken) > max(content * MAX_RATIO, content + 400)
    if not spoken or too_short or too_long:
        note(f"spoken: reecriture rejetee (ratio {ratio:.2f}), repli sur l'aplatissement")
        sys.stdout.write(extract.truncate(flat))
        return 0

    append_history(question or "", spoken)
    note(f"spoken: reecrit par {MODEL} ({content} -> {len(spoken)} car., ratio {ratio:.2f}, "
         f"{len(history)} tours de contexte)")
    sys.stdout.write(spoken)
    return 0


if __name__ == "__main__":
    sys.exit(main())
