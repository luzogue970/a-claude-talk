#!/usr/bin/env python3
"""Tell human speech apart from ambient noise, and from the reading being played back."""

import json
import os

import numpy as np
import webrtcvad

RATE = 16000
FRAME = 320
PITCH_LO, PITCH_HI = 40, 229  # 400 Hz down to 70 Hz at 16 kHz
PROFILE = os.path.expanduser("~/.claude/tts/mic-profile.json")

DEFAULTS = {
    "floor_rms": 400.0,
    "speech_rms": 1200.0,
    "harmonic_min": 0.38,
    "window_frames": 12,
    "window_hits": 8,
    "vad_aggressiveness": 3,
}


def load_profile():
    profile = dict(DEFAULTS)
    if os.path.isfile(PROFILE):
        with open(PROFILE, encoding="utf-8") as fh:
            profile.update(json.load(fh))
    return profile


def rms(frame):
    return float(np.sqrt((frame.astype(np.float64) ** 2).mean()))


def harmonic_ratio(frame, previous=None):
    """Peak normalized autocorrelation over the human pitch range.

    Normalizing each lag by the energy of the two segments it actually compares keeps
    low-pitched voices from scoring lower than high-pitched ones purely from overlap.
    """
    signal = np.concatenate([previous, frame]) if previous is not None else frame
    signal = signal.astype(np.float64)
    signal -= signal.mean()
    size = signal.size
    high = min(PITCH_HI, size - 1)
    if high <= PITCH_LO:
        return 0.0
    squares = signal * signal
    cumulative = np.concatenate([[0.0], np.cumsum(squares)])
    total = cumulative[size]
    if total <= 0:
        return 0.0
    correlation = np.correlate(signal, signal, mode="full")[size - 1:]
    lags = np.arange(PITCH_LO, high)
    tail = total - cumulative[lags]
    head = cumulative[size - lags]
    ratios = correlation[PITCH_LO:high] / np.sqrt(np.maximum(tail * head, 1e-9))
    return float(ratios.max())


class SpeechDetector:
    def __init__(self, profile=None):
        self.profile = profile or load_profile()
        self.vad = webrtcvad.Vad(int(self.profile["vad_aggressiveness"]))
        self.history = []
        self.previous = None

    def measure(self, frame):
        """Per-filter verdict, so the meter can show which one rejects a frame."""
        level = rms(frame)
        harmonic = harmonic_ratio(frame, self.previous)
        self.previous = frame
        return {
            "rms": level,
            "harmonic": harmonic,
            "loud": level >= self.profile["speech_rms"],
            "voiced": harmonic >= self.profile["harmonic_min"],
            "vad": self.vad.is_speech(frame.tobytes(), RATE),
        }

    def is_speech_frame(self, frame):
        # Echo is handled by the loop's reference hangover: this threshold compared a
        # monitor level against a microphone noise floor, two unrelated scales.
        m = self.measure(frame)
        return m["loud"] and m["voiced"] and m["vad"]

    def feed(self, frame):
        """True once the window holds enough speech frames to call it a real utterance."""
        self.history.append(self.is_speech_frame(frame))
        del self.history[: -int(self.profile["window_frames"])]
        return sum(self.history) >= self.profile["window_hits"]

    def reset(self):
        self.history.clear()
