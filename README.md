# Mitschrift

Lokale Audiotranskription und Protokollerstellung für die Verwaltung – Open Source, ohne Cloud,
für Standard-Büro-PCs (die Rechenarbeit macht ein Server im Hausnetz, nicht der Arbeitsplatz).

**Kernidee gegenüber dem Lieferanten-Ansatz:** Das Modell läuft nicht im Browser jedes Arbeitsplatz-PCs,
sondern **einmal auf einem Server**. Browser und Handy nehmen nur auf. Dadurch: kein Modell im
Browser-Cache, keine 9 Minuten für 1 Minute Audio, keine Freezes – und jeder Office-PC reicht.

| | |
|---|---|
| Spracherkennung | NVIDIA Parakeet TDT 0.6B v3 (int8, 25 Sprachen, sehr schnell auf CPU) **oder** whisper-large-v3-turbo-german (faster-whisper) – per Konfiguration umschaltbar |
| Satz-Erkennung | Silero VAD (ONNX, im Repo enthalten, kein Download) |
| Live | grauer Zwischentext nach ~2 s, fester Satz nach Sprechpause, optional KI-Bereinigung je Satz; Wellenform läuft mit |
| Nach dem Stopp | Ergebnis sofort gespeichert; im Hintergrund: genauer Durchlauf, Sprechererkennung (pyannote + 3D-Speaker), KI-Bereinigung |
| KI-Knöpfe | „Text bereinigen“ (korrigiert nur, Treue-Prüfung je Satz) und „Zusammenfassen & strukturieren“ (Belegpflicht) – direkt über das eigene Sprachmodell |
| Upload | mp3, m4a, wav, mp4, webm, ogg … (ffmpeg), Warteschlange |
| Protokoll | Belegpflicht: jede Aussage trägt `[S12]` = Segmentnummer; Prüf-Durchlauf markiert Unbelegtes; Klick springt zur Audiostelle |
| KI-Anbindung | eigenes Sprachmodell auf dem Server: Ministral 3 (Mistral AI, Apache-2.0) über llama.cpp – keine externe KI. Ohne Modell: Prompt kopieren/einfügen als Notlösung |
| Export | Word (.docx, auch mit Protokoll), Text, Markdown, Untertitel (.srt) |
| Speicherung | SQLite + Audio-Dateien im `data/`-Ordner; Audio einzeln oder per Frist löschbar |
| Lizenz-Kosten | 0 € (MIT/Apache-2.0/CC-BY-4.0) |

---

## 1. Schnellstart (Demo auf einem Windows-PC, ohne Docker)

Voraussetzungen: Python 3.11 oder 3.12, ffmpeg (`winget install ffmpeg`), einmalig Internet für den Modell-Download.

```powershell
cd mitschrift
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env            # bei Bedarf anpassen (Standard: Parakeet, CPU)
python scripts\download_models.py --only parakeet   # ~600 MB, einmalig
python -m uvicorn app.main:app --port 8000
```

Dann im Browser **http://localhost:8000** öffnen. Auf `localhost` erlaubt der Browser den Mikrofonzugriff
ohne HTTPS. Für andere Rechner/Handys im Netz ist HTTPS nötig → Abschnitt 2.

Linux/macOS analog (`source .venv/bin/activate`, `apt install ffmpeg` bzw. `brew install ffmpeg`).

## 2. Betrieb auf dem Server (Hetzner oder Rathaus)

Kurzfassung – Details und Begründung in [`docs/INFRASTRUKTUR.md`](docs/INFRASTRUKTUR.md):

1. Server anlegen (Ubuntu 24.04), unter „Cloud config“ den Inhalt von [`deploy/cloud-init.yml`](deploy/cloud-init.yml) einfügen und darin `REPO_URL`, `ACCESS_PASSWORD`, `ACME_EMAIL` anpassen.
2. Nach ca. 10 Minuten: Test unter `https://test.<IP-mit-Bindestrichen>.sslip.io`, Produktiv unter `https://<IP-mit-Bindestrichen>.sslip.io`.
3. Jede Änderung auf GitHub (`main`) landet automatisch in der Test-Instanz, `stable` in der Produktiv-Instanz.

Enthalten: HTTPS (Caddy), eigenes Sprachmodell (Ministral 3, llama.cpp), Firewall, automatische Sicherheitsupdates, nächtliche Sicherung.

## 3. Bake-off: Modelle auf den eigenen Aufnahmen vergleichen

```bash
python bench/bakeoff.py --audio-dir ./feldtest --backends parakeet,whisper --out bench/ergebnisse
```

* Liegt neben `sitzung3.m4a` eine `sitzung3.txt` mit dem Referenz-Wortlaut, wird die Wortfehlerrate (WER) berechnet.
* Ausgabe: `ergebnisse.md` (Tabelle für Folien), `ergebnisse.csv`, und je Modell die Transkripte als Text – für Blindbewertung durch Tester.
* Die Normalisierung (`bench/normalize.py`) zählt Groß-/Kleinschreibung, Satzzeichen, ß/ss und „äh“ nicht als Fehler.

## 4. Architektur

```
Browser / Handy (PWA)                     Server (Docker, Hausnetz oder DE-Hoster)
┌───────────────────────┐   PCM 16 kHz   ┌───────────────────────────────────────────┐
│ Mikrofon → Worklet    │ ─WebSocket──▶  │ Silero VAD → Satzende → ASR (Parakeet/    │
│ Live-Text, Editor,    │ ◀─JSON────────  │ Whisper) → Segment mit Zeitstempeln       │
│ Protokoll-Ansicht     │   HTTP         │ Upload-Warteschlange (ffmpeg → VAD → ASR) │
│ Upload                │ ─────────────▶ │ SQLite + Audio in /data                   │
└───────────────────────┘                │ Protokoll: Prompt / OpenAI-kompatible API │
                                         │ Prüf-Durchlauf [S12]-Belege (ohne KI)     │
                                         └───────────────────────────────────────────┘
```

Code-Übersicht:

| Datei | Zweck |
|---|---|
| `app/main.py` | REST-API, WebSocket `/ws/live`, statische Oberfläche |
| `app/live.py` | Live-Sitzung: PCM-Empfang, VAD, Sofort-Transkription, zweiter Durchlauf |
| `app/vad.py` | Silero VAD + Segment-Zustandsautomat (live und offline identisch) |
| `app/asr/` | Backends `parakeet.py`, `whisper.py`, `fake.py` hinter einer Schnittstelle |
| `app/pipeline.py` | Datei → Segmente → Modell (Batch) → Segmente mit Wort-Zeitstempeln |
| `app/protokoll.py` | Prompt-Bau, KI-Notizen mit Inhaltsprüfung, Aufbau je Protokollart, Belegprüfung |
| `app/store.py` | SQLite (Transkripte, Segmente, Protokolle, Glossar) |
| `app/export.py` | TXT/MD/SRT/DOCX |
| `app/static/` | Oberfläche (reines HTML/JS/CSS, keine externen Dienste, PWA) |
| `bench/` | Bake-off-Skript und Text-Normalisierung |
| `app/bereinigung.py` | KI-Bereinigung mit Treue-Prüfung (Wort- und Buchstabenvergleich, erfundene Zahlen) |
| `app/diarize.py` | Sprechererkennung, Teilung von Segmenten an Wortgrenzen |
| `tests/` | 24 Tests inkl. Browser-Ende-zu-Ende (Playwright, Fake-Mikrofon) |

## 5. Protokoll mit Belegpflicht – warum so

Der Test mit NOVA hat gezeigt: Zusammenfassung gut, aber nicht originalgetreu. Deshalb:

1. **Jede Aussage muss einen Beleg tragen** – `[S12]` verweist auf Segment 12 des Transkripts. Das steht
   so in der Anweisung an die KI (NOVA-Prompt oder API).
2. **Prüf-Durchlauf ohne KI**: Zeilen ohne Beleg werden gelb, Zeilen mit nicht existierendem Beleg rot.
   Das Ergebnis ist eine Quote „Aussagen mit gültigem Beleg“.
3. **API-Weg zusätzlich zweistufig**: erst Bausteine mit wörtlichem Zitat extrahieren; jedes Zitat wird
   gegen das Segment abgeglichen (unscharfer Textvergleich); Bausteine ohne passendes Zitat werden verworfen,
   bevor das Protokoll geschrieben wird.
4. **Mensch bestätigt**: Protokoll ist „Entwurf“, bis der Schriftführer es als geprüft markiert. Klick auf
   `S12` springt zur Stelle im Audio – Prüfen dauert Sekunden statt Minuten.

## 6. Datenschutz, IT-Sicherheit, Personalrat – Bausteine

* Datenfluss: Audio verlässt den Server nie; keine ausgehende Verbindung im Betrieb; kein Training mit Inhalten.
* Einwilligungs-Hinweis vor jeder Aufnahme (Checkbox „Teilnehmende informiert“).
* Löschkonzept: Audio getrennt vom Transkript löschbar; Fristen `RETENTION_AUDIO_HOURS`/`RETENTION_TRANSCRIPT_HOURS`.
* Zugriff: Anmeldung mit Zugangspasswort (`ACCESS_PASSWORD`, signiertes Sitzungs-Cookie, Sperre nach Fehlversuchen) hinter HTTPS; Stufe 2: SSO (Entra ID/AD).
* Quellcode vollständig einsehbar; Modelle mit offener Lizenz (Parakeet CC-BY-4.0, Whisper MIT, Silero MIT).
* Offen für die DSFA: Rollenkonzept, Protokollierung von Zugriffen (Audit-Log), Backup – siehe Roadmap.

## 7. Konfiguration

Alle Einstellungen in `.env` (siehe `.env.example`). Wichtigste:

| Variable | Bedeutung |
|---|---|
| `ASR_BACKEND` | `parakeet` (Standard) / `whisper` / `fake` |
| `DEVICE` | `cpu` / `cuda` |
| `VAD_MIN_SILENCE_MS` | Pause bis Satzende (550 ms; kleiner = schneller, mehr Satzbrüche) |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Sprachmodell für Bereinigen/Zusammenfassen (OpenAI-kompatibel; auf dem Server automatisch gesetzt) |
| `RETENTION_*_HOURS` | automatische Löschfristen (0 = aus) |
| `ACCESS_PASSWORD` | Zugangspasswort (auf einem Server Pflicht) |

## 8. Tests

```bash
pip install -r requirements-dev.txt
playwright install chromium            # nur für die Browser-Tests
pytest -q
```

## 9. Annahmen, Grenzen, Roadmap

**Annahmen im Prototyp**
* Sprache Deutsch; ein Mikrofon pro Sitzung (Konferenzmikrofon empfohlen – im lauten Raum ist das Mikrofon der größte Hebel, nicht das Modell).
* Sprechererkennung ist automatisch (nach dem Stopp); sehr ähnliche Stimmen können zusammenfallen – Namen per Klick korrigierbar.
* Handy: Aufnahme im Browser funktioniert über HTTPS; ohne VPN/Intranet-Zugriff bleibt „aufnehmen und später hochladen“.
* Parakeet und die Sprechererkennung werden von GitHub-Releases (sherpa-onnx) geladen und sind getestet; das optionale Whisper-Modell (`cstr/whisper-large-v3-turbo-german-int8_float32`) kommt von Hugging Face und ist noch ungetestet.

**Roadmap**
* Stufe 2: automatische Sprecherzuordnung, SSO, Audit-Log, Teams-Aufzeichnungs-Import, TOP-Vorlagen/Word-Vorlage der Stadt, Löschprotokoll
* Stufe 3: Chat mit dem Transkript, lokales LLM auf GPU, Gremiensaal-/Telefonanbindung
