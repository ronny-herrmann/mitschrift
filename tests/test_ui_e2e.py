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
           "MITSCHRIFT_ENV_FILE": str(data / "none.env"), "VAD_MIN_SILENCE_MS": "600", "PORT": str(port),
           "DIARIZATION": "0"}
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


def _shot(page, name):
    d = os.environ.get("SCREENSHOT_DIR")
    if d:
        page.screenshot(path=f"{d}/{name}.png", full_page=False)


def test_live_recording_in_browser(page, server):
    page.goto(server + "/#/aufnahme")
    page.wait_for_selector("#r-start")
    page.wait_for_function("document.querySelector('#sys').classList.contains('ok')", timeout=20_000)
    page.fill("#r-title", "E2E Live")
    assert page.is_disabled("#r-start")
    page.check("#r-consent")
    page.click("#r-start")
    page.wait_for_selector("#r-text .fin", timeout=20_000)
    page.wait_for_timeout(5000)
    assert page.locator("#r-text .fin").count() >= 2
    page.click("#r-pause")
    page.wait_for_selector("#r-state.paused")
    assert "Pausiert" in page.inner_text("#r-state")
    t1 = page.inner_text("#r-time")
    page.wait_for_timeout(1500)
    assert page.inner_text("#r-time") == t1, "Zeit steht während der Pause"
    _shot(page, "aufnahme_pause")
    page.click("#r-pause")
    page.wait_for_timeout(800)
    page.fill("#r-title-live", "E2E Live umbenannt")
    page.press("#r-title-live", "Enter")
    page.click("#r-stop")
    page.wait_for_selector(".done-card", timeout=30_000)
    _shot(page, "aufnahme_fertig")
    page.click(".done-card .btn.primary")
    page.wait_for_selector(".block")
    assert page.locator(".seg").count() >= 2
    assert page.locator("#audio").count() == 1
    assert page.input_value("#d-title") == "E2E Live umbenannt"
    _shot(page, "detail")
    page.click("[data-mtab=notes]") if page.is_visible("[data-mtab=notes]") else None
    _shot(page, "detail_notes")
    page.click("#d-export")
    _shot(page, "export_menu")
    page.keyboard.press("Escape")
    assert not [e for e in page.errors if "favicon" not in e], page.errors


def test_upload_detail_edit_and_summary_import(page, server):
    page.goto(server + "/#/hochladen")
    page.set_input_files("#file", str(FIXTURES / "drei_saetze_de.wav"))
    page.wait_for_selector(".job .chip.ok", timeout=30_000)
    page.click(".job .chip.ok a")
    page.wait_for_selector(".seg")
    assert page.locator(".seg").count() == 3

    seg = page.locator(".seg .txt").first
    seg.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Korrigiert per UI")
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    page.reload()
    page.wait_for_selector(".seg")
    assert page.inner_text(".seg .txt >> nth=0").strip() == "Korrigiert per UI"

    page.once("dialog", lambda d: d.accept("Frau Müller"))
    page.locator(".bname").first.click()
    page.wait_for_selector("text=Frau Müller")

    assert page.locator(".seg .mk.edit").count() == 1, "eigene Korrektur wird markiert"

    # Protokoll ohne KI → Art wählen → NOVA-Dialog
    page.click("#ai-sum")
    page.click(".menu-list [data-act=ergebnis]")
    page.wait_for_selector("#m-in")
    page.fill("#m-in", "## Ergebnisse\n- Korrigiert per UI. [S0]\n- Ohne Beleg.\n## Beschlüsse\nkeine")
    page.click("#m-ok")
    page.wait_for_selector(".prot .pl")
    assert page.locator(".prot .pl.unbelegt").count() == 1
    assert "50 % belegt" in page.inner_text(".pstats")
    page.click(".prot .ref")

    # Bereinigen ohne KI → NOVA-Dialog, Import mit Treue-Prüfung
    page.click("#ai-clean")
    page.wait_for_selector("#m-in")
    page.fill("#m-in", "[S0] Korrigiert per UI.\n[S1] Der Stadtrat hat 12 Millionen für ein neues Stadion beschlossen und alles vertagt.")
    page.click("#m-ok")
    page.wait_for_selector(".seg .mk.warn")
    # Die eigene Korrektur (S0) wird von der Bereinigung nicht angefasst
    assert page.inner_text(".seg .txt >> nth=0").strip() == "Korrigiert per UI"
    assert page.locator(".seg .mk.edit").count() == 1

    page.goto(server + "/#/transkripte")
    page.wait_for_selector(".item")
    assert page.locator(".item").count() >= 2
    assert not [e for e in page.errors if "favicon" not in e], page.errors


def test_info_pages(page, server):
    page.goto(server + "/#/faq")
    page.wait_for_selector("#doc details")
    assert "Häufige Fragen" in page.inner_text(".page-head")
    for tab, needle in [("technik", "Live-Aufnahme"), ("infrastruktur", "Schaubild"), ("datenschutz", "Fragen an den"),
                        ("sicherheit", "Schwächen"), ("personalrat", "Mitbestimmung")]:
        page.goto(server + f"/#/infos/{tab}")
        page.wait_for_selector("#doc p.lead")
        assert needle in page.inner_text("#doc"), tab
        assert page.locator(".info-tabs a.active").count() == 1
    assert not [e for e in page.errors if "favicon" not in e], page.errors
