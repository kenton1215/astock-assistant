# -*- coding: utf-8 -*-
"""规则初筛：按短线作手体系量化过滤 + 打分 + 模式分桶"""
import numpy as np
import pandas as pd

from config import SCREEN


def prefilter(spot):
    """粗筛（纯向量化硬性条件，缩小后续指标计算范围）：
    排除 ST/退市、量能不足、市值过小/过大、涨幅不足的股票"""
    df = spot[spot["price"] > 0].copy()
    if "name" in df.columns:
        df = df[~df["name"].str.upper().str.contains("ST|退", na=False)]
    df = df[df["amount"] >= SCREEN["min_amount"]]
    df = df[(df["circ_mv"] >= SCREEN["min_circ_mv"]) & (df["circ_mv"] <= SCREEN["max_circ_mv"])]
    df = df[df["pct_chg"] >= SCREEN["min_pct"]]
    df = df[df["pct_chg"] < 21.0]
    df = df[df["turnover"].isna() | df["turnover"].between(SCREEN["min_turnover"], SCREEN["limit_turnover"])]
    return df.reset_index(drop=True)


def screen(enriched, sector_rank_map=None, sector_pct_map=None):
    """精选：按模式过滤 + 多因子打分，返回送入大模型复核的候选池"""
    df = enriched.copy()
    if df.empty:
        return df
    sector_rank_map = sector_rank_map or {}
    sector_pct_map = sector_pct_map or {}

    # 换手率：涨停/连板股放宽上限
    t_max = np.where(df["is_limit"] | (df["lianban"] >= 1),
                     SCREEN["limit_turnover"], SCREEN["max_turnover"])
    ok = df["turnover"].isna() | ((df["turnover"] >= SCREEN["min_turnover"]) & (df["turnover"] <= t_max))
    df = df[ok]

    # 量比：涨停/连板股放宽
    df = df[df["vol_ratio"].isna() | (df["vol_ratio"] >= SCREEN["min_vol_ratio"]) | (df["lianban"] >= 1)]

    # 主力净流入：有数据时要求为正（涨停股放宽）
    if df["main_inflow"].notna().any():
        df = df[df["main_inflow"].isna() | (df["main_inflow"] > 0) | (df["is_limit"])]

    # 趋势：站上MA5，或 MA5>MA10 且站上 MA10（强势整理）
    trend_ok = ((df["price"] >= df["ma5"] * 0.995)
                | ((df["ma5"] >= df["ma10"]) & (df["price"] >= df["ma10"] * 0.99)))
    df = df[trend_ok]

    # 一字板买不进，剔除
    df = df[~df["one_word"]]

    # 模式分桶（对应五种作手买点模式）
    def _pattern(r):
        if r["lianban"] >= 1 or r["is_limit"]:
            return "连板/涨停"
        if r["dist20"] >= -0.5 and r["vol_ratio"] >= SCREEN["breakout_vol_ratio"]:
            return "突破"
        if r["ma10"] >= r["ma20"] and r["pct_chg"] >= SCREEN["pullback_pct"]:
            return "趋势回踩"
        return "强势跟随"

    df["pattern"] = df.apply(_pattern, axis=1)

    # 板块强度
    df["sector_rank"] = df["industry"].map(sector_rank_map).fillna(99).astype(int)
    df["sector_pct"] = df["industry"].map(sector_pct_map).fillna(0.0)

    # 板块效应铁律：板块排名前30（或连板/涨停股自成一势可豁免；行业数据缺失时放宽）
    df = df[(df["sector_rank"] <= 30) | (df["lianban"] >= 1) | (df["is_limit"]) | (df["sector_rank"] == 99)]

    # 多因子打分：涨幅/量能/股性/位置/资金/板块
    inflow_ratio = (df["main_inflow"] / df["amount"]).replace([np.inf, -np.inf], np.nan).fillna(0).clip(0, 0.3)
    score = (
        df["pct_chg"] * 1.0
        + df["vol_ratio"].fillna(1) * 2.0
        + df["lianban"] * 8.0
        + df["lim20"] * 2.5
        + df["dist20"].fillna(-5).clip(-5, 8) * 1.5
        + inflow_ratio * 20
        - (df["sector_rank"] - 1).clip(0, 30) * 0.4
        + np.where(df["pattern"] == "突破", 5.0, 0.0)
    )
    df["score"] = score.round(1)
    df = df.sort_values(["score", "pct_chg"], ascending=False).reset_index(drop=True)
    return df.head(SCREEN["max_llm_candidates"])
