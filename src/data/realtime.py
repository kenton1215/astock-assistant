# -*- coding: utf-8 -*-
"""实时行情获取链：东方财富(首选) → 腾讯(备源)
东财单请求即可拉全市场且含行业/OHLC；被限流时自动切换腾讯排行榜，
OHLC/精确昨收由 market_data 层对候选股用腾讯批量行情补齐。"""
import pandas as pd

from . import em_realtime, tencent
from ..utils.logger import setup_logger

log = setup_logger("realtime")

MIN_SPOT_ROWS = 1000  # 全市场快照低于此数视为获取失败


def get_spot_all():
    """返回 (spot_df, source)"""
    df = em_realtime.get_spot_all()
    if len(df) >= MIN_SPOT_ROWS:
        return df, "东方财富实时行情"
    log.warning("东财实时行情不可用(%d行)，切换腾讯排行榜", len(df))
    df = tencent.get_rank_list_all()
    if len(df) >= MIN_SPOT_ROWS:
        return df, "腾讯实时行情(东财受限)"
    log.error("东财与腾讯实时行情均获取失败")
    return pd.DataFrame(), ""


def get_sector_board(board_type):
    """行业/概念板块：东财接口（腾讯无对应板块接口，失败时由行情聚合兜底）"""
    return em_realtime.get_sector_board(board_type)


def get_indices():
    """主要指数：东财 → 腾讯个股接口兜底"""
    df = em_realtime.get_indices()
    if not df.empty:
        return df
    q = tencent.get_quotes(["000001.SH", "399001.SZ", "399006.SZ", "000300.SH", "000688.SH"])
    if q.empty:
        return pd.DataFrame()
    order = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
             "sh000300": "沪深300", "sh000688": "科创50"}
    rows = []
    for _, r in q.iterrows():
        rows.append({"code": r["qcode"], "name": order.get(r["qcode"], r.get("name", "")),
                     "price": r["price"], "pct_chg": r["pct_chg"], "change": None})
    return pd.DataFrame(rows).reset_index(drop=True)
