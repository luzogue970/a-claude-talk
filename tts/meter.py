#!/usr/bin/env python3
"""Live view of what the detector sees, so a rejected voice tells you which filter rejected it."""

import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.expanduser("~/.claude/tts"))
from detector import FRAME, RATE, SpeechDetector  # noqa: E402

DEVICE = os.environ.get("VOICE_DEVICE", "echo-cancel-source")
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0


def main():
    detector = SpeechDetector()
    profile = detector.profile
    print(f"Seuils actifs : rms>={profile['speech_rms']:.0f}  harmonicite>={profile['harmonic_min']}")
    print("Parle normalement. Colonnes : niveau, harmonicite, puis les 3 filtres.\n")

    recorder = subprocess.Popen(
        ["parecord", f"--device={DEVICE}", "--format=s16le", f"--rate={RATE}",
         "--channels=1", "--raw"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + SECONDS
    accepted = triggers = total = 0
    peak_rms = peak_harmonic = 0.0
    try:
        while time.monotonic() < deadline:
            chunk = recorder.stdout.read(FRAME * 2)
            if len(chunk) < FRAME * 2:
                break
            samples = np.frombuffer(chunk, dtype=np.int16)
            m = detector.measure(samples)
            ok = m["loud"] and m["voiced"] and m["vad"]
            detector.history.append(ok)
            del detector.history[: -int(profile["window_frames"])]
            fired = sum(detector.history) >= profile["window_hits"]
            total += 1
            accepted += ok
            peak_rms = max(peak_rms, m["rms"])
            peak_harmonic = max(peak_harmonic, m["harmonic"])
            if fired:
                triggers += 1
                detector.history.clear()
            if total % 5 == 0:
                bar = "#" * min(int(m["rms"] / 200), 24)
                sys.stdout.write(
                    f"\r\033[K{m['rms']:6.0f} |{bar:<24}| h={m['harmonic']:.2f} "
                    f"[{'FORT' if m['loud'] else '....'}]"
                    f"[{'VOIX' if m['voiced'] else '....'}]"
                    f"[{'VAD ' if m['vad'] else '....'}]"
                    f"{'  >>> PAROLE DETECTEE' if fired else ''}")
                sys.stdout.flush()
    finally:
        recorder.terminate()

    print(f"\n\ntrames analysees      : {total}")
    print(f"trames retenues       : {accepted} ({100 * accepted // max(total, 1)}%)")
    print(f"declenchements        : {triggers}")
    print(f"niveau maximum vu     : {peak_rms:.0f}   (seuil {profile['speech_rms']:.0f})")
    print(f"harmonicite maximum   : {peak_harmonic:.2f}  (seuil {profile['harmonic_min']})")
    if peak_rms < profile["speech_rms"]:
        print("\n-> Ta voix n'atteint jamais le seuil de niveau. Relance vcal, ou rapproche le micro.")
    elif peak_harmonic < profile["harmonic_min"]:
        print("\n-> Le niveau passe mais l'harmonicite non. Relance vcal.")
    elif triggers == 0:
        print("\n-> Les filtres passent mais jamais assez longtemps d'affilee. Baisse window_hits.")


if __name__ == "__main__":
    main()
