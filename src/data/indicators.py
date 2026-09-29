# -*- coding: utf-8 -*-
"""技术指标与涨停/连板判定（纯函数，无IO）"""
import numpy as np
import pandas as pd

from config import LIMIT


def to_ts_code(code):
    """600519 / 600519.SH / SH600519 → 600519.SH"""
    code = str(code).strip()
    if "." in code:
        return code.upper()
    if len(code) == 6:
        return code + (".SH" if code[0] == "6" else ".SZ")
    return code


def limit_ratio(code, name=""):
    """涨停判定阈值(%)：ST 5%、创业板/科创板 20%、主板 10%"""
    if "ST" in str(name or "").upper():
        return LIMIT["st"]
    c = str(code or "")
    if c.startswith(("300", "301", "302", "688", "689")):
        return LIMIT["gem"]
    return LIMIT["main"]


def ratio_series(codes, names=None):
    """向量化计算每只股票的涨停判定阈值"""
    code = codes.astype(str)
    if names is not None:
        is_st = names.astype(str).str.upper().str.contains("ST", na=False).to_numpy()
    else:
        is_st = np.zeros(len(code), dtype=bool)
    is_20 = code.str.startswith(("300", "301", "302", "688", "689")).to_numpy()
    return np.where(is_st, LIMIT["st"], np.where(is_20, LIMIT["gem"], LIMIT["main"])).astype(float)


def limit_price(prev_close, ratio):
    """涨停价（四舍五入到分）"""
    if prev_close is None or pd.isna(prev_close) or prev_close <= 0:
        return np.nan
    return np.floor(prev_close * (1 + ratio / 100) * 100 + 0.5) / 100


def is_limit_up(pct, ratio):
    """是否涨停（含容差）"""
    return pct >= ratio - 0.2


def ma_of(a, n):
    """最后n个有效值的均值（对应MA5/10/20/60）"""
    a = a[~np.isnan(a)]
    if len(a) < n:
        return np.nan
    return float(np.mean(a[-n:]))


def consec_limits(pct_a, ratio):
    """连续涨停天数（从最后一根K线往回数，含当日；当日未涨停为0）"""
    a = pct_a[~np.isnan(pct_a)]
    cnt = 0
    for v in a[::-1]:
        if v >= ratio - 0.2:
            cnt += 1
        else:
            break
    return cnt
