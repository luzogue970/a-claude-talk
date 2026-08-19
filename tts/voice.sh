#!/usr/bin/env bash
# Start, stop or inspect the hands-free voice loop.
set -uo pipefail

TTS_DIR="$HOME/.claude/tts"
PYTHON="$TTS_DIR/venv/bin/python"
PIDFILE="$TTS_DIR/voice.pid"
LOG="$TTS_DIR/voice.log"

running() {
    [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null
}

case "${1:-status}" in
    start)
        running && { echo "deja actif (pid $(<"$PIDFILE"))"; exit 0; }
        pactl list short sources 2>/dev/null | grep -q echo-cancel-source \
            || echo "attention: echo-cancel-source absent, le micro entendra la lecture" >&2
        setsid "$PYTHON" "$TTS_DIR/voice.py" >>"$LOG" 2>&1 &
        echo $! >"$PIDFILE"
        sleep 1
        running && echo "mode vocal: on (journal: $LOG)" || { echo "echec, voir $LOG" >&2; exit 1; }
        ;;
    stop)
        running || { echo "mode vocal: deja off"; exit 0; }
        kill -TERM "$(<"$PIDFILE")" 2>/dev/null
        rm -f "$PIDFILE" "$TTS_DIR/voice.running"
        "$TTS_DIR/say.sh" --stop
        echo "mode vocal: off"
        ;;
    meter)
        running && { echo "coupe le demon d'abord: voff" >&2; exit 1; }
        exec "$PYTHON" "$TTS_DIR/meter.py" "${2:-20}"
        ;;
    doctor)
        echo "--- ce qui peut empecher une lecture ---"
        for f in auto-enabled skip-next disabled mic-muted; do
            if [[ -e "$TTS_DIR/$f" ]]; then
                printf '  %-14s PRESENT  (%ss)\n' "$f" "$(( $(date +%s) - $(stat -c %Y "$TTS_DIR/$f") ))"
            else
                printf '  %-14s absent\n' "$f"
            fi
        done
        [[ -e "$TTS_DIR/auto-enabled" ]] \
            || echo "  -> lecture automatique INACTIVE : lance vwatch, ou 'say.sh --auto on'"
        [[ -e "$TTS_DIR/disabled" ]] && echo "  -> TTS coupe globalement : say.sh --on"
        [[ -e "$TTS_DIR/mic-muted" ]] && echo "  -> micro coupe : ctrl+alt+w"
        echo "--- moteur ---"
        grep -q '^AZURE_SPEECH_KEY=.\+' "$TTS_DIR/azure.conf" && echo "  cle Azure presente" \
            || echo "  PAS DE CLE AZURE : plus aucune lecture (piper desactive)"
        printf '  voix    %s\n' "$(grep -m1 '^AZURE_SPEECH_VOICE=' "$TTS_DIR/azure.conf" | cut -d= -f2-)"
        etat=$("$TTS_DIR/say.sh" --state)
        printf '  etat    %s\n' "$etat"
        [[ $etat == paused ]] && echo "  -> LECTURE EN PAUSE : elle bloque le micro. ctrl+alt+space, ou say.sh --stop"
        echo "--- micro ---"
        [[ -f "$TTS_DIR/mic-profile.json" ]] && echo "  profil calibre present" || echo "  PAS CALIBRE : lance vcal"
        pactl list short sources 2>/dev/null | grep -q echo-cancel-source \
            && echo "  echo-cancel-source actif" || echo "  echo-cancel-source ABSENT"
        ;;
    mute)
        case "${2:-toggle}" in
            on) touch "$TTS_DIR/mic-muted" ;;
            off) rm -f "$TTS_DIR/mic-muted" ;;
            toggle)
                if [[ -f "$TTS_DIR/mic-muted" ]]; then rm -f "$TTS_DIR/mic-muted"; else touch "$TTS_DIR/mic-muted"; fi
                ;;
            *) echo "usage: voice.sh mute [toggle|on|off]" >&2; exit 1 ;;
        esac
        [[ -f "$TTS_DIR/mic-muted" ]] && echo "micro: coupe" || echo "micro: actif"
        ;;
    inject)
        shift
        exec "$PYTHON" "$TTS_DIR/voice.py" --inject "$@"
        ;;
    calibrate)
        running && { echo "coupe le demon d'abord: voff" >&2; exit 1; }
        cd "$TTS_DIR" && exec "$PYTHON" calibrate.py
        ;;
    watch)
        running && { echo "le demon tourne deja (pid $(<"$PIDFILE")) - fais 'voff' d'abord" >&2; exit 1; }
        pactl list short sources 2>/dev/null | grep -q echo-cancel-source \
            || echo "attention: echo-cancel-source absent, le micro entendra la lecture" >&2
        echo "mode interactif - ctrl+c pour sortir"
        exec "$PYTHON" "$TTS_DIR/voice.py"
        ;;
    status)
        running && echo "on (pid $(<"$PIDFILE"))" || echo "off"
        ;;
    log)
        tail -n "${2:-30}" "$LOG" 2>/dev/null || echo "(journal vide)"
        ;;
    *)
        echo "usage: voice.sh start|stop|status|log [n]" >&2
        exit 1
        ;;
esac
