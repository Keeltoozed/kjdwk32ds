"""Судья нарратива (публичная Gemini, free tier): читает МЕМ, а не цифры.

Профи отличают смешной/вирусный тикер с живым комьюнити от сгенерированного
мусора — наши гейты смотрят только числа. Модуль спрашивает у бесплатной
модели мнение о нарративе и отдаёт скор 0-100.

Режим ADVISORY (по умолчанию): только тегирует сигнал (MEME85), входы не
режет и не открывает. После статистики по тегам решим пороги.
Fail-open везде: нет ключа/квоты/сети → None.

Квоты: только финалисты (единицы в час), кэш 6ч по mint, общий cooldown 1ч
после 429/quota. Всё через fetch_json (семафор + троттлинг).
"""
import re as _re
import time as _t

_CACHE: dict = {}  # mint_lower -> (ts, score, why)
_COOLDOWN_UNTIL = 0.0
CALLS = {"hit": 0, "miss": 0, "cooldown": 0}


def _cfg(name, default):
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception:
        return default


def _key():
    try:
        import config as _c
        return (getattr(_c, "GEMINI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def _prompt(symbol: str, name: str, mcap: float, age_min: float,
            socials: int, chain: str) -> str:
    return (
        "Meme coin launch pad review. Coin: "
        f"{(name or symbol or '?')[:40]} ({(symbol or '?')[:12]}), "
        f"chain {chain}, mcap ${mcap:,.0f}, age {age_min:.0f}min, "
        f"social links {socials}. "
        "Rate short-term viral/rocket narrative potential 0-100 "
        "(catchy meme + community effort = high; generic/bot-made/Test/scam words = low). "
        "Reply with exactly two lines:\nSCORE: <0-100>\nWHY: <max 10 words>"
    )


def _parse(text: str):
    try:
        m = _re.search(r"SCORE\s*:\s*(\d{1,3})", text or "")
        if not m:
            return None, ""
        s = max(0, min(100, int(m.group(1))))
        w = ""
        m2 = _re.search(r"WHY\s*:\s*(.+)", text or "", _re.S)
        if m2:
            w = " ".join(m2.group(1).split())[:80]
        return s, w
    except Exception:
        return None, ""


async def judge(mint: str, symbol: str, pair_data: dict, chain: str = "solana"):
    """(score 0-100 | None, why). Кэш, кулдаун, fail-open."""
    global _COOLDOWN_UNTIL
    if not _cfg("MEME_JUDGE_ENABLED", True):
        return None, "off"
    key = _key()
    if not key or not mint:
        return None, "no-key" if not key else "no-addr"
    ck = (chain, mint.lower())
    ttl = float(_cfg("MEME_JUDGE_TTL_SEC", 6 * 3600) or 0)
    hit = _CACHE.get(ck)
    if hit and ttl > 0 and (_t.time() - hit[0]) < ttl:
        CALLS["hit"] += 1
        return hit[1], hit[2]
    if _t.time() < _COOLDOWN_UNTIL:
        CALLS["cooldown"] += 1
        return None, "cooldown"
    CALLS["miss"] += 1
    try:
        pd = pair_data or {}
        base = pd.get("baseToken") or {}
        info = pd.get("info") or {}
        socials = len(info.get("socials") or []) + len(info.get("websites") or [])
        liq = float((pd.get("liquidity") or {}).get("usd", 0) or 0)
        fdv = float(pd.get("fdv", 0) or 0)
        mcap = fdv or liq * 5
        age = 999.0
        try:
            _cr = pd.get("pairCreatedAt") or 0
            if _cr:
                age = max(0.0, (_t.time() * 1000 - float(_cr)) / 60000.0)
        except Exception:
            pass
        model = _cfg("GEMINI_MODEL", "gemini-2.0-flash") or "gemini-2.0-flash"
        from http_client import fetch_json
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        status, data = await fetch_json(
            url, timeout=20, retries=1, method="POST",
            json_payload={"contents": [{"parts": [{"text": _prompt(
                symbol, base.get("name", ""), mcap, age, socials, chain)}]}]})
        if status == 429:
            _COOLDOWN_UNTIL = _t.time() + float(_cfg("MEME_JUDGE_COOLDOWN_SEC", 3600) or 3600)
            return None, "429-cooldown"
        if status in (400, 401, 403):
            # Ключ мёртв (проверено: текущий даёт 401) — не долбим каждый вход,
            # молчим сутки. Свежий ключ: aistudio.google.com (бесплатно).
            _COOLDOWN_UNTIL = _t.time() + 24 * 3600
            print(f"⚠️ MEME-JUDGE: ключ отклонён (HTTP {status}) — пауза 24ч, нужен свежий с aistudio.google.com.")
            return None, "bad-key"
        if status != 200 or not isinstance(data, dict):
            return None, f"http-{status}"
        try:
            text = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text", "")
        except Exception:
            return None, "parse"
        s, w = _parse(text)
        if s is None:
            return None, "parse"
        if ttl > 0:
            _CACHE[ck] = (_t.time(), s, w)
            if len(_CACHE) > 2000:
                _CACHE.clear()
        return s, w
    except Exception:
        return None, "error"
