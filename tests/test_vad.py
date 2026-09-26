"""VAD- und Segmentierungstests mit synthetischer deutscher Sprache (espeak-ng)."""

import numpy as np
import soundfile as sf

from app.vad import CHUNK, Segmenter, SileroVAD, segment_audio
from tests.conftest import FIXTURES


def load_fixture():
    x, sr = sf.read(FIXTURES / "drei_saetze_de.wav", dtype="float32")
    assert sr == 16_000
    return x


def test_silence_yields_no_segments():
    vad = SileroVAD()
    assert segment_audio(np.zeros(16_000 * 3, dtype=np.float32), vad) == []


def test_three_sentences_become_three_segments():
    vad = SileroVAD()
    segs = segment_audio(load_fixture(), vad, min_silence_ms=600)
    assert len(segs) == 3, [(s.start_s, s.end_s) for s in segs]
    # Segmente liegen in Reihenfolge und überlappen nicht mit Sprache
    for a, b in zip(segs, segs[1:]):
        assert a.end <= b.start + CHUNK  # minimaler Vorlauf-Überlapp erlaubt
    assert all(1.5 < (s.end_s - s.start_s) < 5 for s in segs)


def test_streaming_matches_offline():
    x = load_fixture()
    vad = SileroVAD()
    offline = segment_audio(x, vad, min_silence_ms=600)
    vad.reset()
    seg = Segmenter(min_silence_ms=600)
    live = []
    for i in range(0, len(x) - CHUNK + 1, CHUNK):
        c = x[i:i + CHUNK]
        live += seg.push(c, vad.prob(c))
    live += seg.flush()
    assert [(s.start, s.end) for s in live] == [(s.start, s.end) for s in offline]


def test_max_segment_forces_cut():
    # 40 s Dauersprache: durch Wiederholen des Fixtures ohne Pausen
    x = load_fixture()
    speech = np.concatenate([x[int(0.3 * 16000):int(3.4 * 16000)]] * 13)  # ~40 s ohne lange Pause
    vad = SileroVAD()
    segs = segment_audio(speech, vad, min_silence_ms=2000, max_segment_s=8.0)
    assert len(segs) >= 4
    assert all((s.end_s - s.start_s) <= 8.7 for s in segs)
