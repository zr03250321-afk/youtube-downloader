#!/bin/bash
# ============================================
# YouTube Downloader - Mac起動スクリプト
# ============================================

cd "$(dirname "$0")"

echo ""
echo "🎬 YouTube Downloader を起動しています..."
echo ""

# Python仮想環境をチェック
if [ ! -d "venv" ]; then
    echo "⚠️  初回起動のため、セットアップを実行します..."
    echo "   （1〜2分かかる場合があります）"
    echo ""
    
    # Python3をチェック
    if ! command -v python3 &> /dev/null; then
        echo "❌ エラー: Python3がインストールされていません"
        echo ""
        echo "以下のコマンドでインストールしてください:"
        echo "  brew install python3"
        echo ""
        echo "Homebrewがない場合は先に以下を実行:"
        echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo ""
        read -p "Enterキーで終了..."
        exit 1
    fi
    
    # 仮想環境を作成
    echo "📦 仮想環境を作成中..."
    python3 -m venv venv
    
    # 依存パッケージをインストール
    echo "📥 必要なパッケージをインストール中..."
    ./venv/bin/pip install --upgrade pip > /dev/null 2>&1
    ./venv/bin/pip install -r requirements.txt
    
    echo ""
    echo "✅ セットアップ完了！"
    echo ""
fi

# アプリを起動
echo "🚀 アプリを起動中..."
echo "   ブラウザが自動で開きます"
echo ""
echo "⏹️  終了するには、このウィンドウを閉じるか Ctrl+C を押してください"
echo ""

./venv/bin/python app.py
