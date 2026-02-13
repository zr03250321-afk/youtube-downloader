#!/bin/bash
set -e

echo "=== YouTube Downloader ==="

# 1. PO Token サーバーをバックグラウンド起動
echo "Starting PO Token server..."
cd /opt/bgutil/server
node build/main.js &
POT_PID=$!
cd /app

# サーバー起動を待つ
sleep 5
echo "PO Token server started (PID: $POT_PID)"

# 2. Web アプリを起動（フォアグラウンド）
echo "Starting web application..."
exec gunicorn -c gunicorn.conf.py app:app
