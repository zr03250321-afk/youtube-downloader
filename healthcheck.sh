#!/bin/bash
# =====================================================
#  YouTube Downloader — ヘルスチェック＆自動復旧スクリプト
#
#  仕組み: 5分おきに2段階でサービスの状態を確認する。
#    [1] サーバー内部 (localhost:8080) — アプリ本体の生存確認
#        → 応答がなければ Docker コンテナを自動で再起動
#    [2] 外部URL (https://dl.movie-pro.co.jp) — 利用者と同じ経路
#        → 応答がなければ nginx を確認・自動再起動
#          （DNS・SSL証明書・nginx の障害もここで検知できる）
#  異常時は Discord に通知を飛ばす。
#
#  Discord Webhook は同じフォルダの .env.monitor から読み込む
#  （Webhook URLを知っていれば誰でもチャンネルに投稿できてしまうため、
#   GitHub には置かず、サーバー上のこのファイルだけに保存する）:
#      DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
#
#  使い方: cron に登録して定期実行する（setup_cron.sh 参照）
# =====================================================

set -u

# ----- 設定 -----
HEALTH_URL="http://localhost:8080/health"
PUBLIC_URL="https://dl.movie-pro.co.jp/health"
COMPOSE_DIR="/opt/youtube-downloader"
LOG_FILE="/var/log/ytdl_healthcheck.log"
MAX_LOG_SIZE=$((5 * 1024 * 1024))  # 5MB でローテーション
EXT_FAIL_STAMP="/tmp/ytdl_ext_fail_notified"  # 外部経路異常の通知抑制用
EXT_NOTIFY_INTERVAL=$((6 * 3600))             # 同一障害の再通知は6時間おき

# ----- Discord Webhook を秘密ファイルから読み込む -----
DISCORD_WEBHOOK=""
if [ -f "$COMPOSE_DIR/.env.monitor" ]; then
    # shellcheck disable=SC1091
    . "$COMPOSE_DIR/.env.monitor"
fi

# ----- ログ関数 -----
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] $2" >> "$LOG_FILE"
}

# ----- Discord 通知関数 -----
notify_discord() {
    local color="$1"   # 赤=16711680 黄=16776960 緑=65280
    local title="$2"
    local message="$3"

    if [ -z "$DISCORD_WEBHOOK" ]; then
        log "WARN" "Webhook未設定（.env.monitor なし）のため通知をスキップ: $title"
        return
    fi

    curl -s -o /dev/null -X POST "$DISCORD_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "{
            \"username\": \"VPS警察\",
            \"embeds\": [{
                \"title\": \"$title\",
                \"description\": \"$message\",
                \"color\": $color,
                \"footer\": {\"text\": \"YouTube Downloader 監視\"},
                \"timestamp\": \"$(date -u '+%Y-%m-%dT%H:%M:%SZ')\"
            }]
        }" 2>/dev/null
}

# ----- ログローテーション（5MBを超えたら古いログを削除） -----
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null)" -gt "$MAX_LOG_SIZE" ] 2>/dev/null; then
    tail -n 500 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
    log "INFO" "ログファイルをローテーションしました"
fi

# =====================================================
#  [2] 外部経路チェック（内部が正常なときに呼ばれる）
# =====================================================
check_public_route() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$PUBLIC_URL" 2>/dev/null)

    if [ "$code" = "200" ]; then
        # 障害から復旧した場合は一度だけ知らせる
        if [ -f "$EXT_FAIL_STAMP" ]; then
            rm -f "$EXT_FAIL_STAMP"
            log "INFO" "外部経路が復旧 (HTTP 200)"
            notify_discord 65280 "✅ 外部アクセス 復旧" "https://dl.movie-pro.co.jp が外部から再び見えるようになりました。"
        fi
        return 0
    fi

    # 一時的な揺らぎかもしれないので10秒後にもう一度
    sleep 10
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$PUBLIC_URL" 2>/dev/null)
    if [ "$code" = "200" ]; then
        return 0
    fi

    log "ERROR" "外部経路チェック失敗 (HTTP: ${code:-timeout}) — 内部は正常"

    # nginx が止まっているだけなら自動復旧できる
    if ! systemctl is-active --quiet nginx; then
        log "WARN" "nginx が停止 — 再起動します"
        systemctl restart nginx
        sleep 5
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$PUBLIC_URL" 2>/dev/null)
        if [ "$code" = "200" ]; then
            log "INFO" "nginx 再起動で復旧"
            notify_discord 16776960 "⚠️ nginx 停止 → 自動復旧" "nginx が停止していたため自動で再起動しました。外部アクセスは復旧済みです。"
            return 0
        fi
    fi

    # 自動復旧できない障害（DNS・SSL証明書・ルーター等）→ 通知は6時間に1回に抑える
    local now stamp_age
    now=$(date +%s)
    if [ -f "$EXT_FAIL_STAMP" ]; then
        stamp_age=$((now - $(stat -c %Y "$EXT_FAIL_STAMP" 2>/dev/null || echo 0)))
    else
        stamp_age=$((EXT_NOTIFY_INTERVAL + 1))
    fi

    if [ "$stamp_age" -gt "$EXT_NOTIFY_INTERVAL" ]; then
        touch "$EXT_FAIL_STAMP"
        notify_discord 16711680 "🚨 外部からアクセス不可" "アプリ本体（サーバー内部）は正常ですが、外部URLが応答しません（HTTP: ${code:-timeout}）。\nDNS設定・SSL証明書・nginx設定のいずれかに問題がある可能性があります。\n**手動での確認が必要です。**\n\`\`\`\nssh root@162.43.6.162\nsystemctl status nginx\ncertbot certificates\n\`\`\`"
    fi
    return 1
}

# =====================================================
#  [1] 内部チェック（アプリ本体）
# =====================================================
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HEALTH_URL" 2>/dev/null)

if [ "$HTTP_CODE" = "200" ]; then
    # 内部正常 → 外部経路も確認
    check_public_route

    # 正常時のログは15分に1回だけ記録して肥大化を防ぐ
    MINUTE=$(date '+%M')
    if [ "$((MINUTE % 15))" -eq 0 ]; then
        log "OK" "サービス正常 (HTTP $HTTP_CODE)"
    fi
    exit 0
fi

# ----- 異常検出 -----
log "WARN" "ヘルスチェック失敗 (HTTP: ${HTTP_CODE:-timeout})"

# 10秒待ってもう1回確認（一時的なスパイクかもしれないので）
sleep 10
HTTP_CODE2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HEALTH_URL" 2>/dev/null)

if [ "$HTTP_CODE2" = "200" ]; then
    log "INFO" "再確認で復旧を確認 (HTTP $HTTP_CODE2) — 再起動不要"
    exit 0
fi

# ----- 自動復旧: コンテナを再起動 -----
log "ERROR" "2回連続で応答なし — コンテナを再起動します"
notify_discord 16776960 "⚠️ サービス異常を検知" "ヘルスチェックが2回連続で失敗しました（HTTP: ${HTTP_CODE:-timeout} → ${HTTP_CODE2:-timeout}）\nコンテナを自動再起動します..."

cd "$COMPOSE_DIR" || { log "ERROR" "ディレクトリ移動失敗: $COMPOSE_DIR"; exit 1; }

docker compose restart >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    log "INFO" "コンテナ再起動 完了"

    # 起動完了を待って最終確認（最大60秒）
    for i in $(seq 1 6); do
        sleep 10
        FINAL=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HEALTH_URL" 2>/dev/null)
        if [ "$FINAL" = "200" ]; then
            log "INFO" "再起動後のヘルスチェック OK (${i}0秒後)"
            notify_discord 65280 "✅ 自動復旧 完了" "コンテナ再起動後、${i}0秒でサービスが復旧しました。"
            exit 0
        fi
    done

    log "ERROR" "再起動後もサービスが応答しません — 手動対応が必要です"
    notify_discord 16711680 "🚨 自動復旧 失敗" "コンテナを再起動しましたが、60秒経ってもサービスが応答しません。\n**手動での確認が必要です。**\n\`\`\`\nssh root@162.43.6.162\ndocker logs youtube-downloader-app-1\n\`\`\`"
else
    log "ERROR" "コンテナ再起動 失敗 — 手動対応が必要です"
    notify_discord 16711680 "🚨 コンテナ再起動 失敗" "docker compose restart が失敗しました。\n**手動での確認が必要です。**"
fi
