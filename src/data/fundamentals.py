# -*- coding: utf-8 -*-
"""Tushare Pro 增强数据：估值/财务指标/业绩预告/资金流向/龙虎榜/涨跌停明细
设计原则：
  1. 优先使用全市场"单次调用"（按交易日/报告期），效率高、限流风险小；
  2. 积分不足（如 daily_basic 需2000积分）自动降级：本次会话不再重试失效接口，
     120积分核心功能（行情/筛选）不受任何影响；
  3. 数据仅用于候选与持仓的基本面/资金面验证，不改变初筛主逻辑。"""
import numpy as np
import pandas as pd

from .tushare_client import TushareClient
from ..utils.logger import setup_logger

log = setup_logger("fundamentals")


class FundamentalsClient:
    def __init__(self, tushare=None):
        self.ts = tushare or TushareClient()
        self._dead = set()  # 积分不足/失效接口，本次会话不再重试

    def _opt(self, name, **kw):
        if name in self._dead:
            return pd.DataFrame()
        df, err = self.ts.call_optional(name, **kw)
        if df is None or df.empty:
            if err:
                if ("积分" in err) or ("权限" in err) or ("2000" in err):
                    self._dead.add(name)
                    log.info("Tushare接口 %s 不可用(积分不足)，自动降级跳过", name)
                elif err:
                    log.warning("Tushare接口 %s 失败: %s", name, err[:120])
            return pd.DataFrame()
        return df

    @staticmethod
    def _candidate_periods(today):
        """按当前月份推断已披露完毕的最近报告期"""
        y, m = int(today[:4]), int(today[4:6])
        if m >= 11:
            order = [f"{y}0930", f"{y}0630", f"{y}0331"]
        elif m >= 9:
            order = [f"{y}0630", f"{y}0331", f"{y - 1}1231"]
        elif m >= 5:
            order = [f"{y}0331", f"{y - 1}1231", f"{y - 1}0930"]
        else:
            order = [f"{y - 1}0930", f"{y - 1}0630", f"{y - 1}0331"]
        return order

    # ---------- 当日增强数据包 ----------
    def fetch_daily_bundle(self, today, hist_dates, ts_codes=None):
        """返回 dict：各数据表(或None) + available 标记
        全市场单次调用的接口按交易日拉全；fina_indicator 必须带ts_code，仅对
        头部候选与持仓逐股获取（ts_codes，最多40只）"""
        b = {}
        ts_codes = list(ts_codes or [])[:40]
        # 1) 估值指标（最近可用交易日）
        for d in [today] + list(reversed(hist_dates[-3:])):
            df = self._opt("daily_basic", trade_date=d,
                           fields="ts_code,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
            if not df.empty:
                b["daily_basic"], b["daily_basic_date"] = df, d
                break
        # 2) 个股资金流向（近5个交易日）
        mfs = {}
        for d in list(reversed(hist_dates[-5:])):
            df = self._opt("moneyflow", trade_date=d,
                           fields="ts_code,buy_lg_amount,sell_lg_amount,net_mf_amount")
            if not df.empty:
                mfs[d] = df
        b["moneyflow"] = mfs
        # 3) 龙虎榜（最近可用交易日）
        for d in [today] + list(reversed(hist_dates[-5:])):
            df = self._opt("top_list", trade_date=d,
                           fields="ts_code,name,net_amount,net_rate,reason")
            if not df.empty:
                b["top_list"], b["top_list_date"] = df, d
                break
        # 4) 涨跌停明细（官方连板数/封单额/炸板次数）
        for d in [today] + list(reversed(hist_dates[-3:])):
            df = self._opt("limit_list_d", trade_date=d,
                           fields="ts_code,limit_times,fd_amount,open_times,up_stat")
            if not df.empty:
                b["limit_list"], b["limit_list_date"] = df, d
                break
        # 5) 最新财务指标（fina_indicator 必须带ts_code → 对头部候选与持仓逐股获取）
        fina_rows = []
        for ts in ts_codes:
            df = self._opt("fina_indicator", ts_code=ts, limit=2,
                           fields="ts_code,end_date,roe,netprofit_yoy,tr_yoy,"
                                  "grossprofit_margin,debt_to_assets,eps")
            if not df.empty:
                fina_rows.append(df.iloc[0])
        if fina_rows:
            b["fina"] = pd.DataFrame(fina_rows).reset_index(drop=True)
            b["fina_period"] = str(b["fina"].iloc[0].get("end_date", ""))[:10]
        # 6) 业绩预告（近30天公告，全市场）
        from datetime import datetime, timedelta
        start = (datetime.strptime(today, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
        df = self._opt("forecast", ann_date=f"{start}-{today}",
                       fields="ts_code,type,p_change_min,p_change_max,ann_date")
        if not df.empty:
            b["forecast"] = df
        # 7) 北向资金日度总量（沪深港通，按日期区间）
        if hist_dates:
            df = self._opt("moneyflow_hsgt", start_date=hist_dates[-1], end_date=today)
            if not df.empty:
                b["hsgt"], b["hsgt_date"] = df, str(df.iloc[-1].get("trade_date", ""))[:10]
        # 8) 筹码获利盘（头部候选+持仓逐股，衡量上方抛压）
        cyq_rows = []
        cyq_date = b.get("daily_basic_date") or (hist_dates[-1] if hist_dates else today)
        for ts in ts_codes[:20]:
            df = self._opt("cyq_perf", trade_date=cyq_date, ts_code=ts)
            if not df.empty:
                r0 = df.iloc[0]
                cyq_rows.append({"ts_code": ts, "winner_rate": r0.get("winner_rate")})
        if cyq_rows:
            b["cyq"] = pd.DataFrame(cyq_rows)
            b["cyq_date"] = cyq_date
        b["available"] = {k: True for k, v in b.items()
                          if k not in ("available",) and not k.endswith("_date")
                          and v is not None and (not isinstance(v, dict) or v)}
        return b

    # ---------- 附加到候选池 ----------
    def attach_candidates(self, cand, b):
        df = cand.copy()
        if "daily_basic" in b:
            db = b["daily_basic"].set_index("ts_code")
            for col in ("pe_ttm", "pb", "ps_ttm", "dv_ttm"):
                if col in db.columns:
                    df[col] = df["ts_code"].map(db[col])
        if "fina" in b:
            fi = b["fina"].set_index("ts_code")
            for col in ("roe", "netprofit_yoy", "tr_yoy", "grossprofit_margin", "debt_to_assets", "eps"):
                if col in fi.columns:
                    df[col] = df["ts_code"].map(fi[col])
        if "forecast" in b:
            fc = b["forecast"].sort_values("ann_date").drop_duplicates("ts_code", keep="last").set_index("ts_code")
            if "type" in fc.columns:
                df["forecast_type"] = df["ts_code"].map(fc["type"])
                df["forecast_pct"] = df["ts_code"].map(fc.get("p_change_max"))
        if b.get("moneyflow"):
            acc = pd.concat(list(b["moneyflow"].values()))
            acc = acc.groupby("ts_code")["net_mf_amount"].sum()
            df["mf_5d"] = df["ts_code"].map(acc)  # 万元
        if "top_list" in b:
            tl = b["top_list"].drop_duplicates("ts_code", keep="last").set_index("ts_code")
            df["tl_net"] = df["ts_code"].map(tl["net_amount"])
        if "limit_list" in b:
            ll = b["limit_list"].set_index("ts_code")
            for col in ("limit_times", "fd_amount", "open_times"):
                if col in ll.columns:
                    df[col] = df["ts_code"].map(ll[col])
        if "cyq" in b:
            cy = b["cyq"].set_index("ts_code")
            df["winner_rate"] = df["ts_code"].map(cy["winner_rate"])
        return df

    # ---------- 附加到持仓（含风险体检） ----------
    def attach_holdings(self, holds, b):
        if not holds:
            return holds
        df = self.attach_candidates(
            pd.DataFrame([{"ts_code": h["ts_code"]} for h in holds]), b)
        row = df.set_index("ts_code")
        for h in holds:
            ts = h["ts_code"]
            if ts in row.index:
                for col in ("pe_ttm", "pb", "roe", "netprofit_yoy", "tr_yoy",
                            "grossprofit_margin", "debt_to_assets", "eps",
                            "forecast_type", "forecast_pct", "mf_5d", "tl_net",
                            "limit_times", "fd_amount", "open_times", "winner_rate"):
                    if col in row.columns:
                        v = row.loc[ts, col]
                        h[col] = float(v) if pd.notna(v) else None
            h["risk_flags"] = self._risk_flags(h)
        return holds

    @staticmethod
    def _risk_flags(h):
        """基本面/资金面风险体检（规则化，供大模型与报告引用）"""
        flags = []
        ft = h.get("forecast_type")
        if ft in ("预减", "首亏", "续亏", "略减"):
            flags.append(f"业绩预告:{ft}")
        roe = h.get("roe")
        if roe is not None and roe < 0:
            flags.append("最新财报亏损(ROE<0)")
        yoy = h.get("netprofit_yoy")
        if yoy is not None and yoy < -30:
            flags.append(f"净利同比{yoy:.0f}%")
        debt = h.get("debt_to_assets")
        if debt is not None and debt > 75:
            flags.append(f"负债率{debt:.0f}%偏高")
        pe = h.get("pe_ttm")
        if pe is not None and (pe > 100 or pe < 0):
            flags.append(f"估值异常(PE={pe:.0f})")
        tl = h.get("tl_net")
        if tl is not None and tl < 0:
            flags.append("龙虎榜净卖出")
        mf = h.get("mf_5d")
        if mf is not None and mf < 0:
            flags.append("近5日主力净流出")
        wr = h.get("winner_rate")
        if wr is not None and wr > 95:
            flags.append(f"获利盘{wr:.0f}%(抛压重)")
        return flags

    # ---------- 单只股票深度数据（个股体检用） ----------
    def fetch_stock(self, ts_code, today, hist_dates):
        out = {"available": {}}
        for d in [today] + list(reversed(hist_dates[-3:])):
            df = self._opt("daily_basic", ts_code=ts_code, trade_date=d,
                           fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
            if not df.empty:
                out["daily_basic"], out["daily_basic_date"] = df, d
                break
        df = self._opt("fina_indicator", ts_code=ts_code, limit=8,
                       fields="ts_code,end_date,roe,netprofit_yoy,tr_yoy,grossprofit_margin,debt_to_assets,eps,or_yoy")
        if not df.empty:
            out["fina"] = df
        df = self._opt("forecast", ts_code=ts_code, limit=3,
                       fields="ts_code,type,p_change_min,p_change_max,ann_date,end_date")
        if not df.empty:
            out["forecast"] = df
        if len(hist_dates) >= 5:
            df = self._opt("moneyflow", ts_code=ts_code, start_date=hist_dates[-5], end_date=today,
                           fields="ts_code,trade_date,buy_lg_amount,sell_lg_amount,net_mf_amount")
            if not df.empty:
                out["moneyflow"] = df
        if len(hist_dates) >= 5:
            tl_rows = []
            for d in list(reversed(hist_dates[-5:])):
                df = self._opt("top_list", ts_code=ts_code, trade_date=d,
                               fields="ts_code,trade_date,net_amount,net_rate,reason")
                if not df.empty:
                    tl_rows.append(df)
            if tl_rows:
                out["top_list"] = pd.concat(tl_rows, ignore_index=True)
        out["available"] = {k: True for k, v in out.items()
                            if k != "available" and v is not None and (not isinstance(v, pd.DataFrame) or not v.empty)}
        return out
