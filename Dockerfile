# =====================================================
#  YouTube Downloader — Production Dockerfile
#  Python 3.11 + ffmpeg + deno + Node.js + PO Token
# =====================================================
FROM python:3.11-slim

# システム依存パッケージ
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
        ca-certificates \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# deno インストール（yt-dlp の YouTube JS 解析に必要）
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# Node.js 20 インストール（PO Token 生成に必要）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# bgutil PO Token サーバーをビルド
RUN git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil && \
    cd /opt/bgutil/server && \
    npm install && \
    npx tsc

WORKDIR /app

# Python 依存パッケージ
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーション
COPY . .
RUN chmod +x start.sh

CMD ["./start.sh"]
