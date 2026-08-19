#!/usr/bin/env python3
"""Synthesize speech with ElevenLabs and stream raw PCM to stdout.

Exit codes: 0 ok, 2 no key configured, 3 empty input, 4 every attempt failed.
The caller uses a non-zero code to fall back to another engine.
"""

import json
import os
import sys
import urllib.error
import urllib.request

CONF = os.path.expanduser("~/.claude/tts/eleven.conf")
API = "https://api.elevenlabs.io/v1"
OUTPUT_FORMAT = "pcm_24000"
CHUNK = 8192
DEFAULTS = {
    "ELEVEN_MODEL": "eleven_v3",
    "ELEVEN_MODEL_FALLBACK": "eleven_multilingual_v2",
    "ELEVEN_VOICE_ID": "",
    "ELEVEN_STABILITY": "0.5",
    "ELEVEN_SIMILARITY": "0.75",
    "ELEVEN_SPEED": "1.0",
}


def setting(name):
    value = os.environ.get(name)
    if value:
        return value
    if os.path.isfile(CONF):
        with open(CONF, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, raw = line.partition("=")
                if key.strip() == name:
                    value = raw.strip().strip("\"'")
                    if value:
                        return value
    return DEFAULTS.get(name, "")


def call(path, key, data=None, stream=False):
    request = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(data).encode("utf-8") if data else None,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    response = urllib.request.urlopen(request, timeout=60)
    if not stream:
        with response:
            return json.load(response)
    with response:
        out = sys.stdout.buffer
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
        out.flush()
    return None


def list_voices(key):
    return call("/voices", key).get("voices", [])


def resolve_voice(key):
    voice = setting("ELEVEN_VOICE_ID")
    if voice:
        return voice
    voices = list_voices(key)
    if not voices:
        return ""
    print(f"eleven: no voice configured, using {voices[0].get('name')}", file=sys.stderr)
    return voices[0]["voice_id"]


def main():
    key = setting("ELEVEN_API_KEY")
    if not key:
        return 2

    if "--voices" in sys.argv:
        for voice in list_voices(key):
            labels = voice.get("labels") or {}
            print(f"{voice['voice_id']}\t{voice.get('name')}\t{labels.get('gender', '?')}\t{labels.get('accent', '?')}")
        return 0

    text = sys.stdin.read().strip()
    if not text:
        return 3

    try:
        voice = resolve_voice(key)
    except (urllib.error.HTTPError, OSError) as err:
        print(f"eleven: {err}", file=sys.stderr)
        return 4
    if not voice:
        print("eleven: account exposes no voice", file=sys.stderr)
        return 4

    body = {
        "text": text,
        "voice_settings": {
            "stability": float(setting("ELEVEN_STABILITY")),
            "similarity_boost": float(setting("ELEVEN_SIMILARITY")),
            "speed": float(setting("ELEVEN_SPEED")),
        },
    }

    models = [setting("ELEVEN_MODEL"), setting("ELEVEN_MODEL_FALLBACK")]
    for model in [m for m in models if m]:
        try:
            call(f"/text-to-speech/{voice}?output_format={OUTPUT_FORMAT}", key, {**body, "model_id": model}, stream=True)
            print(f"eleven: {model}: ok", file=sys.stderr)
            return 0
        except urllib.error.HTTPError as err:
            print(f"eleven: {model}: HTTP {err.code} {err.reason}", file=sys.stderr)
            if err.code in (401, 403, 429):
                break
        except OSError as err:
            print(f"eleven: {model}: {err}", file=sys.stderr)
            break
    return 4


if __name__ == "__main__":
    sys.exit(main())
