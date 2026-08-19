#!/usr/bin/env python3
"""Synthesize French speech with Azure Speech and stream raw PCM to stdout.

Exit codes: 0 ok, 2 no key configured, 3 empty input, 4 every attempt failed.
The caller uses a non-zero code to fall back to the local engine.
"""

import os
import sys
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

CONF = os.path.expanduser("~/.claude/tts/azure.conf")
OUTPUT_FORMAT = "raw-48khz-16bit-mono-pcm"
CHUNK = 8192
DEFAULTS = {
    "AZURE_SPEECH_REGION": "francecentral",
    "AZURE_SPEECH_VOICE": "fr-FR-Remy:DragonHDOmniLatestNeural",
    "AZURE_SPEECH_VOICE_FALLBACK": "fr-FR-RemyMultilingualNeural",
    "AZURE_SPEECH_PARAMETERS": "enhancePronunciation=true",
    "AZURE_SPEECH_RATE": "0%",
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


def build_ssml(text, voice, parameters):
    # HD voices reject <prosody>; standard neural voices need it for the rate.
    # Every HD tier (DragonHD, DragonHDOmni, MAI-Voice) uses a colon in its name.
    if ":" in voice:
        attrs = f" parameters='{parameters}'" if parameters else ""
        body = f"<voice name='{voice}'{attrs}>{escape(text)}</voice>"
    else:
        rate = setting("AZURE_SPEECH_RATE")
        body = f"<voice name='{voice}'><prosody rate='{rate}'>{escape(text)}</prosody></voice>"
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='fr-FR'>"
        f"{body}</speak>"
    )


def synthesize(ssml, key, region):
    request = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
            "User-Agent": "claude-code-tts",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        out = sys.stdout.buffer
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
        out.flush()


def main():
    key = setting("AZURE_SPEECH_KEY")
    if not key:
        return 2

    text = sys.stdin.read().strip()
    if not text:
        return 3

    voice = setting("AZURE_SPEECH_VOICE")
    fallback = setting("AZURE_SPEECH_VOICE_FALLBACK")
    region = setting("AZURE_SPEECH_REGION")

    # Degrade one step at a time: tuned HD voice, plain HD voice, standard voice.
    attempts = [(voice, setting("AZURE_SPEECH_PARAMETERS")), (voice, "")]
    if fallback and fallback != voice:
        attempts.append((fallback, ""))

    for name, parameters in attempts:
        try:
            synthesize(build_ssml(text, name, parameters), key, region)
            print(f"azure: {name}: ok", file=sys.stderr)
            return 0
        except urllib.error.HTTPError as err:
            print(f"azure: {name}: HTTP {err.code} {err.reason}", file=sys.stderr)
            if err.code in (401, 403, 429):
                break
        except OSError as err:
            print(f"azure: {name}: {err}", file=sys.stderr)
            break
    return 4


if __name__ == "__main__":
    sys.exit(main())
