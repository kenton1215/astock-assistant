# -*- coding: utf-8 -*-
"""核心流水线：历史数据 → 实时快照 → 情绪/板块 → 初筛打分 → 资讯 → 大模型分析 → 报告
run_analysis(): 返回结构化结果dict（CLI与网页版共用），支持进度回调
run_session():   CLI单次运行入口包装"""
import time

import numpy as np
import pandas as pd

from config import SESSIONS
from .utils.logger import setup_logger
from .data.market_data import MarketDataService
from .data.screener import prefilter, screen
from .data.realtime import get_indices
from .news.tavily_client import search_daily_news
from .llm.deepseek_client import chat_json
from .prompts.prompts import build_system_prompt, build_user_prompt
from .portfolio.holdings import load_holdings
from .report.reporter import render_report

log = setup_logger("pipeline")

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def run_analysis(session, force=False, use_llm=True, use_news=True, progress=None,
                 llm_key=None, llm_base=None, llm_model=None,
                 use_fundamentals=True, holdings=None):
    """执行一次完整分析。
    progress(stage, detail) 可选进度回调
    llm_key/base/model：网页版用户级Key覆盖；holdings：网页版用户持仓（默认读holdings.json）
    返回: 结构化结果dict；非交易日返回 None"""
    today = time.strftime("%Y%m%d")
    weekday = WEEKDAYS[time.localtime().tm_wday]
    t0 = time.time()

    def _p(stage, detail=""):
        if progress:
            progress(stage, detail)

    svc = MarketDataService()
    if not force and not svc.is_trading_day():
        log.info("%s 非交易日，跳过本次运行", today)
        return None

    log.info("开始会话: %s(%s)", session, SESSIONS[session]["name"])

    # 1. 历史日线（增量缓存）
    _p("history", "① 更新历史日线缓存…")
    svc.update_history()
    svc.load_history()

    # 2. 当日实时快照（东财→腾讯→Tushare回退）
    _p("spot", "② 获取当日实时行情…")
    spot, source = svc.load_spot()

    # 3. 指数与板块
    _p("market", "③ 统计市场情绪与板块…")
    indices = get_indices()
    ind, con, rank_map, pct_map = svc.get_sector_data(spot)

    # 4. 初筛（硬性条件，无需OHLC）
    pf = prefilter(spot)
    holdings = holdings if holdings is not None else load_holdings()
    hcodes = [h["ts_code"] for h in holdings]
    codes = [c for c in pf["ts_code"].tolist() if c not in hcodes]

    # 5. 补齐候选股盘中OHLC（腾讯源无OHLC；东财源缺失值同样补齐）→ 指标与情绪
    #    范围：初筛候选 + 持仓 + 当日涨幅≥5%的股票（保证炸板/一字板统计覆盖涨停与准涨停股）
    _p("enrich", "④ 计算候选股短线指标…")
    ohlc_codes = list(dict.fromkeys(codes + hcodes + spot.loc[spot["pct_chg"] >= 5, "ts_code"].tolist()))
    svc.fill_intraday_ohlc(ohlc_codes)
    svc.prepare_pivots(spot)
    breadth = svc.market_breadth(spot)
    log.info("情绪: 涨停%d 跌停%d 炸板率%s%% 最高%s板", breadth["limit_up"],
             breadth["limit_down"], breadth["break_rate"], breadth["max_board"])

    # 6. 个股指标 + 精选打分
    enriched = svc.enrich(list(dict.fromkeys(codes + hcodes)))
    cand = screen(enriched[enriched["ts_code"].isin(codes)], rank_map, pct_map)
    log.info("规则初筛后候选 %d 只，送入大模型复核", len(cand))
    hold_rows = enriched[enriched["ts_code"].isin(hcodes)] if hcodes else pd.DataFrame()
    holdings_detail = build_holdings_detail(holdings, hold_rows)

    # 7. 资讯
    _p("news", "⑤ 检索今日政策/热点资讯…")
    news = search_daily_news(today) if use_news else []

    # 8. Tushare财务/资金/龙虎榜增强（积分不足自动降级，不影响主流程）
    fund_note = ""
    if use_fundamentals:
        _p("fund", "⑥ Tushare财务/资金/龙虎榜增强（积分不足自动降级）…")
        try:
            from .data.fundamentals import FundamentalsClient
            fund = FundamentalsClient(svc.tushare)
            bundle = fund.fetch_daily_bundle(
                today, svc.hist_dates,
                ts_codes=list(cand.head(35)["ts_code"]) + hcodes)
            if bundle.get("available"):
                cand = fund.attach_candidates(cand, bundle)
                holdings_detail = fund.attach_holdings(holdings_detail, bundle)
                fund_note = "、".join(bundle["available"])
                log.info("基本面增强可用: %s", fund_note)
                # 北向资金日度总量并入市场情绪
                if "hsgt" in bundle and not bundle["hsgt"].empty:
                    r0 = bundle["hsgt"].iloc[-1]
                    breadth["hsgt"] = {
                        "date": bundle.get("hsgt_date", ""),
                        "hgt": r0.get("hgt"), "sgt": r0.get("sgt"),
                        "ggt_ss": r0.get("ggt_ss"), "ggt_sz": r0.get("ggt_sz"),
                    }
            else:
                log.info("基本面增强接口均不可用（积分不足或未发布），跳过")
        except Exception as e:
            log.warning("基本面增强失败(已跳过): %s", e)

    # 9. 快照 → 提示词 → 大模型
    snapshot = build_snapshot(session, today, weekday, source, breadth,
                              indices, ind, con, cand, holdings_detail, news)
    analysis = None
    if use_llm:
        _p("llm", "⑦ 大模型按作手体系+检查清单分析中（约1分钟）…")
        analysis = chat_json([
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(session, snapshot)},
        ], api_key=llm_key, base_url=llm_base, model=llm_model)
    if analysis is None:
        log.warning("大模型不可用或调用失败，降级为规则模式")
        analysis = rule_based_analysis(session, cand, holdings_detail, breadth)
        analysis["_rule_mode"] = True

    # 10. 报告
    _p("report", "⑧ 生成报告文件…")
    report_path = render_report(session, today, weekday, source, breadth, indices,
                                ind, con, cand, holdings_detail, analysis, news,
                                fund_note=fund_note)
    log.info("会话完成，耗时 %.1fs", time.time() - t0)
    return dict(
        session=session, today=today, weekday=weekday, source=source,
        breadth=breadth, indices=indices, ind=ind, con=con, cand=cand,
        holdings_detail=holdings_detail, news=news, analysis=analysis,
        report_path=report_path, elapsed=round(time.time() - t0, 1),
        rule_mode=bool(analysis.get("_rule_mode")),
        fund_note=fund_note,
    )


def run_session(session, force=False, use_llm=True, use_news=True):
    """CLI入口：执行分析并打印摘要，返回报告文件路径"""
    result = run_analysis(session, force=force, use_llm=use_llm, use_news=use_news)
    if result is None:
        return None
    print_summary(session, result["analysis"], result["report_path"])
    return result["report_path"]


def build_holdings_detail(holdings, hold_rows):
    """持仓 + 行情指标合并"""
    out = []
    row_map = {r["ts_code"]: r for _, r in hold_rows.iterrows()}
    for h in holdings:
        r = row_map.get(h["ts_code"])
        if r is None:
            out.append({**h, "status": "数据不足(可能停牌/退市/新股)",
                        "price": None, "pct_chg": None, "pnl_pct": None})
            continue
        pnl = (r["price"] / h["cost"] - 1) * 100 if h["cost"] else None
        out.append({
            "ts_code": h["ts_code"], "name": r["name"] or h["name"],
            "cost": h["cost"], "volume": h["volume"], "buy_date": h["buy_date"],
            "price": r["price"], "pct_chg": r["pct_chg"],
            "pnl_pct": round(pnl, 2) if pnl is not None else None,
            "ma5": r["ma5"], "ma10": r["ma10"], "ma20": r["ma20"],
            "dist20": r["dist20"], "lianban": r["lianban"], "is_limit": r["is_limit"],
            "industry": r["industry"], "main_inflow": r["main_inflow"], "note": h["note"],
        })
    return out


def _clean(obj):
    """递归清洗：NaN/Inf→None，numpy类型→python类型"""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if np.isnan(obj) or np.isinf(obj) else round(float(obj), 2)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _r2(x):
    return round(float(x), 2) if x is not None and not pd.isna(x) else None


def build_snapshot(session, today, weekday, source, breadth, indices,
                   ind, con, cand, holdings_detail, news):
    cand_records = []
    for _, r in cand.iterrows():
        sector = str(r["industry"] or "—")
        if r["sector_rank"] < 99:
            sector += f"(+{_r2(r['sector_pct'])}%,行业第{int(r['sector_rank'])}名)"
        rec = {
            "代码": r["ts_code"], "名称": r["name"], "板块": sector,
            "现价": _r2(r["price"]), "涨幅%": _r2(r["pct_chg"]),
            "换手%": _r2(r["turnover"]), "量比": _r2(r["vol_ratio"]),
            "成交额亿": _r2(r["amount"] / 1e8) if r["amount"] else None,
            "主力净流入万": _r2(r["main_inflow"] / 1e4) if r["main_inflow"] and not pd.isna(r["main_inflow"]) else None,
            "流通市值亿": _r2(r["circ_mv"] / 1e8) if r["circ_mv"] and not pd.isna(r["circ_mv"]) else None,
            "MA5": _r2(r["ma5"]), "MA10": _r2(r["ma10"]), "MA20": _r2(r["ma20"]),
            "距20日新高%": _r2(r["dist20"]), "连板": int(r["lianban"]),
            "近20日涨停次数": int(r["lim20"]), "模式": r["pattern"],
            "涨停中": bool(r["is_limit"]), "炸板": bool(r["broken"]),
        }
        # Tushare基本面增强字段（可用时附带）
        for col, label in (("pe_ttm", "PE"), ("pb", "PB"), ("roe", "ROE%"),
                           ("netprofit_yoy", "净利同比%"), ("tr_yoy", "营收同比%"),
                           ("forecast_type", "业绩预告"), ("forecast_pct", "预告净利上限%"),
                           ("mf_5d", "近5日主力净流入万"), ("tl_net", "龙虎榜净买万"),
                           ("fd_amount", "封单额万"), ("open_times", "炸板次数"),
                           ("winner_rate", "获利盘%")):
            v = r.get(col)
            if v is not None and not pd.isna(v):
                rec[label] = _r2(v)
        cand_records.append(rec)
    snapshot = {
        "会话": SESSIONS[session]["name"],
        "日期": f"{today[:4]}-{today[4:6]}-{today[6:]}",
        "星期": weekday,
        "数据源": source,
        "市场情绪": breadth,
        "指数": [{"名称": r["name"], "点位": _r2(r["price"]), "涨跌幅%": _r2(r["pct_chg"])}
                 for _, r in indices.iterrows()] if not indices.empty else [],
        "行业板块Top15": [{"板块": r["board_name"], "涨幅%": _r2(r["pct_chg"]),
                           "主力净流入亿": _r2(r["main_inflow"] / 1e8) if r["main_inflow"] and not pd.isna(r["main_inflow"]) else None,
                           "上涨家数": int(r["up_count"]) if pd.notna(r["up_count"]) else None,
                           "下跌家数": int(r["down_count"]) if pd.notna(r["down_count"]) else None,
                           "板块内涨停家数": int(r["limit_up_count"]),
                           "领涨股": f"{r['leader_name']}({_r2(r['leader_pct'])}%)" if pd.notna(r["leader_name"]) else "—"}
                          for _, r in ind.head(15).iterrows()] if not ind.empty else [],
        "概念板块Top10": [{"概念": r["board_name"], "涨幅%": _r2(r["pct_chg"]),
                           "主力净流入亿": _r2(r["main_inflow"] / 1e8) if r["main_inflow"] and not pd.isna(r["main_inflow"]) else None,
                           "领涨股": f"{r['leader_name']}({_r2(r['leader_pct'])}%)" if pd.notna(r["leader_name"]) else "—"}
                          for _, r in con.head(10).iterrows()] if not con.empty else [],
        "候选股池": cand_records,
        "我的持仓": holdings_detail,
        "今日资讯": [{"主题": n["tag"], "标题": n["title"], "摘要": n["snippet"]} for n in news],
    }
    return _clean(snapshot)


def _judge_cycle(b):
    lu, ld, br, mb = b["limit_up"], b["limit_down"], b["break_rate"], b["max_board"]
    if lu >= 110 or (lu >= 80 and br < 22):
        return "发酵/高潮"
    if lu >= 50:
        return "启动/发酵"
    if br >= 40 or ld >= 20:
        return "退潮"
    if lu < 40 and mb <= 2:
        return "冰点"
    return "启动"


_CYCLE_POS = {"冰点": "≤30%", "退潮": "≤20%或空仓", "启动": "50-60%", "启动/发酵": "60-70%", "发酵/高潮": "70-80%(注意兑现)"}


def rule_based_analysis(session, cand, holdings_detail, breadth):
    """大模型不可用时的规则模式兜底（保证每天仍有可用报告）"""
    cycle = _judge_cycle(breadth)
    buys = []
    for _, r in cand.head(6).iterrows():
        price = _r2(r["price"])
        if price is None:
            continue
        pattern = r["pattern"]
        if pattern == "连板/涨停":
            lo, hi = round(price * 0.97, 2), price
            stop = round(min(_r2(r["low"]) or price * 0.95, price * 0.95), 2)
            note = "仅分歧低吸/回封介入，不追高；一字板不参与；破今日低点止损"
        elif pattern == "突破":
            lo, hi = round(price * 0.99, 2), round(price * 1.03, 2)
            stop = round(min(_r2(r["ma5"]) or price * 0.95, price * 0.95), 2)
            note = f"放量突破20日新高，回踩不破MA5({_r2(r['ma5'])})可介入"
        else:
            lo, hi = round(price * 0.99, 2), round(price * 1.02, 2)
            stop = round(min(_r2(r["ma10"]) or price * 0.95, price * 0.95), 2)
            note = f"趋势回踩企稳，MA10({_r2(r['ma10'])})附近低吸"
        buys.append({
            "ts_code": r["ts_code"], "name": r["name"], "pattern": pattern,
            "buy_price_low": lo, "buy_price_high": hi, "buy_point_note": note,
            "stop_loss": stop, "target": round(price * 1.10, 2), "position_pct": 20,
            "reason": (f"规则模式：现价{price} 涨幅{_r2(r['pct_chg'])}% 量比{_r2(r['vol_ratio'])} "
                       f"换手{_r2(r['turnover'])}% 连板{int(r['lianban'])} 近20日涨停{int(r['lim20'])}次 "
                       f"板块{r['industry'] or '—'}(第{int(r['sector_rank'])}名)"),
            "risk": "规则模式未做大模型复核，请人工确认后操作",
        })
    holds = []
    for h in holdings_detail:
        price = h.get("price")
        pnl = h.get("pnl_pct")
        flags = h.get("risk_flags") or []
        if price is None:
            action, reason, padv = "继续持有", "数据不足，无法分析", "—"
        elif pnl is not None and pnl <= -5:
            action, reason, padv = "卖出", f"亏损{pnl}%触及-5%止损纪律", f"参考卖出价{price}"
        elif flags and any(f.startswith(("业绩预告", "最新财报")) for f in flags):
            action, reason, padv = "减仓", f"基本面风险信号：{'、'.join(flags)}", f"反抽MA10({_r2(h.get('ma10'))})附近减仓"
        elif h.get("ma5") and h.get("ma10") and price < h["ma10"] and h["ma5"] < h["ma10"]:
            action, reason, padv = "减仓", "跌破MA10且短期均线走弱", f"反抽MA10({_r2(h['ma10'])})附近减仓"
        elif h.get("is_limit"):
            action, reason, padv = "继续持有", "涨停中，持有让利润奔跑", f"跌破MA5({_r2(h.get('ma5'))})离场"
        else:
            action, reason, padv = "继续持有", "未触发卖点，按纪律持有", f"跌破MA10({_r2(h.get('ma10') or price * 0.95)})离场"
        if flags:
            reason += f"；风险：{'、'.join(flags)}"
        holds.append({
            "ts_code": h["ts_code"], "name": h["name"], "action": action,
            "price_advice": padv, "stop_loss": _r2(h.get("ma10") or (price or 0) * 0.95),
            "reason": reason,
        })
    return {
        "market_review": {
            "情绪周期": cycle,
            "周期依据": f"涨停{breadth['limit_up']}家/跌停{breadth['limit_down']}家/炸板率{breadth['break_rate']}%/最高{int(breadth['max_board'])}板",
            "大盘判断": f"上涨{breadth['up']}家/下跌{breadth['down']}家，成交额{breadth['total_amount_yi']}亿",
            "总仓位建议": _CYCLE_POS[cycle],
            "今日主线": [],
        },
        "buy_candidates": buys,
        "holdings_advice": holds,
        "checklist_conclusions": [
            {"项": "A1", "结论": f"涨跌家数{breadth['up']}/{breadth['down']}"},
            {"项": "A4", "结论": f"涨停{breadth['limit_up']} 跌停{breadth['limit_down']} 炸板率{breadth['break_rate']}%"},
            {"项": "A5", "结论": f"最高{int(breadth['max_board'])}板，梯队{breadth.get('ladder') or '无'}"},
            {"项": "A6", "结论": f"昨日涨停今日均值{breadth.get('prev_limit_avg_pct')}%，晋级率{breadth.get('prev_limit_up_ratio')}%"},
            {"项": "E23", "结论": f"情绪周期:{cycle}，建议总仓位{_CYCLE_POS[cycle]}"},
            {"项": "E25", "结论": "规则模式：请按纪律控制单笔风险≤总资金2%"},
        ],
        "risk_notes": [
            "本次为规则模式输出（大模型未调用或失败），仅供初步参考",
            "所有操作请自行二次确认，严格执行-5%止损纪律",
            "短线交易需结合次日竞价与分时验证，触发条件不成立不介入",
        ],
        "tomorrow_plan": "" if session != "15:00" else "（规则模式）明日盘前请重点核对：①今日候选的竞价表现；②主线板块是否延续；③持仓是否触发止损条件。",
    }


def print_summary(session, analysis, report_path):
    print()
    print("=" * 62)
    print(f"A股短线交易助手 · {SESSIONS[session]['name']} 报告")
    print("=" * 62)
    mr = analysis.get("market_review") or {}
    mode = "规则模式" if analysis.get("_rule_mode") else "大模型分析"
    print(f"分析引擎: {mode} | 情绪周期: {mr.get('情绪周期', '—')} | 总仓位建议: {mr.get('总仓位建议', '—')}")
    buys = analysis.get("buy_candidates") or []
    if buys:
        print(f"买入候选 ({len(buys)}只):")
        for b in buys:
            print(f"  · {b.get('name')}({b.get('ts_code')}) [{b.get('pattern')}] "
                  f"区间 {b.get('buy_price_low')}~{b.get('buy_price_high')} 止损 {b.get('stop_loss')}")
    else:
        print("买入候选: 无（宁缺毋滥，等待模式内机会）")
    for a in analysis.get("holdings_advice") or []:
        print(f"持仓建议: {a.get('name')}({a.get('ts_code')}) → {a.get('action')}")
    print(f"报告文件: {report_path}")
    print("=" * 62)
    print()
