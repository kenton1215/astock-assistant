# -*- coding: utf-8 -*-
"""Markdown 报告生成：情绪 → 板块 → 买入候选 → 持仓 → 检查清单 → 明日计划"""
import time

from config import REPORT_DIR, SESSIONS, DEEPSEEK_MODEL


def _md_table(headers, rows):
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join("—" if x is None or x == "" else str(x) for x in r) + " |")
    return "\n".join(lines)


def _f(x, nd=2):
    try:
        if x is None or (isinstance(x, float) and x != x):
            return "—"
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)


def render_report(session, today, weekday, source, breadth, indices, ind, con,
                  cand, holdings_detail, analysis, news, fund_note=""):
    rule_mode = bool(analysis.get("_rule_mode"))
    name = SESSIONS[session]["name"]
    L = []
    L.append(f"# 📈 A股短线交易助手 · {name}")
    L.append("")
    head = (f"> 生成时间：{today[:4]}-{today[4:6]}-{today[6:]} {weekday} {time.strftime('%H:%M:%S')}"
            f"　|　数据源：{source}　|　分析引擎：{'规则模式(大模型未调用)' if rule_mode else DEEPSEEK_MODEL}")
    if fund_note:
        head += f"　|　基本面增强：{fund_note}"
    L.append(head)
    L.append(">")
    L.append("> ⚠️ **风险提示：本报告由量化模型自动生成，仅供研究参考，不构成任何投资建议。股市有风险，入市需谨慎。**")
    L.append("")

    # 一、大盘与市场情绪
    b = breadth
    L.append("## 一、大盘与市场情绪")
    L.append("")
    L.append(_md_table(
        ["上涨", "下跌", "平盘", "两市成交额(亿)", "涨停", "跌停", "炸板", "炸板率", "最高连板", "昨涨停今均值", "昨涨停晋级率"],
        [[b.get("up"), b.get("down"), b.get("flat"), b.get("total_amount_yi"),
          b.get("limit_up"), b.get("limit_down"), b.get("broken"),
          f"{b.get('break_rate')}%", f"{b.get('max_board')}板",
          _f(b.get("prev_limit_avg_pct")), f"{_f(b.get('prev_limit_up_ratio'))}%"]]))
    ladder = b.get("ladder") or {}
    if ladder:
        L.append(f"- 连板梯队：{'、'.join(f'{k}板×{v}' for k, v in sorted(ladder.items()))}")
    hsgt = b.get("hsgt")
    if hsgt:
        parts = []
        if hsgt.get("hgt") is not None:
            parts.append(f"沪股通{_f(hsgt['hgt'])}亿")
        if hsgt.get("sgt") is not None:
            parts.append(f"深股通{_f(hsgt['sgt'])}亿")
        if hsgt.get("ggt_ss") is not None:
            parts.append(f"港股通(沪){_f(hsgt['ggt_ss'])}亿")
        if parts:
            L.append(f"- 北向资金({hsgt.get('date', '')}，Tushare)：{' / '.join(parts)}")
    if indices is not None and not indices.empty:
        L.append("")
        L.append(_md_table(["指数", "点位", "涨跌幅%"],
                           [[r["name"], _f(r["price"]), _f(r["pct_chg"])] for _, r in indices.iterrows()]))
    mr = analysis.get("market_review") or {}
    if mr:
        L.append("")
        L.append(f"**情绪周期：{mr.get('情绪周期', '—')}**（{mr.get('周期依据', '—')}）")
        L.append(f"- 大盘判断：{mr.get('大盘判断', '—')}")
        L.append(f"- 总仓位建议：{mr.get('总仓位建议', '—')}")
        if mr.get("今日主线"):
            L.append(f"- 今日主线：{'、'.join(mr['今日主线'])}")
    L.append("")

    # 二、板块主线
    L.append("## 二、板块主线")
    L.append("")
    if ind is not None and not ind.empty:
        rows = []
        for _, r in ind.head(15).iterrows():
            rows.append([r["board_name"], _f(r["pct_chg"]),
                         _f(r["main_inflow"] / 1e8) if pd_notna(r["main_inflow"]) else "—",
                         int(r["up_count"]) if pd_notna(r["up_count"]) else "—",
                         int(r["down_count"]) if pd_notna(r["down_count"]) else "—",
                         int(r["limit_up_count"]),
                         f"{r['leader_name']}({_f(r['leader_pct'])}%)" if pd_notna(r["leader_name"]) else "—"])
        L.append("### 行业板块 Top15")
        L.append("")
        L.append(_md_table(["板块", "涨幅%", "主力净流入(亿)", "上涨", "下跌", "板块内涨停", "领涨股"], rows))
        L.append("")
    if con is not None and not con.empty:
        rows = []
        for _, r in con.head(10).iterrows():
            rows.append([r["board_name"], _f(r["pct_chg"]),
                         _f(r["main_inflow"] / 1e8) if pd_notna(r["main_inflow"]) else "—",
                         f"{r['leader_name']}({_f(r['leader_pct'])}%)" if pd_notna(r["leader_name"]) else "—"])
        L.append("### 概念板块 Top10")
        L.append("")
        L.append(_md_table(["概念", "涨幅%", "主力净流入(亿)", "领涨股"], rows))
        L.append("")

    # 三、买入候选
    L.append("## 三、买入候选（最多6只）")
    L.append("")
    buys = analysis.get("buy_candidates") or []
    if not buys:
        L.append("- 今日无符合条件的买入候选（宁缺毋滥，等待模式内机会）。")
        L.append("")
    for i, bc in enumerate(buys, 1):
        L.append(f"### {i}. {bc.get('name', '')}（{bc.get('ts_code', '')}）　模式：{bc.get('pattern', '')}")
        L.append("")
        L.append(_md_table(["建议买入区间", "买入方式", "止损位", "目标位", "建议仓位"],
                           [[f"{_f(bc.get('buy_price_low'))} ~ {_f(bc.get('buy_price_high'))}",
                             bc.get("buy_point_note", "—"), _f(bc.get("stop_loss")),
                             _f(bc.get("target")), f"{bc.get('position_pct', 20)}%"]]))
        L.append(f"- **买入理由：**{bc.get('reason', '—')}")
        L.append(f"- **风险提示：**{bc.get('risk', '—')}")
        L.append("")

    # 四、持仓分析
    L.append("## 四、持仓分析")
    L.append("")
    if not holdings_detail:
        L.append("- 未配置持仓（请在 `holdings.json` 中填入持仓，格式参考 `holdings.example.json`）。")
        L.append("")
    else:
        advice_map = {a.get("ts_code"): a for a in (analysis.get("holdings_advice") or [])}
        rows = []
        for h in holdings_detail:
            a = advice_map.get(h["ts_code"]) or {}
            rows.append([f"{h.get('name', '')}({h['ts_code']})", _f(h.get("cost")), _f(h.get("price")),
                         f"{_f(h.get('pnl_pct'))}%", f"**{a.get('action', '—')}**",
                         a.get("price_advice", "—")])
        L.append(_md_table(["持仓", "成本", "现价", "盈亏%", "操作建议", "价格参考"], rows))
        for h in holdings_detail:
            a = advice_map.get(h["ts_code"]) or {}
            if a.get("reason"):
                L.append(f"- {h.get('name', '')}：{a.get('reason')}")
        # 基本面风险体检（Tushare增强数据可用时）
        flag_rows = [h for h in holdings_detail if h.get("risk_flags")]
        if flag_rows:
            L.append("")
            L.append("**🏥 基本面风险体检：**")
            for h in flag_rows:
                L.append(f"- {h.get('name', '')}：⚠️ {'、'.join(h['risk_flags'])}")
        L.append("")

    # 五、检查清单结论
    cc = analysis.get("checklist_conclusions") or []
    if cc:
        L.append("## 五、检查清单核对结论")
        L.append("")
        L.append(_md_table(["检查项", "结论"],
                           [[c.get("项", ""), c.get("结论", "")] for c in cc]))
        L.append("")

    # 六、明日计划
    tp = analysis.get("tomorrow_plan") or ""
    if session == "15:00" or tp:
        L.append("## 六、明日盘前计划")
        L.append("")
        L.append(tp or "- 无")
        L.append("")

    # 七、风险提示
    rn = analysis.get("risk_notes") or []
    L.append("## 七、今日风险提示")
    L.append("")
    if rn:
        for n in rn:
            L.append(f"- {n}")
    else:
        L.append("- 无特别提示")
    L.append("")

    # 附：资讯摘要
    if news:
        L.append("## 附：今日资讯摘要")
        L.append("")
        for n in news:
            L.append(f"- [{n.get('tag')}] {n.get('title')}：{n.get('snippet')}")
        L.append("")

    # 附：规则初筛池
    if cand is not None and not cand.empty:
        L.append("## 附：规则初筛候选池（大模型复核前的原始Top30）")
        L.append("")
        fund_cols = [("pe_ttm", "PE"), ("roe", "ROE%"), ("netprofit_yoy", "净利同比%"),
                     ("forecast_type", "业绩预告"), ("mf_5d", "近5日主力净流入(万)"),
                     ("tl_net", "龙虎榜净买(万)"), ("winner_rate", "获利盘%")]
        fund_cols = [(c, l) for c, l in fund_cols if c in cand.columns]
        headers = ["代码", "名称", "行业", "现价", "涨幅%", "换手%", "量比", "成交额(亿)",
                   "主力净流入(万)", "连板", "模式", "评分"] + [l for _, l in fund_cols]
        rows = []
        for _, r in cand.head(30).iterrows():
            row = [r["ts_code"], r["name"], r["industry"] or "—", _f(r["price"]),
                   _f(r["pct_chg"]), _f(r["turnover"]), _f(r["vol_ratio"]),
                   _f(r["amount"] / 1e8) if r["amount"] else "—",
                   _f(r["main_inflow"] / 1e4) if r["main_inflow"] and pd_notna(r["main_inflow"]) else "—",
                   int(r["lianban"]), r["pattern"], _f(r.get("score", ""), 1)]
            for c, _ in fund_cols:
                v = r.get(c)
                row.append(_f(v) if v is not None and pd_notna(v) else "—")
            rows.append(row)
        L.append(_md_table(headers, rows))
        L.append("")

    L.append("---")
    L.append("")
    L.append("> 本助手严格按短线作手体系运行：只做强势股、只做模式内、买点条件化、卖点纪律化、仓位可控化、复盘持续化。**以上内容不构成投资建议。**")

    text = "\n".join(L)
    out_dir = REPORT_DIR / f"{today[:4]}-{today[4:6]}-{today[6:]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{session.replace(':', '')}_{name}.md"
    path = out_dir / fname
    path.write_text(text, encoding="utf-8-sig")
    return str(path)


def pd_notna(x):
    """pandas notna 的便捷封装（避免 None 场景报错）"""
    try:
        return x is not None and x == x
    except Exception:
        return False
