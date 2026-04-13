"""
Gunicorn 設定ファイル

Render Free Tier の制約に最適化:
- 512 MB RAM → ワーカー 1 + スレッド 4 で省メモリ
- 動画ダウンロードは長時間 → タイムアウト 600 秒
"""
import os

# ===== サーバー設定 =====
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

# ===== ワーカー設定 =====
workers = 1               # メモリ節約のため 1 ワーカー
threads = 4               # スレッドで並行処理
worker_class = "gthread"  # スレッド対応ワーカー
timeout = 600             # 10 分（大きなファイルのストリーミング用）
graceful_timeout = 30     # シャットダウン猶予

# ===== ワーカー自動リサイクル =====
# 一定回数のリクエスト処理後にワーカーを自動再起動する
# → メモリリークやハングの予防（交代制のような仕組み）
max_requests = 50         # 50 リクエスト処理後に再起動
max_requests_jitter = 10  # ±10 のランダム幅で一斉再起動を防ぐ
worker_tmp_dir = "/dev/shm"  # RAM 上に健全性チェック用ファイルを置く（高速）

# ===== ログ設定 =====
accesslog = "-"           # stdout へ出力
errorlog = "-"            # stdout へ出力
loglevel = "info"

# ===== パフォーマンス =====
preload_app = True        # 起動を高速化
keepalive = 5             # Keep-Alive 接続の保持秒数
