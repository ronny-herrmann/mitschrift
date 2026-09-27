"""Zwei Instanzen laden gleichzeitig dasselbe Modell in denselben Ordner – Ergebnis muss intakt sein."""

import io
import multiprocessing as mp
import tarfile
from pathlib import Path

from app import modelcache


def _make_archive(path: Path, dirname: str) -> None:
    with tarfile.open(path, "w:bz2") as tar:
        for name in ("encoder.onnx", "tokens.txt"):
            data = (name * 200_000).encode()  # ein paar MB, damit sich die Prozesse überschneiden
            info = tarfile.TarInfo(f"{dirname}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _worker(url: str, models_dir: str, dirname: str, q) -> None:
    try:
        with modelcache.download_lock(Path(models_dir)):
            target = Path(models_dir) / dirname
            if not (target / "tokens.txt").exists():
                modelcache.fetch_archive(url, Path(models_dir), dirname)
        q.put("ok")
    except Exception as e:  # pragma: no cover
        q.put(repr(e))


def test_parallel_fetch_is_safe(tmp_path):
    src = tmp_path / "src.tar.bz2"
    _make_archive(src, "modell")
    models = tmp_path / "models"
    q = mp.Queue()
    procs = [mp.Process(target=_worker, args=(src.as_uri(), str(models), "modell", q)) for _ in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert sorted(q.get() for _ in procs) == ["ok"] * 3
    assert (models / "modell" / "tokens.txt").read_bytes() == b"tokens.txt" * 200_000
    assert (models / "modell" / "encoder.onnx").stat().st_size == len("encoder.onnx") * 200_000
    leftovers = [p.name for p in models.iterdir() if p.name not in ("modell", ".download.lock")]
    assert leftovers == []


def test_download_is_atomic(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 1000)
    dest = tmp_path / "out" / "a.bin"
    modelcache.download(src.as_uri(), dest)
    assert dest.read_bytes() == b"x" * 1000
    assert [p.name for p in dest.parent.iterdir()] == ["a.bin"]
