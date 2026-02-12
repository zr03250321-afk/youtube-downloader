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
MAX_CONCURRENT = 3            # 同時ダウンロード上限
MAX_DURATION_SEC = 7200       # ダウンロード可能な動画の最大長さ（2時間）
FILE_TTL_SEC = 1800           # 一時ファイルの保持時間（30分）
CLEANUP_INTERVAL_SEC = 300    # クリーンアップの実行間隔（5分）

os.makedirs(TEMP_BASE_DIR, exist_ok=True)


# =====================================================================
#  スレッドセーフなタスク管理
# =====================================================================
_tasks: dict = {}
_tasks_lock = threading.Lock()


def _create_task(task_id: str, **kwargs) -> None:
    with _tasks_lock:
        _tasks[task_id] = kwargs


def _get_task(task_id: str) -> dict:
    with _tasks_lock:
        return _tasks.get(task_id, {}).copy()


def _update_task(task_id: str, **kwargs) -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


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
    """タスクに紐づく一時ディレクトリを安全に削除"""
    task_dir = os.path.join(TEMP_BASE_DIR, task_id)
    if os.path.isdir(task_dir):
        try:
            shutil.rmtree(task_dir)
            logger.info("Cleaned up files for task %s", task_id)
        except OSError as exc:
            logger.warning("Failed to clean up %s: %s", task_id, exc)


def _cleanup_worker() -> None:
    """期限切れタスクを定期的に削除するバックグラウンドワーカー"""
    while True:
        time.sleep(CLEANUP_INTERVAL_SEC)
        try:
            now = time.time()
            expired_ids: list[str] = []
            with _tasks_lock:
                for tid, task in _tasks.items():
                    age = now - task.get("created_at", now)
                    if age > FILE_TTL_SEC and task.get("status") in (
                        "ready",
                        "error",
                        "completed",
                    ):
                        expired_ids.append(tid)
            for tid in expired_ids:
                _cleanup_task_files(tid)
                _remove_task(tid)
                logger.info("Expired task removed: %s", tid)
        except Exception as exc:
            logger.error("Cleanup worker error: %s", exc)


threading.Thread(target=_cleanup_worker, daemon=True).start()


# =====================================================================
#  yt-dlp ダウンロード処理
# =====================================================================
def _progress_hook(d: dict, task_id: str) -> None:
    """yt-dlp からの進捗コールバック"""
    task = _get_task(task_id)
    if not task or task.get("status") == "cancelled":
        return

    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        percent = min(int((downloaded / total) * 100), 99) if total > 0 else 0

        _update_task(
            task_id,
            status="downloading",
            percent=percent,
            speed=d.get("_speed_str", ""),
            eta=d.get("_eta_str", ""),
        )

    elif d["status"] == "finished":
        _update_task(
            task_id,
            status="processing",
            percent=99,
            message="変換処理中...",
            speed="",
            eta="",
        )


def _run_download(
    url: str,
    task_id: str,
    format_type: str = "video",
    quality: str = "1080",
) -> None:
    """バックグラウンドスレッドでダウンロードを実行"""
    task_dir = os.path.join(TEMP_BASE_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    try:
        output_tpl = os.path.join(task_dir, "%(title)s.%(ext)s")

        ydl_opts: dict = {
            "outtmpl": output_tpl,
            "progress_hooks": [lambda d: _progress_hook(d, task_id)],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "windowsfilenames": True,  # ファイル名の安全化
        }

        if format_type == "audio":
            ydl_opts.update(
                {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ],
                }
            )
        else:
            h = int(quality) if quality else 1080
            ydl_opts.update(
                {
                    "format": (
                        f"bestvideo[height<={h}]+bestaudio/"
                        f"best[height<={h}]/best"
                    ),
                    "merge_output_format": "mp4",
                }
            )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # まず情報だけ取得してタスクに反映
            info = ydl.extract_info(url, download=False)
            _update_task(
                task_id,
                title=info.get("title", ""),
                channel=info.get("channel", info.get("uploader", "")),
            )
            # ダウンロード実行
            ydl.download([url])

        # ダウンロード完了 — 最終ファイルを特定
        files = [f for f in os.listdir(task_dir) if not f.startswith(".")]
        if not files:
            raise FileNotFoundError("ダウンロードしたファイルが見つかりません")

        # 複数ファイルがある場合（中間ファイル残存時）最大サイズを選択
        filepath = max(
            (os.path.join(task_dir, f) for f in files),
            key=os.path.getsize,
        )
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        _update_task(
            task_id,
            status="ready",
            percent=100,
            filepath=filepath,
            filename=filename,
            filesize=filesize,
            message="ダウンロード準備完了",
        )
        logger.info(
            "Download ready: %s (%s bytes)", filename, f"{filesize:,}"
        )

    except Exception as exc:
        logger.error("Download error [%s]: %s", task_id, exc)
        _update_task(task_id, status="error", message=str(exc))


# =====================================================================
#  API エンドポイント
# =====================================================================
@app.route("/")
def index():
    """メインページを表示"""
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    """動画情報を取得（ダウンロードはしない・軽量）"""
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URLを入力してください"}), 400
    if "youtube.com" not in url and "youtu.be" not in url:
        return jsonify({"error": "有効なYouTube URLを入力してください"}), 400

    try:
        ydl_opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        duration = info.get("duration", 0) or 0

        # 利用可能な画質をリストアップ
        qualities: list[dict] = []
        if info.get("formats"):
            seen: set[int] = set()
            for fmt in info["formats"]:
                h = fmt.get("height")
                if h and h >= 360 and h not in seen:
                    seen.add(h)
                    qualities.append({"value": str(h), "label": f"{h}p"})
            qualities.sort(key=lambda x: int(x["value"]), reverse=True)

        # フォールバック
        if not qualities:
            qualities = [
                {"value": "1080", "label": "1080p"},
                {"value": "720", "label": "720p"},
                {"value": "480", "label": "480p"},
            ]

        return jsonify(
            {
                "title": info.get("title", "不明"),
                "channel": info.get(
                    "channel", info.get("uploader", "不明")
                ),
                "duration": duration,
                "thumbnail": info.get("thumbnail", ""),
                "view_count": info.get("view_count", 0),
                "qualities": qualities,
                "too_long": duration > MAX_DURATION_SEC,
            }
        )

    except Exception as exc:
        return (
            jsonify({"error": f"動画情報を取得できませんでした: {exc}"}),
            400,
        )


@app.route("/api/prepare", methods=["POST"])
def api_prepare():
    """ダウンロードをバックグラウンドで開始"""
    # 同時実行数チェック
    if _count_active() >= MAX_CONCURRENT:
        return (
            jsonify(
                {
                    "error": (
                        "サーバーが混雑しています。"
                        "しばらくしてから再度お試しください。"
                    )
                }
            ),
            429,
        )

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
        task_id,
        status="starting",
        percent=0,
        url=url,
        format=fmt,
        quality=quality,
        created_at=time.time(),
        message="ダウンロードを開始しています...",
    )

    threading.Thread(
        target=_run_download,
        args=(url, task_id, fmt, quality),
        daemon=True,
    ).start()

    return jsonify({"task_id": task_id})


@app.route("/api/progress/<task_id>")
def api_progress(task_id: str):
    """ダウンロード進捗を返す"""
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "不明なタスクID"}), 404

    return jsonify(
        {
            "status": task.get("status", "unknown"),
            "percent": task.get("percent", 0),
            "speed": task.get("speed", ""),
            "eta": task.get("eta", ""),
            "message": task.get("message", ""),
            "filename": task.get("filename", ""),
            "filesize": task.get("filesize", 0),
            "title": task.get("title", ""),
        }
    )


@app.route("/api/download/<task_id>")
def api_download(task_id: str):
    """準備済みファイルをブラウザへストリーミング送信"""
    task = _get_task(task_id)
    if not task:
        return jsonify({"error": "不明なタスクID"}), 404
    if task.get("status") != "ready":
        return (
            jsonify({"error": "ファイルの準備ができていません"}),
            400,
        )

    filepath = task.get("filepath", "")
    filename = task.get("filename", "download")

    if not filepath or not os.path.isfile(filepath):
        return (
            jsonify(
                {"error": "ファイルが見つかりません（有効期限切れの可能性があります）"}
            ),
            404,
        )

    filesize = os.path.getsize(filepath)

    # MIME タイプの判定
    ext = os.path.splitext(filename)[1].lower()
    mime_map = {
        ".mp4": "video/mp4",
        ".mp3": "audio/mpeg",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".m4a": "audio/mp4",
        ".opus": "audio/opus",
        ".ogg": "audio/ogg",
    }
    content_type = mime_map.get(ext, "application/octet-stream")

    # 64KB チャンクでストリーミング（メモリを節約）
    def _generate():
        with open(filepath, "rb") as fh:
            while True:
                chunk = fh.read(65_536)
                if not chunk:
                    break
                yield chunk

    # RFC 5987 でファイル名をエンコード（日本語タイトル対応）
    encoded_name = quote(filename)

    response = Response(
        _generate(),
        mimetype=content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{encoded_name}"
            ),
            "Content-Length": str(filesize),
            "Cache-Control": "no-cache",
        },
    )

    # ダウンロード完了後に一定時間経ってからファイルを削除
    def _delayed_cleanup():
        time.sleep(120)
        _cleanup_task_files(task_id)
        _remove_task(task_id)
        logger.info("Post-download cleanup: %s", task_id)

    threading.Thread(target=_delayed_cleanup, daemon=True).start()

    return response


@app.route("/health")
def health():
    """Render のヘルスチェック用"""
    return jsonify({"status": "ok", "timestamp": time.time()})


# =====================================================================
#  ローカル実行用エントリポイント
# =====================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    print()
    print("=" * 50)
    print("  YouTube Downloader")
    print("=" * 50)
    print(f"  http://localhost:{port}")
    print("  終了するには Ctrl+C を押してください")
    print("=" * 50)
    print()

    app.run(host="0.0.0.0", port=port, debug=debug)
