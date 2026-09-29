# -*- coding: utf-8 -*-
"""持仓读取：holdings.json → 标准化列表
格式示例：
[
  {"ts_code": "600519.SH", "name": "贵州茅台", "cost": 1400.0,
   "volume": 100, "buy_date": "2026-08-10", "note": "备注"}
]
ts_code 支持 6 位代码（600519）或带交易所后缀（600519.SH）
"""
import json

from config import HOLDINGS_FILE
from ..utils.logger import setup_logger

log = setup_logger("holdings")


def norm_code(code):
    """600519 / 600519.SH / SH600519 → 600519.SH"""
    code = str(code).strip().upper()
    if len(code) == 8 and code.startswith(("SH", "SZ")):
        code = code[2:] + "." + code[:2]
    if len(code) == 6:
        code = code + (".SH" if code[0] == "6" else ".SZ")
    return code


def load_holdings():
    if not HOLDINGS_FILE.exists():
        return []
    try:
        with open(HOLDINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            log.warning("holdings.json 格式错误：应为列表")
            return []
        out = []
        for h in data:
            if not isinstance(h, dict) or not h.get("ts_code"):
                continue
            try:
                out.append({
                    "ts_code": norm_code(h["ts_code"]),
                    "name": str(h.get("name", "")),
                    "cost": float(h.get("cost") or 0),
                    "volume": float(h.get("volume") or 0),
                    "buy_date": str(h.get("buy_date", "")),
                    "note": str(h.get("note", "")),
                })
            except (TypeError, ValueError):
                log.warning("持仓条目解析失败，已跳过: %s", h)
        return out
    except Exception as e:
        log.warning("持仓文件解析失败: %s", e)
        return []
