"""Gemeinsamer Modell-Ordner für mehrere Instanzen (Test + Produktiv auf einem Server).

Beide Instanzen starten gleichzeitig und würden sonst dieselbe Datei parallel herunterladen und
entpacken – das Ergebnis wäre ein beschädigtes Modell. Deshalb:
- eine Dateisperre (flock) im Modell-Ordner: nur ein Prozess lädt, der andere wartet und nutzt dann das Ergebnis
- Download in eine eigene Temp-Datei, Entpacken in einen Temp-Ordner, danach atomar umbenennen:
  ein Modell-Ordner ist entweder vollständig da oder gar nicht.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


@contextlib.contextmanager
def download_lock(models_dir: Path):
    models_dir.mkdir(parents=True, exist_ok=True)
    lock_path = models_dir / ".download.lock"
    try:
        import fcntl
    except ImportError:  # Windows: nur eine Instanz pro Rechner, keine Sperre nötig
        yield
        return
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def download(url: str, dest: Path) -> None:
    """Lädt url nach dest (atomar: erst Temp-Datei, dann umbenennen)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    try:
        log.info("Lade %s …", url)
        with urllib.request.urlopen(url, timeout=60) as r, os.fdopen(fd, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def fetch_archive(url: str, models_dir: Path, dirname: str) -> Path:
    """Lädt ein .tar.bz2 und entpackt es atomar nach models_dir/dirname."""
    target = models_dir / dirname
    archive = models_dir / f".{dirname}.tar.bz2"
    download(url, archive)
    tmpdir = Path(tempfile.mkdtemp(dir=models_dir, prefix=f".{dirname}."))
    try:
        log.info("Entpacke %s …", dirname)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(tmpdir, filter="data")
        src = tmpdir / dirname if (tmpdir / dirname).is_dir() else tmpdir
        if target.exists():
            shutil.rmtree(target)
        os.replace(src, target)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(tmpdir, ignore_errors=True)
    return target


def purge(path: Path) -> None:
    """Beschädigtes Modell entfernen, damit es beim nächsten Versuch neu geladen wird."""
    log.warning("Entferne beschädigtes Modell %s", path)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
