@echo off
chcp 65001 > nul
title YouTube Downloader

echo.
echo 🎬 YouTube Downloader を起動しています...
echo.

cd /d "%~dp0"

REM Python仮想環境をチェック
if not exist "venv" (
    echo ⚠️  初回起動のため、セットアップを実行します...
    echo    （1〜2分かかる場合があります）
    echo.
    
    REM Python3をチェック
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo ❌ エラー: Pythonがインストールされていません
        echo.
        echo 以下のURLからPythonをダウンロードしてインストールしてください:
        echo   https://www.python.org/downloads/
        echo.
        echo ※インストール時に「Add Python to PATH」にチェックを入れてください
        echo.
        pause
        exit /b 1
    )
    
    REM 仮想環境を作成
    echo 📦 仮想環境を作成中...
    python -m venv venv
    
    REM 依存パッケージをインストール
    echo 📥 必要なパッケージをインストール中...
    call venv\Scripts\pip.exe install --upgrade pip > nul 2>&1
    call venv\Scripts\pip.exe install -r requirements.txt
    
    echo.
    echo ✅ セットアップ完了！
    echo.
)

REM アプリを起動
echo 🚀 アプリを起動中...
echo    ブラウザが自動で開きます
echo.
echo ⏹️  終了するには、このウィンドウを閉じてください
echo.

call venv\Scripts\python.exe app.py

pause
