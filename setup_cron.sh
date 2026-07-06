#!/bin/bash
# =====================================================
#  YouTube Downloader — cron ジョブ設定スクリプト
#
#  VPS 上で実行して、以下を自動化する:
#   1. ヘルスチェック    — 5分ごと（内部＋外部経路、異常時に自動再起動）
#   2. yt-dlp 定期更新  — 毎日 午前5時に再起動（yt-dlp更新）
#   3. 実DL監視        — 毎日 午前6:30 に実際のダウンロードテスト
#                        （失敗時Discord通知・毎週月曜に週次報告）
#   4. Docker掃除      — 毎月1日に不要イメージを削除（ディスク節約）
#
#  使い方:
#    VPS にファイルを転送後:
#      chmod +x setup_cron.sh healthcheck.sh synthetic_dl_test.sh
#      ./setup_cron.sh
# =====================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HEALTHCHECK_SCRIPT="$SCRIPT_DIR/healthcheck.sh"
SYNTHETIC_SCRIPT="$SCRIPT_DIR/synthetic_dl_test.sh"
COMPOSE_DIR="/opt/youtube-downloader"
LOG_FILE="/var/log/ytdl_healthcheck.log"

# ----- 前提チェック -----
if [ ! -f "$HEALTHCHECK_SCRIPT" ]; then
    echo "ERROR: healthcheck.sh が見つかりません: $HEALTHCHECK_SCRIPT"
    exit 1
fi
if [ ! -f "$SYNTHETIC_SCRIPT" ]; then
    echo "ERROR: synthetic_dl_test.sh が見つかりません: $SYNTHETIC_SCRIPT"
    exit 1
fi

chmod +x "$HEALTHCHECK_SCRIPT" "$SYNTHETIC_SCRIPT"

# ----- Discord Webhook 設定ファイルの確認 -----
# （Webhook URL は GitHub に置けないため、サーバー上の .env.monitor に保存する）
if [ ! -f "$SCRIPT_DIR/.env.monitor" ]; then
    cat > "$SCRIPT_DIR/.env.monitor" <<'EOF'
# 監視通知用の Discord Webhook URL（このファイルは GitHub に公開しない）
# Discord のチャンネル設定 → 連携サービス → Webhook から取得して貼り付ける
DISCORD_WEBHOOK=""
EOF
    echo ""
    echo "⚠️  $SCRIPT_DIR/.env.monitor を新規作成しました。"
    echo "    Discord Webhook URL を記入しないと異常時の通知が飛びません。"
    echo ""
fi

# ----- ログファイル準備 -----
touch "$LOG_FILE" /var/log/ytdl_synthetic.log

# ----- 既存の ytdl 関連 cron を削除して再登録 -----
# 重複登録を防ぐため、次の2段階で掃除する:
#   1. マーカー（>>> ytdl-monitor >>> 〜 <<<）で囲まれたブロックを丸ごと削除
#   2. マーカー導入前の古い形式の行も、キーワードで拾って削除
EXISTING_CRON=$(crontab -l 2>/dev/null || echo "")
CLEANED_CRON=$(echo "$EXISTING_CRON" \
    | sed '/# >>> ytdl-monitor >>>/,/# <<< ytdl-monitor <<</d' \
    | grep -v "ytdl\|youtube-downloader\|YouTube Downloader\|yt-dlp\|ヘルスチェック: 5分ごと\|実DL監視:\|Docker掃除:" || true)

NEW_CRON="$CLEANED_CRON
# >>> ytdl-monitor >>> （このブロックは setup_cron.sh が自動管理・手で編集しない）
# ヘルスチェック: 5分ごとに内部＋外部経路を確認し、異常時に自動再起動
*/5 * * * * $HEALTHCHECK_SCRIPT
# yt-dlp 定期更新: 毎日 AM5:00 にコンテナ再起動（start.sh 内で yt-dlp が更新される）
0 5 * * * cd $COMPOSE_DIR && docker compose restart >> $LOG_FILE 2>&1
# 実DL監視: 毎日 AM6:30 に実際のダウンロードテスト（失敗時Discord通知・月曜に週次報告）
30 6 * * * $SYNTHETIC_SCRIPT
# Docker掃除: 毎月1日 AM4:30 に使われていない古いイメージを削除
30 4 1 * * docker image prune -f >> $LOG_FILE 2>&1
# <<< ytdl-monitor <<<
"

# 空行の重複を整理してからインストール
echo "$NEW_CRON" | sed '/^$/N;/^\n$/d' | crontab -

echo "=== cron ジョブを設定しました ==="
echo ""
echo "  [1] ヘルスチェック    : 5分ごと（内部＋外部経路）"
echo "  [2] yt-dlp 定期更新   : 毎日 AM 5:00"
echo "  [3] 実DL監視         : 毎日 AM 6:30（月曜は週次報告つき）"
echo "  [4] Docker掃除       : 毎月1日 AM 4:30"
echo ""
echo "  ログ確認: tail -f $LOG_FILE"
echo "  実DLログ: tail -f /var/log/ytdl_synthetic.log"
echo "  cron 確認: crontab -l"
echo ""

# 現在の cron を表示
echo "--- 登録された cron ---"
crontab -l
