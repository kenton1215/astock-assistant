# -*- coding: utf-8 -*-
"""全局配置：环境变量、目录、交易体系量化参数"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get_secret(name, default=""):
    """密钥读取优先级：环境变量 > .env > streamlit secrets（Cloud部署用）"""
    v = os.getenv(name, "").strip()
    if v:
        return v
    try:
        import streamlit as st
        sec = st.secrets.get(name, "") if hasattr(st, "secrets") else ""
        return str(sec).strip() if sec else default
    except Exception:
        return default


# ---------- API Keys ----------
TUSHARE_TOKEN = _get_secret("TUSHARE_TOKEN")
DEEPSEEK_API_KEY = _get_secret("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _get_secret("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = _get_secret("DEEPSEEK_MODEL", "deepseek-v4.1-flash").strip()  # API模型ID: deepseek-flash(=V4.1-Flash) / deepseek-v4-pro
TAVILY_API_KEY = _get_secret("TAVILY_API_KEY")

# ---------- 账户系统（网页版多用户） ----------
SECRET_KEY = _get_secret("SECRET_KEY", "astock-local-secret-change-me")  # 生产环境务必覆盖（用于加密用户APIKey）
AUTH = dict(
    backend=os.getenv("AUTH_BACKEND", "local"),  # local=JSON文件(单机) / supabase=云端持久化
    free_trial_credits=5,     # 新注册赠送次数（用站长Key）
    daily_limit=30,           # 每人每日分析次数上限
    default_code_credits=10,  # 激活码默认次数
    username_min=2,           # 用户名最少字符数（宽松策略，方便注册）
    username_max=30,          # 用户名最多字符数
    password_min=4,           # 密码最少位数
)
SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = _get_secret("SUPABASE_KEY")  # service_role key（仅服务端使用，勿泄露）

# ---------- 目录 ----------
DATA_CACHE_DIR = BASE_DIR / "data_cache"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"
HOLDINGS_FILE = BASE_DIR / "holdings.json"
USER_DB_FILE = DATA_CACHE_DIR / "users.json"
for _d in (DATA_CACHE_DIR, REPORT_DIR, LOG_DIR):
    _d.mkdir(exist_ok=True)

# ---------- 选股初筛硬性条件（短线作手体系量化） ----------
SCREEN = dict(
    min_circ_mv=20e8,        # 流通市值 ≥ 20亿（太小易被操纵）
    max_circ_mv=500e8,       # 流通市值 ≤ 500亿（弹性优先，可自行调整）
    min_amount=2e8,          # 当日成交额 ≥ 2亿（流动性）
    min_turnover=3.0,        # 换手率 3% ~ 25%（涨停/连板股放宽到 40%）
    max_turnover=25.0,
    limit_turnover=40.0,
    min_vol_ratio=1.2,       # 量比 ≥ 1.2（放量验证）
    min_pct=2.0,             # 当日涨幅 ≥ 2%
    min_list_bars=60,        # 上市 ≥ 60 个交易日（排除新股）
    breakout_vol_ratio=1.5,  # 突破模式量比要求
    pullback_pct=3.0,        # 趋势回踩模式当日涨幅要求
    max_llm_candidates=50,   # 送入大模型复核的候选数量
    max_buy=6,               # 每天最多推荐买入数
)

# ---------- 涨跌停判定阈值(%) ----------
LIMIT = dict(
    main=9.7,   # 主板 10%
    gem=19.5,   # 创业板/科创板 20%
    st=4.7,     # ST 5%
)

# ---------- 风控 ----------
RISK = dict(
    stop_loss_pct=5.0,       # 常规止损比例 -5%
    single_position=20,      # 单票仓位上限 20%
    max_positions=4,         # 同时持股数量上限
)

HISTORY_DAYS = 70   # 历史日线窗口（MA60 + 缓冲）

# ---------- 会话定义（每天6个时段） ----------
SESSIONS = {
    "10:00": dict(name="早盘观察", hint="开盘30分钟：定调当日情绪与主线，验证盘前计划"),
    "10:30": dict(name="盘中观察", hint="盘中：验证主线持续性，筛选今日可介入买点"),
    "11:30": dict(name="午间复盘", hint="上午收盘：上午总结 + 下午交易计划"),
    "14:00": dict(name="午后观察", hint="午后开盘30分钟：验证上午主线延续性，捕捉午后新方向"),
    "14:30": dict(name="尾盘观察", hint="尾盘半小时：尾盘异动、持仓处置与次日预判"),
    "15:00": dict(name="收盘报告", hint="收盘：全天复盘 + 买入候选 + 持仓处置 + 明日盘前计划"),
}
