#!/usr/bin/env bash
# Richtet einen frischen Ubuntu-24.04-Server vollständig ein (wird von cloud-init aufgerufen,
# kann aber auch manuell als root laufen). Idempotent: mehrfaches Ausführen schadet nicht.
#
# Erwartet /opt/mitschrift/settings.env mit mindestens:
#   REPO_URL=https://github.com/<konto>/mitschrift.git
#   ACCESS_PASSWORD=<Zugangspasswort für die Oberfläche>
# Optional: GITHUB_TOKEN (nur bei privatem Repo, nur Lese-Recht), ACME_EMAIL, TEST_HOST, PROD_HOST,
#           LLM_HF_MODEL, PROD_BRANCH (Standard: stable), TEST_BRANCH (Standard: main)
set -euo pipefail
BASE=/opt/mitschrift
source "$BASE/settings.env"
: "${REPO_URL:?REPO_URL fehlt in settings.env}"
: "${ACCESS_PASSWORD:?ACCESS_PASSWORD fehlt in settings.env}"
TEST_BRANCH=${TEST_BRANCH:-main}
PROD_BRANCH=${PROD_BRANCH:-stable}
log() { echo "[mitschrift] $*"; }

# --- 1. System: Pakete, Firewall, Swap, automatische Sicherheitsupdates ---------------------
export DEBIAN_FRONTEND=noninteractive
log "Pakete installieren …"
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 git ufw fail2ban curl sqlite3 unattended-upgrades
systemctl enable --now docker
dpkg-reconfigure -f noninteractive unattended-upgrades || true

log "Firewall: nur SSH, HTTP, HTTPS"
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

if ! swapon --show | grep -q .; then
  log "Swap (4 GB) anlegen"
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- 2. Adressen -------------------------------------------------------------------------
IP=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
DASHED=${IP//./-}
TEST_HOST=${TEST_HOST:-test.$DASHED.sslip.io}
PROD_HOST=${PROD_HOST:-$DASHED.sslip.io}

# --- 3. Code holen (zwei Arbeitskopien: test folgt $TEST_BRANCH, prod folgt $PROD_BRANCH) ----
AUTH_URL=$REPO_URL
if [ -n "${GITHUB_TOKEN:-}" ]; then AUTH_URL=${REPO_URL/https:\/\//https:\/\/x-access-token:$GITHUB_TOKEN@}; fi
for inst in test prod; do
  dir=$BASE/$inst
  if [ ! -d "$dir/.git" ]; then
    log "Klone $inst …"
    git clone -q "$AUTH_URL" "$dir"
  fi
  branch=$([ "$inst" = test ] && echo "$TEST_BRANCH" || echo "$PROD_BRANCH")
  git -C "$dir" fetch -q origin
  if git -C "$dir" rev-parse -q --verify "origin/$branch" >/dev/null; then
    git -C "$dir" checkout -q -B "$branch" "origin/$branch"
  else
    log "Branch $branch gibt es noch nicht – $inst nutzt vorerst $TEST_BRANCH"
    git -C "$dir" checkout -q -B "$TEST_BRANCH" "origin/$TEST_BRANCH"
  fi
done

# --- 4. Konfiguration je Instanz --------------------------------------------------------------
mkdir -p "$BASE/models" "$BASE/backup"
for inst in test prod; do
  dir=$BASE/$inst
  mkdir -p "$dir/data"
  if [ ! -f "$dir/.env" ]; then
    cp "$dir/.env.example" "$dir/.env"
    set_env() { if grep -q "^$1=" "$dir/.env"; then sed -i "s|^$1=.*|$1=$2|" "$dir/.env"; else echo "$1=$2" >> "$dir/.env"; fi; }
    set_env INSTANCE "$inst"
    set_env ACCESS_PASSWORD "$ACCESS_PASSWORD"
    set_env SESSION_SECRET "$(openssl rand -hex 32)"
    set_env COOKIE_SECURE 1
    set_env MODELS_HOST_DIR "$BASE/models"
    set_env LLM_BASE_URL "http://llm:8080/v1"
    set_env LLM_MODEL "mitschrift-llm"
    set_env LLM_TIMEOUT_S 900
    # Datensparsamkeit im Test: Audio nach 7 Tagen, Transkripte nach 30 Tagen automatisch löschen
    set_env RETENTION_AUDIO_HOURS 168
    set_env RETENTION_TRANSCRIPT_HOURS 720
  fi
  chown -R 1000:1000 "$dir/data"
done
chown -R 1000:1000 "$BASE/models"

cat > "$BASE/platform.env" <<EOF
TEST_HOST=$TEST_HOST
PROD_HOST=$PROD_HOST
ACME_EMAIL=${ACME_EMAIL:-}
LLM_HF_MODEL=${LLM_HF_MODEL:-mistralai/Ministral-3-3B-Instruct-2512-GGUF:Q4_K_M}
LLM_THREADS=$(nproc)
LLM_CTX=${LLM_CTX:-24576}
LLM_PARALLEL=${LLM_PARALLEL:-2}
EOF

# --- 5. Starten ---------------------------------------------------------------------------------
log "Plattform (HTTPS + Sprachmodell) starten …"
docker compose -f "$BASE/prod/deploy/platform/docker-compose.yml" --env-file "$BASE/platform.env" up -d
for inst in test prod; do
  log "Instanz $inst bauen und starten …"
  (cd "$BASE/$inst" && docker compose -p "mitschrift-$inst" up -d --build)
done

# --- 6. Automatische Updates (alle 2 Minuten: neuer Code auf GitHub → neu bauen) + Sicherung ----
install -m 755 "$BASE/prod/deploy/update.sh" /usr/local/bin/mitschrift-update
cat > /etc/systemd/system/mitschrift-update.service <<'EOF'
[Unit]
Description=Mitschrift: neuen Code holen und ausrollen
[Service]
Type=oneshot
ExecStart=/usr/local/bin/mitschrift-update
EOF
cat > /etc/systemd/system/mitschrift-update.timer <<'EOF'
[Unit]
Description=Mitschrift: alle 2 Minuten auf neuen Code prüfen
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
EOF
install -m 755 "$BASE/prod/deploy/backup.sh" /usr/local/bin/mitschrift-backup
cat > /etc/cron.d/mitschrift-backup <<'EOF'
15 2 * * * root /usr/local/bin/mitschrift-backup >> /var/log/mitschrift-backup.log 2>&1
EOF
systemctl daemon-reload
systemctl enable --now mitschrift-update.timer

cat > /root/MITSCHRIFT.txt <<EOF
Mitschrift ist eingerichtet.
  Test:      https://$TEST_HOST     (folgt Branch $TEST_BRANCH – jede Änderung landet hier zuerst)
  Produktiv: https://$PROD_HOST     (folgt Branch $PROD_BRANCH – erst nach Freigabe)
Beim ersten Aufruf lädt der Server die Modelle (Spracherkennung ~0,5 GB, Sprachmodell ~2 GB) – 5–10 Minuten.
Logs:      docker compose -p mitschrift-test logs -f     |   Update-Protokoll: journalctl -u mitschrift-update
EOF
log "Fertig."; cat /root/MITSCHRIFT.txt
