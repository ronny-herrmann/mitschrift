"""Sprechererkennung (Diarisierung) mit sherpa-onnx.

- Segmentierung: pyannote segmentation 3.0 (MIT), ~6 MB
- Sprecher-Merkmale: 3D-Speaker ERes2Net (Apache-2.0), ~40 MB
Getestet auf 4 Stimmen (2 weiblich, 2 männlich): bei Schwellwert 0,7 alle Abschnitte
sauber zugeordnet, Wiedererkennung desselben Sprechers funktioniert; zwei sehr ähnliche
Männerstimmen mit identischem Text wurden zusammengelegt. Rechenzeit ≈ 1/5 der Audiodauer (2 Kerne).

Ergebnis wird auf Wort-Ebene zugeordnet: Wechselt der Sprecher mitten in einem Segment,
wird das Segment an der Wortgrenze geteilt.
"""

from __future__ import annotations

import logging
import tarfile
import threading
import urllib.request
from pathlib import Path

import numpy as np

from .store import Segment

log = logging.getLogger(__name__)

SEG_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
SEG_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/{SEG_DIR}.tar.bz2"
EMB_FILE = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
EMB_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/{EMB_FILE}"


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("Lade %s …", url)
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.rename(dest)


def ensure_models(models_dir: str | Path) -> tuple[Path, Path]:
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    seg = models_dir / SEG_DIR / "model.onnx"
    if not seg.exists():
        archive = models_dir / f"{SEG_DIR}.tar.bz2"
        _download(SEG_URL, archive)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(models_dir, filter="data")
        archive.unlink(missing_ok=True)
    emb = models_dir / EMB_FILE
    if not emb.exists():
        _download(EMB_URL, emb)
    return seg, emb


class Diarizer:
    def __init__(self, models_dir: str | Path, threshold: float = 0.7, num_speakers: int = 0, threads: int = 2):
        import sherpa_onnx

        seg, emb = ensure_models(models_dir)
        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg)), num_threads=threads),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb), num_threads=threads),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers if num_speakers > 0 else -1,
                                                         threshold=threshold),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not cfg.validate():
            raise RuntimeError("Konfiguration der Sprechererkennung ungültig")
        self._sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
        self._lock = threading.Lock()

    def turns(self, audio: np.ndarray) -> list[tuple[float, float, int]]:
        with self._lock:
            res = self._sd.process(audio.astype(np.float32)).sort_by_start_time()
        return [(float(r.start), float(r.end), int(r.speaker)) for r in res]


def _speaker_at(turns: list[tuple[float, float, int]], a: float, b: float) -> int | None:
    best, best_ov = None, 0.0
    mid = (a + b) / 2
    for s, e, spk in turns:
        ov = min(b, e) - max(a, s)
        if ov > best_ov:
            best, best_ov = spk, ov
    if best is None:  # Wort liegt in einer Lücke → nächstgelegener Abschnitt
        near = min(turns, key=lambda t: min(abs(mid - t[0]), abs(mid - t[1])), default=None)
        best = near[2] if near else None
    return best


def _drop_minor_speakers(turns: list[tuple[float, float, int]]) -> list[tuple[float, float, int]]:
    """Sprecher mit sehr wenig Redezeit (< 4 % und < 6 s) sind meist Fehlzuordnungen → Nachbar übernimmt."""
    total: dict[int, float] = {}
    for s, e, spk in turns:
        total[spk] = total.get(spk, 0.0) + (e - s)
    all_t = sum(total.values()) or 1.0
    minor = {spk for spk, t in total.items() if t < 6.0 and t / all_t < 0.04 and len(total) > 1}
    if not minor:
        return turns
    out = []
    for i, (s, e, spk) in enumerate(turns):
        if spk in minor:
            prev = next((t[2] for t in reversed(turns[:i]) if t[2] not in minor), None)
            nxt = next((t[2] for t in turns[i + 1:] if t[2] not in minor), None)
            spk = prev if prev is not None else nxt
        out.append((s, e, spk))
    return out


def assign_speakers(segments: list[Segment], turns: list[tuple[float, float, int]]) -> list[Segment]:
    """Sprecher zuordnen; Segmente bei Sprecherwechsel an Wortgrenzen teilen."""
    if not turns:
        return segments
    turns = _drop_minor_speakers(turns)
    # Sprecher in Reihenfolge des ersten Auftretens nummerieren: Sprecher 1, 2, …
    order: dict[int, str] = {}
    for _, _, spk in turns:
        if spk not in order:
            order[spk] = f"Sprecher {len(order) + 1}"

    out: list[Segment] = []
    for seg in segments:
        words = seg.words or []
        if len(words) < 2:
            spk = _speaker_at(turns, seg.start, seg.end)
            out.append(Segment(len(out), seg.start, seg.end, seg.text, order.get(spk, ""), seg.words))
            continue
        labels = [_speaker_at(turns, w["s"], w["e"]) for w in words]
        # Einzelne Ausreißer-Wörter glätten (A B A → A A A)
        for i in range(1, len(labels) - 1):
            if labels[i - 1] == labels[i + 1] != labels[i]:
                labels[i] = labels[i - 1]
        groups: list[tuple[int | None, list[dict]]] = []
        for w, lab in zip(words, labels):
            if groups and groups[-1][0] == lab:
                groups[-1][1].append(w)
            else:
                groups.append((lab, [w]))
        # Mini-Gruppen (< 2 Wörter) an Nachbarn anhängen
        merged: list[tuple[int | None, list[dict]]] = []
        for lab, ws in groups:
            if merged and len(ws) < 2:
                merged[-1][1].extend(ws)
            else:
                merged.append((lab, ws))
        if len(merged) == 1:
            out.append(Segment(len(out), seg.start, seg.end, seg.text, order.get(merged[0][0], ""), seg.words))
            continue
        for lab, ws in merged:
            text = " ".join(w["w"] for w in ws).strip()
            out.append(Segment(len(out), round(ws[0]["s"], 2), round(ws[-1]["e"], 2), text, order.get(lab, ""), ws))
    return out
