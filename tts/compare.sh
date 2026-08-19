#!/usr/bin/env bash
# Play one self-announcing sample per candidate voice, in order.
set -uo pipefail

TTS_DIR="$HOME/.claude/tts"
PYTHON="$TTS_DIR/venv/bin/python"
LENGTH_SCALE="${CLAUDE_TTS_LENGTH_SCALE:-1.0}"

CANDIDATES=(
    "fr_FR-tom-medium:0:tom"
    "fr_FR-upmc-medium:0:upmc jessica"
    "fr_FR-upmc-medium:1:upmc pierre"
    "fr_FR-mls-medium:0:m l s, locuteur zero"
    "fr_FR-mls-medium:1:m l s, locuteur un"
    "fr_FR-mls-medium:2:m l s, locuteur deux"
    "fr_FR-siwis-medium:0:siwis"
)

PHRASE="Le hook de fin de tour recupere ta derniere reponse, en retire le code et les tableaux, puis me la fait lire. Tu peux couper la lecture a tout moment."

for entry in "${CANDIDATES[@]}"; do
    IFS=: read -r voice speaker label <<<"$entry"
    model="$TTS_DIR/voices/$voice.onnx"
    [[ -f "$model" ]] || continue
    rate=$(jq -r '.audio.sample_rate' "$model.json")
    echo "-> $voice speaker=$speaker"
    printf '%s' "Voix $label. $PHRASE" \
        | "$PYTHON" -m piper -m "$model" -s "$speaker" --length-scale "$LENGTH_SCALE" --output-raw 2>/dev/null \
        | aplay -q -r "$rate" -f S16_LE -t raw - 2>/dev/null
done
