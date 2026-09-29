# -*- coding: utf-8 -*-
"""东方财富公开行情接口：全市场实时快照 / 行业与概念板块 / 指数
说明：Tushare 实时行情需要高积分且无法批量获取，本模块用东财免费接口
获取当日实时数据（无需Token），历史日线仍以 Tushare 为准。"""
import time

import requests
import pandas as pd

from ..utils.logger import setup_logger

log = setup_logger("em")

EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
}
# 东财公开接口的固定 ut 参数 + 可用子域名（失败自动轮换）
EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
EM_HOSTS = [
    "https://push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://48.push2.eastmoney.com",
]

# 沪深A股（深主板+创业板+沪主板+科创板，不含北交所）
A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
SPOT_FIELDS = "f2,f3,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f62,f100"
COLUMN_MAP = {
    "f12": "code", "f14": "name", "f2": "price", "f3": "pct_chg", "f5": "volume",
    "f6": "amount", "f7": "amplitude", "f8": "turnover", "f9": "pe_ttm", "f10": "vol_ratio",
    "f15": "high", "f16": "low", "f17": "open", "f18": "pre_close",
    "f20": "total_mv", "f21": "circ_mv", "f62": "main_inflow", "f100": "industry",
}
# 字符串字段不能做数值转换
TEXT_COLUMNS = {"name", "industry", "code"}

# 东财被风控时的熔断：失败后10分钟内不再尝试（自动切换到腾讯备源，避免每次浪费等待时间）
_EM_FAIL_TS = 0.0
_EM_COOLDOWN = 600


def em_available():
    return time.time() - _EM_FAIL_TS > _EM_COOLDOWN


def _get_json(url_path, params, retries=3, backoff=3.0):
    """带主机轮换与退避重试的请求（东财风控严格，退避要足够长）"""
    global _EM_FAIL_TS
    if not em_available():
        return None
    last_err = None
    for i in range(retries):
        host = EM_HOSTS[i % len(EM_HOSTS)]
        try:
            r = requests.get(host + url_path, params=params, headers=EM_HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(backoff * (i + 1))
    _EM_FAIL_TS = time.time()
    log.error("东方财富接口请求失败(10分钟内自动切换腾讯备源): %s", last_err)
    return None


def _to_df(js):
    data = (js or {}).get("data") or {}
    diff = data.get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not diff:
        return pd.DataFrame()
    return pd.DataFrame(diff)


def _fetch_spot_page(pn, pz):
    js = _get_json("/api/qt/clist/get", {
        "pn": pn, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "ut": EM_UT, "fid": "f3", "fs": A_SHARE_FS, "fields": SPOT_FIELDS,
    }, retries=2, backoff=2.5)
    data = (js or {}).get("data") or {}
    diff = data.get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    return diff, data.get("total") or 0


def get_spot_all():
    """全市场沪深A股实时快照：先单请求拉全(pz=50000)，不足则分页补拉。
    注意：东财对高频请求有风控，本函数每次运行最多尝试少量请求，失败返回部分/空数据，
    由 realtime 层决定是否切换腾讯备源。"""
    diff, total = _fetch_spot_page(1, 50000)
    rows = list(diff)
    if total and len(rows) < total:
        # 服务端限页时用分页补拉
        for pn in range(2, 9):
            page, _ = _fetch_spot_page(pn, 1000)
            if not page:
                break
            rows.extend(page)
            if len(rows) >= total:
                break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns=COLUMN_MAP)
    for c in COLUMN_MAP.values():
        if c in df.columns and c not in TEXT_COLUMNS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.reset_index(drop=True)


def get_sector_board(board_type):
    """行业板块 board_type='industry'；概念板块 board_type='concept'"""
    fs = "m:90+t:2+f:!50" if board_type == "industry" else "m:90+t:3+f:!50"
    df = _to_df(_get_json("/api/qt/clist/get", {
        "pn": 1, "pz": 200, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "ut": EM_UT, "fid": "f3", "fs": fs,
        "fields": "f3,f12,f14,f62,f104,f105,f128,f136,f140",
    }, retries=1))
    if df.empty:
        return df
    df = df.rename(columns={
        "f12": "board_code", "f14": "board_name", "f3": "pct_chg",
        "f62": "main_inflow", "f104": "up_count", "f105": "down_count",
        "f128": "leader_name", "f136": "leader_pct", "f140": "leader_code",
    })
    for c in ("pct_chg", "main_inflow", "up_count", "down_count", "leader_pct"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.reset_index(drop=True)


def get_indices():
    """主要指数：上证/深成/创业板指/沪深300/科创50"""
    df = _to_df(_get_json("/api/qt/ulist.np/get", {
        "fltt": 2, "invt": 2, "ut": EM_UT,
        "fields": "f2,f3,f4,f12,f14",
        "secids": "1.000001,0.399001,0.399006,1.000300,1.000688",
    }, retries=1))
    if df.empty:
        return df
    df = df.rename(columns={"f12": "code", "f14": "name", "f2": "price", "f3": "pct_chg", "f4": "change"})
    for c in ("price", "pct_chg", "change"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.reset_index(drop=True)
