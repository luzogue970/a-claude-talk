#!/usr/bin/env bash
# Speak text through piper (local) or Azure Speech (cloud).
# The Stop hook always uses local; /lecture asks for cloud and falls back to local.
set -uo pipefail

TTS_DIR="$HOME/.claude/tts"
VOICES_DIR="$TTS_DIR/voices"
SAMPLES_DIR="$TTS_DIR/samples"
PIDFILE="$TTS_DIR/current.pgid"
SKIPFILE="$TTS_DIR/skip-next"
LASTFILE="$TTS_DIR/last-spoken"
UNTILFILE="$TTS_DIR/playing-until"
DEDUPE_SECONDS="${CLAUDE_TTS_DEDUPE:-120}"
CONF="$TTS_DIR/voice.conf"
AZURE_CONF="$TTS_DIR/azure.conf"
ELEVEN_CONF="$TTS_DIR/eleven.conf"
PYTHON="$TTS_DIR/venv/bin/python"
AZURE_RATE=48000
ELEVEN_RATE=24000
CLOUD_ENGINE=azure

VOICE=fr_FR-tom-medium
SPEAKER=0
LENGTH_SCALE=1.0
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && source "$CONF"
VOICE="${CLAUDE_TTS_VOICE:-$VOICE}"
SPEAKER="${CLAUDE_TTS_SPEAKER:-$SPEAKER}"
LENGTH_SCALE="${CLAUDE_TTS_LENGTH_SCALE:-$LENGTH_SCALE}"

signal_group() {
    [[ -f "$PIDFILE" ]] || return 1
    local pgid
    pgid=$(<"$PIDFILE")
    [[ -n "$pgid" ]] || return 1
    kill -"$1" -- "-$pgid" 2>/dev/null
}

playback_state() {
    [[ -f "$PIDFILE" ]] || { echo idle; return; }
    local state
    state=$(ps -o state= -p "$(<"$PIDFILE")" 2>/dev/null | tr -d ' ')
    [[ -z $state ]] && { echo idle; return; }
    [[ $state == T* ]] && echo paused || echo playing
}

stop_current() {
    # A stopped process never reaps SIGTERM, so wake it before killing it.
    signal_group CONT
    signal_group TERM
    # Leaving UNTILFILE behind kept the guard closed for seconds after a cut.
    rm -f "$PIDFILE" "$UNTILFILE"
}

toggle_pause() {
    case "$(playback_state)" in
        playing)
            # Without a monitor on screen, a pause is invisible and blocks the microphone
            # until someone notices. Cut instead.
            if [[ -f "$TTS_DIR/voice.running" ]]; then
                signal_group STOP && echo "pause"
            else
                stop_current
                echo "arret (aucun moniteur vocal actif, une pause serait invisible)"
            fi
            ;;
        paused) signal_group CONT && echo "reprise" ;;
        *) echo "aucune lecture en cours" ;;
    esac
}

play_local() {
    local text model rate
    model="$VOICES_DIR/$VOICE.onnx"
    [[ -f "$model" && -f "$model.json" ]] || return 1
    rate=$(jq -r '.audio.sample_rate' "$model.json")
    # espeak-ng reads English with French rules; the lexicon fixes it. The cloud
    # voice is multilingual and must receive the original spelling instead.
    text=$(printf '%s' "$1" | "$TTS_DIR/frenchify.py")
    stop_current
    # setsid may fork, so $! is not reliably the group leader: let the child record itself.
    setsid bash -c '
        echo $$ >"$1"
        printf "%s" "$2" \
          | "$3" -m piper -m "$4" -s "$5" --length-scale "$6" --output-raw 2>/dev/null \
          | aplay -q -r "$7" -f S16_LE -t raw - 2>/dev/null
        rm -f "$1"
    ' _ "$PIDFILE" "$text" "$PYTHON" "$model" "$SPEAKER" "$LENGTH_SCALE" "$rate" >/dev/null 2>&1 &
}

play_cloud() {
    local text="$1" engine="$2" pcm err backend rate
    if [[ $engine == eleven ]]; then
        backend="$TTS_DIR/eleven.py"
        rate=$ELEVEN_RATE
    else
        backend="$TTS_DIR/azure.py"
        rate=$AZURE_RATE
    fi
    pcm=$(mktemp "${TMPDIR:-/tmp}/claude-tts-XXXXXX.pcm")
    err="$pcm.err"
    if ! printf '%s' "$text" | "$backend" >"$pcm" 2>"$err" || [[ ! -s "$pcm" ]]; then
        [[ -s "$err" ]] && cat "$err" >&2
        rm -f "$pcm" "$err"
        # Silence beats the robotic local voice; opt back in with CLAUDE_TTS_FALLBACK=local.
        if [[ ${CLAUDE_TTS_FALLBACK:-none} == local ]]; then
            play_local "$text"
        else
            echo "cloud indisponible, lecture abandonnee (CLAUDE_TTS_FALLBACK=local pour piper)" >&2
        fi
        return
    fi
    cat "$err" >&2
    rm -f "$err"
    stop_current
    # The exact end of the audio is computable from its size: no need to infer it from a
    # process state, which says nothing about the sound still travelling to the speakers.
    local deadline
    deadline=$(awk -v b="$(stat -c %s "$pcm")" -v r="$rate" -v n="$(date +%s.%N)" \
        'BEGIN{printf "%.3f", n + b/(r*2) + 0.3}')
    printf '%s\n' "$deadline" >"$UNTILFILE"
    setsid bash -c '
        echo $$ >"$1"
        aplay -q -r "$2" -f S16_LE -t raw "$3" 2>/dev/null
        rm -f "$3"
        [[ $(cat "$1" 2>/dev/null) == "$$" ]] && rm -f "$1" "$4"
    ' _ "$PIDFILE" "$rate" "$pcm" "$UNTILFILE" >/dev/null 2>&1 &
}

list_voices() {
    local v name
    for v in "$VOICES_DIR"/*.onnx.json; do
        name=$(basename "$v" .onnx.json)
        printf "%-22s speakers=%-4s %s\n" "$name" "$(jq -r '.num_speakers' "$v")" \
            "$([[ $name == "$VOICE" ]] && echo '<- active')"
    done
}

list_azure_voices() {
    local key region
    key=$(grep -m1 '^AZURE_SPEECH_KEY=' "$AZURE_CONF" 2>/dev/null | cut -d= -f2-)
    region=$(grep -m1 '^AZURE_SPEECH_REGION=' "$AZURE_CONF" 2>/dev/null | cut -d= -f2-)
    key="${AZURE_SPEECH_KEY:-$key}"
    region="${AZURE_SPEECH_REGION:-${region:-francecentral}}"
    [[ -n "$key" ]] || { echo "no key in $AZURE_CONF" >&2; return 1; }
    curl -sf -H "Ocp-Apim-Subscription-Key: $key" \
        "https://$region.tts.speech.microsoft.com/cognitiveservices/voices/list" \
        | jq -r '.[] | select(.Locale == "fr-FR") | "\(.ShortName)\t\(.Gender)\t\(.VoiceType)"'
}

build_samples() {
    local text="Bonjour. Voici un extrait de la voix de synthese, pour que tu puisses la comparer aux autres avant de choisir."
    mkdir -p "$SAMPLES_DIR"
    local v name
    for v in "$VOICES_DIR"/*.onnx.json; do
        name=$(basename "$v" .onnx.json)
        printf '%s' "$text" | "$PYTHON" -m piper -m "${v%.json}" -s 0 \
            --length-scale "$LENGTH_SCALE" -f "$SAMPLES_DIR/$name.wav" 2>/dev/null
        echo "$SAMPLES_DIR/$name.wav"
    done
}

engine=local
from_hook=0
text=

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stop) stop_current; exit 0 ;;
        --pause) [[ $(playback_state) == playing ]] && signal_group STOP; echo "$(playback_state)"; exit 0 ;;
        --resume) [[ $(playback_state) == paused ]] && signal_group CONT; echo "$(playback_state)"; exit 0 ;;
        --toggle) toggle_pause; exit 0 ;;
        --state) playback_state; exit 0 ;;
        --off) stop_current; touch "$TTS_DIR/disabled"; echo "TTS off"; exit 0 ;;
        --on) rm -f "$TTS_DIR/disabled"; echo "TTS on"; exit 0 ;;
        --auto)
            case "${2:-}" in
                on) touch "$TTS_DIR/auto-enabled"; echo "lecture automatique: on" ;;
                off) rm -f "$TTS_DIR/auto-enabled" "$SKIPFILE"; echo "lecture automatique: off" ;;
                *) [[ -f "$TTS_DIR/auto-enabled" ]] && echo "on" || echo "off" ;;
            esac
            exit 0
            ;;
        --key | --region | --set-azure-voice)
            [[ -n "${2:-}" ]] || { echo "usage: say.sh $1 <valeur>" >&2; exit 1; }
            case "$1" in
                --key) field=AZURE_SPEECH_KEY ;;
                --region) field=AZURE_SPEECH_REGION ;;
                *) field=AZURE_SPEECH_VOICE ;;
            esac
            AZURE_CONF="$AZURE_CONF" FIELD="$field" python3 -c '
import os, re, sys
path, field = os.environ["AZURE_CONF"], os.environ["FIELD"]
with open(path, encoding="utf-8") as fh:
    body = fh.read()
body = re.sub(rf"(?m)^{field}=.*$", f"{field}=" + sys.argv[1], body)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(body)
' "$2" || exit 1
            chmod 600 "$AZURE_CONF"
            echo "$field enregistre dans $AZURE_CONF"
            exec "$0" --test
            ;;
        --eleven-key)
            [[ -n "${2:-}" ]] || { echo "usage: say.sh --eleven-key <cle>" >&2; exit 1; }
            ELEVEN_CONF="$ELEVEN_CONF" python3 -c '
import os, re, sys
path = os.environ["ELEVEN_CONF"]
with open(path, encoding="utf-8") as fh:
    body = fh.read()
body = re.sub(r"(?m)^ELEVEN_API_KEY=.*$", "ELEVEN_API_KEY=" + sys.argv[1], body)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(body)
' "$2" || exit 1
            chmod 600 "$ELEVEN_CONF"
            echo "cle enregistree dans $ELEVEN_CONF"
            exec "$0" --test eleven
            ;;
        --set-eleven-voice)
            [[ -n "${2:-}" ]] || { echo "usage: say.sh --set-eleven-voice <voice_id>" >&2; exit 1; }
            ELEVEN_CONF="$ELEVEN_CONF" python3 -c '
import os, re, sys
path = os.environ["ELEVEN_CONF"]
with open(path, encoding="utf-8") as fh:
    body = fh.read()
body = re.sub(r"(?m)^ELEVEN_VOICE_ID=.*$", "ELEVEN_VOICE_ID=" + sys.argv[1], body)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(body)
' "$2" || exit 1
            echo "voix ElevenLabs: $2"
            exec "$0" --test eleven
            ;;
        --set-engine)
            case "${2:-}" in
                azure | eleven)
                    printf 'CLOUD_ENGINE=%s\n' "$2" >>"$CONF"
                    python3 - "$CONF" "$2" <<'PY'
import re, sys
path, value = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    lines = [l for l in fh if not l.startswith("CLOUD_ENGINE=")]
lines.append(f"CLOUD_ENGINE={value}\n")
with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)
PY
                    echo "moteur cloud: $2"
                    ;;
                *) echo "usage: say.sh --set-engine azure|eleven" >&2; exit 1 ;;
            esac
            exit 0
            ;;
        --test)
            backend="${2:-azure}"
            [[ $backend == eleven ]] && rate=$ELEVEN_RATE || rate=$AZURE_RATE
            printf '%s' "Test de la voix. Le workflow de build echoue sur un timeout, et la pull request attend une code review." \
                | "$TTS_DIR/$backend.py" >/tmp/claude-tts-test.pcm
            status=$?
            if [[ $status -ne 0 ]]; then
                rm -f /tmp/claude-tts-test.pcm
                echo "echec (code $status) - /lecture utilisera le repli" >&2
                exit $status
            fi
            aplay -q -r "$rate" -f S16_LE -t raw /tmp/claude-tts-test.pcm
            rm -f /tmp/claude-tts-test.pcm
            exit 0
            ;;
        --voices) list_voices; exit 0 ;;
        --azure-voices) list_azure_voices; exit ;;
        --eleven-voices) "$TTS_DIR/eleven.py" --voices </dev/null; exit ;;
        --samples) build_samples; exit 0 ;;
        --set)
            [[ -f "$VOICES_DIR/${2:-}.onnx" ]] || { echo "unknown voice: ${2:-}" >&2; exit 1; }
            printf 'VOICE=%s\nSPEAKER=%s\nLENGTH_SCALE=%s\n' "$2" "${3:-0}" "${4:-$LENGTH_SCALE}" >"$CONF"
            echo "voice=$2 speaker=${3:-0} length_scale=${4:-$LENGTH_SCALE}"
            exit 0
            ;;
        --cloud) engine="$CLOUD_ENGINE"; shift ;;
        --azure) engine=azure; shift ;;
        --eleven) engine=eleven; shift ;;
        --local) engine=local; shift ;;
        --from-hook) from_hook=1; shift ;;
        --text) text="${2:-}"; shift 2 ;;
        *) shift ;;
    esac
done

[[ -f "$TTS_DIR/disabled" ]] && exit 0
[[ -x "$PYTHON" ]] || exit 0

[[ -n "$text" ]] || text=$(cat)
[[ -z "${text//[[:space:]]/}" ]] && exit 0

# A bare "recupere" is read wrong by every French voice; restore what was dropped.
text=$(printf '%s' "$text" | "$TTS_DIR/accents.py")

# Two entry points can target the same answer; never read it twice in a row.
digest=$(printf '%s' "$text" | sha256sum | cut -d' ' -f1)
if [[ -f "$LASTFILE" ]]; then
    read -r last_digest last_time <"$LASTFILE"
    if [[ $digest == "$last_digest" ]] && (( $(date +%s) - last_time < DEDUPE_SECONDS )); then
        echo "deja lu il y a moins de ${DEDUPE_SECONDS}s, lecture ignoree" >&2
        exit 0
    fi
fi
printf '%s %s\n' "$digest" "$(date +%s)" >"$LASTFILE"

# /lecture speaks its own rewrite; the Stop hook that follows must stay quiet.
[[ $from_hook -eq 0 && -f "$TTS_DIR/auto-enabled" ]] && touch "$SKIPFILE"

if [[ $engine == local ]]; then
    play_local "$text"
else
    play_cloud "$text" "$engine"
fi
exit 0
