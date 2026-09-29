# -*- coding: utf-8 -*-
"""腾讯行情公开接口：全市场排行榜 + 个股批量行情
用途：东财接口被限流时的实时数据备源（免费、无需Token）"""
import re
import time

import numpy as np
import pandas as pd
import requests

from ..utils.logger import setup_logger

log = setup_logger("tencent")

QQ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://gu.qq.com/",
}
RANK_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
QT_URL = "https://qt.gtimg.cn/q="


def _get(url, params=None, retries=3, delay=1.5):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=QQ_HEADERS, timeout=20)
            r.raise_for_status()
            return r
        except Exception as e:
            if i == retries - 1:
                log.error("腾讯行情请求失败: %s", e)
                return None
            time.sleep(delay * (i + 1))
    return None


def get_rank_list_all(page_size=200, max_pages=32):
    """全市场A股排行（按涨跌幅降序分页，剔除北交所），返回归一化DataFrame"""
    rows = []
    for offset in range(0, max_pages * page_size, page_size):
        r = _get(RANK_URL, {"board_code": "aStock", "sort_type": "PriceRatio",
                            "direct": "down", "offset": offset, "count": page_size,
                            "app": "pc_rank"})
        data = ((r.json() or {}).get("data") or {}) if r is not None else {}
        page = data.get("rank_list") or []
        if not page:
            break
        rows.extend(page)
        total = data.get("total") or 0
        if offset + page_size >= total:
            break
        time.sleep(0.4)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[~df["code"].str.startswith("bj")].copy()  # 剔除北交所
    df["code"] = df["code"].str[2:]
    df["ts_code"] = df["code"].map(lambda c: c + (".SH" if c[0] == "6" else ".SZ"))
    df = df.rename(columns={
        "zxj": "price", "zdf": "pct_chg", "hsl": "turnover", "lb": "vol_ratio",
        "ltsz": "circ_mv_yi", "zsz": "total_mv_yi", "zljlr": "main_inflow_wan",
        "pe_ttm": "pe_ttm", "turnover": "amount_wan", "volume": "volume", "zf": "amplitude",
    })
    for c in ("price", "pct_chg", "turnover", "vol_ratio", "circ_mv_yi", "total_mv_yi",
              "main_inflow_wan", "pe_ttm", "amount_wan", "volume", "amplitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["amount"] = df["amount_wan"] * 1e4    # 万元 → 元
    df["circ_mv"] = df["circ_mv_yi"] * 1e8   # 亿 → 元
    df["total_mv"] = df["total_mv_yi"] * 1e8
    df["main_inflow"] = df["main_inflow_wan"] * 1e4
    df["industry"] = ""
    for c in ("open", "high", "low", "pre_close"):
        df[c] = np.nan
    df["pre_close"] = (df["price"] / (1 + df["pct_chg"] / 100)).round(2)  # 近似昨收，候选股稍后精确补齐
    df = df.drop(columns=["circ_mv_yi", "total_mv_yi", "main_inflow_wan", "amount_wan"],
                 errors="ignore")
    return df.reset_index(drop=True)


def get_quotes(codes, batch=60):
    """批量个股行情（qt.gtimg.cn）：补齐 open/high/low/pre_close/涨停价/量比
    codes: ['600519.SH', '000001.SZ', ...]"""
    qcodes = [("sh" if c.endswith(".SH") else "sz") + c[:6] for c in codes]
    out = []
    for i in range(0, len(qcodes), batch):
        chunk = qcodes[i:i + batch]
        r = _get(QT_URL + ",".join(chunk), retries=2)
        if r is None:
            continue
        r.encoding = "gbk"
        for line in r.text.strip().splitlines():
            m = re.match(r'v_(s[hz]\d{6})="(.*)";', line)
            if not m:
                continue
            q, body = m.group(1), m.group(2)
            f = body.split("~")
            if len(f) < 50:
                continue  # 停牌等异常格式
            try:
                out.append({
                    "qcode": q,
                    "name": f[1],
                    "price": float(f[3]), "pre_close": float(f[4]), "open": float(f[5]),
                    "high": float(f[33]), "low": float(f[34]),
                    "pct_chg": float(f[32]), "volume": float(f[36]),
                    "amount": float(f[37]) * 1e4,  # 万元 → 元
                    "turnover": float(f[38]), "vol_ratio": float(f[49]),
                    "limit_up_price": float(f[47]), "limit_down_price": float(f[48]),
                })
            except (ValueError, IndexError):
                continue
        time.sleep(0.3)
    return pd.DataFrame(out)
