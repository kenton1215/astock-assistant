# -*- coding: utf-8 -*-
"""个股深度体检：技术面 + 资金面 + 基本面 + 消息面 → 大模型综合建议
供网页版「个股体检」页签调用；任一环节失败自动降级，不阻断整体输出。"""
import time

import numpy as np
import pandas as pd

from ..utils.logger import setup_logger
from ..data.market_data import MarketDataService
from ..data.fundamentals import FundamentalsClient
from ..data.tencent import get_quotes
from ..news.tavily_client import search_stock_news
from ..llm.deepseek_client import chat_json
from ..prompts.prompts import CHECKUP_SYSTEM
from ..portfolio.holdings import norm_code

log = setup_logger("checkup")


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if np.isnan(obj) else round(float(obj), 2)
    return obj


def _r2(x):
    return round(float(x), 2) if x is not None and not pd.isna(x) else None


def run_checkup(ts_code, progress=None, llm_key=None, llm_base=None, llm_model=None,
                use_llm=True, use_news=True):
    """个股体检主流程，返回结构化dict"""
    ts_code = norm_code(ts_code)
    today = time.strftime("%Y%m%d")
    out = {"ts_code": ts_code, "today": today, "name": "", "tech": None,
           "fund": {}, "news": [], "analysis": None}

    def _p(stage, detail=""):
        if progress:
            progress(stage, detail)

    _p("data", "获取行情与历史数据…")
    svc = MarketDataService()
    svc.update_history()
    svc.load_history()
    spot, source = svc.load_spot()
    out["source"] = source

    # ---------- 技术面 ----------
    tech_row = None
    if ts_code in set(spot["ts_code"]):
        svc.fill_intraday_ohlc([ts_code])
        svc.prepare_pivots(spot)
        enriched = svc.enrich([ts_code])
        if not enriched.empty:
            tech_row = enriched.iloc[0]
    if tech_row is None:
        # 停牌/新股等无实时数据：用最近历史收盘
        try:
            last = svc.cache.load(svc.hist_dates[-1])
            row = last[last["ts_code"] == ts_code]
            if not row.empty:
                r = row.iloc[0]
                tech_row = dict(ts_code=ts_code, name="", price=r["close"], pct_chg=r["pct_chg"],
                                high=r["high"], low=r["low"], open=r["open"], pre_close=r["pre_close"],
                                amount=r["amount"], turnover=None, vol_ratio=None,
                                circ_mv=None, main_inflow=None, pe_ttm=None,
                                ma5=None, ma10=None, ma20=None, ma60=None,
                                dist20=None, is_limit=False, lianban=0, lim20=0,
                                broken=False, one_word=False)
                tech_row = pd.Series(tech_row)
        except Exception:
            tech_row = None
    if tech_row is None:
        return {"ts_code": ts_code, "today": today, "error": "未找到该股票数据（请检查代码，或该股停牌/退市）"}
    out["name"] = str(tech_row.get("name") or "")
    if not out["name"]:
        q = get_quotes([ts_code])
        if not q.empty:
            out["name"] = str(q.iloc[0].get("name") or "")
    out["tech"] = {k: _r2(tech_row.get(k)) if not isinstance(tech_row.get(k), (bool, str)) else tech_row.get(k)
                   for k in ("price", "pct_chg", "open", "high", "low", "pre_close", "amount",
                             "turnover", "vol_ratio", "circ_mv", "main_inflow", "pe_ttm",
                             "ma5", "ma10", "ma20", "ma60", "dist20", "lianban", "lim20",
                             "is_limit", "broken")}
    out["tech"]["industry"] = str(tech_row.get("industry") or "")

    # ---------- 基本面/资金面（Tushare，积分不足自动降级） ----------
    _p("fund", "获取财务/资金/龙虎榜数据…")
    try:
        fund = FundamentalsClient(svc.tushare)
        f = fund.fetch_stock(ts_code, today, svc.hist_dates)
        if f.get("available"):
            out["fund"]["available"] = list(f["available"])
            if "daily_basic" in f:
                db = f["daily_basic"].iloc[-1]
                out["fund"]["估值"] = {k: _r2(db.get(k)) for k in
                                       ("pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv")}
            if "fina" in f:
                out["fund"]["财务指标"] = []
                for _, r in f["fina"].head(4).iterrows():
                    out["fund"]["财务指标"].append({
                        "报告期": str(r.get("end_date", ""))[:10],
                        "ROE%": _r2(r.get("roe")), "净利同比%": _r2(r.get("netprofit_yoy")),
                        "营收同比%": _r2(r.get("tr_yoy")), "毛利率%": _r2(r.get("grossprofit_margin")),
                        "负债率%": _r2(r.get("debt_to_assets")), "EPS": _r2(r.get("eps")),
                    })
            if "forecast" in f:
                fc = f["forecast"].iloc[0]
                out["fund"]["业绩预告"] = {"类型": str(fc.get("type", "")),
                                           "净利变动下限%": _r2(fc.get("p_change_min")),
                                           "净利变动上限%": _r2(fc.get("p_change_max")),
                                           "公告日": str(fc.get("ann_date", ""))[:10]}
            if "moneyflow" in f:
                mf = f["moneyflow"].sort_values("trade_date")
                out["fund"]["近5日主力资金"] = [{"日期": str(r["trade_date"])[:10],
                                                 "主力净流入(万)": _r2(r.get("net_mf_amount")),
                                                 "大单买入(万)": _r2(r.get("buy_lg_amount")),
                                                 "大单卖出(万)": _r2(r.get("sell_lg_amount"))}
                                                for _, r in mf.iterrows()]
            if "top_list" in f:
                out["fund"]["龙虎榜"] = [{"日期": str(r["trade_date"])[:10],
                                          "净买额(万)": _r2(r.get("net_amount")),
                                          "净买占比%": _r2(r.get("net_rate")),
                                          "上榜原因": str(r.get("reason", ""))[:40]}
                                         for _, r in f["top_list"].tail(5).iterrows()]
    except Exception as e:
        log.warning("个股体检-基本面失败(已跳过): %s", e)

    # ---------- 消息面 ----------
    if use_news:
        _p("news", "检索个股资讯…")
        out["news"] = search_stock_news(out["name"] or ts_code, ts_code[:6])

    # ---------- 大模型综合 ----------
    if use_llm:
        _p("llm", "大模型综合研判中…")
        prompt = (f"【个股】{out['name']}({ts_code})　{out['today'][:4]}-{out['today'][4:6]}-{out['today'][6:]}\n"
                  f"【数据】\n{_clean(out)}")
        out["analysis"] = chat_json([
            {"role": "system", "content": CHECKUP_SYSTEM},
            {"role": "user", "content": prompt},
        ], api_key=llm_key, base_url=llm_base, model=llm_model)
    return out
