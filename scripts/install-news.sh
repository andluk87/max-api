#!/usr/bin/env bash
# Run from the existing project directory to retain its data volumes.
set -euo pipefail
if [[ ! -f compose.yaml || ! -f compose.news.yaml ]]; then
  echo 'Запустите скрипт из каталога max-api.' >&2
  exit 1
fi
compose=(docker compose -f compose.yaml -f compose.news.yaml)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --build
"${compose[@]}" exec -T ollama ollama pull qwen2.5:3b
"${compose[@]}" exec -T ollama ollama list
printf '%s\n' 'Готово. Откройте панель администратора → ИИ-парсер новостей.' 'Ollama: http://ollama:11434; модель: qwen2.5:3b.' 'Подключите аккаунт MAX, выберите каналы/группы и включите модуль.'
