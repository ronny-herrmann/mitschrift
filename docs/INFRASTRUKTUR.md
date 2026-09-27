# Infrastruktur – Mitschrift

## Zielbild: ein geschlossenes System

Alles läuft auf **einem Server in Deutschland**. Es gibt keine externe KI-API, kein NOVA im Datenfluss und im Betrieb keine Verbindung nach außen.

```
                   Internet / Stadtnetz (nur HTTPS 443)
                                │
                     ┌──────────▼──────────┐
                     │ Caddy (HTTPS, Let's │   Sicherheits-Header, Zertifikate automatisch
                     │ Encrypt, Weiterleit.)│
                     └───┬─────────────┬───┘
            test.<host>  │             │  <host>
                ┌────────▼───┐   ┌─────▼──────┐
                │ Instanz    │   │ Instanz    │   FastAPI-App: Oberfläche, Live-WebSocket,
                │ TEST       │   │ PRODUKTIV  │   Spracherkennung (Parakeet), Sprechererkennung,
                │ (Branch    │   │ (Branch    │   Warteschlange, SQLite + Audio je Instanz
                │  main)     │   │  stable)   │
                └─────┬──────┘   └─────┬──────┘
                      └───────┬────────┘   internes Docker-Netz, kein Port nach außen
                      ┌───────▼────────┐
                      │ Sprachmodell   │   llama.cpp-Server, Ministral 3 (Mistral AI, Apache-2.0),
                      │ (llm:8080)     │   OpenAI-kompatible Schnittstelle, gemeinsam genutzt
                      └────────────────┘
```

| Baustein | Technik | Warum |
|---|---|---|
| Spracherkennung | NVIDIA Parakeet TDT 0.6B v3 (int8, sherpa-onnx) | gemessen ~10× Echtzeit auf 2 CPU-Kernen, gute deutsche Qualität |
| Sprechererkennung | pyannote Segmentation 3.0 + 3D-Speaker (sherpa-onnx) | offen lizenziert, CPU-tauglich, im Test zuverlässig |
| Sprachmodell (Bereinigen, Zusammenfassen) | **Ministral 3** von Mistral AI (Frankreich), Apache-2.0, llama.cpp | europäischer Anbieter, freie Lizenz, 40+ Sprachen, klein genug für CPU, auf GPU skalierbar |
| Schutz gegen Erfundenes | Treue-Prüfung je Satz, Belegpflicht [S12] in Zusammenfassungen | unabhängig vom Modell – auch kleine Modelle können nichts unbemerkt dazudichten |
| HTTPS | Caddy + Let's Encrypt | automatische Zertifikate, Sicherheits-Header |
| Auslieferung | Pull-Deployment (Server holt Code alle 2 min von GitHub) | GitHub braucht keinen Zugang zum Server; kein offener Admin-Port |
| Test vs. Produktiv | zwei Instanzen auf einem Server (Branches `main` / `stable`) | Änderungen erst testen, dann freigeben |
| Sicherung | nächtliche SQLite-Sicherung (14 Tage) + Hetzner-Backups | Audio wird bewusst nicht gesichert |
| Qualitätssicherung | GitHub Actions: 24 Tests inkl. Browser-Test + Docker-Build bei jedem Push | nichts Kaputtes landet auf dem Server |

## Stufen

| Stufe | Server | Kosten/Monat | Sprachmodell | Kapazität (Schätzung) |
|---|---|---|---|---|
| **Test** (jetzt) | Hetzner CPX42 – 8 vCPU, 16 GB (CX43 derzeit nicht lieferbar) | 0,133 €/Std., max. 83,29 €/Monat | Ministral 3 **3B** (CPU) | Abteilungstest; gemessen: 5 Sätze bereinigt in ca. 3–4 s (~30 Tokens/s) |
| Pilot mehrere Ämter | Hetzner CCX33 – 8 dedizierte vCPU, 32 GB | ca. 165 € | Ministral 3 **3B/8B** (CPU) | ~20 gleichzeitige Aufnahmen |
| **Produktiv (2.500 Nutzer)** | Hetzner **GEX44** – RTX 4000 SFF Ada 20 GB | ca. 184 € + einmalige Einrichtung | Ministral 3 **14B** (GPU) | Spracherkennung >500× Echtzeit, Sprachmodell für viele parallele Nutzer |
| alternativ | GPU-Server im Rathaus-Rechenzentrum | Anschaffung | wie oben | Datenschutz wie „lokal“ |

Spracherkennung und Sprachmodell (3B) sind gemessen; die Kapazitätsangaben für Pilot und Regelbetrieb sind Schätzungen.

Wechsel zwischen den Stufen = anderer Server + eine Zeile in `settings.env` (`LLM_HF_MODEL`). Am Code ändert sich nichts.

## Arbeitsablauf für Änderungen

1. Ronny beschreibt die Änderung im Chat (Claude Code).
2. Claude ändert den Code, lässt alle Tests laufen und pusht auf `main`.
3. GitHub prüft automatisch (Tests + Docker-Build).
4. Der Server holt `main` innerhalb von 2 Minuten → **Test-Instanz** aktualisiert.
5. Ronny prüft auf `https://test.<host>`. Nach seinem OK wird `main` nach `stable` übernommen → **Produktiv-Instanz** aktualisiert.

Rückgängig machen: `stable` auf die vorherige Version zurücksetzen – der Server rollt sie automatisch wieder aus.

## Datenschutz-Eckpunkte

- Ein Anbieter (Hetzner, deutsches Unternehmen, Rechenzentren in Nürnberg/Falkenstein, ISO 27001), AVV direkt in der Hetzner-Konsole abschließbar.
- Keine Drittland-Übermittlung, keine externe KI, kein Training mit Inhalten.
- Löschfristen voreingestellt (Test: Audio 7 Tage, Transkripte 30 Tage), Audio jederzeit einzeln löschbar.
- Zugang nur mit Passwort über HTTPS; Stufe 2: Anmeldung über das städtische Konto (SSO).
- Für den Test mit privatem Konto: nur selbst erstellte Testaufnahmen ohne echte Personendaten. Für echte Sitzungen: Vertrag + AVV zwischen Stadt und Hetzner (oder GPU-Server im Rathaus).
