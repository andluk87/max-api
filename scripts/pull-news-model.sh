#!/usr/bin/env bash
# Retry transient registry/CDN failures without deleting downloaded model layers.
set -euo pipefail
compose=(docker compose -f compose.yaml -f compose.news.yaml)
for attempt in 1 2 3; do
  echo "Загрузка qwen2.5:3b: попытка ${attempt}/3..."
  if "${compose[@]}" exec -T ollama ollama pull qwen2.5:3b; then
    echo 'Локальная модель загружена.'
    exit 0
  fi
  if [[ "$attempt" -lt 3 ]]; then
    echo 'Не удалось скачать модель; повтор через 10 секунд.' >&2
    sleep 10
  fi
done
printf '%s\n' 'Модель не загружена: проверьте доступ сервера к registry.ollama.ai и хранилищу из сообщения об ошибке.' 'Для повтора без пересборки: bash scripts/pull-news-model.sh' >&2
exit 1
