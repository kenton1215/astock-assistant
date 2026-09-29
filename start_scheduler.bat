@echo off
chcp 65001 >nul
title A股短线交易助手-定时调度器
cd /d %~dp0
python scheduler.py
pause
