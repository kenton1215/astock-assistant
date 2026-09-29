# -*- coding: utf-8 -*-
"""Excel 报告生成：多Sheet（总览/情绪/板块/买入候选/持仓/检查清单/初筛池/明日计划）
返回 xlsx 字节流，供下载按钮使用"""
import io

import pandas as pd

from config import SESSIONS, DEEPSEEK_MODEL


def _f(x, nd=2):
    try:
        if x is None or (isinstance(x, float) and x != x):
            return None
        return round(float(x), nd)
    except Exception:
        return x


def build_excel(result) -> bytes:
    analysis = result["analysis"] or {}
    mr = analysis.get("market_review") or {}
    b = result["breadth"]
    today = result["today"]
    session = result["session"]
    date_s = f"{today[:4]}-{today[4:6]}-{today[6:]}"

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        # ---------- Sheet1 报告总览 ----------
        ladder = "、".join(f"{k}板×{v}" for k, v in sorted((b.get("ladder") or {}).items())) or "无"
        overview = pd.DataFrame([
            ["报告类型", f"{SESSIONS[session]['name']}（{session}）"],
            ["交易日期", f"{date_s} {result['weekday']}"],
            ["数据源", result["source"]],
            ["分析引擎", "规则模式(大模型未调用)" if result["rule_mode"] else DEEPSEEK_MODEL],
            ["基本面增强", result.get("fund_note") or "未启用"],
            ["分析用时(秒)", result["elapsed"]],
            ["", ""],
            ["情绪周期", mr.get("情绪周期", "")],
            ["周期依据", mr.get("周期依据", "")],
            ["大盘判断", mr.get("大盘判断", "")],
            ["总仓位建议", mr.get("总仓位建议", "")],
            ["今日主线", "、".join(mr.get("今日主线") or [])],
            ["连板梯队", ladder],
            ["", ""],
            ["风险提示", ""],
        ], columns=["项目", "内容"])
        for i, n in enumerate(analysis.get("risk_notes") or []):
            overview.loc[len(overview)] = [f"风险{i + 1}", n]
        overview.to_excel(w, sheet_name="报告总览", index=False)

        # ---------- Sheet2 市场情绪 ----------
        breadth_df = pd.DataFrame([{
            "上涨家数": b.get("up"), "下跌家数": b.get("down"), "平盘": b.get("flat"),
            "两市成交额(亿)": b.get("total_amount_yi"),
            "涨停家数": b.get("limit_up"), "跌停家数": b.get("limit_down"),
            "炸板家数": b.get("broken"), "炸板率%": b.get("break_rate"),
            "最高连板": b.get("max_board"),
            "昨日涨停今日均值%": b.get("prev_limit_avg_pct"),
            "昨日涨停晋级率%": b.get("prev_limit_up_ratio"),
        }])
        breadth_df.to_excel(w, sheet_name="市场情绪", index=False)
        hsgt = b.get("hsgt")
        if hsgt:
            pd.DataFrame([{
                "日期": hsgt.get("date", ""), "沪股通净买(亿)": hsgt.get("hgt"),
                "深股通净买(亿)": hsgt.get("sgt"),
                "港股通沪(亿)": hsgt.get("ggt_ss"), "港股通深(亿)": hsgt.get("ggt_sz"),
            }]).to_excel(w, sheet_name="市场情绪", startrow=3, index=False)
        if not result["indices"].empty:
            idx_df = result["indices"][["name", "price", "pct_chg"]].rename(
                columns={"name": "指数", "price": "点位", "pct_chg": "涨跌幅%"})
            idx_df.to_excel(w, sheet_name="市场情绪", startrow=4, index=False)

        # ---------- Sheet3 板块主线 ----------
        ind = result["ind"]
        if not ind.empty:
            ind_df = ind.head(15)[["board_name", "pct_chg", "main_inflow",
                                   "up_count", "down_count", "limit_up_count",
                                   "leader_name", "leader_pct"]].copy()
            ind_df["main_inflow"] = ind_df["main_inflow"].apply(lambda x: _f(x / 1e8))
            ind_df = ind_df.rename(columns={
                "board_name": "行业板块", "pct_chg": "涨幅%", "main_inflow": "主力净流入(亿)",
                "up_count": "上涨家数", "down_count": "下跌家数",
                "limit_up_count": "板块内涨停", "leader_name": "领涨股", "leader_pct": "领涨股涨幅%"})
            ind_df.to_excel(w, sheet_name="板块主线", index=False)
        con = result["con"]
        if not con.empty:
            con_df = con.head(10)[["board_name", "pct_chg", "main_inflow", "leader_name", "leader_pct"]].copy()
            con_df["main_inflow"] = con_df["main_inflow"].apply(lambda x: _f(x / 1e8))
            con_df = con_df.rename(columns={
                "board_name": "概念板块", "pct_chg": "涨幅%", "main_inflow": "主力净流入(亿)",
                "leader_name": "领涨股", "leader_pct": "领涨股涨幅%"})
            con_df.to_excel(w, sheet_name="概念板块Top10", index=False)

        # ---------- Sheet4 买入候选 ----------
        buys = analysis.get("buy_candidates") or []
        if buys:
            buy_df = pd.DataFrame([{
                "代码": x.get("ts_code"), "名称": x.get("name"), "模式": x.get("pattern"),
                "买入区间下限": _f(x.get("buy_price_low")), "买入区间上限": _f(x.get("buy_price_high")),
                "介入方式": x.get("buy_point_note"), "止损价": _f(x.get("stop_loss")),
                "目标价": _f(x.get("target")), "建议仓位%": x.get("position_pct"),
                "买入理由": x.get("reason"), "风险提示": x.get("risk"),
            } for x in buys])
            buy_df.to_excel(w, sheet_name="买入候选", index=False)
        else:
            pd.DataFrame([{"说明": "今日无符合条件的买入候选（宁缺毋滥，等待模式内机会）"}]).to_excel(
                w, sheet_name="买入候选", index=False)

        # ---------- Sheet5 持仓分析 ----------
        holds = result["holdings_detail"]
        advice_map = {a.get("ts_code"): a for a in (analysis.get("holdings_advice") or [])}
        if holds:
            hold_df = pd.DataFrame([{
                "代码": h.get("ts_code"), "名称": h.get("name"),
                "成本": _f(h.get("cost")), "现价": _f(h.get("price")),
                "当日涨幅%": _f(h.get("pct_chg")), "盈亏%": _f(h.get("pnl_pct")),
                "MA5": _f(h.get("ma5")), "MA10": _f(h.get("ma10")),
                "连板": h.get("lianban"), "行业": h.get("industry"),
                "操作建议": (advice_map.get(h.get("ts_code")) or {}).get("action", "—"),
                "价格参考": (advice_map.get(h.get("ts_code")) or {}).get("price_advice", "—"),
                "理由": (advice_map.get(h.get("ts_code")) or {}).get("reason", "—"),
                "基本面风险体检": "；".join(h.get("risk_flags") or []) or "无",
                "备注": h.get("note"),
            } for h in holds])
            hold_df.to_excel(w, sheet_name="持仓分析", index=False)
        else:
            pd.DataFrame([{"说明": "未配置持仓（在网页侧边栏或 holdings.json 中填写）"}]).to_excel(
                w, sheet_name="持仓分析", index=False)

        # ---------- Sheet6 检查清单 ----------
        cc = analysis.get("checklist_conclusions") or []
        if cc:
            pd.DataFrame([{"检查项": c.get("项", ""), "结论": c.get("结论", "")} for c in cc]).to_excel(
                w, sheet_name="检查清单", index=False)

        # ---------- Sheet7 初筛候选池 ----------
        cand = result["cand"]
        if cand is not None and not cand.empty:
            base_cols = ["ts_code", "name", "industry", "price", "pct_chg", "turnover",
                         "vol_ratio", "amount", "main_inflow", "lianban", "pattern", "score"]
            fund_cols = [c for c in ("pe_ttm", "pb", "roe", "netprofit_yoy", "tr_yoy",
                                     "forecast_type", "mf_5d", "tl_net", "fd_amount",
                                     "open_times", "winner_rate") if c in cand.columns]
            pool = cand.head(30)[base_cols + fund_cols].copy()
            pool["amount"] = pool["amount"].apply(lambda x: _f(x / 1e8))
            pool["main_inflow"] = pool["main_inflow"].apply(lambda x: _f(x / 1e4))
            for c in ("mf_5d", "tl_net", "fd_amount"):
                if c in pool.columns:
                    pool[c] = pool[c].apply(lambda x: _f(x))
            rename = {"ts_code": "代码", "name": "名称", "industry": "行业", "price": "现价",
                      "pct_chg": "涨幅%", "turnover": "换手%", "vol_ratio": "量比",
                      "amount": "成交额(亿)", "main_inflow": "主力净流入(万)",
                      "lianban": "连板", "pattern": "模式", "score": "评分",
                      "pe_ttm": "PE", "pb": "PB", "roe": "ROE%", "netprofit_yoy": "净利同比%",
                      "tr_yoy": "营收同比%", "forecast_type": "业绩预告",
                      "mf_5d": "近5日主力净流入(万)", "tl_net": "龙虎榜净买(万)",
                      "fd_amount": "封单额(万)", "open_times": "炸板次数",
                      "winner_rate": "获利盘%"}
            pool = pool.rename(columns=rename)
            pool.to_excel(w, sheet_name="初筛候选池", index=False)

        # ---------- Sheet8 明日计划 ----------
        tp = analysis.get("tomorrow_plan") or ""
        if session == "15:00" or tp:
            pd.DataFrame([["明日盘前计划", tp or "- 无"]]).to_excel(w, sheet_name="明日计划",
                                                                    index=False, header=False)

    # ---------- 样式：表头加粗 + 自适应列宽 ----------
    try:
        from openpyxl.styles import Font
        for ws in w.book.worksheets:
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for col in ws.columns:
                width = 8
                for c in list(col)[:120]:
                    v = c.value
                    if v is None:
                        continue
                    try:
                        s = str(v)
                        width = max(width, min(len(s.encode("gbk", errors="ignore")) + 2, 60))
                    except Exception:
                        pass
                ws.column_dimensions[col[0].column_letter].width = width
    except Exception:
        pass

    buf.seek(0)
    return buf.getvalue()
