#!/usr/bin/env bash
# Prüft, ob es auf GitHub neuen Code gibt, und rollt ihn aus (Pull-Deployment).
# Vorteil: GitHub braucht keinen Zugang zum Server – der Server holt sich den Code selbst.
set -uo pipefail
BASE=/opt/mitschrift
# settings.env wörtlich einlesen (nicht per "source": Passwörter mit $ & ; Leerzeichen bleiben unverändert)
load_settings() {
  local k v
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
    k=${line%%=*}; v=${line#*=}
    k=$(echo "$k" | tr -d '[:space:]')
    [[ "$k" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    printf -v "$k" '%s' "$v"; export "$k"
  done < "$1"
}
load_settings "$BASE/settings.env"
TEST_BRANCH=${TEST_BRANCH:-main}
PROD_BRANCH=${PROD_BRANCH:-stable}

for inst in test prod; do
  dir=$BASE/$inst
  [ -d "$dir/.git" ] || continue
  want=$([ "$inst" = test ] && echo "$TEST_BRANCH" || echo "$PROD_BRANCH")
  git -C "$dir" fetch -q origin 2>/dev/null || { echo "$inst: GitHub nicht erreichbar"; continue; }
  git -C "$dir" rev-parse -q --verify "origin/$want" >/dev/null || want=$TEST_BRANCH
  cur=$(git -C "$dir" rev-parse HEAD)
  new=$(git -C "$dir" rev-parse "origin/$want")
  [ "$cur" = "$new" ] && continue
  echo "$(date -Is) $inst: ${cur:0:7} → ${new:0:7} ($want)"
  changed_platform=$(git -C "$dir" diff --name-only "$cur" "$new" -- deploy/platform | wc -l)
  git -C "$dir" checkout -q -B "$want" "origin/$want"
  (cd "$dir" && docker compose -p "mitschrift-$inst" up -d --build) || echo "$inst: Build fehlgeschlagen – alte Version läuft weiter"
  if [ "$inst" = prod ] && [ "$changed_platform" -gt 0 ]; then
    docker compose -f "$dir/deploy/platform/docker-compose.yml" --env-file "$BASE/platform.env" up -d
  fi
  if [ "$dir/deploy/update.sh" -nt /usr/local/bin/mitschrift-update ] && [ "$inst" = prod ]; then
    install -m 755 "$dir/deploy/update.sh" /usr/local/bin/mitschrift-update
  fi
done
docker image prune -f >/dev/null 2>&1 || true
