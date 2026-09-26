# Demo-Drehbuch (10 Minuten) – „Server statt Browser"

Ziel: zeigen, dass Qualität und Geschwindigkeit auf einem normalen Office-PC als Client
möglich sind, wenn ein Server im Hausnetz rechnet. Und dass ein Protokoll nichts erfindet.

## Vorbereitung (am Vortag)
1. Server starten (Docker oder `uvicorn`), Modell einmal warmlaufen lassen (`/api/health` liefert `startup_seconds`).
2. `python bench/bakeoff.py --audio-dir ./feldtest --backends parakeet,whisper` laufen lassen → `bench/ergebnisse/ergebnisse.md` in die Folien.
3. Zwei Feldtest-Aufnahmen bereithalten: eine gute Akustik, eine schlechte (lauter Raum).
4. Glossar füllen: Amtsbezeichnungen, Gremien, Ortsteile, Namen der Teilnehmenden.
5. Testlauf auf genau dem Office-PC, der in der Demo benutzt wird (Browser, Mikrofon, HTTPS/Zertifikat).

## Ablauf
| Min | Schritt | Was die Runde sieht |
|---|---|---|
| 0–1 | Ausgangslage | Audio-Transkriptor des Lieferanten: Modell im Browser-Cache, 1 Minute Audio = 9 Minuten Warten, Freezes, schwaches Deutsch. Ursache benennen: Architektur, nicht das Modell allein. |
| 1–4 | Live-Aufnahme | Checkbox „Teilnehmende informiert“, Aufnahme starten, 2–3 Personen sprechen je 2 Sätze. Text erscheint satzweise, Rechenzeit pro Satz in ms steht daneben. Stopp → „113× schneller als Echtzeit“-Badge. |
| 4–6 | Upload Feldtest | Schlechte-Akustik-Aufnahme hochladen; parallel zeigen, wie die Warteschlange arbeitet. Ergebnis öffnen, Klick auf Zeitstempel → Audio springt. Fehler live korrigieren, Sprecher benennen. |
| 6–8 | Protokoll mit Beleg | „In NOVA öffnen“ → Prompt einfügen → Antwort zurück einfügen → Prüfung: grün/gelb/rot, Quote. Eine gelbe Zeile anklicken: „Das steht so nicht im Transkript“ – der Kern der Originaltreue. |
| 8–9 | Zahlen | Bake-off-Tabelle: RTF und WER je Modell auf euren 13 Aufnahmen. Kosten: Software 0 €, Betrieb Server im Hausnetz. |
| 9–10 | Nächste Schritte | Abteilungstest (2 Wochen), DSB/IT-Security-Termin mit Datenflussbild, Entscheidung Server (vorhandene VM vs. GPU-Box), Sprecherzuordnung Stufe 2. |

## Sätze, die man ehrlich sagen sollte
* „Bei starkem Lärm hilft kein Modell – das Mikrofon ist der größte Hebel.“ (Konferenzmikrofon einplanen)
* „Die KI schreibt den Entwurf, der Schriftführer bestätigt.“ (Belegpflicht + Bestätigung)
* „Alles bleibt im Haus – im Betrieb gibt es keine einzige ausgehende Verbindung.“

## Wenn etwas schiefgeht
* Mikrofon-Freigabe fehlt → Seite über `https://` oder `localhost` öffnen; Browser-Berechtigung prüfen.
* Kein Text erscheint → Pegelanzeige beobachten; anderes Mikrofon im Dropdown wählen.
* Zweiter Durchlauf dauert → `LIVE_FINAL_PASS=0` setzen (Live-Ergebnis wird direkt gespeichert).
