#!/usr/bin/env bash
# Run from the existing project directory to preserve the Compose volume name.
set -euo pipefail
if [[ ! -f compose.yaml || ! -d app ]]; then
  echo 'Run this command from the existing MAX API directory (app/ and compose.yaml).' >&2
  exit 1
fi
command -v git >/dev/null
command -v docker >/dev/null
docker compose version >/dev/null
docker compose config --quiet
if git rev-parse --show-toplevel >/dev/null 2>&1 && [[ $(git rev-parse --show-toplevel) == "$PWD" ]]; then
  if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    echo 'Commit or save tracked local changes before updating.' >&2
    exit 1
  fi
  git pull --ff-only origin main
else
  # Archive installations have no .git; keep their directory and Compose settings.
  stage=$(mktemp -d)
  trap 'rm -rf "$stage"' EXIT
  git clone --quiet --depth=1 --branch main https://github.com/andluk87/max-api.git "$stage/source"
  mkdir -p .update-backups
  backup=".update-backups/$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
  files=(app)
  for item in wiki scripts tests examples Dockerfile requirements.txt requirements-dev.txt setup.py VERSION README.md CHANGELOG.md .gitignore .dockerignore; do
    [[ ! -e "$item" ]] || files+=("$item")
  done
  tar -czf "$backup" -- "${files[@]}"
  echo "Source backup: $backup"
  for item in app wiki scripts tests examples Dockerfile requirements.txt requirements-dev.txt setup.py VERSION README.md CHANGELOG.md .gitignore .dockerignore; do
    cp -a "$stage/source/$item" .
  done
fi
docker compose up -d --build --force-recreate
for attempt in {1..30}; do
  if docker compose exec -T max-api python -c "import urllib.request; import json; assert json.load(urllib.request.urlopen('http://127.0.0.1:8087/healthz', timeout=3)) == {'ok': True}" 2>/dev/null; then
    echo 'MAX API updated; healthz OK. Refresh the browser with Ctrl+F5.'
    exit 0
  fi
  sleep 2
done
docker compose ps
echo 'Readiness failed; inspect docker compose logs --tail=100 max-api.' >&2
exit 1
