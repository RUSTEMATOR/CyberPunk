#!/bin/bash
# Run the bot locally without Docker: bash run.sh
set -e
cd "$(dirname "$0")"

PY=""
for cand in python3.13 python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "Нужен Python 3.12 или новее. Поставь: brew install python@3.12"
  exit 1
fi

if [ ! -f .env ]; then
  echo "Нет файла .env. Скопируй .env.example в .env и впиши BOT_TOKEN."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Создаю окружение (.venv) на $($PY --version)..."
  "$PY" -m venv .venv
fi
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

mkdir -p data
python -m app.content.loader >/dev/null
echo "Бот запускается. Остановить: Ctrl+C. Лог: data/bot.log"
exec python -m app.main
