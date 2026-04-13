#!/bin/bash
set -e

echo "=== YouTube Downloader ==="

# 1. yt-dlp を最新版に自動更新（起動のたびに最新の鍵を取得）
echo "Updating yt-dlp to latest version..."
pip install --no-cache-dir --upgrade yt-dlp bgutil-ytdlp-pot-provider 2>&1 | tail -1
echo "yt-dlp version: $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)')"

# 2. PO Token サーバーをバックグラウンド起動
start_pot_server() {
    cd /opt/bgutil/server
    node build/main.js &
    POT_PID=$!
    cd /app
    echo "PO Token server started (PID: $POT_PID)"
}

echo "Starting PO Token server..."
start_pot_server
sleep 5

# 3. PO Token サーバーの見守り役（落ちたら自動で再起動）
#    30秒ごとにプロセスの生存を確認する
(
    while true; do
        sleep 30
        if ! kill -0 $POT_PID 2>/dev/null; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] PO Token server (PID: $POT_PID) が停止 — 再起動します"
            start_pot_server
            sleep 5
        fi
    done
) &
echo "PO Token server monitor started"

# 4. Web アプリを起動（フォアグラウンド）
echo "Starting web application..."
exec gunicorn -c gunicorn.conf.py app:app
