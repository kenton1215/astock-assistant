# -*- coding: utf-8 -*-
"""A股短线交易助手 · 网页版（Streamlit，支持多用户与Streamlit Cloud部署）
运行: streamlit run app.py  （或双击 start_web.bat）
功能:
  - 账户系统：注册(送免费次数)/登录；配置自己的DeepSeek Key免费使用，或兑换激活码
  - 时段分析：6个时段一键分析 → 指标卡+页签报告 → 下载Excel/Markdown
  - 个股体检：输入代码 → 技术/资金/财务/消息面深度体检
  - 持仓管理：网页表格编辑，登录用户独立保存"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

from config import SESSIONS, SCREEN, RISK, HOLDINGS_FILE, DEEPSEEK_MODEL, AUTH
from src.pipeline import run_analysis
from src.portfolio.holdings import norm_code, load_holdings
from src.report.excel import build_excel
from src.auth import get_store, consume_analysis_quota, _enc

st.set_page_config(page_title="A股短线交易助手", page_icon="📈", layout="wide")

DISCLAIMER = "⚠️ 本工具输出为量化模型研究结果，不构成投资建议。股市有风险，入市需谨慎。"

ACTION_EMOJI = {"继续持有": "✅", "卖出": "🚫", "减仓": "⚠️", "加仓": "➕"}
ADVICE_EMOJI = {"短线关注": "👀", "等待买点": "⏳", "回避": "🚫"}

# ---------- 会话状态初始化 ----------
if "user" not in st.session_state:
    st.session_state["user"] = None


def _current_user():
    if not st.session_state.get("user"):
        return None
    return get_store().get_user(st.session_state["user"])


def _holdings_of_user():
    u = _current_user()
    return u.get("holdings") or [] if u else []


def _load_holdings_df():
    data = _holdings_of_user() if st.session_state.get("user") else load_holdings()
    # 防御：数据必须是列表（Supabase文本列返回JSON字符串时在此兜底）
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = []
    if not isinstance(data, list) or not data:
        return pd.DataFrame(columns=["代码", "名称", "成本", "数量", "买入日期", "备注"])
    df = pd.DataFrame(data)
    df = df.rename(columns={"ts_code": "代码", "name": "名称", "cost": "成本",
                            "volume": "数量", "buy_date": "买入日期", "note": "备注"})
    return df[["代码", "名称", "成本", "数量", "买入日期", "备注"]]


def _save_holdings(rows):
    if st.session_state.get("user"):
        get_store().update_user(st.session_state["user"], holdings=rows)
    else:
        HOLDINGS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _df_to_holdings(df):
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("代码") or "").strip()
        if not code:
            continue
        try:
            rows.append({
                "ts_code": norm_code(code),
                "name": str(r.get("名称") or ""),
                "cost": float(r.get("成本") or 0),
                "volume": float(r.get("数量") or 0),
                "buy_date": str(r.get("买入日期") or ""),
                "note": str(r.get("备注") or ""),
            })
        except (TypeError, ValueError):
            continue
    return rows


# ================= 侧边栏 =================
with st.sidebar:
    st.title("📈 短线交易助手")
    st.caption("短线作手体系 · 量化智能体")

    # ---------- 账户区 ----------
    user = st.session_state["user"]
    if user:
        u = _current_user() or {}
        st.markdown(f"👤 **{user}**　|　剩余次数：**{u.get('credits', 0)}**　|　今日已用：{u.get('daily_count', 0)}")
        with st.expander("🔑 我的API Key（配置后免费使用，不扣次数）"):
            with st.form("apikey_form"):
                k = st.text_input("DeepSeek API Key", type="password",
                                  placeholder="sk-...", key="ak_k")
                b = st.text_input("Base URL（可留空）", placeholder="https://api.deepseek.com/v1", key="ak_b")
                m = st.text_input("模型名（可留空）", placeholder="deepseek-flash", key="ak_m")
                if st.form_submit_button("💾 保存Key（加密存储）"):
                    get_store().update_user(user, llm_key=_enc(k.strip()),
                                            llm_base=b.strip(), llm_model=m.strip())
                    st.toast("✅ 已保存，下次分析生效")
        with st.expander("🎟️ 兑换激活码"):
            with st.form("code_form"):
                code = st.text_input("激活码", placeholder="AK-XXXXXXXX", key="cd")
                if st.form_submit_button("兑换"):
                    ok, msg = get_store().redeem_code(user, code)
                    if ok:
                        st.toast(f"✅ {msg}")
                    else:
                        st.error(msg)
        if st.button("🚪 退出登录", use_container_width=True):
            st.session_state["user"] = None
            st.rerun()
    else:
        at1, at2 = st.tabs(["🔐 登录", "📝 注册"])
        with at1:
            with st.form("login_form"):
                lu = st.text_input("用户名", key="lu")
                lp = st.text_input("密码", type="password", key="lp")
                if st.form_submit_button("登录", use_container_width=True):
                    uu, msg = get_store().login(lu, lp)
                    if uu:
                        st.session_state["user"] = uu["username"]
                        st.rerun()
                    else:
                        st.error(msg)
        with at2:
            with st.form("reg_form"):
                ru = st.text_input("用户名", key="ru")
                rp = st.text_input("密码（至少4位）", type="password", key="rp")
                rp2 = st.text_input("确认密码", type="password", key="rp2")
                if st.form_submit_button(f"注册（送{AUTH['free_trial_credits']}次免费体验）", use_container_width=True):
                    if rp != rp2:
                        st.error("两次密码不一致")
                    else:
                        uu, msg = get_store().register(ru, rp)
                        if uu:
                            st.session_state["user"] = uu["username"]
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
        st.caption("💡 未登录＝本地模式（用本机.env的Key）。注册送免费次数；之后可配置自己的DeepSeek Key免费使用，或购买激活码。")

    st.divider()
    st.subheader("1️⃣ 分析时段")
    session = st.selectbox("时段", list(SESSIONS.keys()),
                           format_func=lambda k: f"{k} · {SESSIONS[k]['name']}")

    st.subheader("2️⃣ 运行选项")
    c1, c2, c3, c4 = st.columns(4)
    use_llm = c1.checkbox("大模型", value=True)
    use_news = c2.checkbox("资讯", value=True)
    use_fund = c3.checkbox("财务增强", value=True, help="Tushare估值/财务/龙虎榜（积分不足自动降级）")
    force = c4.checkbox("强制", value=False, help="非交易日也运行")

    # 登录用户：选择扣费模式
    llm_key = llm_base = llm_model = None
    if user:
        u = _current_user() or {}
        has_key = bool(u.get("llm_key"))
        mode_label = ("用我的Key（免费）" if has_key else "用平台Key（扣1次）")
        key_mode = st.radio("模型Key来源",
                            ["用平台Key（扣1次）", "用我的Key（免费）"] if has_key else ["用平台Key（扣1次）"],
                            horizontal=True)
        use_own = key_mode == "用我的Key（免费）"
    else:
        use_own = False

    if st.button("🚀 开始分析", type="primary", use_container_width=True):
        st.session_state["run_error"] = ""
        if user:
            ok, msg, mode = consume_analysis_quota(user, use_own)
            if not ok:
                st.session_state["run_error"] = msg
            else:
                if mode[0] == "own":
                    llm_key, llm_base, llm_model = mode[1], mode[2], mode[3]
                st.session_state["run_llm"] = (llm_key, llm_base, llm_model)
                st.session_state["running"] = True
                st.session_state["result"] = None
        else:
            st.session_state["running"] = True
            st.session_state["result"] = None

    st.divider()
    st.subheader("3️⃣ 持仓管理")
    hold_label = f"（用户 {user} 独立保存）" if user else "（本地模式，保存到 holdings.json）"
    st.caption("在表格中直接编辑，点保存生效（代码可写 600519 或 600519.SH）" + hold_label)
    edited = st.data_editor(
        _load_holdings_df(), num_rows="dynamic", key="holdings_editor", use_container_width=True,
        column_config={
            "代码": st.column_config.TextColumn("代码", width="small"),
            "名称": st.column_config.TextColumn("名称", width="small"),
            "成本": st.column_config.NumberColumn("成本", format="%.2f", width="small"),
            "数量": st.column_config.NumberColumn("数量(股)", format="%d", width="small"),
            "买入日期": st.column_config.TextColumn("买入日期", width="small"),
            "备注": st.column_config.TextColumn("备注", width="medium"),
        },
    )
    if st.button("💾 保存持仓", use_container_width=True):
        rows = _df_to_holdings(edited)
        _save_holdings(rows)
        st.toast(f"✅ 已保存 {len(rows)} 条持仓，下次分析生效")

    st.divider()
    with st.expander("⚙️ 选股参数（config.py 可调）"):
        params = [
            ["流通市值", f"{SCREEN['min_circ_mv'] / 1e8:.0f}亿 ~ {SCREEN['max_circ_mv'] / 1e8:.0f}亿"],
            ["成交额下限", f"{SCREEN['min_amount'] / 1e8:.0f}亿"],
            ["换手率", f"{SCREEN['min_turnover']}% ~ {SCREEN['max_turnover']}%（涨停股放宽到{SCREEN['limit_turnover']}%）"],
            ["量比下限", f"{SCREEN['min_vol_ratio']}（突破模式{SCREEN['breakout_vol_ratio']}）"],
            ["当日涨幅", f"{SCREEN['min_pct']}% 起"],
            ["上市时长", f"≥{SCREEN['min_list_bars']}个交易日"],
            ["常规止损", f"-{RISK['stop_loss_pct']}%"],
            ["单票仓位上限", f"{RISK['single_position']}%"],
            ["每日最多推荐", f"{SCREEN['max_buy']}只"],
        ]
        st.table(pd.DataFrame(params, columns=["参数", "阈值"]))
    st.caption(DISCLAIMER)


# ================= 主界面 =================
st.title("📈 A股短线交易助手")
st.caption("严格执行短线作手体系：只做强势股、只做模式内、买点条件化、卖点纪律化、仓位可控化。")


def _render_result(res):
    analysis = res["analysis"] or {}
    mr = analysis.get("market_review") or {}
    b = res["breadth"]
    sname = SESSIONS[res["session"]]["name"]
    rule_mode = res["rule_mode"]
    engine = "规则模式" if rule_mode else DEEPSEEK_MODEL

    st.markdown(
        f"**{sname}** · {res['today'][:4]}-{res['today'][4:6]}-{res['today'][6:]} {res['weekday']}"
        f"　|　数据源：{res['source']}　|　分析引擎：{engine}　|　耗时 {res['elapsed']}s"
        + (f"　|　基本面增强：{res.get('fund_note', '')}" if res.get("fund_note") else ""))

    # ---- 指标卡 ----
    m = st.columns(8)
    m[0].metric("情绪周期", mr.get("情绪周期", "—"))
    m[1].metric("涨停", b.get("limit_up"), delta_color="off")
    m[2].metric("跌停", b.get("limit_down"), delta_color="off")
    m[3].metric("炸板率", f"{b.get('break_rate')}%", delta_color="off")
    m[4].metric("最高板", f"{b.get('max_board')}板", delta_color="off")
    m[5].metric("上涨/下跌", f"{b.get('up')}/{b.get('down')}", delta_color="off")
    m[6].metric("成交额(亿)", f"{b.get('total_amount_yi'):,.0f}", delta_color="off")
    m[7].metric("总仓位建议", mr.get("总仓位建议", "—"), delta_color="off")
    st.info(f"**大盘判断：**{mr.get('大盘判断', '—')}　｜　**周期依据：**{mr.get('周期依据', '—')}")
    if mr.get("今日主线"):
        st.markdown("**🎯 今日主线：** " + "、".join(f"`{x}`" for x in mr["今日主线"]))

    # ---- 下载 ----
    excel_bytes = build_excel(res)
    try:
        md_text = Path(res["report_path"]).read_text(encoding="utf-8-sig")
    except Exception:
        md_text = ""
    stamp = f"{res['today']}_{res['session'].replace(':', '')}"
    d1, d2, d3 = st.columns(3)
    d1.download_button("📥 下载 Excel 报告", data=excel_bytes,
                       file_name=f"短线交易报告_{stamp}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
    d2.download_button("📄 下载 Markdown 原文", data=md_text,
                       file_name=f"短线交易报告_{stamp}.md", use_container_width=True)
    d3.caption(f"💾 报告文件：`{res['report_path']}`")

    st.divider()
    tabs = st.tabs(["🎯 买入候选", "💼 持仓分析", "🏭 板块主线", "📋 报告概览", "✅ 检查清单", "📰 今日资讯", "🏥 个股体检"])

    # 候选基本面对照表（规则初筛数据，供展示）
    cand_map = {r["ts_code"]: r for _, r in res["cand"].iterrows()} if res["cand"] is not None else {}

    # ---- Tab 买入候选 ----
    with tabs[0]:
        buys = analysis.get("buy_candidates") or []
        if not buys:
            st.info("今日无符合条件的买入候选——宁缺毋滥，等待模式内机会。")
        for i, bc in enumerate(buys, 1):
            with st.container(border=True):
                hd = st.columns([3, 2, 1.5, 1.5, 1.2])
                hd[0].markdown(f"### {i}. {bc.get('name', '')} `{bc.get('ts_code', '')}`")
                hd[1].markdown(f"**模式：**{bc.get('pattern', '')}")
                hd[2].metric("买入区间", f"{bc.get('buy_price_low')}~{bc.get('buy_price_high')}")
                hd[3].metric("止损", bc.get("stop_loss"))
                hd[4].metric("目标", bc.get("target"))
                st.markdown(f"**建议仓位：**{bc.get('position_pct', '—')}%　｜　"
                            f"**介入方式：**{bc.get('buy_point_note', '—')}")
                st.markdown(f"**买入理由：**{bc.get('reason', '—')}")
                # 基本面速览（Tushare增强数据）
                cw = cand_map.get(bc.get("ts_code"))
                if cw is not None:
                    parts = []
                    if pd.notna(cw.get("pe_ttm")):
                        parts.append(f"PE {cw['pe_ttm']:.0f}")
                    if pd.notna(cw.get("roe")):
                        parts.append(f"ROE {cw['roe']:.1f}%")
                    if pd.notna(cw.get("netprofit_yoy")):
                        parts.append(f"净利同比 {cw['netprofit_yoy']:.0f}%")
                    ft = cw.get("forecast_type")
                    if isinstance(ft, str) and ft:
                        parts.append(f"预告:{ft}")
                    if pd.notna(cw.get("mf_5d")):
                        parts.append(f"近5日主力 {cw['mf_5d']/1e4:.2f}亿")
                    if pd.notna(cw.get("tl_net")):
                        parts.append(f"龙虎榜净买 {cw['tl_net']/1e4:.2f}亿")
                    if parts:
                        st.markdown("**📊 基本面/资金面：**" + "　｜　".join(parts))
                st.caption(f"⚠️ 风险：{bc.get('risk', '—')}")

    # ---- Tab 持仓分析 ----
    with tabs[1]:
        holds = res["holdings_detail"]
        if not holds:
            st.info("未配置持仓：请在左侧「持仓管理」表格中填写并保存。")
        else:
            advice_map = {a.get("ts_code"): a for a in (analysis.get("holdings_advice") or [])}
            rows = []
            for h in holds:
                a = advice_map.get(h["ts_code"]) or {}
                action = a.get("action", "—")
                rows.append({
                    "代码": h.get("ts_code"), "名称": h.get("name"),
                    "成本": h.get("cost"), "现价": h.get("price"),
                    "盈亏%": h.get("pnl_pct"),
                    "操作建议": f"{ACTION_EMOJI.get(action, '')} {action}",
                    "价格参考": a.get("price_advice", "—"),
                    "风险体检": "⚠️" + "；".join(h.get("risk_flags") or []) if h.get("risk_flags") else "正常",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                         column_config={
                             "盈亏%": st.column_config.NumberColumn("盈亏%", format="%.2f%%"),
                             "成本": st.column_config.NumberColumn("成本", format="%.2f"),
                             "现价": st.column_config.NumberColumn("现价", format="%.2f"),
                         })
            for h in holds:
                a = advice_map.get(h["ts_code"]) or {}
                if a.get("reason"):
                    st.markdown(f"- **{h.get('name', '')}**：{a.get('reason')}")
                if h.get("risk_flags"):
                    st.caption(f"　🏥 风险体检：{'、'.join(h['risk_flags'])}")

    # ---- Tab 板块主线 ----
    with tabs[2]:
        ind = res["ind"]
        con = res["con"]
        left, right = st.columns(2)
        with left:
            st.subheader("行业板块 Top15")
            if ind is not None and not ind.empty:
                show = ind.head(15)[["board_name", "pct_chg", "main_inflow", "limit_up_count", "leader_name"]].copy()
                show["main_inflow"] = (show["main_inflow"] / 1e8).round(2)
                show = show.rename(columns={"board_name": "板块", "pct_chg": "涨幅%",
                                            "main_inflow": "主力净流入(亿)",
                                            "limit_up_count": "板块内涨停", "leader_name": "领涨股"})
                st.dataframe(show, use_container_width=True, hide_index=True)
            else:
                st.info("板块数据不可用")
        with right:
            st.subheader("概念板块 Top10")
            if con is not None and not con.empty:
                show = con.head(10)[["board_name", "pct_chg", "main_inflow", "leader_name"]].copy()
                show["main_inflow"] = (show["main_inflow"] / 1e8).round(2)
                show = show.rename(columns={"board_name": "概念", "pct_chg": "涨幅%",
                                            "main_inflow": "主力净流入(亿)", "leader_name": "领涨股"})
                st.dataframe(show, use_container_width=True, hide_index=True)
            else:
                st.info("概念板块数据不可用（腾讯备源模式下按行业聚合）")

    # ---- Tab 报告概览 ----
    with tabs[3]:
        idx = res["indices"]
        if idx is not None and not idx.empty:
            cols = st.columns(len(idx))
            for c, (_, r) in zip(cols, idx.iterrows()):
                c.metric(r["name"], f"{r['price']:,.2f}", f"{r['pct_chg']:+.2f}%")
        ladder = b.get("ladder") or {}
        if ladder:
            st.markdown("**连板梯队：**" + "、".join(f"{k}板×{v}" for k, v in sorted(ladder.items())))
        rn = analysis.get("risk_notes") or []
        if rn:
            st.subheader("⚠️ 今日风险提示")
            for n in rn:
                st.markdown(f"- {n}")
        tp = analysis.get("tomorrow_plan") or ""
        if res["session"] == "15:00" or tp:
            st.subheader("📅 明日盘前计划")
            st.markdown(tp or "- 无")

    # ---- Tab 检查清单 ----
    with tabs[4]:
        cc = analysis.get("checklist_conclusions") or []
        if cc:
            st.dataframe(pd.DataFrame(cc), use_container_width=True, hide_index=True)
        cand = res["cand"]
        if cand is not None and not cand.empty:
            with st.expander("🔍 规则初筛候选池（大模型复核前 Top30，含基本面增强列）"):
                show = cand.head(30).copy()
                keep = [c for c in ("ts_code", "name", "industry", "price", "pct_chg", "turnover",
                                    "vol_ratio", "lianban", "pattern", "score",
                                    "pe_ttm", "roe", "netprofit_yoy", "forecast_type",
                                    "mf_5d", "tl_net") if c in show.columns]
                show = show[keep].rename(columns={
                    "ts_code": "代码", "name": "名称", "industry": "行业", "price": "现价",
                    "pct_chg": "涨幅%", "turnover": "换手%", "vol_ratio": "量比",
                    "lianban": "连板", "pattern": "模式", "score": "评分",
                    "pe_ttm": "PE", "roe": "ROE%", "netprofit_yoy": "净利同比%",
                    "forecast_type": "业绩预告", "mf_5d": "近5日主力净流入(万)",
                    "tl_net": "龙虎榜净买(万)"})
                st.dataframe(show, use_container_width=True, hide_index=True)

    # ---- Tab 资讯 ----
    with tabs[5]:
        news = res["news"]
        if not news:
            st.info("未检索资讯（可勾选侧边栏「资讯」选项）")
        for n in news:
            title = n.get("title", "")
            url = n.get("url", "")
            snippet = n.get("snippet", "")
            if url:
                st.markdown(f"- **[{n.get('tag')}] [{title}]({url})**：{snippet}")
            else:
                st.markdown(f"- **[{n.get('tag')}] {title}**：{snippet}")

    # ---- Tab 个股体检 ----
    with tabs[6]:
        st.subheader("🏥 个股深度体检")
        st.caption("输入代码，综合技术面/资金面/基本面/消息面给出短线操作建议（需已登录且消耗1次，或本地模式直接使用）")
        ccol1, ccol2 = st.columns([2, 1])
        chk_code = ccol1.text_input("股票代码", placeholder="如 600519 / 688116", key="chk_code")
        if ccol2.button("开始体检", type="primary", use_container_width=True) or st.session_state.get("chk_pending"):
            if not str(chk_code or "").strip():
                st.warning("请先输入股票代码")
            else:
                chk_llm_key = chk_llm_base = chk_llm_model = None
                use_chk_llm = use_llm
                if user:
                    ok, msg, mode = consume_analysis_quota(user, use_own)
                    if not ok:
                        st.error(msg)
                        use_chk_llm = False
                    elif mode[0] == "own":
                        chk_llm_key, chk_llm_base, chk_llm_model = mode[1], mode[2], mode[3]
                with st.spinner(f"体检中（约30-60秒）…"):
                    from src.analysis.stock_checkup import run_checkup
                    try:
                        chk = run_checkup(str(chk_code).strip(),
                                          llm_key=chk_llm_key, llm_base=chk_llm_base,
                                          llm_model=chk_llm_model, use_llm=use_chk_llm)
                        st.session_state["chk_result"] = chk
                        st.session_state["chk_pending"] = False
                    except Exception as e:
                        st.error(f"体检失败：{e}")
        chk = st.session_state.get("chk_result")
        if chk:
            if chk.get("error"):
                st.error(chk["error"])
            else:
                tech = chk.get("tech") or {}
                a = chk.get("analysis") or {}
                m1 = st.columns(6)
                m1[0].metric("现价", tech.get("price"))
                m1[1].metric("当日涨幅", f"{tech.get('pct_chg')}%")
                m1[2].metric("换手%", tech.get("turnover"))
                m1[3].metric("量比", tech.get("vol_ratio"))
                m1[4].metric("MA5", tech.get("ma5"))
                m1[5].metric("距20日新高", f"{tech.get('dist20')}%")
                if a:
                    adv = a.get("操作建议", "—")
                    st.markdown(f"### 操作建议：{ADVICE_EMOJI.get(adv, '')} **{adv}**")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("支撑位", a.get("支撑位"))
                    c2.metric("压力位", a.get("压力位"))
                    c3.metric("止损参考", a.get("止损参考"))
                    st.markdown(f"**建议买点：**{a.get('建议买点', '—')}")
                    for title, key in (("📈 技术面", "技术面评价"), ("💰 资金面", "资金面评价"),
                                       ("📊 基本面", "基本面评价"), ("📰 消息面", "消息面评价")):
                        if a.get(key):
                            st.markdown(f"**{title}：**{a[key]}")
                    st.markdown(f"**🎯 综合结论：**{a.get('综合结论', '—')}")
                    rp = a.get("风险点") or []
                    if rp:
                        st.markdown("**⚠️ 风险点：**")
                        for x in rp:
                            st.markdown(f"- {x}")
                fund = chk.get("fund") or {}
                if fund.get("业绩预告"):
                    st.markdown("**业绩预告：**" + "，".join(f"{k}: {v}" for k, v in fund["业绩预告"].items()))
                if fund.get("财务指标"):
                    st.dataframe(pd.DataFrame(fund["财务指标"]), use_container_width=True, hide_index=True)
                if fund.get("近5日主力资金"):
                    st.markdown("**近5日主力资金：**")
                    st.dataframe(pd.DataFrame(fund["近5日主力资金"]), use_container_width=True, hide_index=True)
                if fund.get("龙虎榜"):
                    st.markdown("**近期龙虎榜：**")
                    st.dataframe(pd.DataFrame(fund["龙虎榜"]), use_container_width=True, hide_index=True)
                if chk.get("news"):
                    st.markdown("**📰 个股资讯：**")
                    for n in chk["news"][:6]:
                        if n.get("url"):
                            st.markdown(f"- [{n['title']}]({n['url']})：{n.get('snippet', '')}")
                        else:
                            st.markdown(f"- {n['title']}：{n.get('snippet', '')}")

    st.divider()
    st.caption(DISCLAIMER)


# ================= 执行逻辑 =================
if st.session_state.get("run_error"):
    st.error("❌ " + st.session_state["run_error"])
    st.session_state["run_error"] = ""

if st.session_state.get("running"):
    with st.status("分析进行中…", expanded=True) as status:
        def cb(stage, detail):
            status.write(detail)
        run_failed = False
        try:
            _k = st.session_state.get("run_llm", (None, None, None))
            _holdings = _holdings_of_user() if st.session_state.get("user") else None
            res = run_analysis(session, force=force, use_llm=use_llm, use_news=use_news,
                               progress=cb, llm_key=_k[0], llm_base=_k[1], llm_model=_k[2],
                               use_fundamentals=use_fund, holdings=_holdings)
        except Exception as e:
            st.error(f"❌ 运行失败：{e}（详见 logs/astock.log）")
            res = None
            run_failed = True
        if res is None:
            status.update(label="❌ 运行失败（见上方错误）" if run_failed else "今日非交易日或数据不可用",
                          state="error", expanded=True)
        else:
            status.update(label="分析完成 ✅", state="complete", expanded=False)
        st.session_state["result"] = res
        st.session_state["running"] = False

res = st.session_state.get("result")
if res is not None:
    _render_result(res)
else:
    if st.session_state.get("running") is None:
        st.info("👈 在左侧选择时段并点击「🚀 开始分析」，即可生成今日报告。")
        st.markdown("**使用步骤：**\n"
                    "1. 注册/登录（送免费次数；或配置自己的DeepSeek Key免费使用；也可不登录用本地模式）\n"
                    "2. 左侧选择分析时段（交易日 10:00 / 10:30 / 11:30 / 14:00 / 14:30 / 15:00 各有侧重）\n"
                    "3. 在「持仓管理」中填写你的持仓代码并保存（自动分析卖出/持有建议）\n"
                    "4. 点击「🚀 开始分析」，约 1~2 分钟生成完整报告（含财务/资金/龙虎榜增强）\n"
                    "5. 顶部下载 Excel 报告存档；「个股体检」页签可对任意个股做深度体检")
        st.caption(DISCLAIMER)
