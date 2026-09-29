# -*- coding: utf-8 -*-
"""DeepSeek 大模型客户端（OpenAI 兼容协议，JSON输出模式 + 修复重试）"""
import json
import re
import time

import requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from ..utils.logger import setup_logger

log = setup_logger("deepseek")


def _extract_json(text):
    """多策略解析JSON：直接解析 → 代码块围栏 → 首{末}贪婪"""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def chat_json(messages, temperature=0.3, max_tokens=16000, retries=3,
              api_key=None, base_url=None, model=None):
    """调用 DeepSeek 并解析 JSON 输出；失败返回 None（由上层降级规则模式）
    输出非法JSON时自动追加修复提示重试。
    api_key/base_url/model：用户级覆盖（网页版自带Key模式）"""
    key = (api_key or DEEPSEEK_API_KEY).strip()
    if not key:
        log.info("未配置 DEEPSEEK_API_KEY，跳过 LLM 分析")
        return None
    url = (base_url or DEEPSEEK_BASE_URL).rstrip("/") + "/chat/completions"
    m = (model or DEEPSEEK_MODEL).strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    msgs = list(messages)
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json={
                "model": m,
                "messages": msgs,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "stream": False,
            }, timeout=300)
            if r.status_code == 429 or r.status_code >= 500:
                log.warning("DeepSeek 限流/服务异常(%d)，%ds后重试", r.status_code, 4 * (attempt + 1))
                time.sleep(4 * (attempt + 1))
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            if parsed is not None:
                return parsed
            log.warning("DeepSeek 输出非合法JSON(长度%d)，追加修复提示重试", len(content or ""))
            msgs = msgs + [
                {"role": "assistant", "content": (content or "")[:4000]},
                {"role": "user", "content": "上面的输出不是合法的JSON。请重新输出：只输出一个完整的JSON对象，"
                                           "不包含任何其它文字、代码块标记或注释。"},
            ]
        except Exception as e:
            log.warning("DeepSeek 调用失败(第%d次): %s", attempt + 1, e)
        time.sleep(2 * (attempt + 1))
    return None
