#!/usr/bin/env python3
"""
YouTube Downloader - Web App (Cloud Version)
ブラウザからYouTube動画をダウンロードできるWebアプリ
Render / クラウドデプロイ対応版
"""

import os
import uuid
import time
import threading
import tempfile
import shutil
import logging
from urllib.parse import quote

from flask import Flask, render_template, request, jsonify, Response
import yt_dlp

# =====================================================================
#  アプリケーション設定
# =====================================================================
app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ytdl")

# ----- 定数 -----
TEMP_BASE_DIR = os.path.join(tempfile.gettempdir(), "ytdl_app")
MAX_CONCURRENT = 3
MAX_DURATION_SEC = 7200
FILE_TTL_SEC = 1800
CLEANUP_INTERVAL_SEC = 300

# Cookie ソースファイル（Render Secret Files）
_SECRET_COOKIES = "/etc/secrets/cookies.txt"

# PO Token サーバー（bgutil-ytdlp-pot-provider）
_POT_SERVER_URL = "http://127.0.0.1:4416"

os.makedirs(TEMP_BASE_DIR, exist_ok=True)


# =====================================================================
#  Cookie ヘルパー — 毎回フレッシュなコピーを生成
# =====================================================================
def _has_cookies() -> bool:
    """Cookieソースが存在するか"""
    return os.path.isfile(_SECRET_COOKIES)


def _fresh_cookie_path(task_dir: str | None = None) -> str | None:
    """
    Secret File から書き込み可能な新しいコピーを作成して返す。
    yt-dlp は Cookie ファイルに書き込むため、毎回新鮮なコピーが必要。
    """
    if not _has_cookies():
        # 環境変数フォールバック
        cookies_data = os.environ.get("YOUTUBE_COOKIES", "").strip()
        if not cookies_data:
            return None
        dest_dir = task_dir or TEMP_BASE_DIR
        dest = os.path.join(dest_dir, f"cookies_{uuid.uuid4().hex[:8]}.txt")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(cookies_data)
        return dest

    dest_dir = task_dir or TEMP_BASE_DIR
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"cookies_{uuid.uuid4().hex[:8]}.txt")
    shutil.copy2(_SECRET_COOKIES, dest)
    return dest


# =====================================================================
#  スレッドセーフなタスク管理
# =====================================================================
_tasks: dict = {}
_tasks_lock = threading.Lock()


def _create_task(task_id: str, **kw) -> None:
    with _tasks_lock:
        _tasks[task_id] = kw


def _get_task(task_id: str) -> dict:
    with _tasks_lock:
        return _tasks.get(task_id, {}).copy()


def _update_task(task_id: str, **kw) -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kw)


def _remove_task(task_id: str) -> dict | None:
    with _tasks_lock:
        return _tasks.pop(task_id, None)


def _count_active() -> int:
    with _tasks_lock:
        return sum(
            1
            for t in _tasks.values()
            if t.get("status") in ("starting", "downloading", "processing")
        )


# =====================================================================
#  一時ファイルのクリーンアップ
# =====================================================================
def _cleanup_task_files(task_id: str) -> None:
    task_dir = os.path.join(TEMP_BASE_DIR, task_id)
    if os.path.isdir(task_dir):
        try:
            shutil.rmtree(task_dir)
        except OSError as exc:
            logger.warning("Cleanup failed %s: %s", task_id, exc)


def _cleanup_worker() -> None:
    while True:
        time.sleep(CLEANUP_INTERVAL_SEC)
        try:
            now = time.time()
            expired: list[str] = []
            with _tasks_lock:
                for tid, t in _tasks.items():
                    age = now - t.get("created_at", now)
                    if age > FILE_TTL_SEC and t.get("status") in (
                        "ready", "error", "completed",
                    ):
                        expired.append(tid)
            for tid in expired:
                _cleanup_task_files(tid)
                _remove_task(tid)
        except Exception as exc:
            logger.error("Cleanup error: %s", exc)


threading.Thread(target=_cleanup_worker, daemon=True).start()


# =====================================================================
#  yt-dlp ダウンロード処理（リトライ付き）
# =====================================================================
def _progress_hook(d: dict, task_id: str) -> None:
    task = _get_task(task_id)
    if not task or task.get("status") == "cancelled":
        return

    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        pct = min(int((downloaded / total) * 100), 99) if total > 0 else 0
        _update_task(
            task_id, status="downloading", percent=pct,
            speed=d.get("_speed_str", ""), eta=d.get("_eta_str", ""),
        )
    elif d["status"] == "finished":
        _update_task(
            task_id, status="processing", percent=99,
            message="変換処理中...", speed="", eta="",
        )


# フォーマット試行リスト（上から順に試す）
def _video_format_chain(height: int) -> list[str]:
    """動画フォーマットの優先順位リスト"""
    return [
        # 1. 指定画質で映像+音声を結合
        f"bestvideo[height<={height}]+bestaudio",
        # 2. 画質制限なしで映像+音声を結合
        "bestvideo+bestaudio",
        # 3. 指定画質以下の結合済みファイル
        f"best[height<={height}]",
        # 4. とにかく最高品質
        "best",
    ]


def _run_download(
    url: str, task_id: str,
    format_type: str = "video", quality: str = "1080",
) -> None:
    """バックグラウンドでダウンロード — フォーマット自動リトライ付き"""
    task_dir = os.path.join(TEMP_BASE_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    try:
        output_tpl = os.path.join(task_dir, "%(title)s.%(ext)s")

        # ----- 動画情報を先に取得（フレッシュCookie + PO Token）-----
        info_cookie = _fresh_cookie_path(task_dir)
        info_opts: dict = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "extractor_args": {
                "youtube": {
                    "getpot_bgutil_baseurl": [_POT_SERVER_URL],
                },
            },
        }
        if info_cookie:
            info_opts["cookiefile"] = info_cookie

        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        _update_task(
            task_id,
            title=info.get("title", ""),
            channel=info.get("channel", info.get("uploader", "")),
        )

        # ----- フォーマット候補を決定 -----
        if format_type == "audio":
            format_candidates = ["bestaudio/best"]
            postprocessors = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
            merge_format = None
        else:
            h = int(quality) if quality else 1080
            format_candidates = _video_format_chain(h)
            postprocessors = []
            merge_format = "mp4"

        # ----- フォーマットを順に試す -----
        last_error = None
        for i, fmt_str in enumerate(format_candidates):
            # 毎回フレッシュなCookieコピーを使う
            dl_cookie = _fresh_cookie_path(task_dir)

            dl_opts: dict = {
                "outtmpl": output_tpl,
                "progress_hooks": [lambda d: _progress_hook(d, task_id)],
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "windowsfilenames": True,
                "format": fmt_str,
                "extractor_args": {
                    "youtube": {
                        "getpot_bgutil_baseurl": [_POT_SERVER_URL],
                    },
                },
            }
            if dl_cookie:
                dl_opts["cookiefile"] = dl_cookie
            if postprocessors:
                dl_opts["postprocessors"] = postprocessors
            if merge_format and "+" in fmt_str:
                dl_opts["merge_output_format"] = merge_format

            try:
                logger.info(
                    "[%s] Attempt %d/%d: format='%s'",
                    task_id, i + 1, len(format_candidates), fmt_str,
                )
                _update_task(task_id, message=f"ダウンロード中... (形式 {i+1}/{len(format_candidates)})")

                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    ydl.download([url])

                # 成功 — ループを抜ける
                last_error = None
                logger.info("[%s] Success with format='%s'", task_id, fmt_str)
                break

            except Exception as exc:
                last_error = exc
                err_msg = str(exc).lower()
                logger.warning(
                    "[%s] Format '%s' failed: %s", task_id, fmt_str, exc,
                )

                # Cookie/認証エラーの場合はリトライしても無駄
                if "sign in" in err_msg or "bot" in err_msg:
                    logger.error("[%s] Authentication error — aborting", task_id)
                    break

                # ダウンロード途中のファイルを掃除
                for f in os.listdir(task_dir):
                    if f.startswith("cookies_"):
                        continue
                    fpath = os.path.join(task_dir, f)
                    if os.path.isfile(fpath):
                        os.remove(fpath)

                continue

        # ----- 全フォーマット失敗 -----
        if last_error:
            raise last_error

        # ----- 成功: ファイルを特定 -----
        result_files = [
            f for f in os.listdir(task_dir)
            if not f.startswith("cookies_") and not f.startswith(".")
        ]
        if not result_files:
            raise FileNotFoundError("ダウンロードしたファイルが見つかりません")

        filepath = max(
            (os.path.join(task_dir, f) for f in result_files),
            key=os.path.getsize,
        )
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        _update_task(
            task_id, status="ready", percent=100,
            filepath=filepath, filename=filename, filesize=filesize,
            message="ダウンロード準備完了",
        )
        logger.info("Download ready: %s (%s bytes)", filename, f"{filesize:,}")

    except Exception as exc:
        logger.error("Download error [%s]: %s", task_id, exc)
        _update_task(task_id, status="error", message=str(exc))


# =====================================================================
#  API エンドポイント
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    """動画情報を取得（フレッシュCookie使用）"""
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URLを入力してください"}), 400
    if "youtube.com" not in url and "youtu.be" not in url:
        return jsonify({"error": "有効なYouTube URLを入力してください"}), 400

    try:
        cookie_path = _fresh_cookie_path()
        ydl_opts: dict = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "extractor_args": {
                "youtube": {
                    "getpot_bgutil_baseurl": [_POT_SERVER_URL],
                },
            },
        }
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        duration = info.get("duration", 0) or 0

        # 利用可能な画質
        qualities: list[dict] = []
        if info.get("formats"):
            seen: set[int] = set()
            for fmt in info["formats"]:
                h = fmt.get("height")
                if h and h >= 360 and h not in seen:
                    seen.add(h)
                    qualities.append({"value": str(h), "label": f"{h}p"})
            qualities.sort(key=lambda x: int(x["value"]), reverse=True)

        if not qualities:
            qualities = [
                {"value": "1080", "label": "1080p"},
                {"value": "720", "label": "720p"},
                {"value": "480", "label": "480p"},
            ]

        # Cookie 一時ファイルを掃除
        if cookie_path and os.path.isfile(cookie_path):
            try:
                os.remove(cookie_path)
            except OSError:
                pass

        return jsonify({
            "title": info.get("title", "不明"),
            "channel": info.get("channel", info.get("uploader", "不明")),
            "duration": duration,
            "thumbnail": info.get("thumbnail", ""),
            "view_count": info.get("view_count", 0),
            "qualities": qualities,
            "too_long": duration > MAX_DURATION_SEC,
        })

    except Exception as exc:
        return jsonify({"error": f"動画情報を取得できませんでした: {exc}"}), 400


@app.route("/api/prepare", methods=["POST"])
def api_prepare():
    """ダウンロードをバックグラウンドで開始"""
    if _count_active() >= MAX_CONCURRENT:
        return jsonify({
            "error": "サーバーが混雑しています。しばらくしてから再度お試しください。"
        }), 429

    data = request.json or {}
    url = data.get("url", "").strip()
    fmt = data.get("format", "video")
    quality = data.get("quality", "1080")

    if not url:
        return jsonify({"error": "URLを入力してください"}), 400
    if "youtube.com" not in url and "youtu.be" not in url:
        return jsonify({"error": "有効なYouTube URLを入力してください"}), 400

    task_id = uuid.uuid4().hex
    _create_task(
        task_id, status="starting", percent=0,
        url=url, format=fmt, quality=quality,
        created_at=time.time(), message="ダウンロードを開始しています...",
    )

    threading.Thread(
        target=_run_download,
        args=(url, task_id, fmt, quality),
        daemon=True,
    ).start()

    return jsonify({"task_id": task_id})


@app.route("/api/progress/<task_id>")
def api_progress(task_id: str):
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "不明なタスクID"}), 404
    return jsonify({
        "status": task.get("status", "unknown"),
        "percent": task.get("percent", 0),
        "speed": task.get("speed", ""),
        "eta": task.get("eta", ""),
        "message": task.get("message", ""),
        "filename": task.get("filename", ""),
        "filesize": task.get("filesize", 0),
        "title": task.get("title", ""),
    })


@app.route("/api/download/<task_id>")
def api_download(task_id: str):
    """準備済みファイルをブラウザへストリーミング送信"""
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "不明なタスクID"}), 404
    if task.get("status") != "ready":
        return jsonify({"error": "ファイルの準備ができていません"}), 400

    filepath = task.get("filepath", "")
    filename = task.get("filename", "download")

    if not filepath or not os.path.isfile(filepath):
        return jsonify({
            "error": "ファイルが見つかりません（有効期限切れの可能性があります）"
        }), 404

    filesize = os.path.getsize(filepath)
    ext = os.path.splitext(filename)[1].lower()
    mime_map = {
        ".mp4": "video/mp4", ".mp3": "audio/mpeg",
        ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".m4a": "audio/mp4", ".opus": "audio/opus", ".ogg": "audio/ogg",
    }

    def _stream():
        with open(filepath, "rb") as fh:
            while chunk := fh.read(65_536):
                yield chunk

    response = Response(
        _stream(),
        mimetype=mime_map.get(ext, "application/octet-stream"),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(filesize),
            "Cache-Control": "no-cache",
        },
    )

    def _delayed_cleanup():
        time.sleep(120)
        _cleanup_task_files(task_id)
        _remove_task(task_id)

    threading.Thread(target=_delayed_cleanup, daemon=True).start()
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": time.time()})


# =====================================================================
#  ローカル実行用
# =====================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  YouTube Downloader — http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True)
