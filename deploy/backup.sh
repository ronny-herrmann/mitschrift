#!/usr/bin/env bash
# Nächtliche Sicherung der Datenbanken (Transkripte, Protokolle, Glossar) – 14 Tage aufbewahrt.
# Audio wird bewusst nicht gesichert (Datensparsamkeit). Für Ausfallsicherheit zusätzlich
# die Hetzner-Backups des Servers aktivieren (Konsole → Server → Backups).
set -euo pipefail
BASE=/opt/mitschrift
STAMP=$(date +%F)
for inst in test prod; do
  db=$BASE/$inst/data/mitschrift.sqlite3
  [ -f "$db" ] || continue
  sqlite3 "$db" ".backup '$BASE/backup/$inst-$STAMP.sqlite3'"
  # eigene Briefköpfe (Word-Vorlagen) mitsichern
  [ -d "$BASE/$inst/data/vorlagen" ] && tar czf "$BASE/backup/$inst-$STAMP-vorlagen.tgz" -C "$BASE/$inst/data" vorlagen
done
find "$BASE/backup" -name '*-vorlagen.tgz' -mtime +14 -delete
find "$BASE/backup" -name '*.sqlite3' -mtime +14 -delete
echo "$(date -Is) Sicherung ok"
