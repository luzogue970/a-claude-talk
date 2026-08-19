#!/usr/bin/env bash
# Play one self-announcing sample per Azure voice. Pass voice names to restrict
# the list, otherwise every male candidate is played.
set -uo pipefail

TTS_DIR="$HOME/.claude/tts"
RATE=48000

declare -A LABELS=(
    ["fr-FR-Remy:DragonHDOmniLatestNeural"]="Remy HD Omni"
    ["fr-FR-Lucien:DragonHDOmniLatestNeural"]="Lucien HD Omni"
    ["fr-FR-Remy:DragonHDLatestNeural"]="Remy Dragon HD"
    ["fr-FR-Marc:MAI-Voice-2"]="Marc MAI Voice 2"
    ["fr-FR-Marc:MAI-Voice-2-Flash"]="Marc MAI Voice 2 Flash"
    ["fr-FR-RemyMultilingualNeural"]="Remy multilingue standard"
    ["fr-FR-LucienMultilingualNeural"]="Lucien multilingue standard"
    ["fr-FR-HenriNeural"]="Henri neural standard"
    ["en-US-Ethan:MAI-Voice-2-Flash"]="Ethan, MAI anglais lisant du francais"
    ["en-US-Grant:MAI-Voice-2-Flash"]="Grant, MAI anglais lisant du francais"
    ["en-US-Jasper:MAI-Voice-2-Flash"]="Jasper, MAI anglais lisant du francais"
    ["de-DE-Klaus:MAI-Voice-2-Flash"]="Klaus, MAI allemand lisant du francais"
)

DEFAULT=(
    "fr-FR-Remy:DragonHDOmniLatestNeural"
    "fr-FR-Lucien:DragonHDOmniLatestNeural"
    "fr-FR-Remy:DragonHDLatestNeural"
    "fr-FR-Marc:MAI-Voice-2"
    "fr-FR-Marc:MAI-Voice-2-Flash"
    "fr-FR-RemyMultilingualNeural"
    "fr-FR-HenriNeural"
)

PHRASE="Le hook de fin de tour recupere ta reponse, retire le code et les tableaux, puis me la fait lire. Le workflow de build echoue sur un timeout, et la pull request attend une code review."

voices=("$@")
[[ ${#voices[@]} -eq 0 ]] && voices=("${DEFAULT[@]}")

index=0
for voice in "${voices[@]}"; do
    index=$((index + 1))
    label="${LABELS[$voice]:-$voice}"
    printf 'numero %d  %-40s ' "$index" "$voice"
    pcm=$(mktemp "${TMPDIR:-/tmp}/azure-cmp-XXXXXX.pcm")
    if printf '%s' "Numero $index. $label. $PHRASE" \
        | AZURE_SPEECH_VOICE="$voice" AZURE_SPEECH_VOICE_FALLBACK="$voice" \
          "$TTS_DIR/azure.py" >"$pcm" 2>/dev/null && [[ -s "$pcm" ]]; then
        echo "ok"
        aplay -q -r "$RATE" -f S16_LE -t raw "$pcm" 2>/dev/null
    else
        echo "indisponible"
    fi
    rm -f "$pcm"
done
