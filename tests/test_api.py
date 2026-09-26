"""API-Tests (Fake-Backend): Upload, Transkript, Bearbeitung, Export, Glossar, Live-WebSocket."""

import json
import time

import numpy as np
import soundfile as sf

from tests.conftest import FIXTURES


def wait_done(client, tid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/transcripts/{tid}").json()
        if d["status"] in ("done", "error"):
            return d
        time.sleep(0.2)
    raise AssertionError("Job nicht fertig")


def test_health(client):
    h = client.get("/api/health").json()
    assert h["model"]["backend"] == "fake"
    assert "ergebnis" in h["styles"]


def test_upload_flow(client):
    with open(FIXTURES / "drei_saetze_de.wav", "rb") as f:
        r = client.post("/api/upload", files={"file": ("drei_saetze_de.wav", f, "audio/wav")}, data={"title": "Testsitzung"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    d = wait_done(client, tid)
    assert d["status"] == "done", d
    assert d["title"] == "Testsitzung"
    assert 12 < d["duration"] < 13
    assert len(d["segments"]) == 3
    assert d["segments"][0]["start"] < d["segments"][1]["start"] < d["segments"][2]["start"]
    assert all(s["text"] for s in d["segments"])
    assert d["has_audio"]

    # Liste
    items = client.get("/api/transcripts").json()
    assert any(i["id"] == tid for i in items)

    # Bearbeiten
    r = client.patch(f"/api/transcripts/{tid}/segments/0", json={"text": "Korrigierter Text", "speaker": "Sprecher 1"})
    assert r.status_code == 200
    r = client.patch(f"/api/transcripts/{tid}/segments/1", json={"speaker": "Sprecher 1"})
    r = client.post(f"/api/transcripts/{tid}/speakers/rename", json={"von": "Sprecher 1", "zu": "Frau Müller"})
    assert r.json()["geaendert"] == 2
    d = client.get(f"/api/transcripts/{tid}").json()
    assert d["segments"][0]["text"] == "Korrigierter Text"
    assert d["segments"][0]["speaker"] == "Frau Müller"

    # Titel
    client.patch(f"/api/transcripts/{tid}", json={"title": "Neuer Titel"})
    assert client.get(f"/api/transcripts/{tid}").json()["title"] == "Neuer Titel"

    # Exporte
    txt = client.get(f"/api/transcripts/{tid}/export?format=txt").text
    assert "Frau Müller: Korrigierter Text" in txt and "[00:00]" in txt
    srt = client.get(f"/api/transcripts/{tid}/export?format=srt").text
    assert "-->" in srt
    md = client.get(f"/api/transcripts/{tid}/export?format=md").text
    assert md.startswith("# Neuer Titel")
    docx = client.get(f"/api/transcripts/{tid}/export?format=docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"

    # NOVA-Prompt enthält Regeln + Segmente
    prompt = client.get(f"/api/transcripts/{tid}/nova-prompt?style=ergebnis").text
    assert "[S0 |" in prompt and "Ergebnisprotokoll" in prompt and "[unklar]" in prompt

    # Protokoll einfügen und prüfen lassen
    md_in = "## Ergebnisse\n- Der Haushalt wurde vorgestellt. [S1]\n- Ohne Beleg.\n## Beschlüsse\nkeine\n- Falscher Beleg [S99]"
    r = client.post(f"/api/transcripts/{tid}/protokoll/import", json={"style": "ergebnis", "protokoll_md": md_in})
    assert r.status_code == 200, r.text
    p = r.json()["content"]["pruefung"]
    assert p["zeilen_inhalt"] == 3 and p["unbelegt"] == 1 and p["ungueltige_belege"] == 1
    statuses = [z["status"] for z in p["zeilen"] if not z["heading"] and z["text"].strip()]
    assert statuses == ["ok", "unbelegt", "ok", "ungueltig"] or statuses[:2] == ["ok", "unbelegt"]

    # Bestätigen
    r = client.patch(f"/api/transcripts/{tid}/protokoll", json={"status": "bestaetigt"})
    assert r.json()["status"] == "bestaetigt"

    # Audio löschen, Transkript bleibt
    assert client.delete(f"/api/transcripts/{tid}/audio").status_code == 200
    d = client.get(f"/api/transcripts/{tid}").json()
    assert not d["has_audio"] and len(d["segments"]) == 3

    # Ganz löschen
    assert client.delete(f"/api/transcripts/{tid}").status_code == 200
    assert client.get(f"/api/transcripts/{tid}").status_code == 404


def test_upload_rejects_unknown_type(client):
    r = client.post("/api/upload", files={"file": ("x.exe", b"abc", "application/octet-stream")})
    assert r.status_code == 400


def test_glossar_applies(client):
    client.put("/api/glossar", json={"eintraege": [{"von": "Testtext", "zu": "Glossartext"}, {"von": "", "zu": "x"}]})
    assert client.get("/api/glossar").json() == [{"von": "Testtext", "zu": "Glossartext"}]
    with open(FIXTURES / "drei_saetze_de.wav", "rb") as f:
        tid = client.post("/api/upload", files={"file": ("a.wav", f, "audio/wav")}).json()["id"]
    d = wait_done(client, tid)
    assert all("Glossartext" in s["text"] for s in d["segments"])
    client.put("/api/glossar", json={"eintraege": []})
    client.delete(f"/api/transcripts/{tid}")


def test_live_websocket(client):
    x, sr = sf.read(FIXTURES / "drei_saetze_de.wav", dtype="float32")
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    frame = 3200  # 100 ms
    with client.websocket_connect("/ws/live?title=Livetest") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        tid = ready["transcript_id"]
        for i in range(0, len(pcm), frame):
            ws.send_bytes(pcm[i:i + frame])
        ws.send_text(json.dumps({"type": "stop"}))
        segments = []
        final = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "segment":
                segments.append(msg)
            elif msg["type"] == "final":
                final = msg
                break
            elif msg["type"] == "error":
                raise AssertionError(msg)
        assert len(segments) == 3, segments
        assert [s["idx"] for s in segments] == [0, 1, 2]
        assert final["transcript_id"] == tid
        assert 12 < final["duration"] < 13
        assert final["refining"] is True
        assert len(final["segments"]) == 3  # Live-Ergebnis sofort gespeichert
    d = wait_done(client, tid)  # Verfeinerung im Hintergrund
    assert d["status"] == "done" and d["source"] == "live" and d["has_audio"]
    assert len(d["segments"]) == 3
    audio = client.get(f"/api/transcripts/{tid}/audio")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    client.delete(f"/api/transcripts/{tid}")


def test_live_disconnect_is_saved(client):
    x, sr = sf.read(FIXTURES / "drei_saetze_de.wav", dtype="float32")
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with client.websocket_connect("/ws/live?title=Abbruch") as ws:
        tid = ws.receive_json()["transcript_id"]
        for i in range(0, len(pcm) // 2, 3200):
            ws.send_bytes(pcm[i:i + 3200])
        # Verbindung einfach schließen → Server sichert
    d = wait_done(client, tid)
    assert d["status"] == "done" and d["duration"] > 5
    client.delete(f"/api/transcripts/{tid}")


def test_login_required_when_password_set(client, monkeypatch):
    from app import auth
    from app.config import settings
    monkeypatch.setattr(settings, "access_password", "geheim-123")
    client.cookies.clear()
    assert client.get("/api/transcripts").status_code == 401
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.post("/api/login", json={"password": "falsch"}).status_code == 401
    r = client.post("/api/login", json={"password": "geheim-123"})
    assert r.status_code == 204 and auth.COOKIE in r.cookies
    client.cookies.set(auth.COOKIE, r.cookies[auth.COOKIE])
    assert client.get("/api/transcripts").status_code == 200
    with client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "stop"}))
        while ws.receive_json()["type"] != "final":
            pass
    client.cookies.clear()
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_json()
    auth._failures.clear()
