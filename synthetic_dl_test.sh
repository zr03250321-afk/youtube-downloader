#!/bin/bash
# =====================================================
#  YouTube Downloader — 実ダウンロード監視（毎日1回）
#
#  なぜ必要か:
#    YouTube側の仕様変更で yt-dlp が壊れても、Webアプリ自体は
#    正常に応答し続ける（/health は 200 のまま）。つまり
#    「サーバーは生きているのにダウンロードだけ全滅」という
#    一番起きやすい故障は、普通のヘルスチェックでは見つからない。
#    そこで毎日1回、実際に短いテスト動画をダウンロードして
#    「利用者が本当に使える状態か」を確認する。
#
#  動作:
#    - 成功: ログに記録するだけ（通知なし・静かに見守る）
#    - 失敗: Discord に赤色アラート
#    - 毎週月曜: 直近7日の結果をまとめて Discord に生存報告
#      （「通知が来ない＝監視自体が死んでいる」事故を防ぐため）
#
#  Discord Webhook は .env.monitor から読み込む（healthcheck.sh と共用）
#  使い方: cron で毎日1回実行する（setup_cron.sh 参照）
# =====================================================

set -u

# ----- 設定 -----
BASE_URL="https://dl.movie-pro.co.jp"
# テスト動画: YouTube最初の動画「Me at the zoo」(19秒) — 消える心配がほぼ無い
TEST_VIDEO="https://www.youtube.com/watch?v=jNQXAC9IVRw"
COMPOSE_DIR="/opt/youtube-downloader"
LOG_FILE="/var/log/ytdl_synthetic.log"
MAX_LOG_SIZE=$((2 * 1024 * 1024))  # 2MB でローテーション
POLL_INTERVAL=10                    # 進捗確認の間隔（秒）
TIMEOUT_SEC=300                     # 5分待ってダメなら失敗扱い

# ----- Discord Webhook を秘密ファイルから読み込む -----
DISCORD_WEBHOOK=""
if [ -f "$COMPOSE_DIR/.env.monitor" ]; then
    # shellcheck disable=SC1091
    . "$COMPOSE_DIR/.env.monitor"
fi

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] $2" >> "$LOG_FILE"
}

notify_discord() {
    local color="$1" title="$2" message="$3"
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
                \"footer\": {\"text\": \"YouTube Downloader 実DL監視\"},
                \"timestamp\": \"$(date -u '+%Y-%m-%dT%H:%M:%SZ')\"
            }]
        }" 2>/dev/null
}

# ----- ログローテーション -----
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_SIZE" ] 2>/dev/null; then
    tail -n 300 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

# JSON から値を1つ取り出す小道具（jq が無くても動くよう python3 を使う）
json_get() {
    python3 -c "import sys, json
try:
    print(json.load(sys.stdin).get('$1', ''))
except Exception:
    print('')" 2>/dev/null
}

# =====================================================
#  テストダウンロード実行
# =====================================================
run_test() {
    # --- ダウンロード開始を依頼 ---
    local resp task_id
    resp=$(curl -s --max-time 20 -X POST "$BASE_URL/api/prepare" \
        -H "Content-Type: application/json" \
        -d "{\"url\":\"$TEST_VIDEO\",\"format\":\"video\",\"quality\":\"360\"}")

    # サーバー混雑（他の人が3件使用中）なら2分待って1回だけやり直す
    if echo "$resp" | grep -q "混雑"; then
        log "WARN" "サーバー混雑中 — 2分後に再試行"
        sleep 120
        resp=$(curl -s --max-time 20 -X POST "$BASE_URL/api/prepare" \
            -H "Content-Type: application/json" \
            -d "{\"url\":\"$TEST_VIDEO\",\"format\":\"video\",\"quality\":\"360\"}")
    fi

    task_id=$(echo "$resp" | json_get task_id)
    if [ -z "$task_id" ]; then
        FAIL_REASON="開始API失敗: $(echo "$resp" | head -c 200)"
        return 1
    fi

    # --- 完了まで進捗を見守る ---
    local waited=0 status message
    while [ "$waited" -lt "$TIMEOUT_SEC" ]; do
        sleep "$POLL_INTERVAL"
        waited=$((waited + POLL_INTERVAL))

        resp=$(curl -s --max-time 15 "$BASE_URL/api/progress/$task_id")
        status=$(echo "$resp" | json_get status)

        case "$status" in
            ready)
                RESULT_DETAIL="${waited}秒で完了 ($(echo "$resp" | json_get filesize) bytes)"
                return 0
                ;;
            error)
                message=$(echo "$resp" | json_get message)
                FAIL_REASON="ダウンロードエラー: $(echo "$message" | head -c 300)"
                return 1
                ;;
            "")
                FAIL_REASON="進捗APIが応答しません（タスク消失）"
                return 1
                ;;
        esac
    done

    FAIL_REASON="タイムアウト（${TIMEOUT_SEC}秒以内に完了せず）"
    return 1
}

FAIL_REASON=""
RESULT_DETAIL=""

if run_test; then
    log "RESULT" "OK $RESULT_DETAIL"
else
    log "RESULT" "FAIL $FAIL_REASON"
    notify_discord 16711680 "🚨 実ダウンロード監視 失敗" "テスト動画のダウンロードに失敗しました。\n**利用者もダウンロードできない状態の可能性が高いです。**\n\n原因: ${FAIL_REASON}\n\n（Webアプリ自体は動いているため、YouTube仕様変更で yt-dlp が壊れた可能性が高い。まず明朝5時の自動更新で直るか様子見も可）\n\`\`\`\nssh root@162.43.6.162\ndocker logs youtube-downloader-app-1 --tail 100\n\`\`\`"
fi

# =====================================================
#  毎週月曜: 週次生存報告（監視自体が生きている証明）
# =====================================================
if [ "$(date +%u)" = "1" ]; then
    WEEK_RESULTS=$(grep "\[RESULT\]" "$LOG_FILE" | tail -7)
    OK_COUNT=$(echo "$WEEK_RESULTS" | grep -c "OK" || true)
    FAIL_COUNT=$(echo "$WEEK_RESULTS" | grep -c "FAIL" || true)

    # サーバーの今の状態も添える
    HEALTH=$(curl -s --max-time 15 "$BASE_URL/health")
    YTDLP_VER=$(echo "$HEALTH" | python3 -c "import sys,json
try:
    print(json.load(sys.stdin)['checks']['yt_dlp_version'])
except Exception:
    print('不明')" 2>/dev/null)
    DISK_MB=$(echo "$HEALTH" | python3 -c "import sys,json
try:
    print(json.load(sys.stdin)['checks']['disk_free_mb'])
except Exception:
    print('不明')" 2>/dev/null)

    if [ "$FAIL_COUNT" -gt 0 ]; then
        COLOR=16776960
        TITLE="📋 週次報告: 直近7日で失敗あり"
    else
        COLOR=65280
        TITLE="📋 週次報告: 全システム正常"
    fi

    notify_discord "$COLOR" "$TITLE" "**実DLテスト（直近7日）:** 成功 ${OK_COUNT} / 失敗 ${FAIL_COUNT}\n**yt-dlp:** ${YTDLP_VER}\n**ディスク空き:** ${DISK_MB} MB\n\nこの報告が毎週月曜に届いていれば、監視システム自体も正常です。"
    log "INFO" "週次報告を送信 (OK:$OK_COUNT FAIL:$FAIL_COUNT)"
fi
