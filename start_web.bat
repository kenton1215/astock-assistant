@echo off
chcp 65001 >nul
title A股短线交易助手-网页版
cd /d %~dp0
streamlit run app.py
pause
