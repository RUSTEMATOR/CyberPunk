#!/bin/bash
# One-time setup on macOS: the bot becomes a system service.
# It starts at boot (no login needed) and launchd restarts it after any crash.
# Run from Terminal: bash ~/Desktop/cyberpunk_bot/deploy/install-mac-service.sh
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
DEST="$HOME/cyberpunk_bot"
USER_NAME="$(id -un)"
LABEL="com.nightcity.bot"
PLIST="/Library/LaunchDaemons/$LABEL.plist"

echo "Понадобится пароль от Мака (sudo): служба ставится для всей системы."
sudo -v

echo "1/6 Останавливаю бота, запущенного вручную"
pkill -if "python -m app.main" || true
sleep 3

if [ "$SRC" != "$DEST" ]; then
  if [ -e "$DEST" ]; then echo "Папка $DEST уже есть. Переименуй её и запусти снова."; exit 1; fi
  echo "2/6 Переношу бота в $DEST (фоновым службам macOS не даёт читать Рабочий стол)"
  mv "$SRC" "$DEST"
  ln -s "$DEST" "$SRC"
else
  echo "2/6 Бот уже в $DEST"
fi
cd "$DEST"

echo "3/6 Ставлю живую базу"
mkdir -p data/old
if [ -f data/game-live.db ]; then
  ts=$(date +%Y%m%d-%H%M%S)
  for f in game.db game.db-wal game.db-shm; do
    if [ -f "data/$f" ]; then mv "data/$f" "data/old/$f.$ts"; fi
  done
  mv data/game-live.db data/game.db
  echo "   живая база на месте, старая сложена в data/old"
else
  echo "   новой базы нет, оставляю текущую"
fi

echo "4/6 Проверяю окружение Python"
if ! .venv/bin/python -c "import aiogram, sqlalchemy, apscheduler" 2>/dev/null; then
  PY=""
  for cand in python3.13 python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  done
  if [ -z "$PY" ]; then echo "Нужен Python 3.12+: brew install python@3.12"; exit 1; fi
  rm -rf .venv
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi
.venv/bin/python -m app.content.loader >/dev/null
chmod +x deploy/service.sh

echo "5/6 Регистрирую службу $LABEL"
sudo tee "$PLIST" >/dev/null <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>UserName</key><string>$USER_NAME</string>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$DEST/deploy/service.sh</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$DEST/data/service.err</string>
</dict>
</plist>
PLIST
sudo chown root:wheel "$PLIST"
sudo chmod 644 "$PLIST"
sudo launchctl bootout system/$LABEL 2>/dev/null || true
sleep 1
sudo launchctl bootstrap system "$PLIST"
sudo launchctl enable system/$LABEL

echo "6/6 Мак не засыпает и сам включается после отключения света"
sudo pmset -a sleep 0 autorestart 1

sleep 12
if pgrep -if "python -m app.main" >/dev/null; then
  echo
  echo "Готово: бот работает как служба."
  echo "Лог:          tail -f ~/cyberpunk_bot/data/bot.log"
  echo "Перезапуск:   sudo launchctl kickstart -k system/$LABEL"
  echo "Выключить:    sudo launchctl bootout system/$LABEL"
else
  echo "Бот не поднялся. Ошибки:"
  tail -30 data/service.err 2>/dev/null
  tail -30 data/bot.log 2>/dev/null
  exit 1
fi
