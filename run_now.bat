@echo off
chcp 65001 >nul
title A股短线交易助手-立即运行
cd /d %~dp0
python main.py --time auto
pause
