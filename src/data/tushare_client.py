# -*- coding: utf-8 -*-
"""Tushare Pro 数据客户端：日线、股票列表、交易日历（带限速与重试）"""
import time

import pandas as pd

try:
    import tushare as ts
except ImportError:  # 依赖未安装时给出友好提示
    ts = None

from config import TUSHARE_TOKEN
from ..utils.logger import setup_logger

log = setup_logger("tushare")


class TushareClient:
    """封装 Tushare Pro 接口：只使用基础积分(120)可用的接口"""

    def __init__(self, token=TUSHARE_TOKEN):
        if ts is None:
            raise RuntimeError("未安装 tushare，请先执行: pip install -r requirements.txt")
        if not token:
            raise RuntimeError("缺少 TUSHARE_TOKEN，请在 .env 中配置（https://tushare.pro 注册获取）")
        ts.set_token(token)
        self.pro = ts.pro_api()
        self._min_interval = 0.25  # 限速：两次调用最小间隔

    def _call(self, name, **kwargs):
        """带重试的接口调用：频率超限自动退避"""
        fn = getattr(self.pro, name)
        last_err = None
        for attempt in range(3):
            try:
                time.sleep(self._min_interval)
                return fn(**kwargs)
            except Exception as e:  # tushare 抛出的限流/网络异常
                last_err = e
                msg = str(e)
                if "每分钟" in msg or "频率" in msg or "limit" in msg.lower():
                    time.sleep(5 * (attempt + 1))
                else:
                    time.sleep(1.5 * (attempt + 1))
        log.error("Tushare %s 调用失败: %s", name, last_err)
        return pd.DataFrame()

    def call_optional(self, name, **kwargs):
        """可选接口调用（如财务/龙虎榜等需高积分的接口）
        失败不抛异常，返回 (空DataFrame, 错误信息)，由调用方决定降级"""
        fn = getattr(self.pro, name, None)
        if fn is None:
            return pd.DataFrame(), f"接口{name}不存在"
        try:
            time.sleep(self._min_interval)
            return fn(**kwargs), ""
        except Exception as e:
            return pd.DataFrame(), str(e)

    # ---------- 交易日历 ----------
    def trade_cal(self, start, end):
        df = self._call("trade_cal", exchange="SSE", start_date=start, end_date=end, is_open="1")
        if df.empty:
            return pd.DataFrame(columns=["cal_date"])
        df["cal_date"] = df["cal_date"].astype(str)
        return df.sort_values("cal_date").reset_index(drop=True)

    # ---------- 股票基础列表（沪深A股，剔除北交所） ----------
    def stock_basic(self):
        df = self._call("stock_basic", exchange="", list_status="L",
                        fields="ts_code,symbol,name,area,industry,market,list_date,float_share")
        if df.empty:
            return df
        df = df[df["ts_code"].str.endswith((".SH", ".SZ"))].reset_index(drop=True)
        return df

    # ---------- 全市场日线（按交易日，分页拉全） ----------
    def daily_by_date(self, trade_date):
        rows = []
        for offset in range(0, 6):  # A股约5400只，正常一页拉完
            page = self._call("daily", trade_date=trade_date, offset=offset * 6000, limit=6000,
                              fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount")
            if page is None or page.empty:
                break
            rows.append(page)
            if len(page) < 6000:
                break
        if not rows:
            return pd.DataFrame()
        df = pd.concat(rows, ignore_index=True)
        df["trade_date"] = df["trade_date"].astype(str)
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000  # 千元 → 元
        df["vol"] = pd.to_numeric(df["vol"], errors="coerce")              # 手
        for c in ("open", "high", "low", "close", "pre_close", "pct_chg"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
