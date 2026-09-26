"""End-to-End-Test der Oberfläche mit Playwright (Chromium, Fake-Mikrofon aus WAV-Datei).

Wird übersprungen, wenn Playwright/Chromium nicht installiert ist:
    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import FIXTURES, ROOT

playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("e2e")
    port = _free_port()
    env = {**os.environ, "ASR_BACKEND": "fake", "DATA_DIR": str(data), "MODELS_DIR": str(data / "models"),
           "MITSCHRIFT_ENV_FILE": str(data / "none.env"), "VAD_MIN_SILENCE_MS": "600", "PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    import urllib.request
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("Server startet nicht:\n" + (proc.stdout.read() if proc.stdout else ""))
    yield url
    proc.kill()


@pytest.fixture(scope="module")
def page(server):
    wav = FIXTURES / "drei_saetze_de.wav"
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}",
            "--autoplay-policy=no-user-gesture-required",
        ])
        ctx = browser.new_context(permissions=["microphone"], viewport={"width": 1100, "height": 900})
        pg = ctx.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.errors = errors  # type: ignore[attr-defined]
        yield pg
        browser.close()


def test_live_recording_in_browser(page, server):
    page.goto(server + "/#/aufnahme")
    page.wait_for_selector("#rec-btn")
    page.fill("#rec-title", "E2E Live")
    assert page.is_disabled("#rec-btn")
    page.check("#rec-consent")
    page.click("#rec-btn")
    # Fake-Mikrofon spielt die Datei (12 s) in Schleife → nach ~9 s müssen Segmente da sein
    page.wait_for_selector("#rec-live .seg", timeout=20_000)
    time.sleep(6)
    n = page.locator("#rec-live .seg").count()
    assert n >= 2, f"nur {n} Live-Segmente"
    assert "ms" in page.inner_text("#rec-live .seg .ms")
    page.click("#rec-btn")  # Stopp
    page.wait_for_selector("#rec-final:not(.hidden)", timeout=30_000)
    info = page.inner_text("#rec-final-info")
    assert "Segmente" in info
    page.click("#rec-final-link")
    page.wait_for_selector(".segrow")
    assert page.locator(".segrow").count() >= 2
    assert page.locator("#d-audio").count() == 1
    assert not [e for e in page.errors if "favicon" not in e], page.errors


def test_upload_list_detail_protokoll(page, server):
    page.goto(server + "/#/hochladen")
    page.set_input_files("#file", str(FIXTURES / "drei_saetze_de.wav"))
    page.wait_for_selector(".job .badge.ok", timeout=30_000)
    page.click(".job .badge.ok a")
    page.wait_for_selector(".segrow")
    assert page.locator(".segrow").count() == 3

    # Segment bearbeiten
    seg = page.locator(".segrow .txt").first
    seg.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Korrigiert per UI")
    page.locator("#d-title").click()  # blur → speichert
    page.wait_for_timeout(500)
    page.reload()
    page.wait_for_selector(".segrow")
    assert page.inner_text(".segrow .txt >> nth=0").strip() == "Korrigiert per UI"

    # Sprecher umbenennen (Prompt-Dialog)
    page.once("dialog", lambda d: d.accept("Frau Müller"))
    page.locator(".segrow .spk").first.click()
    page.wait_for_selector("text=Frau Müller")

    # Protokoll importieren und prüfen
    page.click("button[data-tab=protokoll]")
    page.click("#p-import")
    page.fill("#p-paste", "## Ergebnisse\n- Sitzung eröffnet. [S0]\n- Ohne Beleg.\n## Beschlüsse\nkeine")
    page.click("#p-paste-ok")
    page.wait_for_selector(".prot")
    assert page.locator(".prot .pl.ok").count() == 1
    assert page.locator(".prot .pl.unbelegt").count() == 1
    assert "50 %" in page.inner_text(".stat")
    page.click(".prot .ref")  # springt ins Transkript
    page.wait_for_selector(".segrow.active")

    # Liste
    page.goto(server + "/#/transkripte")
    page.wait_for_selector(".item")
    assert page.locator(".item").count() >= 2
    assert not [e for e in page.errors if "favicon" not in e], page.errors
