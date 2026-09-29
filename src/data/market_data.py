# -*- coding: utf-8 -*-
"""行情数据服务：Tushare历史日线缓存 + 东财实时快照 + 指标合并 + 市场情绪统计"""
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA_CACHE_DIR, HISTORY_DAYS, SCREEN
from .tushare_client import TushareClient
from .realtime import get_spot_all as rt_get_spot_all
from .realtime import get_sector_board as rt_get_sector_board
from .tencent import get_quotes as tencent_get_quotes
from .indicators import (limit_ratio, limit_price, is_limit_up, to_ts_code,
                         ma_of, consec_limits, ratio_series)
from ..utils.logger import setup_logger

log = setup_logger("market")


class HistoryCache:
    """按交易日缓存全市场日线（pickle）"""

    def __init__(self, cache_dir=None):
        self.dir = Path(cache_dir) if cache_dir else DATA_CACHE_DIR / "daily"
        self.dir.mkdir(parents=True, exist_ok=True)

    def has(self, date):
        return (self.dir / f"{date}.pkl").exists()

    def save(self, date, df):
        with open(self.dir / f"{date}.pkl", "wb") as f:
            pickle.dump(df, f)

    def load(self, date):
        with open(self.dir / f"{date}.pkl", "rb") as f:
            return pickle.load(f)


class MarketDataService:
    """数据编排：日历 → 历史日线(增量缓存) → 当日快照 → 透视表 → 个股指标 → 市场情绪"""

    def __init__(self, tushare=None):
        self.tushare = tushare or TushareClient()
        self.cache = HistoryCache()
        self.today = time.strftime("%Y%m%d")
        self.trade_dates = None
        self.hist_dates = []
        self._hist = None
        self._spot = None
        self._piv = None
        self._basic = None
        self._names = {}

    # ---------- 交易日历 ----------
    def load_trade_cal(self):
        if self.trade_dates is not None:
            return self.trade_dates
        cache_file = DATA_CACHE_DIR / "trade_cal.pkl"
        dates = None
        if cache_file.exists():
            try:
                with open(cache_file, "rb") as f:
                    dates = pickle.load(f)
            except Exception:
                dates = None
        if not dates or self.today not in dates:
            start = f"{int(self.today[:4]) - 1}0101"
            cal = self.tushare.trade_cal(start, self.today)
            if not cal.empty:
                dates = cal["cal_date"].tolist()
                with open(cache_file, "wb") as f:
                    pickle.dump(dates, f)
        self.trade_dates = dates or []
        return self.trade_dates

    def is_trading_day(self, date=None):
        date = date or self.today
        dates = self.load_trade_cal()
        if not dates:
            return True  # 日历不可用时放行，后续数据层再兜底
        return str(date) in dates

    def last_trade_dates(self, n):
        dates = [d for d in self.load_trade_cal() if d <= self.today]
        return dates[-n:] if len(dates) > n else dates

    # ---------- 股票基础信息 ----------
    def stock_basic_df(self):
        if self._basic is None:
            try:
                self._basic = self.tushare.stock_basic()
            except Exception as e:
                log.warning("stock_basic 获取失败: %s", e)
                self._basic = pd.DataFrame()
        return self._basic

    def _name_of(self, ts_code):
        return self._names.get(ts_code, "")

    # ---------- 历史日线（增量更新） ----------
    def update_history(self, n_days=HISTORY_DAYS):
        dates = self.last_trade_dates(n_days)
        missing = [d for d in dates if not self.cache.has(d)]
        for i, d in enumerate(missing, 1):
            df = self.tushare.daily_by_date(d)
            if df.empty:
                log.warning("交易日 %s 日线暂不可用（可能尚未发布），跳过", d)
                continue
            self.cache.save(d, df)
            if i % 10 == 0 or i == len(missing):
                log.info("历史日线缓存进度 %d/%d", i, len(missing))
        self.hist_dates = [d for d in dates if self.cache.has(d)]
        return self.hist_dates

    def load_history(self):
        if not self.hist_dates:
            self.update_history()
        frames = [self.cache.load(d) for d in self.hist_dates if self.cache.has(d)]
        if not frames:
            raise RuntimeError("无历史日线数据：请检查网络与 Tushare Token（需≥120积分）")
        self._hist = pd.concat(frames, ignore_index=True)
        return self._hist

    # ---------- 当日快照 ----------
    def load_spot(self):
        """今日实时快照：东财→腾讯；两者都失败时回退最近交易日收盘数据(Tushare)"""
        spot, source = pd.DataFrame(), ""
        try:
            spot, source = rt_get_spot_all()
        except Exception as e:
            log.warning("实时行情获取异常: %s", e)
        if spot.empty:
            log.warning("实时行情获取失败，回退到最近交易日收盘数据(Tushare)")
            df = self.cache.load(self.hist_dates[-1]).copy()
            basic = self.stock_basic_df()
            if not basic.empty:
                df = df.merge(basic[["ts_code", "name", "industry", "list_date", "float_share"]],
                              on="ts_code", how="left")
            df["code"] = df["ts_code"].str[:6]
            df["price"] = df["close"]
            df["turnover"] = np.nan
            df["vol_ratio"] = np.nan
            df["pe_ttm"] = np.nan
            df["circ_mv"] = np.nan
            df["main_inflow"] = np.nan
            if "float_share" in df.columns:  # float_share 单位：万股
                df["circ_mv"] = df["float_share"] * 1e4 * df["price"]
                df["turnover"] = df["vol"] * 100 / (df["float_share"] * 1e4) * 100
            df["name"] = df["name"].fillna("")
            df["industry"] = df["industry"].fillna("")
            spot = df
            source = "最近交易日收盘数据(Tushare，回退模式)"
        else:
            spot["ts_code"] = spot["ts_code"].map(to_ts_code)
            spot["name"] = spot["name"].fillna("")
            spot["industry"] = spot["industry"].fillna("")
            if (spot["industry"] == "").any():  # 行业缺失时用Tushare行业补齐
                basic = self.stock_basic_df()
                if not basic.empty:
                    ind_map = dict(zip(basic["ts_code"], basic["industry"]))
                    spot["industry"] = spot["ts_code"].map(ind_map).fillna(spot["industry"])
            # 关键防御：腾讯排行按涨跌幅排序分页，盘中排名实时变动时同一股票可能跨页重复出现
            # （导致后续按 ts_code 取数返回 Series，触发"truth value of a Series is ambiguous"）
            n0 = len(spot)
            spot = spot.drop_duplicates(subset=["ts_code"], keep="first").reset_index(drop=True)
            if len(spot) < n0:
                log.warning("实时快照发现 %d 条重复记录（盘中排名变动所致），已去重", n0 - len(spot))
        self._spot = spot
        return spot, source

    # ---------- 候选股盘中OHLC补齐（腾讯源缺少OHLC字段） ----------
    @staticmethod
    def _scalar(v):
        """Series→标量（防御重复索引取数）"""
        if isinstance(v, (pd.Series, pd.DataFrame)):
            return v.iloc[0]
        return v

    def fill_intraday_ohlc(self, ts_codes):
        """用腾讯批量行情补齐候选股的 open/high/low/pre_close/量比（仅补缺失值）"""
        spot = self._spot
        if spot is None or spot.empty or not ts_codes:
            return
        spot_idx = spot.set_index("ts_code")
        need = [ts for ts in ts_codes
                if ts in spot_idx.index and pd.isna(self._scalar(spot_idx.loc[ts, "high"]))]
        if not need:
            return
        log.info("腾讯批量行情补齐 %d 只候选股盘中OHLC…", len(need))
        try:
            q = tencent_get_quotes(need)
        except Exception as e:
            log.warning("腾讯批量行情失败: %s", e)
            return
        if q.empty:
            return
        q["ts_code"] = q["qcode"].map(lambda c: c[2:] + (".SH" if c.startswith("sh") else ".SZ"))
        for ts, row in q.set_index("ts_code").iterrows():
            for col in ("open", "high", "low", "pre_close", "vol_ratio",
                        "turnover", "amount", "volume", "price", "pct_chg"):
                v = row.get(col)
                if v is not None and not pd.isna(v):
                    self._spot.loc[self._spot["ts_code"] == ts, col] = v

    # ---------- 透视表（历史+今日合成） ----------
    def prepare_pivots(self, spot):
        hist = self._hist
        piv = dict(
            close=hist.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last"),
            pct=hist.pivot_table(index="trade_date", columns="ts_code", values="pct_chg", aggfunc="last"),
            high=hist.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last"),
            vol=hist.pivot_table(index="trade_date", columns="ts_code", values="vol", aggfunc="last"),
        )
        today_idx = self.today
        for _, r in spot.iterrows():
            ts = r["ts_code"]
            piv["close"].loc[today_idx, ts] = r["price"]
            piv["pct"].loc[today_idx, ts] = r["pct_chg"]
            piv["high"].loc[today_idx, ts] = r["high"]
            vol = r.get("volume")
            if vol is not None and not pd.isna(vol):
                piv["vol"].loc[today_idx, ts] = vol
        for p in piv.values():
            p.sort_index(inplace=True)
        self._piv = piv
        # 名称映射（用于涨停阈值/昨日涨停表现）
        basic = self.stock_basic_df()
        if not basic.empty:
            self._names = dict(zip(basic["ts_code"], basic["name"]))
        self._names.update(dict(zip(spot["ts_code"], spot["name"])))
        return piv

    # ---------- 个股短线指标 ----------
    def enrich(self, ts_codes):
        """为指定股票计算短线指标（需先 load_spot + prepare_pivots）"""
        piv = self._piv
        spot_rows = {r["ts_code"]: r for _, r in self._spot.iterrows()}
        rows = []
        for ts in ts_codes:
            if ts not in piv["close"].columns:
                continue
            r = spot_rows.get(ts)
            if r is None:
                continue
            price = r.get("price")
            if price is None or pd.isna(price) or price <= 0:
                continue
            close_a = piv["close"][ts].to_numpy(dtype=float)
            pct_a = piv["pct"][ts].to_numpy(dtype=float)
            high_a = piv["high"][ts].to_numpy(dtype=float)
            vol_a = piv["vol"][ts].to_numpy(dtype=float)
            if np.count_nonzero(~np.isnan(close_a)) < SCREEN["min_list_bars"]:
                continue  # 上市不足60个交易日
            name = str(r.get("name") or self._name_of(ts))
            ratio = limit_ratio(ts, name)
            ma5, ma10, ma20, ma60 = (ma_of(close_a, n) for n in (5, 10, 20, 60))
            prior20 = np.nanmax(high_a[-21:-1]) if len(high_a) >= 21 else np.nan
            dist20 = (price / prior20 - 1) * 100 if prior20 == prior20 and prior20 > 0 else np.nan
            is_limit = is_limit_up(r.get("pct_chg") or 0, ratio)
            lianban = consec_limits(pct_a, ratio)
            lim20 = int(np.sum(pct_a[-20:] >= ratio - 0.2))
            vr = r.get("vol_ratio")
            if vr is None or pd.isna(vr):  # 量比缺失时用近5日均量估算
                v5 = vol_a[~np.isnan(vol_a)]
                if len(v5) >= 6 and np.mean(v5[-6:-1]) > 0:
                    vr = float(v5[-1] / np.mean(v5[-6:-1]))
            pre_close = r.get("pre_close")
            lp = limit_price(pre_close, ratio) if pre_close is not None and not pd.isna(pre_close) else np.nan
            high = r.get("high") if r.get("high") is not None and not pd.isna(r.get("high")) else np.nan
            opn, low = r.get("open"), r.get("low")
            broken = bool(lp == lp and high >= lp - 0.01 and not is_limit and (r.get("amount") or 0) > 0)
            one_word = bool(is_limit and opn and high and low
                            and abs(opn - high) < 0.005 and abs(high - low) < 0.005)
            rows.append(dict(
                ts_code=ts, code=r.get("code", ts[:6]), name=name,
                industry=str(r.get("industry") or ""),
                price=price, pct_chg=r.get("pct_chg"), open=opn, high=high, low=low,
                pre_close=pre_close, amount=r.get("amount"), turnover=r.get("turnover"),
                vol_ratio=vr, circ_mv=r.get("circ_mv"), main_inflow=r.get("main_inflow"),
                pe_ttm=r.get("pe_ttm"), ma5=ma5, ma10=ma10, ma20=ma20, ma60=ma60,
                dist20=dist20, is_limit=is_limit, lianban=lianban, lim20=lim20,
                broken=broken, one_word=one_word,
            ))
        return pd.DataFrame(rows)

    # ---------- 市场情绪 ----------
    def market_breadth(self, spot):
        ratio = ratio_series(spot["code"], spot["name"])
        pct = spot["pct_chg"].to_numpy(dtype=float)
        high = spot["high"].to_numpy(dtype=float)
        pre = spot["pre_close"].to_numpy(dtype=float)
        amount = spot["amount"].to_numpy(dtype=float)
        is_lu = pct >= ratio - 0.2
        is_ld = pct <= -ratio + 0.2
        lp = np.floor(pre * (1 + ratio / 100) * 100 + 0.5) / 100  # 涨停价
        is_broken = (high >= lp - 0.01) & ~is_lu & (amount > 0)
        breadth = dict(
            up=int(np.nansum(pct > 0)), down=int(np.nansum(pct < 0)), flat=int(np.nansum(pct == 0)),
            total_amount_yi=round(float(np.nansum(amount)) / 1e8, 0),
            limit_up=int(np.nansum(is_lu)), limit_down=int(np.nansum(is_ld)),
            broken=int(np.nansum(is_broken)),
            break_rate=round(float(np.nansum(is_broken)) / max(int(np.nansum(is_lu)) + int(np.nansum(is_broken)), 1) * 100, 1),
        )
        # 连板梯队（只对涨停股逐只计算）
        ladder, max_board = {}, 0
        if self._piv is not None:
            for ts in spot.loc[is_lu, "ts_code"].tolist():
                if ts not in self._piv["pct"].columns:
                    continue
                n = consec_limits(self._piv["pct"][ts].to_numpy(dtype=float),
                                  limit_ratio(ts, self._name_of(ts)))
                max_board = max(max_board, n)
                if n >= 2:
                    key = str(n) if n < 5 else "5+"
                    ladder[key] = ladder.get(key, 0) + 1
        breadth["max_board"] = max_board
        breadth["ladder"] = ladder
        breadth.update(self._prev_limit_perf(spot))
        return breadth

    def _prev_limit_perf(self, spot):
        """昨日涨停股今日表现：平均涨幅、晋级率"""
        out = dict(prev_limit_avg_pct=None, prev_limit_up_ratio=None)
        piv = self._piv
        if piv is None or piv["pct"].shape[0] < 2:
            return out
        y_row = piv["pct"].iloc[-2]
        y_ratio = pd.Series({ts: limit_ratio(ts, self._name_of(ts)) for ts in y_row.index})
        y_lu = [ts for ts in y_row.index if pd.notna(y_row[ts]) and y_row[ts] >= y_ratio[ts] - 0.2]
        if not y_lu:
            return out
        today_map = dict(zip(spot["ts_code"], spot["pct_chg"]))
        vals = [today_map[ts] for ts in y_lu if ts in today_map and pd.notna(today_map[ts])]
        if vals:
            out["prev_limit_avg_pct"] = round(float(np.mean(vals)), 2)
            up_again = sum(1 for ts in y_lu if ts in today_map and pd.notna(today_map[ts])
                           and today_map[ts] >= y_ratio[ts] - 0.2)
            out["prev_limit_up_ratio"] = round(up_again / max(len(vals), 1) * 100, 1)
        return out

    # ---------- 板块 ----------
    def get_sector_data(self, spot):
        """行业/概念板块行情 + 板块内涨停家数 + 行业排名映射
        东财板块接口失败时，用个股行情按行业聚合兜底"""
        ind = rt_get_sector_board("industry")
        con = rt_get_sector_board("concept")
        rank_map, pct_map = {}, {}
        if ind.empty:
            ind = self._agg_sectors(spot)
        if not ind.empty:
            ratio = ratio_series(spot["code"], spot["name"])
            is_lu = spot["pct_chg"] >= ratio - 0.2
            lu_cnt = spot[is_lu].groupby("industry")["ts_code"].count()
            ind["limit_up_count"] = ind["board_name"].map(lu_cnt).fillna(0).astype(int)
            ind = ind.sort_values("pct_chg", ascending=False).reset_index(drop=True)
            ind["rank"] = np.arange(1, len(ind) + 1)
            rank_map = dict(zip(ind["board_name"], ind["rank"]))
            pct_map = dict(zip(ind["board_name"], ind["pct_chg"]))
        if not con.empty:
            con = con.sort_values("pct_chg", ascending=False).reset_index(drop=True)
        return ind, con, rank_map, pct_map

    @staticmethod
    def _agg_sectors(spot):
        """按个股行业聚合生成板块表（腾讯源兜底，行业来自Tushare stock_basic）
        剔除 N/C 开头的新股（首日无涨跌幅限制，会严重扭曲板块统计）"""
        s = spot[(spot["industry"] != "")
                 & ~spot["name"].str.upper().str.startswith(("N", "C"), na=False)].copy()
        if s.empty:
            return pd.DataFrame()
        grp = s.groupby("industry").agg(
            pct_chg=("pct_chg", "mean"),
            main_inflow=("main_inflow", "sum"),
            up_count=("pct_chg", lambda x: int((x > 0).sum())),
            down_count=("pct_chg", lambda x: int((x < 0).sum())),
            member_count=("ts_code", "count"),
        ).reset_index()
        idx = s.groupby("industry")["pct_chg"].idxmax()
        lead = s.loc[idx, ["industry", "name", "pct_chg"]].rename(
            columns={"name": "leader_name", "pct_chg": "leader_pct"})
        out = grp.merge(lead, on="industry", how="left")
        out = out.rename(columns={"industry": "board_name"})
        out["leader_name"] = out["leader_name"].fillna("")
        out["leader_pct"] = pd.to_numeric(out["leader_pct"], errors="coerce")
        return out.reset_index(drop=True)
