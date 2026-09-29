# -*- coding: utf-8 -*-
"""Tavily 联网搜索：当日政策/热点题材资讯（按日缓存，失败静默降级）"""
import json
import re
import time

import requests

from config import TAVILY_API_KEY, DATA_CACHE_DIR
from ..utils.logger import setup_logger

log = setup_logger("tavily")

TAVILY_URL = "https://api.tavily.com/search"

QUERIES = [
    ("政策面", "A股 证监会 央行 国务院 政策 利好 公告"),
    ("题材热点", "A股 涨停 题材 热点 龙头 概念 板块"),
    ("大盘动向", "A股 大盘 行情 资金 市场情绪"),
]


# 页面导航噪声词（出现在摘要开头时剥除）
_NAV_WORDS = {"首页", "新闻", "体育", "财经", "娱乐", "科技", "博客", "图片", "专栏", "更多",
              "汽车", "教育", "时尚", "女性", "星座", "健康", "房产", "历史", "视频", "收藏",
              "育儿", "读书", "佛学", "游戏", "旅游", "邮箱", "导航", "移动版", "网页版", "关闭"}


def _clean_text(t):
    """去除HTML标签/导航噪声/多余空白，截取有效摘要"""
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = re.sub(r"&[a-z]+;|&#\d+;", " ", t)
    t = re.sub(r"#+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if "正文" in t:  # 正文之后才是文章内容
        t = t.split("正文", 1)[1].strip()
    while t:  # 剥除开头的导航词
        head = t.split(" ", 1)[0]
        if head in _NAV_WORDS:
            t = t[len(head):].strip()
        else:
            break
    return t


def _dedup_key(title):
    """同题去重键：标题去符号取前6字符"""
    return re.sub(r"[^\w一-鿿]", "", title or "")[:6]


def search_daily_news(date_str):
    """检索当日资讯，返回 [{tag,title,url,snippet}]；未配置Key或失败返回[]"""
    if not TAVILY_API_KEY:
        log.info("未配置 TAVILY_API_KEY，跳过资讯检索")
        return []
    cache_file = DATA_CACHE_DIR / f"news_{date_str}.json"
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    results, seen = [], set()
    for tag, q in QUERIES:
        items = _search(f"{q} {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}", 6, tag)
        results.extend(items)
        for it in items:
            seen.add(it["_key"])
        time.sleep(1)
    results = results[:15]
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False)
    except Exception:
        pass
    return results


def search_stock_news(name, code, max_results=8):
    """个股相关资讯检索（个股体检用），返回 [{tag,title,url,snippet}]"""
    if not TAVILY_API_KEY:
        return []
    results = _search(f"{name} {code} 最新 公告 新闻 业绩", max_results, "个股资讯")
    return results


def _search(query, max_results, tag):
    """执行一次Tavily检索，返回带标签的结果列表"""
    try:
        r = requests.post(TAVILY_URL, json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "advanced",  # advanced 提取正文更干净
            "max_results": max_results,
            "topic": "news",
        }, timeout=30)
        r.raise_for_status()
        out = []
        for item in (r.json().get("results") or []):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "tag": tag,
                "title": _clean_text(title)[:120],
                "url": item.get("url", ""),
                "snippet": _clean_text(item.get("content"))[:140],
                "_key": _dedup_key(title),
            })
        # 同题去重（按标题前6字）
        dedup, seen = [], set()
        for it in out:
            if it["_key"] and it["_key"] not in seen:
                seen.add(it["_key"])
                dedup.append({k: v for k, v in it.items() if k != "_key"})
        return dedup
    except Exception as e:
        log.warning("Tavily 检索[%s]失败: %s", tag, e)
        return []
