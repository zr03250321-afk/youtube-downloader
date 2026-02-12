# =====================================================
#  YouTube Downloader — Production Dockerfile
#  Python 3.11 + ffmpeg + gunicorn
# =====================================================
FROM python:3.11-slim

# ffmpeg をインストール（動画・音声の変換に必要）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存関係を先にインストール（Docker キャッシュ活用）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションをコピー
COPY . .

# Render が $PORT を環境変数で渡す
# gunicorn.conf.py で読み取る
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
