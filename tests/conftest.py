"""Gemeinsame Test-Fixtures: App mit Fake-Backend in temporärem Datenverzeichnis."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def app_env(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    os.environ["ASR_BACKEND"] = "fake"
    os.environ["DATA_DIR"] = str(data)
    os.environ["MODELS_DIR"] = str(data / "models")
    os.environ["MITSCHRIFT_ENV_FILE"] = str(data / "nonexistent.env")
    os.environ["LIVE_FINAL_PASS"] = "1"
    os.environ["VAD_MIN_SILENCE_MS"] = "600"
    os.environ["DIARIZATION"] = "0"
    os.environ["OFFLINE_MERGE_SHORT_S"] = "0"
    os.environ["LIVE_PARTIALS"] = "1"
    return data


@pytest.fixture(scope="session")
def client(app_env):
    from fastapi.testclient import TestClient

    from app.main import app

    import time
    with TestClient(app) as c:
        for _ in range(100):
            if c.get("/api/health").json().get("ready"):
                break
            time.sleep(0.05)
        yield c
