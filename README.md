# YouTube Downloader

YouTube動画をダウンロードするツール。**Webアプリ**（ブラウザ版）と**CLI**（コマンドライン版）の2つの使い方があります。

## 構成

```
youtube_downloader/
├── app.py               # Webアプリ本体（Flask）
├── cli.py               # CLIツール（コマンドラインで手軽に使う用）
├── templates/
│   └── index.html       # フロントエンド
├── static/
│   └── style.css        # スタイルシート
├── requirements.txt     # Python依存パッケージ
├── Dockerfile           # 本番用Dockerイメージ
├── docker-compose.yml   # 本番用Docker Compose設定
├── gunicorn.conf.py     # 本番用Gunicorn設定
├── start.sh             # 本番用起動スクリプト（Docker内）
├── healthcheck.sh       # 監視: 5分ごとの死活確認＋自動復旧（VPSのcron）
├── synthetic_dl_test.sh # 監視: 毎日1回の実ダウンロードテスト（VPSのcron）
├── setup_cron.sh        # 上記監視をVPSのcronへ一括登録するスクリプト
├── start_mac.command    # ローカル起動（Mac）
├── start_windows.bat    # ローカル起動（Windows）
├── render.yaml          # Render.comデプロイ設定（現在は未使用）
├── README.txt           # エンドユーザー向けガイド
└── DEPLOYMENT.md        # サーバー運用情報（パスワード等を含むため非公開）
```

## Webアプリ（ブラウザ版）

ブラウザでアクセスして動画をダウンロードできるフル機能版。

### 機能
- 動画情報のプレビュー（タイトル、サムネイル、長さ）
- 画質選択（360p〜最高画質）
- 動画（MP4）/ 音声のみ（MP3）
- Premiere Pro互換形式（H.264 + AAC）への自動変換
- リアルタイム進捗表示
- Cookie認証 / PO Token対応

### ローカルで起動

```bash
# Mac: ダブルクリックで起動
open start_mac.command

# または手動で
python app.py
```

ブラウザで http://localhost:8080 にアクセス。

### 本番環境

`dl.movie-pro.co.jp` で稼働中。詳細は `DEPLOYMENT.md` を参照。

### 放置運用のための自動化（VPS上で稼働）

| 仕組み | 頻度 | 役割 |
|--------|------|------|
| `healthcheck.sh` (cron) | 5分ごと | 内部＋外部経路の死活確認。異常時はコンテナ/nginxを自動再起動し、Discordへ通知 |
| コンテナ再起動 (cron) | 毎日 AM5:00 | 起動時に `start.sh` が yt-dlp を最新版へ自動更新（YouTube仕様変更への追従） |
| `synthetic_dl_test.sh` (cron) | 毎日 AM6:30 | 実際にテスト動画をダウンロードして「本当に使えるか」を確認。失敗時はDiscordへ通知、毎週月曜に週次生存報告 |
| Docker `restart: unless-stopped` | 常時 | VPS再起動後もコンテナが自動で立ち上がる |
| Let's Encrypt (certbot) | 自動 | SSL証明書の自動更新 |

Discord通知用のWebhook URLは、VPS上の `/opt/youtube-downloader/.env.monitor`（GitHub非公開）に保存する。

## CLI（コマンドライン版）

ターミナルからサクッとダウンロードしたい時用。

```bash
# 対話モード
python cli.py

# URL指定で動画ダウンロード
python cli.py "https://www.youtube.com/watch?v=VIDEO_ID"

# 音声のみ（MP3）
python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --audio

# 保存先を指定
python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --output ~/Videos
```

保存先のデフォルトは `~/Downloads/YouTube`。

## セットアップ

```bash
cd youtube_downloader
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 注意事項

- YouTubeの利用規約では動画のダウンロードは基本的に禁止されています
- 著作権のあるコンテンツのダウンロードは法的問題になる可能性があります
- 個人利用の範囲でご使用ください
