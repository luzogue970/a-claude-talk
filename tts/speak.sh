#!/usr/bin/env bash
# Claude Code Stop hook: speak the last assistant response.
set -uo pipefail

TTS_DIR="$HOME/.claude/tts"
SKIPFILE="$TTS_DIR/skip-next"
AUTOFILE="$TTS_DIR/auto-enabled"

# Forward control flags so the older entry point keeps working.
case "${1:-}" in
    --stop | --off | --on | --auto | --voices | --samples | --set)
        exec "$TTS_DIR/say.sh" "$@"
        ;;
esac

# Reading every turn is opt-in: by default only /lecture speaks.
[[ -f "$AUTOFILE" ]] || exit 0

# spoken.py calls `claude -p`, whose own Stop hook would call spoken.py again.
[[ -n "${CLAUDE_TTS_CHILD:-}" ]] && exit 0

if [[ -f "$SKIPFILE" ]]; then
    stale=$(( $(date +%s) - $(stat -c %Y "$SKIPFILE") ))
    rm -f "$SKIPFILE"
    # A sentinel older than one turn was left behind by something that never spoke.
    (( stale < 120 )) && exit 0
fi

[[ -f "$TTS_DIR/disabled" ]] && exit 0

payload=$(cat)
text=$(printf '%s' "$payload" | "$TTS_DIR/venv/bin/python" "$TTS_DIR/spoken.py") \
    || text=$(printf '%s' "$payload" | "$TTS_DIR/extract.py") || exit 0
printf '%s' "$text" | "$TTS_DIR/say.sh" --cloud --from-hook
exit 0
