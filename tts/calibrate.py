#!/usr/bin/env python3
"""Measure this room and this microphone, then write thresholds the detector can trust."""

import json
import subprocess
import sys
import time

import numpy as np

from detector import FRAME, PROFILE, RATE, harmonic_ratio, rms

DEVICE = "echo-cancel-source"


def capture(seconds):
    process = subprocess.Popen(
        ["parecord", f"--device={DEVICE}", "--format=s16le", f"--rate={RATE}",
         "--channels=1", "--raw"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    wanted = int(RATE * seconds) * 2
    data = b""
    while len(data) < wanted:
        chunk = process.stdout.read(4096)
        if not chunk:
            break
        data += chunk
    process.terminate()
    samples = np.frombuffer(data, dtype=np.int16)
    return [samples[i:i + FRAME] for i in range(0, len(samples) - FRAME, FRAME)]


def describe(frames):
    levels = np.array([rms(f) for f in frames])
    previous = None
    harmonics = []
    for frame in frames:
        harmonics.append(harmonic_ratio(frame, previous))
        previous = frame
    return levels, np.array(harmonics)


def audible(levels, harmonics, threshold):
    """Statistics computed on silence are meaningless; keep only frames with signal."""
    mask = levels > threshold
    return (levels[mask], harmonics[mask]) if mask.any() else (levels, harmonics)


def countdown(message, seconds):
    for remaining in range(seconds, 0, -1):
        sys.stdout.write(f"\r\033[K{message} dans {remaining}...")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r\033[K")


def main():
    print("Calibration du micro. Reste dans les conditions habituelles.\n")

    countdown("Phase 1 : NE PARLE PAS, laisse le bruit ambiant", 3)
    print("Phase 1 : silence, 6 secondes... (bruit de fond seulement)")
    noise_levels, noise_harmonics = describe(capture(6))
    # The floor describes the continuous background. Keystrokes and breaths are events:
    # short and broadband, already handled by the duration and harmonicity filters.
    noise_median = float(np.median(noise_levels))
    noise_floor = max(float(np.percentile(noise_levels, 75)), noise_median * 2, 20.0)
    _, audible_noise_h = audible(noise_levels, noise_harmonics, max(noise_median, 40.0))
    noise_harmonic = float(np.percentile(audible_noise_h, 90))
    print(f"  bruit  RMS median={noise_median:.0f}  p75={np.percentile(noise_levels, 75):.0f}"
          f"  p90={np.percentile(noise_levels, 90):.0f}  -> plancher retenu {noise_floor:.0f}")

    countdown("Phase 2 : PARLE normalement, sans t'arreter", 3)
    print("Phase 2 : parle, 6 secondes...")
    voice_levels, voice_harmonics = describe(capture(6))
    # Pauses between words are silence, not voice: they must not define the threshold.
    spoken_levels, spoken_harmonics = audible(voice_levels, voice_harmonics,
                                              max(noise_floor, float(np.median(voice_levels)) / 4))
    voice_body = float(np.percentile(spoken_levels, 30))
    voice_harmonic = float(np.percentile(spoken_harmonics, 25))
    print(f"  voix   RMS median={np.median(voice_levels):.0f}  p30 (trames parlees)={voice_body:.0f}"
          f"  harmonicite p25={voice_harmonic:.2f}  (bruit p90={noise_harmonic:.2f})")

    if voice_body <= noise_floor * 1.5:
        print(f"\nLa voix ({voice_body:.0f}) ne ressort pas assez du bruit continu ({noise_floor:.0f}).")
        print("Rapproche le micro, ou refais la mesure sans source de bruit continue.")
        return 1

    speech_rms = (noise_floor * voice_body) ** 0.5
    # Derived from YOUR voice, not from the noise: the goal is to let you through.
    # Bursts are stopped by the duration filter, not by this threshold.
    harmonic_min = min(max(voice_harmonic - 0.10, 0.20), 0.45)
    profile = {
        "floor_rms": round(noise_floor, 1),
        "speech_rms": round(max(speech_rms, noise_floor * 1.5), 1),
        "harmonic_min": round(max(harmonic_min, 0.2), 2),
        "window_frames": 12,
        "window_hits": 8,
        "vad_aggressiveness": 3,
    }
    with open(PROFILE, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2)

    separation = voice_body / max(noise_floor, 1)
    print(f"\nProfil ecrit dans {PROFILE}")
    print(json.dumps(profile, indent=2))
    print(f"\nSeparation voix/bruit : {separation:.1f}x", end="  ")
    print("(confortable)" if separation >= 3 else "(juste - la detection restera fragile)")
    print("Verifie avec : vmeter 10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
