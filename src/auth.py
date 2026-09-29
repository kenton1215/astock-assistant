# -*- coding: utf-8 -*-
"""账户系统：注册/登录/次数(积分)/激活码/用户API Key管理
- 本地模式（默认）：JSON 文件存储（单机/内网部署）
- Cloud模式：Supabase REST 持久化（部署 Streamlit Cloud 时在 secrets 配置
  SUPABASE_URL / SUPABASE_KEY(service_role) / AUTH_BACKEND=supabase，建表SQL见README）
密码 PBKDF2 加盐哈希；用户自配的 LLM Key 用 Fernet 加密存储（SECRET_KEY 派生密钥）。
"""
import base64
import hashlib
import json
import secrets as _secrets
import threading
import time
from pathlib import Path

import requests

from config import USER_DB_FILE, SECRET_KEY, AUTH, SUPABASE_URL, SUPABASE_KEY
from .utils.logger import setup_logger

log = setup_logger("auth")

_lock = threading.Lock()

try:
    from cryptography.fernet import Fernet
    _fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest()))
except ImportError:
    _fernet = None
    log.warning("未安装cryptography，用户API Key将降级为base64存储（建议安装）")


def _enc(text):
    if not text:
        return ""
    return (_fernet.encrypt(text.encode()).decode()
            if _fernet else "b64:" + base64.b64encode(text.encode()).decode())


def _dec(text):
    if not text:
        return ""
    try:
        if _fernet:
            return _fernet.decrypt(text.encode()).decode()
        if str(text).startswith("b64:"):
            return base64.b64decode(str(text)[4:]).decode()
    except Exception:
        return ""
    return ""


def _hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(),
                               str(salt).encode(), 200_000).hex()


def _validate_credentials(username, password):
    """宽松注册校验：用户名2-30字符（支持中文，不含空白），密码≥4位"""
    u = str(username or "").strip()
    if not (AUTH["username_min"] <= len(u) <= AUTH["username_max"]):
        return None, f"用户名需{AUTH['username_min']}-{AUTH['username_max']}个字符"
    if any(ch.isspace() for ch in u):
        return None, "用户名不能包含空格"
    if len(password or "") < AUTH["password_min"]:
        return None, f"密码至少{AUTH['password_min']}位"
    return u, ""


class BaseUserStore:
    def register(self, username, password):
        raise NotImplementedError

    def login(self, username, password):
        raise NotImplementedError

    def get_user(self, username):
        raise NotImplementedError

    def update_user(self, username, **fields):
        raise NotImplementedError

    def redeem_code(self, username, code):
        raise NotImplementedError

    def generate_codes(self, n, credits):
        raise NotImplementedError


class JsonUserStore(BaseUserStore):
    """本地 JSON 存储（单机默认）"""

    def __init__(self, path=None):
        self.path = Path(path) if path else USER_DB_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = self._load()

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("用户库文件损坏，已重置")
        return {"users": {}, "codes": {}}

    def _save(self):
        with _lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.db, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def _new_user(self, username):
        salt = _secrets.token_hex(8)
        return {"username": username, "salt": salt, "pw": _hash_pw("", salt),
                "credits": AUTH["free_trial_credits"],
                "created": time.strftime("%Y-%m-%d %H:%M"),
                "llm_key": "", "llm_base": "", "llm_model": "",
                "holdings": [], "last_day": "", "daily_count": 0}

    def register(self, username, password):
        u, err = _validate_credentials(username, password)
        if err:
            return None, err
        u = u.lower()
        if u in self.db["users"]:
            return None, "用户名已存在"
        user = self._new_user(u)
        user["salt"] = _secrets.token_hex(8)
        user["pw"] = _hash_pw(password, user["salt"])
        self.db["users"][u] = user
        self._save()
        return user, f"注册成功，赠送{AUTH['free_trial_credits']}次免费分析"

    def login(self, username, password):
        u = str(username).strip().lower()
        user = self.db["users"].get(u)
        if not user or user.get("pw") != _hash_pw(password, user.get("salt", "")):
            return None, "用户名或密码错误"
        return user, "登录成功"

    def get_user(self, username):
        return self.db["users"].get(str(username).strip().lower())

    def update_user(self, username, **fields):
        u = self.get_user(username)
        if u:
            u.update(fields)
            self._save()

    def redeem_code(self, username, code):
        c = self.db["codes"].get(str(code).strip())
        if not c:
            return False, "激活码无效"
        if c.get("used_by"):
            return False, "激活码已被使用"
        u = self.get_user(username)
        self.update_user(username, credits=u.get("credits", 0) + c["credits"])
        c["used_by"] = username
        c["used_at"] = time.strftime("%Y-%m-%d %H:%M")
        self._save()
        return True, f"兑换成功，+{c['credits']}次"

    def generate_codes(self, n, credits):
        codes = []
        for _ in range(n):
            code = "AK-" + _secrets.token_hex(4).upper()
            self.db["codes"][code] = {"credits": credits,
                                      "created": time.strftime("%Y-%m-%d"),
                                      "used_by": "", "used_at": ""}
            codes.append(code)
        self._save()
        return codes


class SupabaseUserStore(BaseUserStore):
    """Supabase REST 适配器（Streamlit Cloud 持久化，建表SQL见README）
    表: users(username pk, salt, pw, credits, created, llm_key, llm_base, llm_model,
             holdings text, last_day, daily_count)
        codes(code pk, credits, created, used_by, used_at)"""

    def __init__(self, url, key):
        self.base = str(url).rstrip("/")
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"}

    def _row(self, table, key_field, key_value):
        try:
            r = requests.get(f"{self.base}/rest/v1/{table}", headers=self.headers,
                             params={key_field: f"eq.{key_value}", "select": "*"}, timeout=15)
            rows = r.json() if r.status_code == 200 else []
            return rows[0] if rows else None
        except Exception as e:
            log.warning("Supabase查询失败: %s", e)
            return None

    def _upsert(self, table, row):
        try:
            r = requests.post(f"{self.base}/rest/v1/{table}",
                              headers={**self.headers,
                                       "Prefer": "resolution=merge-duplicates,return=representation"},
                              json=row, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log.warning("Supabase写入失败: %s", e)

    def register(self, username, password):
        u, err = _validate_credentials(username, password)
        if err:
            return None, err
        u = u.lower()
        if self._row("users", "username", u):
            return None, "用户名已存在"
        salt = _secrets.token_hex(8)
        user = {"username": u, "salt": salt, "pw": _hash_pw(password, salt),
                "credits": AUTH["free_trial_credits"], "created": time.strftime("%Y-%m-%d %H:%M"),
                "llm_key": "", "llm_base": "", "llm_model": "",
                "holdings": "[]", "last_day": "", "daily_count": 0}
        self._upsert("users", user)
        return user, f"注册成功，赠送{AUTH['free_trial_credits']}次免费分析"

    def login(self, username, password):
        u = str(username).strip().lower()
        user = self._row("users", "username", u)
        if not user or user.get("pw") != _hash_pw(password, user.get("salt", "")):
            return None, "用户名或密码错误"
        return user, "登录成功"

    def get_user(self, username):
        u = self._row("users", "username", str(username).strip().lower())
        if u and isinstance(u.get("holdings"), str):
            # Supabase文本列存的JSON字符串 → 转回列表（保持与本地库一致的行为）
            try:
                u["holdings"] = json.loads(u["holdings"])
            except Exception:
                u["holdings"] = []
        return u

    def update_user(self, username, **fields):
        u = self.get_user(username)
        if u:
            u.update(fields)
            # 列表 → JSON字符串后写入文本列
            if isinstance(u.get("holdings"), list):
                u["holdings"] = json.dumps(u["holdings"], ensure_ascii=False)
            self._upsert("users", u)

    def redeem_code(self, username, code):
        c = self._row("codes", "code", str(code).strip())
        if not c:
            return False, "激活码无效"
        if c.get("used_by"):
            return False, "激活码已被使用"
        u = self.get_user(username)
        self.update_user(username, credits=int(u.get("credits", 0)) + int(c.get("credits", 0)))
        c["used_by"] = username
        c["used_at"] = time.strftime("%Y-%m-%d %H:%M")
        self._upsert("codes", c)
        return True, f"兑换成功，+{c['credits']}次"

    def generate_codes(self, n, credits):
        codes = []
        for _ in range(n):
            code = "AK-" + _secrets.token_hex(4).upper()
            self._upsert("codes", {"code": code, "credits": credits,
                                   "created": time.strftime("%Y-%m-%d"),
                                   "used_by": "", "used_at": ""})
            codes.append(code)
        return codes


_store = None


def get_store():
    global _store
    if _store is None:
        if AUTH["backend"] == "supabase" and SUPABASE_URL and SUPABASE_KEY:
            _store = SupabaseUserStore(SUPABASE_URL, SUPABASE_KEY)
            log.info("账户存储: Supabase(%s)", SUPABASE_URL.split("//")[-1].split(".")[0])
        else:
            _store = JsonUserStore()
    return _store


def consume_analysis_quota(username, use_own_key):
    """分析前扣费/校验。返回 (ok, msg, mode)
    mode: ("own", key, base, model) 自带Key | ("owner", None, "", "") 平台Key（扣次数）| None 未通过"""
    store = get_store()
    user = store.get_user(username)
    if not user:
        return False, "未登录", None
    today = time.strftime("%Y%m%d")
    daily = int(user.get("daily_count") or 0) if user.get("last_day") == today else 0
    if daily >= AUTH["daily_limit"]:
        return False, f"今日分析次数已达上限({AUTH['daily_limit']}次/日)", None
    if use_own_key:
        key = _dec(user.get("llm_key", ""))
        if not key:
            return False, "请先在「我的API Key」中配置自己的Key", None
        store.update_user(username, daily_count=daily + 1, last_day=today)
        return True, "", ("own", key, user.get("llm_base") or "", user.get("llm_model") or "")
    credits = int(user.get("credits") or 0)
    if credits <= 0:
        return False, "免费次数已用完：可①在「我的API Key」配置自己的DeepSeek Key（免费不限次）②兑换激活码", None
    store.update_user(username, daily_count=daily + 1, last_day=today, credits=credits - 1)
    return True, "", ("owner", None, "", "")
