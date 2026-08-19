#!/usr/bin/env python3
"""Hands-free voice loop: listen, barge in on the reading, transcribe, answer, speak."""

import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

import numpy as np

sys.path.insert(0, os.path.expanduser("~/.claude/tts"))
from detector import SpeechDetector, rms  # noqa: E402

TTS_DIR = os.path.expanduser("~/.claude/tts")
SAY = os.path.join(TTS_DIR, "say.sh")
STATE_FILE = os.path.join(TTS_DIR, "voice.running")
LOG = os.path.join(TTS_DIR, "voice.log")

DEVICE = os.environ.get("VOICE_DEVICE", "echo-cancel-source")
REFERENCE_DEVICE = os.environ.get("VOICE_REFERENCE", "qubes-sink.monitor")
RATE = 16000
FRAME_MS = 20
FRAME_BYTES = int(RATE * FRAME_MS / 1000) * 2
SILENCE_FRAMES_TO_STOP = int(os.environ.get("VOICE_SILENCE_MS", "700")) // FRAME_MS
# The preroll has to outlast the detector's decision window, or the onset is lost.
PREROLL_FRAMES = int(os.environ.get("VOICE_PREROLL_MS", "1000")) // FRAME_MS
MIN_UTTERANCE_FRAMES = int(os.environ.get("VOICE_MIN_MS", "240")) // FRAME_MS
SESSION_ID = os.environ.get("VOICE_SESSION_ID", "7b3f2a10-9c44-4e51-8d2b-6f0a1c5e9d33")
WORKDIR = os.environ.get("VOICE_WORKDIR", os.path.expanduser("~/Documents/ges/gliphish"))
CLAUDE = "/usr/local/bin/claude"
MODEL = os.environ.get("VOICE_MODEL", "claude-haiku-4-5")
TARGET = os.environ.get("VOICE_TARGET", "vscode")
FOCUS_KEY = os.environ.get("VOICE_FOCUS_KEY", "ctrl+alt+v")
WINDOW_MARK = os.environ.get("VOICE_WINDOW", "VSCodium")
AUTOFILE = os.path.join(TTS_DIR, "auto-enabled")
MUTEFILE = os.path.join(TTS_DIR, "mic-muted")
PHASEFILE = os.path.join(TTS_DIR, "phase")
HISTORYFILE = os.path.join(TTS_DIR, "spoken-history.jsonl")
PIDFILE = os.path.join(TTS_DIR, "current.pgid")
UNTILFILE = os.path.join(TTS_DIR, "playing-until")
WAIT_TIMEOUT = float(os.environ.get("VOICE_WAIT_TIMEOUT", "300"))
# The sink monitor is the only signal in the same time base as the microphone frames.
REF_ACTIVE_RMS = float(os.environ.get("VOICE_REF_RMS", "120"))
# Measured on this machine: the sink monitor goes silent 460 ms before the microphone
# stops hearing the speakers (300 ms of it is pure monitor-to-microphone delay). A 500 ms
# hangover left no margin at all, and the tail of each reading leaked through.
ECHO_HANGOVER_FRAMES = int(os.environ.get("VOICE_ECHO_HANGOVER_MS", "1400")) // FRAME_MS
SPOKEN_PROMPT = (
    "Tes réponses sont lues à voix haute. Réponds en prose continue, sans markdown, "
    "sans liste, sans tableau, sans bloc de code et sans chemin de fichier. "
    "Deux à quatre phrases sauf si on t'en demande plus. Écris le français avec ses accents."
)


INTERACTIVE = sys.stdout.isatty()
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
COLOR = {
    "me": "\033[96m", "claude": "\033[92m", "wait": "\033[93m",
    "warn": "\033[91m", "mute": "\033[95m", "info": "\033[94m",
}
BADGE = {
    "me": "  TOI   ", "claude": " CLAUDE ", "wait": " ATTENTE",
    "warn": " ALERTE ", "mute": "  MICRO ", "info": "  INFO  ",
}


def log(message, kind="info"):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    if not INTERACTIVE:
        return
    color = COLOR.get(kind, COLOR["info"])
    sys.stdout.write(f"\r\033[K{DIM}{time.strftime('%H:%M:%S')}{OFF} "
                     f"{color}{BOLD}{BADGE.get(kind, BADGE['info'])}{OFF} {message}\n")
    sys.stdout.flush()


def status(kind, text, spinner=None):
    if not INTERACTIVE:
        return
    color = COLOR.get(kind, COLOR["info"])
    mark = f"{FRAMES[spinner % len(FRAMES)]} " if spinner is not None else "• "
    sys.stdout.write(f"\r\033[K{color}{mark}{text}{OFF}")
    sys.stdout.flush()


class Phase:
    """Live status line while a step runs; prints its duration on exit."""

    def __init__(self, label):
        self.label = label
        self.started = time.monotonic()
        self.done = threading.Event()
        self.thread = None

    def __enter__(self):
        if INTERACTIVE:
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
        return self

    def _spin(self):
        step = 0
        while not self.done.wait(0.12):
            elapsed = time.monotonic() - self.started
            sys.stdout.write(f"\r\033[K{FRAMES[step % 4]} {self.label} {elapsed:4.1f}s")
            sys.stdout.flush()
            step += 1

    def __exit__(self, *_):
        self.done.set()
        if self.thread:
            self.thread.join(timeout=0.5)
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def seconds(self):
        return time.monotonic() - self.started


def load_flatten():
    spec = importlib.util.spec_from_file_location("extract", os.path.join(TTS_DIR, "extract.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.flatten


def azure_setting(name, default=""):
    path = os.path.join(TTS_DIR, "azure.conf")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.strip().partition("=")
                if key == name and value.strip():
                    return value.strip().strip("\"'")
    return default


def transcribe(pcm):
    key = azure_setting("AZURE_SPEECH_KEY")
    region = azure_setting("AZURE_SPEECH_REGION", "francecentral")
    if not key:
        return ""
    buffer = io_wav(pcm)
    request = urllib.request.Request(
        f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation"
        f"/cognitiveservices/v1?language=fr-FR&format=simple",
        data=buffer,
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={RATE}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response).get("DisplayText", "").strip()
    except (urllib.error.HTTPError, OSError) as err:
        log(f"transcription impossible: {err}", "warn")
        return ""


def io_wav(pcm):
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()


_state = {"value": "idle", "at": 0.0}


def playback_active():
    """True only while sound is actually coming out.

    Cheap enough to check every frame, unlike polling say.sh, whose 400 ms cache let
    more playback through unnoticed than the 160 ms the detector needs to trigger.
    A paused reading (SIGSTOP, ctrl+alt+space) emits nothing, so it must not block us.
    """
    # say.sh computes the exact end of the audio from its byte count. Trust that first:
    # it also covers the window where the process is gone but sound is still coming out.
    try:
        with open(UNTILFILE, encoding="utf-8") as fh:
            if time.time() < float(fh.read().strip()):
                return True
    except (OSError, ValueError):
        pass
    try:
        with open(PIDFILE, encoding="utf-8") as fh:
            pid = fh.read().strip()
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            state = fh.read().rsplit(") ", 1)[1][0]
    except (OSError, IndexError, ValueError):
        return False
    return state not in "TZ"


def say_state(ttl=0.4):
    """Only for the playing/paused distinction; the guard uses playback_active()."""
    now = time.monotonic()
    if now - _state["at"] > ttl:
        _state["value"] = subprocess.run([SAY, "--state"], capture_output=True, text=True).stdout.strip()
        _state["at"] = now
    return _state["value"]


def say(*args):
    subprocess.run([SAY, *args], capture_output=True)


def run_claude(session_flag, prompt):
    # --bare would skip hooks and LSP, but it also drops the credentials and fails to authenticate.
    return subprocess.run(
        [CLAUDE, "-p", *session_flag, "--model", MODEL,
         "--append-system-prompt", SPOKEN_PROMPT, "--output-format", "json", prompt],
        capture_output=True, text=True, cwd=WORKDIR, timeout=300,
    )


def ask(prompt):
    # Sessions are scoped to their directory, so the first turn here has to create one.
    result = run_claude(["--resume", SESSION_ID], prompt)
    if result.returncode != 0 and "No conversation found" in result.stderr + result.stdout:
        log("session absente, creation", "info")
        result = run_claude(["--session-id", SESSION_ID], prompt)
    if result.returncode != 0:
        log(f"echec claude ({result.returncode}): {(result.stderr or result.stdout)[:160]}", "warn")
        return ""
    try:
        return json.loads(result.stdout).get("result", "").strip()
    except json.JSONDecodeError:
        return result.stdout.strip()


def read_phase():
    try:
        with open(PHASEFILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def focused_window():
    result = subprocess.run(["xdotool", "getwindowfocus", "getwindowname"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def inject(text, send=True, trace=None):
    """Type the transcription into the focused Claude Code conversation, or refuse."""
    step = trace or (lambda _: None)
    window = focused_window()
    step(f"fenetre focalisee : {window[:70]!r}")
    if WINDOW_MARK not in window:
        log(f"garde-fou: '{window[:45]}' n'est pas {WINDOW_MARK}, phrase abandonnee", "warn")
        return False
    step(f"envoi de {FOCUS_KEY} (claude-vscode.focus)")
    subprocess.run(["xdotool", "key", "--clearmodifiers", FOCUS_KEY], capture_output=True)
    time.sleep(0.4)
    # The window can change between the check and the keystroke, so verify again.
    window = focused_window()
    step(f"fenetre apres focus: {window[:70]!r}")
    if WINDOW_MARK not in window:
        log(f"garde-fou: le focus a change vers '{window[:45]}', phrase abandonnee", "warn")
        return False
    step(f"frappe de {len(text)} caracteres")
    subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "12", text],
                   capture_output=True)
    time.sleep(0.25)
    if send:
        step("envoi de Return")
        subprocess.run(["xdotool", "key", "--clearmodifiers", "Return"], capture_output=True)
    else:
        step("Return NON envoye - regarde la zone de saisie")
    return True


def drain(stream):
    """Throw away audio buffered while the loop was blocked.

    parecord keeps filling the pipe during transcription, injection and playback. Without
    this, the loop later reads seconds-old audio - recorded while the answer was being
    read - and checks the playback guard against the present. That is how our own voice
    ends up transcribed as the user's.
    """
    fd = stream.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    dropped = 0
    try:
        while True:
            chunk = stream.read(1 << 16)
            if not chunk:
                break
            dropped += len(chunk)
    except (BlockingIOError, OSError, TypeError):
        pass
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
    return dropped / (RATE * 2)


def capture(device):
    return subprocess.Popen(
        ["parecord", f"--device={device}", "--format=s16le", f"--rate={RATE}",
         "--channels=1", "--raw", f"--latency-msec={FRAME_MS}"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


def main():
    flatten = load_flatten()
    detector = SpeechDetector()
    recorder = capture(DEVICE)
    reference = capture(REFERENCE_DEVICE)
    # A Qubes AppVM loses its microphone attachment on reboot and captures pure zeros
    # with no error anywhere. Say so instead of listening to silence for an hour.
    probe = recorder.stdout.read(FRAME_BYTES * 25)
    if not np.frombuffer(probe, dtype=np.int16).any():
        log("MICRO MUET: zero numerique. En dom0: qvm-device mic attach <vm> dom0:mic", "warn")
    open(STATE_FILE, "w").close()
    # In vscode mode the answer lands in the panel, so the Stop hook is what reads it.
    # A new voice session starts a new spoken thread, not a continuation of yesterday's.
    if os.path.exists(HISTORYFILE):
        os.remove(HISTORYFILE)
    borrowed_auto = TARGET == "vscode" and not os.path.exists(AUTOFILE)
    if borrowed_auto:
        open(AUTOFILE, "w").close()

    def shutdown(*_):
        recorder.terminate()
        reference.terminate()
        for path in (STATE_FILE, AUTOFILE if borrowed_auto else None):
            if path and os.path.exists(path):
                os.remove(path)
        log("mode vocal arrete", "info")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    destination = f"conversation {WINDOW_MARK}" if TARGET == "vscode" else f"session headless ({MODEL})"
    if INTERACTIVE:
        print(f"{BOLD}{COLOR['claude']}  chat vocal{OFF}  {DIM}->{OFF} {destination}")
        print(f"{DIM}  micro coupe pendant la lecture | "
              f"seuil {detector.profile['speech_rms']:.0f} | "
              f"ctrl+alt+w micro | ctrl+alt+space pause | ctrl+c quitter{OFF}\n")
    log(f"on (cible={destination}, seuil={detector.profile['speech_rms']:.0f})", "info")

    preroll, utterance = [], []
    silence_run = 0
    listening = False
    was_muted = os.path.exists(MUTEFILE)
    if was_muted:
        log("coupe au demarrage", "mute")
    waiting_since = None
    spin = 0
    echo_hangover = 0
    gate_frames = 0
    leaked = 0
    gated = False

    while True:
        frame = recorder.stdout.read(FRAME_BYTES)
        if len(frame) < FRAME_BYTES:
            log("flux micro interrompu", "warn")
            shutdown()
        samples = np.frombuffer(frame, dtype=np.int16)
        echo = reference.stdout.read(FRAME_BYTES)
        reference_rms = rms(np.frombuffer(echo, dtype=np.int16)) if len(echo) == FRAME_BYTES else 0.0
        if reference_rms >= REF_ACTIVE_RMS:
            echo_hangover = ECHO_HANGOVER_FRAMES
        elif echo_hangover:
            echo_hangover -= 1
        if gated:
            gate_frames += 1

        muted = os.path.exists(MUTEFILE)
        if muted != was_muted:
            log("coupe - le reste du flux continue" if muted else "actif", "mute")
            was_muted = muted

        # Muting stops new capture only. Anything already in flight keeps running.
        if muted and listening:
            listening = False
            detector.reset()
            utterance.clear()
            preroll.clear()

        voiced = False if muted else detector.is_speech_frame(samples)
        spin += 1

        if waiting_since is not None:
            elapsed = time.monotonic() - waiting_since
            phase = read_phase()
            if playback_active():
                log(f"reponse lue apres {elapsed:.1f}s", "claude")
                waiting_since = None
            elif elapsed > WAIT_TIMEOUT:
                log(f"aucune lecture apres {elapsed:.0f}s, abandon de l'attente", "warn")
                waiting_since = None
            elif spin % 5 == 0:
                if phase == "reecriture":
                    status("wait", f"mise en forme orale de la reponse   {elapsed:4.1f}s",
                           spin // 5)
                else:
                    hint = "micro coupe" if muted else "tu peux deja parler"
                    status("wait", f"la conversation reflechit   {elapsed:4.1f}s   {DIM}({hint}){OFF}",
                           spin // 5)
            continue

        if muted:
            if spin % 25 == 0:
                status("mute", "micro coupe   ctrl+alt+w pour reprendre")
            continue

        # Two signals, each covering what the other cannot. The frame-counted hangover is
        # in the same time base as the microphone frames it gates, so a capture delay of
        # two seconds shifts both together; it covers the 460 ms the microphone keeps
        # hearing after the speakers go quiet. The declared end of the reading spans the
        # internal silences of the speech, measured up to 1520 ms between two sentences.
        # As an OR this can only extend the gate: a wall clock can never reopen it early.
        if echo_hangover or playback_active():
            if not gated:
                log("lecture detectee sur la sortie audio, micro coupe", "info")
                gated = True
                leaked = 0
            if listening:
                log("lecture demarree, captation en cours abandonnee", "warn")
            listening = False
            detector.reset()
            utterance.clear()
            preroll.clear()
            if spin % 25 == 0:
                status("claude", "lecture en cours   micro coupe   "
                                 f"{DIM}ctrl+alt+space pause, ctrl+alt+shift+space arret{OFF}")
            continue

        if gated:
            gated = False
            marker = "" if leaked == 0 else f", {BOLD}{leaked} TRAMES PASSANTES = FUITE{OFF}"
            log(f"micro rouvert ({gate_frames} trames coupees{marker})",
                "info" if leaked == 0 else "warn")
            gate_frames = 0
            detector.reset()
            preroll.clear()

        if not listening:
            preroll.append(frame)
            del preroll[:-PREROLL_FRAMES]
            if not detector.feed(samples):
                # A paused reading holds the audio device and is invisible otherwise.
                if spin % 40 == 0 and say_state() == "paused":
                    status("warn", "lecture EN PAUSE   ctrl+alt+space pour reprendre, "
                                   "ctrl+alt+shift+space pour couper")
                continue
            listening = True
            detector.reset()
            utterance = list(preroll)
            silence_run = 0
            status("me", "je t'ecoute...")
            continue

        utterance.append(frame)
        silence_run = 0 if voiced else silence_run + 1
        if silence_run < SILENCE_FRAMES_TO_STOP:
            continue

        listening = False
        detector.reset()
        preroll.clear()
        if len(utterance) < MIN_UTTERANCE_FRAMES:
            continue

        with Phase("transcription") as phase:
            text = transcribe(b"".join(utterance))
            stt = phase.seconds()
        utterance.clear()
        if not text:
            continue

        log(f"{text}   {DIM}[transcription {stt:.1f}s]{OFF}", "me")
        say("--stop")
    
        if TARGET == "vscode":
            sent = inject(text)
            # Both streams together: draining them apart desynchronises the pairing.
            lag = max(drain(recorder.stdout), drain(reference.stdout))
            detector.reset()
            preroll.clear()
            if sent:
                log(f"envoye dans la conversation   {DIM}[{lag:.1f}s d'audio purge]{OFF}", "info")
                waiting_since = time.monotonic()
            continue

        with Phase("Claude reflechit") as phase:
            answer = ask(text)
            thinking = phase.seconds()
        if not answer:
            log(f"aucune reponse   {DIM}[{thinking:.1f}s]{OFF}", "warn")
            continue

        spoken = flatten(answer)
        with Phase("synthese vocale") as phase:
            if spoken:
                say("--cloud", "--text", spoken)
            synthesis = phase.seconds()
        log(f"{spoken[:120]}   {DIM}[reflexion {thinking:.1f}s, synthese {synthesis:.1f}s]{OFF}", "claude")


def diagnose(argv):
    send = "--no-send" not in argv
    text = next((a for a in argv if not a.startswith("--")), "test injection vocale")
    print(f"Cible attendue : une fenetre dont le titre contient {WINDOW_MARK!r}")
    print("Tu as 5 secondes pour cliquer dans la conversation Claude Code.\n")
    for remaining in range(5, 0, -1):
        sys.stdout.write(f"\r\033[K{remaining}...")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r\033[K")
    ok = inject(text, send=send, trace=lambda s: print(f"  {s}"))
    print(f"\nresultat: {'texte tape' if ok else 'refuse par le garde-fou'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--inject" in sys.argv:
        sys.exit(diagnose([a for a in sys.argv[1:] if a != "--inject"]))
    main()
