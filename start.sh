#!/bin/bash
set -e

echo "=== YouTube Downloader ==="

# 1. yt-dlp を最新版に自動更新（起動のたびに最新の鍵を取得）
echo "Updating yt-dlp to latest version..."
pip install --no-cache-dir --upgrade yt-dlp bgutil-ytdlp-pot-provider 2>&1 | tail -1
echo "yt-dlp version: $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)')"

# 2. PO Token サーバーをバックグラウンド起動
echo "Starting PO Token server..."
cd /opt/bgutil/server
node build/main.js &
POT_PID=$!
cd /app

# サーバー起動を待つ
sleep 5
echo "PO Token server started (PID: $POT_PID)"

# 3. Web アプリを起動（フォアグラウンド）
echo "Starting web application..."
exec gunicorn -c gunicorn.conf.py app:app
