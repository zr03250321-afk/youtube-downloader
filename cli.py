#!/usr/bin/env python3
"""
YouTube Downloader - CLI版
URLを指定してコマンドラインからYouTube動画をサクッとダウンロード

使い方:
  python cli.py                          # 対話モード
  python cli.py "URL"                    # 動画ダウンロード
  python cli.py "URL" --audio            # 音声のみ（MP3）
  python cli.py "URL" --output ~/Videos  # 保存先を指定
"""

import yt_dlp
import sys
import os
import argparse


def progress_hook(d: dict):
    """ダウンロード進捗を表示"""
    if d["status"] == "downloading":
        percent = d.get("_percent_str", "???")
        speed = d.get("_speed_str", "???")
        eta = d.get("_eta_str", "???")
        print(f"\r⏬ {percent} | 速度: {speed} | 残り: {eta}    ", end="", flush=True)
    elif d["status"] == "finished":
        print("\n✅ ダウンロード完了！")


def download(url: str, output_path: str, audio_only: bool = False) -> bool:
    """YouTube動画をダウンロード"""
    os.makedirs(output_path, exist_ok=True)

    opts = {
        "outtmpl": f"{output_path}/%(title)s.%(ext)s",
        "progress_hooks": [progress_hook],
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
    }

    if audio_only:
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        opts.update({
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "merge_output_format": "mp4",
        })

    print(f"\n🎬 YouTube Downloader (CLI)")
    print(f"{'=' * 50}")
    print(f"📎 URL: {url}")
    print(f"📁 保存先: {output_path}")
    print(f"🎵 形式: {'音声のみ (MP3)' if audio_only else '動画 (MP4)'}")
    print(f"{'=' * 50}\n")

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            print(f"📺 タイトル: {info.get('title', '不明')}")
            print(f"👤 チャンネル: {info.get('channel', '不明')}")
            duration = info.get("duration", 0)
            print(f"⏱️ 長さ: {duration // 60}分{duration % 60}秒")
            print()
            ydl.download([url])

        print(f"\n🎉 完了！ → {output_path}")
        return True

    except Exception as e:
        print(f"\n❌ エラー: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="YouTube動画をダウンロード（CLI版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n  python cli.py \"https://youtube.com/watch?v=xxx\"\n  python cli.py \"https://youtube.com/watch?v=xxx\" --audio",
    )
    parser.add_argument("url", nargs="?", help="YouTube動画のURL")
    parser.add_argument("-a", "--audio", action="store_true", help="音声のみ（MP3）でダウンロード")
    parser.add_argument("-o", "--output", default=os.path.expanduser("~/Downloads/YouTube"), help="保存先フォルダ（デフォルト: ~/Downloads/YouTube）")

    args = parser.parse_args()

    if not args.url:
        url = input("📎 YouTube URLを入力: ").strip()
        if not url:
            print("❌ URLが入力されていません")
            sys.exit(1)
        choice = input("🎵 音声のみ？ (y/N): ").strip().lower()
        args.audio = choice in ("y", "yes")
    else:
        url = args.url

    success = download(url, args.output, audio_only=args.audio)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
