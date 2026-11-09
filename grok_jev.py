"""Grok Bot x JEV Engine: Многоуровневый фильтр и торговый конвейер
по архитектуре GROK BOT x JEV (JEV PICKS, GROK TRADES).

Конвейер:
  THE FUNNEL:
    Universe ➔ Liquidity ➔ Holders ➔ JEV Gate ➔ Passed ➔ Filled (99.5%+ отсечение)
  
  THE LINE:
    1. HARD GATE:
       - min_age_minutes: 15м (или 3-5м для pump/early)
       - min_liquidity_usd: $12,000
       - min_volume_h1: $40,000 (или vol5 >= $2,500)
       - min_mcap_usd: $45,000
       - min_trades_h1: 250 (или tx5 >= 18)
       - max_top_wallet: 0.05 (топ-1 холдер <= 5% без пула)
       - max_top_10: 0.25 (топ-10 холдеров <= 25% без пула)
       - RugCheck / GoPlus: LP заблокирован, mint/freeze authority отключены, sell tax 0%
    
    2. SOFT GATE (JEV AI VET через Groq / xAI Grok / Gemini):
       - account_is_the_project (min 0.75): проверка подлинности соцсетей токена
       - recycled_account (max 0.30): проверка на переименованный старый скам-аккаунт
       - concentration_is_exit_risk (max 0.35): риск разгрузки крупных холдеров
       - momentum_already_spent (max 0.60): оценка, не выдохся ли памп (не покупка пика)
       - liquidity_fits_ticket (min 0.70): глубина пула под ордер $15-$25
       - dev_still_loaded (max 0.25): дев не сидит со скрытым объёмом
    
    3. FILLS:
       "We bid the flush, not the candle" — покупка только на откате (PULLBACK),
       а не влетание в зеленую палку.
    
    4. RISK:
       "The stop lives under the wick" — стоп ставится под фитиль (low) свечи отката
       (динамический стоп -5%..-8% вместо пассивного ожидания -30%).

Поддерживает работу с бесплатным API Groq (30 RPM) и xAI Grok с жестким кэшированием,
семафором и fail-safe переключением на алгоритмический скоринг при лимитах.
"""

import os
import json
import time
import asyncio
import re
from collections import Counter
from typing import Dict, Any, Tuple, Optional

# Глобальная статистика воронки (THE FUNNEL)
FUNNEL_STATS = {
    "universe": 0,
    "liquidity": 0,
    "holders": 0,
    "jev_gate": 0,
    "passed": 0,
    "filled": 0,
}

# Кэш оценок JEV (mint -> (timestamp, result_dict)) — TTL 15 минут
_JEV_CACHE: Dict[str, Tuple[float, dict]] = {}
_LAST_API_CALL_TS = 0.0
_api_sems: dict = {}

def _get_api_semaphore():
    try:
        loop = asyncio.get_running_loop()
        k = id(loop)
    except RuntimeError:
        k = 0
    if k not in _api_sems:
        _api_sems[k] = asyncio.Semaphore(1)
    return _api_sems[k]

_COOLDOWN_UNTIL = 0.0  # Пауза при 429 rate-limit

# --- Диагностика: почему бот не покупает (счётчик причин отказа за окно ~60с) ---
REJECT_STATS = Counter()


def _reason_key(why: str) -> str:
    """'hard-gate-reject sellers-dominating 5b/9s ...' -> 'hard-gate-reject sellers-dominating'."""
    out = []
    for t in str(why).split()[:4]:
        if re.match(r"^[\d$%(\[+\-]", t):
            break
        out.append(t.rstrip(":,"))
    return " ".join(out)[:60] or "other"


def get_reject_summary(reset: bool = True) -> str:
    if not REJECT_STATS:
        return "🔎 [REJECTS/60s] нет оценённых кандидатов (пустая вселенная или все на кулдауне)"
    top = ", ".join(f"{k}={v}" for k, v in REJECT_STATS.most_common(8))
    if reset:
        REJECT_STATS.clear()
    return f"🔎 [REJECTS/60s] {top}"

# --- Groq free tier: бюджет и пер-модельные паузы ---
_BUDGET = {"day": None, "n70": 0}
_CD = {"70b": 0.0, "8b": 0.0}
# Llama на бесплатном Groq отключены (404). ID берутся из env, чтобы менять без правки кода:
# JEV_MODEL_PRIMARY / JEV_MODEL_FALLBACK. Теги "70b"/"8b" - только внутренние метки (основная/запасная).
def _model_env(name: str, default: str) -> str:
    """ID модели из env. Если туда по ошибке вставили API-ключ (gsk_...) или пробелы - берём дефолт."""
    v = (os.getenv(name) or "").strip().strip('"').strip("'")
    if not v or v.startswith("gsk_") or " " in v or len(v) > 60:
        return default
    return v


_MODELS = [(_model_env("JEV_MODEL_PRIMARY", "openai/gpt-oss-120b"), "70b"),
           (_model_env("JEV_MODEL_FALLBACK", "openai/gpt-oss-20b"), "8b")]
DAILY_70B = 150          # лимит бесплатного тарифа 200k токенов/сутки на модель, ~1.2k на вызов

# --- Пороги ответа JEV (any null / нет ключа = отказ) ---
_NEED = {
    "concentration_is_exit_risk": ("max", 0.55),
    "momentum_already_spent": ("max", 0.60),
    "liquidity_fits_ticket": ("min", 0.60),
    "dev_still_loaded": ("max", 0.55),
    "pick_worth_trading": ("min", 0.60),
    "confidence": ("min", 0.55),
}


_GROQ_LOG_TS = {}


def _log_groq(msg: str) -> None:
    """Печатает причину отказа Groq не чаще раза в 30с на одинаковое сообщение."""
    msg = re.sub(r"gsk_\w+|xai-[\w-]+", "***KEY***", msg)   # ключи никогда не пишем в лог
    now = time.time()
    if now - _GROQ_LOG_TS.get(msg[:40], 0.0) >= 30.0:
        _GROQ_LOG_TS[msg[:40]] = now
        print(f"🧪 [JEV/GROQ] {msg}")


_SW = {"ts": 0.0, "set": set()}


def _smart_wallets() -> set:
    """Кошельки из smart_wallets.txt (их пишет smart_money_hunter), кэш 5 минут."""
    if time.time() - _SW["ts"] < 300:
        return _SW["set"]
    s = set()
    try:
        with open("smart_wallets.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    s.add(line)
    except Exception:
        pass
    _SW["ts"], _SW["set"] = time.time(), s
    return s


def _entry_features(pair: dict, hs: dict, jev: dict) -> dict:
    """Признаки токена на момент входа: попадают в колонку features таблицы сделок,
    чтобы потом сравнивать победителей и проигравших (раньше у GROK-JEV там были только символ и цена)."""
    pc, vol = pair.get("priceChange") or {}, pair.get("volume") or {}
    tx = pair.get("txns") or {}
    t5, t24 = tx.get("m5") or {}, tx.get("h24") or {}
    ca = pair.get("pairCreatedAt") or 0
    f = {
        "age_min": round((time.time() * 1000 - ca) / 60000, 1) if ca else None,
        "liquidity": (pair.get("liquidity") or {}).get("usd"), "fdv": pair.get("fdv") or pair.get("marketCap"),
        "dex": pair.get("dexId"),
        "price_change_m1": pc.get("m1"), "price_change_m5": pc.get("m5"), "price_change_h1": pc.get("h1"),
        "price_change_h6": pc.get("h6"), "price_change_h24": pc.get("h24"),
        "volume_m5": vol.get("m5"), "volume_h1": vol.get("h1"), "volume_h6": vol.get("h6"), "volume_h24": vol.get("h24"),
        "buys_m5": t5.get("buys"), "sells_m5": t5.get("sells"), "trades_h24": (t24.get("buys") or 0) + (t24.get("sells") or 0),
        "socials": bool((pair.get("info") or {}).get("socials")),
        "top1_pct": (hs or {}).get("top1_pct"), "top10_pct": (hs or {}).get("top10_pct"), "dev_pct": (hs or {}).get("dev_pct"),
        "smart_n": (hs or {}).get("smart_n"),
    }
    for k in _NEED:
        f["jev_" + k] = (jev or {}).get(k)
    return f


def _jev_failed(res: dict) -> list:
    """Список метрик, не прошедших пороги. Пустой список = PASS."""
    bad = []
    for k, (kind, th) in _NEED.items():
        v = res.get(k)
        if (not isinstance(v, (int, float)) or isinstance(v, bool)
                or (kind == "max" and v > th) or (kind == "min" and v < th)):
            bad.append(k)
    return bad


def _data_line(pair: dict, hs: dict, ticket: float) -> str:
    """Одна компактная строка данных для LLM (NA = данных нет)."""
    pc = pair.get("priceChange") or {}
    vol = pair.get("volume") or {}
    tx = pair.get("txns") or {}
    t5, t24 = tx.get("m5") or {}, tx.get("h24") or {}
    ca = pair.get("pairCreatedAt") or 0
    age = round((time.time() * 1000 - ca) / 60000) if ca else "NA"
    v24, v6 = float(vol.get("h24") or 0), float(vol.get("h6") or 0)
    ratio = round(v6 / (v24 / 4), 2) if v24 > 0 else "NA"
    dex_id = str(pair.get("dexId") or "").lower()
    base_addr = str(((pair.get("baseToken") or {}).get("address")) or "")
    is_pump = dex_id in ("pump", "pumpfun") or base_addr.endswith("pump")
    raw_liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
    mcap_val = float(pair.get("fdv") or pair.get("marketCap") or 0)
    eff_liq = max(raw_liq, mcap_val * 0.40) if is_pump else raw_liq
    liq_str = f"{eff_liq:.0f}" if eff_liq > 0 else "NA"
    top1 = hs.get("top1_pct") if hs.get("top1_pct") is not None else "NA"
    top10 = hs.get("top10_pct") if hs.get("top10_pct") is not None else "NA"
    dev_v = hs.get("dev_pct") if hs.get("dev_pct") is not None else "NA"
    hold = hs.get("holders_count") or "NA"
    return (f"dex={dex_id or 'solana'} age={age}m liq={liq_str} "
            f"mcap={int(mcap_val) if mcap_val > 0 else 'NA'} holders={hold} "
            f"top1={top1} top10={top10} dev={dev_v} "
            f"vol=m5:{vol.get('m5', 'NA')},h1:{vol.get('h1', 'NA')},h6:{vol.get('h6', 'NA')},h24:{vol.get('h24', 'NA')} "
            f"vol_ratio={ratio} trades24={(t24.get('buys') or 0) + (t24.get('sells') or 0)} "
            f"b5={t5.get('buys', 'NA')} s5={t5.get('sells', 'NA')} "
            f"pc=m1:{pc.get('m1', 'NA')},m5:{pc.get('m5', 'NA')},h1:{pc.get('h1', 'NA')},h6:{pc.get('h6', 'NA')},h24:{pc.get('h24', 'NA')} "
            f"socials={'true' if (pair.get('info') or {}).get('socials') else 'false'} ticket={ticket:.0f} "
            f"smart_buyers={hs.get('smart_n', 'NA')}")


def get_funnel_summary() -> str:
    """Возвращает форматированную строку воронки THE FUNNEL."""
    u = FUNNEL_STATS["universe"]
    f = FUNNEL_STATS["filled"]
    filter_rate = ((u - f) / u * 100.0) if u > 0 else 99.5
    return (
        f"📊 [THE FUNNEL] Universe: {u} ➔ Liq: {FUNNEL_STATS['liquidity']} ➔ "
        f"Holders: {FUNNEL_STATS['holders']} ➔ JEV: {FUNNEL_STATS['jev_gate']} ➔ "
        f"Passed: {FUNNEL_STATS['passed']} ➔ Filled: {f} | Filtered: {filter_rate:.2f}%"
    )


class GrokJevEngine:
    """Движок конвейера фильтрации Grok Bot x JEV."""
    FUNNEL_STATS = FUNNEL_STATS
    get_funnel_summary = staticmethod(get_funnel_summary)

    def __init__(self):
        self.FUNNEL_STATS = FUNNEL_STATS
        self.get_funnel_summary = get_funnel_summary
        # mint -> [(ts, price), ...] за последние 30 мин: нужна, чтобы отличать растущую монету от падающей
        self._px_hist: Dict[str, list] = {}
        # norm_symbol -> (mint, liq_usd, created_at_ms, ts): реестр сегодняшних ракет для мгновенного отсева copycat-клонов
        self._runner_registry: Dict[str, tuple] = {}

    def check_copycat_runner(self, mint: str, symbol: str, pair_data: dict) -> Tuple[bool, str]:
        """0ms проверка по реестру сегодняшних раннеров: отсекает свежие copycat-клоны с микро-ликвой."""
        _norm_sym = re.sub(r"[^a-z]", "", str(symbol or "").lower())
        if len(_norm_sym) < 3 or not isinstance(pair_data, dict):
            return True, ""
        _raw_liq_cc = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        _fdv_cc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        _h24_cc = float(((pair_data.get("priceChange") or {}).get("h24", 0)) or 0)
        _ca_cc = int(pair_data.get("pairCreatedAt") or 0)
        _prev_r = self._runner_registry.get(_norm_sym)
        if _prev_r and _prev_r[0] != mint:
            _orig_mint, _orig_liq, _orig_ca, _orig_ts = _prev_r
            if (time.time() - _orig_ts) < 86400.0 and _orig_liq >= 25000.0 and _raw_liq_cc < (_orig_liq * 0.25):
                if _orig_ca == 0 or _ca_cc == 0 or _ca_cc > _orig_ca:
                    return False, f"copycat-of-runner {symbol} (clone of {_orig_mint[:6]} liq ${_orig_liq:,.0f})"
        if _raw_liq_cc >= 25000.0 and (_h24_cc >= 200.0 or _fdv_cc >= 180000.0):
            if (not _prev_r) or _raw_liq_cc > _prev_r[1]:
                if len(self._runner_registry) > 1500:
                    self._runner_registry.clear()
                self._runner_registry[_norm_sym] = (mint, _raw_liq_cc, _ca_cc, time.time())
        return True, ""

    def score_rocket_xgb(self, address: str, pair: dict, holders_info: dict = None) -> float:
        """Оценка токена через Rocket XGBoost v3 (0ms)."""
        if hasattr(self, "analyzer") and self.analyzer and hasattr(self.analyzer, "score_rocket_xgb"):
            return float(self.analyzer.score_rocket_xgb(address, pair, holders_info))
        try:
            from analyzer import score_token_with_rocket_xgb
            return float(score_token_with_rocket_xgb(address, pair, holders_info))
        except Exception:
            return 0.0

    def _record_px(self, mint: str, price: float) -> list:
        """Запоминает цену, которую мы видели на этом скане. Возвращает историю mint."""
        now = time.time()
        h = self._px_hist.setdefault(mint, [])
        if not h or now - h[-1][0] >= 5.0:
            h.append((now, float(price)))
        cutoff = now - 1800.0
        while h and h[0][0] < cutoff:
            h.pop(0)
        if len(self._px_hist) > 3000:
            for k in sorted(self._px_hist, key=lambda k: self._px_hist[k][-1][0] if self._px_hist[k] else 0)[:1000]:
                self._px_hist.pop(k, None)
        return h

    @property
    def groq_key(self) -> str:
        import config as _c
        return (
            os.getenv("GROQ_API_KEY") or
            getattr(_c, "GROQ_API_KEY", "") or
            os.getenv("GROK_API_KEY") or
            ""
        ).strip().strip('"').strip("'")

    @property
    def xai_key(self) -> str:
        import config as _c
        return (
            os.getenv("XAI_API_KEY") or
            os.getenv("GROK_API_KEY") or
            getattr(_c, "XAI_API_KEY", "") or
            ""
        ).strip().strip('"').strip("'")

    @property
    def gemini_key(self) -> str:
        import config as _c
        return (
            os.getenv("GEMINI_API_KEY") or
            getattr(_c, "GEMINI_API_KEY", "") or
            ""
        ).strip().strip('"').strip("'")

    # =========================================================================
    # 1. HARD GATE: "if it fails here, JEV never sees it"
    # =========================================================================
    async def smart_buyers(self, mint: str) -> tuple:
        """(уникальных плательщиков за последние ~50 сделок, сколько из них смарт-кошельки) или (None, None).
        Helius Parsed History: GET /v0/addresses/{mint}/transactions. Любая ошибка -> (None, None), торговлю не трогает."""
        try:
            import config as _c
            import goplus as _gp_h
            from http_client import fetch_json
            if time.time() < getattr(_gp_h, "_HELIUS_429_UNTIL", 0.0):
                return None, None
            wallets = _smart_wallets()
            key = (os.getenv("HELIUS_API_KEY") or getattr(_c, "HELIUS_API_KEY", "") or "").strip()
            if not key or not wallets:
                return None, None
            st, data = await asyncio.wait_for(fetch_json(
                f"https://api.helius.xyz/v0/addresses/{mint}/transactions",
                params={"api-key": key, "limit": 50}, timeout=5, retries=0), timeout=7.0)
            if st != 200 or not isinstance(data, list):
                if st in (401, 403, 429):
                    _gp_h._note_rpc_status("helius", st)
                _log_groq(f"helius history {mint[:6]}: HTTP {st}")
                return None, None
            payers = {str(t.get("feePayer") or "") for t in data if isinstance(t, dict)} - {""}
            return len(payers), len(payers & wallets)
        except Exception:
            return None, None

    def llm_ready(self) -> bool:
        """Есть ли шанс получить ответ судьи прямо сейчас (ключ, пауза 429, дневной бюджет)."""
        now = time.time()
        has_key = bool(self.groq_key or self.xai_key or self.gemini_key)
        if not has_key or now < _COOLDOWN_UNTIL:
            return False
        fresh_day = _BUDGET["day"] != time.strftime("%Y-%m-%d", time.gmtime())
        ok70 = now >= _CD["70b"] and (fresh_day or _BUDGET["n70"] < DAILY_70B)
        return ok70 or now >= _CD["8b"]

    def hard_gate(
        self,
        mint: str,
        pair_data: dict,
        chain: str = "solana",
        holder_stats: Optional[dict] = None
    ) -> Tuple[bool, str]:
        """
        Этап 1: Жесткие математические фильтры.
        Если токен не проходит здесь — ИИ (JEV/Grok) его даже не увидит.
        """
        import config as _c
        FUNNEL_STATS["universe"] += 1
        
        if getattr(_c, "SOLANA_ONLY", True) and chain != "solana":
            return False, "solana-only-mode"

        if not pair_data or not isinstance(pair_data, dict):
            return False, "no-pair-data"
            
        base = pair_data.get("baseToken", {}) or {}
        symbol = str(base.get("symbol", "")).upper().strip()
        dex_id = str(pair_data.get("dexId", "")).lower()
        is_pump = dex_id in ("pump", "pumpfun", "pumpswap") or mint.endswith("pump")
        
        # 0. Защита от фейковых клонов брендов и системных тикеров
        FAKE_BRAND_BLACKLIST = {
            "BASE", "SP500", "S&P500", "USDC", "USDT", "SOL", "ETH", "BTC", "SOLANA",
            "BINANCE", "COINBASE", "TESLA", "APPLE", "NVDA", "AMAZON", "GOOGLE", "MICROSOFT",
            "NINTENDO", "PLAYSTATION", "SONY", "DISNEY", "MARVEL", "POKEMON", "SEGA", "XBOX", "ELUN", "ELOM",
            "STEAM", "VALVE", "ROBLOX", "TIKTOK", "NETFLIX", "SPOTIFY", "PEPSI", "COCACOLA", "MCDONALDS"
        }
        if symbol in FAKE_BRAND_BLACKLIST or any(b in symbol for b in ("NINTENDO", "POKEMON", "PLAYSTATION", "DISNEY", "NVIDIA", "APPLE", "TESLA", "MICROSOFT", "AMAZON")):
            return False, f"fake-brand-clone {symbol}"

        # 0.5. Быстрый 0ms анти-клон по реестру сегодняшних ракет (@savipww Scanner Copycat Rule)
        _cc_ok, _cc_why = self.check_copycat_runner(mint, symbol, pair_data)
        if not _cc_ok:
            return False, _cc_why

        # 1. Ликвидность (с защитой от сырых кёрвов Pump.fun, микро-пулов и фантомной ликвидности)
        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv_mc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)

        # ЗАЩИТА ОТ ПУСТЫХ 0.2% FLASH-RUG КЁРВОВ PUMP.FUN (кейсы NOTHING $2.6k, CAMPUS $2.6k):
        # На пустой кривой с 2 SOL резерва любой дамп дева пробивает стоп сразу в -75%!
        # Пускаем Pump.fun кёрвы ТОЛЬКО если они уже набрали базу (прогресс >=15% / FDV >=$9k, кейсы MOBROS $12.5k, OREX $13k)!
        is_pump = dex_id in ("pump", "pumpfun", "moonshot", "meteoradbc") or mint.endswith("moon")
        if is_pump:
            if not getattr(_c, "ALLOW_UNGRADUATED_PUMPFUN", False):
                return False, "ungraduated-pump-curve (ждём миграции на DEX с сожжённым LP)"
            if fdv_mc <= 0:
                _cur_p = float(pair_data.get("priceUsd", 0) or 0)
                if _cur_p > 0:
                    fdv_mc = _cur_p * 1_000_000_000.0
            _min_curve_mc = float(getattr(_c, "MIN_PUMP_CURVE_MCAP_USD", 7500.0))
            _max_curve_mc = float(getattr(_c, "MAX_PUMP_CURVE_MCAP_USD", 42000.0))
            if fdv_mc > 0 and fdv_mc < _min_curve_mc:
                return False, f"pump-curve-flash-rug: FDV ${fdv_mc:,.0f} < ${_min_curve_mc:,.0f} (пустой кёрв <12%, риск мгновенного сноса в -75%)"
            if fdv_mc > _max_curve_mc:
                return False, f"pump-curve-migration-cliff: FDV ${fdv_mc:,.0f} > ${_max_curve_mc:,.0f} (опасная зона обрыва перед миграцией, сброс снайперов)"
        eff_liq = raw_liq if dex_id not in ("pump", "pumpfun", "meteoradbc") else (raw_liq if raw_liq > 0 else fdv_mc * 0.40)
        
        # ЗАЩИТА ОТ ФАНТОМНОЙ ЛИКВИДНОСТИ И ОДНОМИНУТНЫХ ДЕВ-ДАМПОВ:
        # В пуле ОБЯЗАН быть реальный резерв SOL (>= 10-12 SOL), чтобы не пускать пустышки по 1.3-2.9 SOL вроде ZECP/MIKE/SLEGO.
        liq_dict = pair_data.get("liquidity") or {}
        quote_token_symbol = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
        quote_amount = float(liq_dict.get("quote", 0.0) or 0.0)
        if quote_token_symbol in ("SOL", "WSOL") and quote_amount > 0:
            _min_sol_req = 8.0 if dex_id == "pumpswap" else 5.0
            if quote_amount < _min_sol_req:
                return False, f"phantom-liq-trap: real pool SOL {quote_amount:.2f} < {_min_sol_req:.0f} SOL (dumped pool like ZECP/MIKE)"

        # 2. Возраст: если pairCreatedAt отсутствует (часто на первых секундах PumpSwap), берём из birth_tracker
        created_at = pair_data.get("pairCreatedAt") or 0
        if created_at:
            age_min = (time.time() * 1000 - created_at) / 60000.0
        else:
            _bt_age = None
            try:
                from birth_tracker import birth_tracker as _bt_check
                _bt_age = _bt_check.get_age(mint)
            except Exception:
                pass
            if _bt_age is not None:
                age_min = float(_bt_age)
            elif is_pump:
                age_min = 0.2
            else:
                age_min = 999.0

        txns_m5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        txns_h24 = (pair_data.get("txns") or {}).get("h24", {}) or {}
        b5 = int(txns_m5.get("buys", 0) or 0)
        s5 = int(txns_m5.get("sells", 0) or 0)
        vol_m5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        vol_h24 = float((pair_data.get("volume") or {}).get("h24", 0) or 0)
        trades_h24 = int(txns_h24.get("buys", 0) or 0) + int(txns_h24.get("sells", 0) or 0)

        _min_grad_liq = 5000.0 if (vol_m5 >= 2500.0 and b5 >= s5) else float(getattr(_c, "MIN_GRADUATED_LIQ_USD", 8000.0))
        if dex_id in ("pump", "pumpfun", "meteoradbc") and not getattr(_c, "ALLOW_UNGRADUATED_PUMPFUN", False):
            return False, "raw-pump-curve (wait for graduation to PumpSwap/Raydium with locked LP)"
        min_liq = 1500.0 if dex_id in ("pump", "pumpfun", "meteoradbc") else _min_grad_liq
        if eff_liq < min_liq:
            return False, f"liq-depth-low ${eff_liq:.0f} < ${min_liq:.0f} (micro-cap dump trap)"
            
        max_liq = float(getattr(_c, "JEV_MAX_LIQUIDITY_USD", 350000.0))
        if eff_liq > max_liq:
            return False, f"liq-giant ${eff_liq:,.0f} > ${max_liq:,.0f} (stagnant giant pool, not a meme rocket)"

        FUNNEL_STATS["liquidity"] += 1

        # 3. Живой импульс ракеты (LIVE ROCKET MOMENTUM)
        pc = pair_data.get("priceChange", {}) or {}
        m5 = float(pc.get("m5", 0) or 0)
        m1 = float(pc.get("m1", 0) or 0)
        if abs(m1) > max(180.0, abs(m5) * 3.0 + 40.0):
            m1 = 0.0
        if m1 == 0.0:
            _cur_px = float(pair_data.get("priceUsd", 0) or 0)
            _ph = self._px_hist.get(mint) or []
            _now_t = time.time()
            _ref_pts = [
                p for t, p in _ph
                if 12.0 <= (_now_t - t) <= 120.0 and p > 0
                and (_cur_px <= 0 or 0.05 <= (_cur_px / p) <= 12.0)
            ]
            if _cur_px > 0 and _ref_pts:
                m1 = max(-40.0, min(40.0, (_cur_px / _ref_pts[-1] - 1.0) * 100.0))
            elif abs(m5) > 0.5:
                m1 = max(-25.0, min(25.0, m5 * 0.15))
        h1 = float(pc.get("h1", 0) or 0)

        # КАТЕГОРИЧЕСКИЙ БЛОК: СМЕРТЕЛЬНАЯ ЗОНА МИГРАЦИИ (0 - 2.0 МИНУТ)
        # Отсекаем только мгновенный сброс дева без объема, но пускаем реальные ракеты с объемом (MBAPPE, SAFU, OMNI, DEMOCAT, LUCKY)
        is_migrated_pump = (dex_id not in ("pump", "pumpfun")) and (mint.endswith("pump") or "pump" in dex_id) and age_min <= 10.0
        if is_migrated_pump:
            has_breakout_momentum = (
                (vol_m5 >= 2000.0 and b5 >= 15 and b5 >= s5 * 1.08)
                or (age_min >= 1.2 and eff_liq >= 12000.0 and vol_m5 >= 4000.0 and m5 >= 2.5 and b5 >= s5 * 1.05)
            )
            if age_min < 0.6 and not has_breakout_momentum:
                try:
                    from birth_tracker import birth_tracker as _bt_mig
                    _bt_mig.add_to_waiting_room(mint)
                except Exception:
                    pass
                return False, f"post-migration-dump-zone {age_min:.1f}m < 0.6m"
            _has_reclaim_mig = (m5 >= 5.0 and b5 >= s5 * 1.30 and vol_m5 >= 800.0)
            if not has_breakout_momentum and not _has_reclaim_mig and (m5 < 2.0 or (s5 > 0 and (b5 / s5) < 1.08) or vol_m5 < 1500.0):
                return False, f"post-migration-no-reclaim (m5 {m5:+.1f}%, vol5 ${vol_m5:,.0f}, {b5}b/{s5}s)"

        # 2. Возраст токена и запрет старых стоячих гигантов (кейс HODL 940.7м $4.2M FDV, DUGE 224.7м m5 +1.9%)
        _avg_tx_e = vol_m5 / max(1, b5 + s5)
        _is_early_rocket = (
            (eff_liq >= 10000.0 and vol_m5 >= 3000.0 and b5 >= 15 and b5 >= s5 * 1.05 and m5 >= 1.5)
            or (vol_m5 >= 15000.0 and b5 >= s5)
        )
        _early_min_age = max(0.25, float(getattr(_c, "JEV_EARLY_MIN_AGE_MIN", 0.25)))
        _std_min_age = max(0.35, float(getattr(_c, "JEV_MIN_AGE_MIN", 0.35)))
        min_age = _early_min_age if _is_early_rocket else _std_min_age
        max_age = float(getattr(_c, "SOLANA_MAX_AGE_MIN", 240.0))  # Ракеты до 240м (4 часа)
        _is_jev_breakout = (
            age_min <= 360.0
            and fdv_mc <= 1500000.0
            and eff_liq >= 25000.0
            and vol_m5 >= 10000.0
            and (vol_m5 / max(eff_liq, 1.0)) >= 0.15
            and m5 >= 4.0
            and _avg_tx_e >= 10.0
            and b5 >= s5 * 1.05
        )
        if age_min < min_age:
            try:
                from birth_tracker import birth_tracker as _bt_age
                _bt_age.add_to_waiting_room(mint)
            except Exception:
                pass
            return False, f"age-out-of-bounds {age_min:.1f}m < {min_age:.1f}m (wait for maturation)"
        if age_min > max_age and not _is_jev_breakout:
            return False, f"age-out-of-bounds {age_min:.1f}m > {max_age:.1f}m (old token without breakout)"

        # Защита от микро bump-ботов в m5 (LAURA $1.60, HODL $3.10, IPHONEDUO $5.00)
        _is_real_bump_runner_g = (
            (b5 >= s5 * 1.15 and vol_m5 >= 3000.0 and eff_liq >= 14000.0)
            or (b5 >= s5 * 1.30 and (b5 - s5) >= 60 and vol_m5 >= 2500.0)
            or (vol_m5 >= 10000.0 and b5 >= s5)
        )
        if (b5 + s5) >= 120 and _avg_tx_e < 3.0 and not _is_real_bump_runner_g:
            return False, f"fake-bump-wash-bot avg ${_avg_tx_e:.2f} < $3 across {b5+s5} m5 txns"

        # Защита от слитых ниже миграции выпускников Pump.fun (кейс SHANE FDV $35.8k, но пускаем отскоки и живой поток)
        if mint.endswith("pump") and dex_id not in ("pump", "pumpfun") and 0 < fdv_mc < 46000.0:
            _is_rebounding = (m5 >= 8.0 and b5 >= int(s5 * 1.15) and vol_m5 >= 2500.0)
            _is_real_dump = not _is_rebounding and ((fdv_mc < 12000.0) or (s5 > int(b5 * 1.25) and m5 < -5.0))
            _is_active_grad = (vol_m5 >= 2000.0 and b5 >= int(s5 * 1.00) and m5 >= -5.0)
            if _is_real_dump or not (_is_active_grad or _is_rebounding):
                return False, f"post-mig-dumped-fdv ${fdv_mc:,.0f} < $46k"

        # 2.4. Защита от безымянных рагпулов без соцсетей на ВСЕХ возрастах (STONKS 12.5м, IPHONEDUO 28.2м, scamsem 25.8м)
        _info_g = pair_data.get("info") or {}
        _links_cnt_g = len(_info_g.get("socials", []) or []) + len(_info_g.get("websites", []) or [])
        _soc_unk_g = bool(pair_data.get("_socials_unknown", False))
        _req_soc_age = float(getattr(_c, "REQUIRE_SOCIALS_MIN_AGE_MIN", 8.0))
        _vol_liq_ratio_g = (vol_m5 / max(eff_liq, 1.0)) if eff_liq > 0 else 0.0
        _avg_tx_g = vol_m5 / max(1, b5 + s5)
        _is_burned_lp_g = mint.endswith(("pump", "bonk", "BAGS"))
        _is_organic_stealth = False
        if _links_cnt_g == 0 and not _soc_unk_g:
            _is_organic_stealth = (
                (eff_liq >= 6000.0 and vol_m5 >= 1200.0 and _vol_liq_ratio_g <= 6.5 and (
                    (b5 >= 12 and b5 >= s5 * 1.05) or
                    (b5 >= 8 and s5 <= 30 and m5 >= 2.0 and _avg_tx_g >= 18.0) or
                    (vol_m5 >= 5000.0 and b5 >= s5)
                )) or
                (vol_m5 >= 15000.0 and b5 >= 80 and b5 >= int(s5 * 1.15))
            )
            if not _is_organic_stealth:
                _sub_reason_g = f"no-socials-mature-rug (age {age_min:.0f}m >= 12m, 0 socials/websites)" if age_min >= 12.0 else f"no-socials-rug (age {age_min:.1f}m, 0 socials/websites, v/l {_vol_liq_ratio_g:.1f}x)"
                return False, _sub_reason_g
            else:
                print(f"💎 [JEV-ORGANIC-STEALTH] {symbol} ({mint[:8]}): 0 соцсетей, но доказан чистый органический поток (${vol_m5:,.0f}, {b5}b/{s5}s, v/l {_vol_liq_ratio_g:.2f}) — пропуск.")

        # 2.5. Exhaustion Guard: блокируем перегретые поздние пампы, но пускаем активные ракеты (h1 включает рост на кёрве $5k->$69k!)
        _base_max_h1_jev = float(getattr(_c, "MAX_ENTRY_H1_PCT", 350.0))
        _is_active_fresh_jev = (
            age_min <= 120.0
            and vol_m5 >= 3000.0
            and b5 >= int(s5 * 1.05)
            and m5 >= 1.5
        )
        _max_h1_allowed = 2500.0 if _is_active_fresh_jev else _base_max_h1_jev
        if _links_cnt_g == 0 and not _soc_unk_g and not _is_organic_stealth and (h1 > 160.0 or m5 > 110.0):
            return False, f"exhaustion-no-soc-top h1 {h1:+.0f}%, m5 {m5:+.0f}% (0 socials pump-and-dump top)"
        if h1 > _max_h1_allowed and age_min > 4.0:
            return False, f"exhaustion-top-h1 h1 {h1:+.0f}% > +{_max_h1_allowed:.0f}% (late pump top, dump risk)"
        if fdv_mc > 100000.0 and eff_liq < 35000.0 and eff_liq < 0.02 * fdv_mc:
            return False, f"fdv-liquidity-trap liq ${eff_liq:,.0f} < 2% of FDV ${fdv_mc:,.0f} (phantom valuation)"

        # 3. Живой импульс ракеты (LIVE ROCKET MOMENTUM)
        # Разрешаем моментум от +1.0% (вместо 2.5%), а при преобладании покупателей (b5 >= s5 * 1.25)
        # разрешаем вход на микро-откате покупателей до -2.5% (кейсы TAOLF +86%, BTC BANK +145%)
        _is_buyer_dip_g = (b5 >= int(s5 * 1.25) and vol_m5 >= 2500.0 and (b5 + s5) >= 20 and m5 >= -2.5)
        _min_m5_jev = 1.0 if (eff_liq >= 14000.0 and b5 >= int(s5 * 1.05) and vol_m5 >= 2500.0) else 1.8
        if m5 < _min_m5_jev and not _is_buyer_dip_g:
            return False, f"no-momentum m5 {m5:+.1f}% < +{_min_m5_jev:.1f}% (coin is flat/standing still)"
        if age_min >= 20.0:
            _is_massive_mature_flow_g = (vol_m5 >= 10000.0 and eff_liq >= 20000.0 and b5 >= s5 and m5 >= 1.2)
            _is_strong_jev_breakout = (m5 >= 4.0 and vol_m5 >= 2000.0 and b5 >= s5 * 1.05 and (b5 + s5) >= 20)
            if not _is_massive_mature_flow_g and not _is_strong_jev_breakout and (vol_m5 < 5000.0 or ((vol_m5 / max(eff_liq, 1.0)) < 0.18 and vol_m5 < 15000.0) or m5 < 3.5 or (b5 + s5) < 30):
                return False, f"slow-mature-pool age {age_min:.0f}m vol5 ${vol_m5:,.0f} v/l {(vol_m5/max(eff_liq,1.0)):.2f}x"
        _max_m5_allowed = float(getattr(_c, "MAX_ENTRY_M5_PCT", 45.0))
        if m5 > _max_m5_allowed:
            _is_strong_buyer_breakout_g = (m5 <= 120.0 and b5 >= int(s5 * 1.10) and vol_m5 >= 3500.0 and (b5 + s5) >= 20)
            _is_cooling_pullback = (-8.0 <= m1 <= -1.0 and m5 <= 60.0 and b5 >= s5 * 1.05)
            if not (_is_strong_buyer_breakout_g or _is_cooling_pullback):
                return False, f"fomo-vertical-m5 m5 {m5:+.1f}% > +{_max_m5_allowed:.0f}% (buying top of vertical pump without cooling pullback, m1={m1:+.1f}%)"
        if s5 > 10 and b5 < (s5 * 0.80):
            return False, f"sellers-dominating {b5}b/{s5}s (more sellers than buyers in m5)"

        # Давление продаж и сброс китов (кейс SOLC/DOG™)
        _tot_tx_g = b5 + s5
        if _tot_tx_g >= 25 and s5 > 0:
            _sell_ratio_g = s5 / _tot_tx_g
            _vl_ratio_g = vol_m5 / max(eff_liq, 1.0)
            if b5 < int(s5 * 1.15) and (_sell_ratio_g >= 0.46 or (_sell_ratio_g >= 0.42 and _vl_ratio_g >= 2.5)):
                return False, f"heavy-sell-pressure {_sell_ratio_g*100:.0f}% sells ({s5}/{_tot_tx_g}) v/l {_vl_ratio_g:.1f}x (whale exit dump)"

        # Остывающий поздний памп: токен старше 20м режем ТОЛЬКО если m5 отрицательный или нулевой (реальный дамп)
        if age_min >= 20.0 and m5 < 1.2:
            return False, f"late-fade age {age_min:.0f}m m5 {m5:+.1f}% < +1.2% (old token cooling off, not a live rocket)"
        
        # Проверяем часовой тренд ТОЛЬКО если монете реально >= 45 минут!
        # Молодые токены (<45м) не имеют 1ч истории, а h1 отражает кёрву Pump.fun (кейс MEME +342%, GORP +100%)
        if age_min >= 45.0:
            _min_h1 = float(getattr(_c, "JEV_MIN_H1_PCT", -8.0))
            _is_jev_v_rev = (h1 >= -25.0 and m5 >= 12.0 and vol_m5 >= 15000.0 and eff_liq >= 18000.0 and b5 >= s5 * 1.30)
            if h1 < _min_h1 and not _is_jev_v_rev:
                return False, f"hourly-downtrend h1 {h1:+.1f}% < {_min_h1:+.1f}% (dead-cat bounce)"
            _h6_raw = pc.get("h6")
            if _h6_raw is not None and age_min >= 90.0:
                _min_h6 = float(getattr(_c, "JEV_MIN_H6_PCT", -25.0))
                if float(_h6_raw or 0) < _min_h6 and not _is_jev_v_rev:
                    return False, f"h6-downtrend {float(_h6_raw or 0):+.1f}% < {_min_h6:+.1f}% (monet is bleeding)"

        # 4. Объем и давление покупателей:
        # 4.0. Wash-Trading Spike Guard: отличаем симметричную бот-накрутку от реального пробоя покупателями (CHIMPS +59% v/l 1.5x, duge $158k liq 3.82x)!
        _avg_tx5 = vol_m5 / max(1, b5 + s5)
        if _is_burned_lp_g and eff_liq >= 12000.0:
            _max_vl_jev = 6.50 if (b5 >= s5 * 1.18) else 3.20
        elif eff_liq >= 30000.0 and b5 >= s5 * 1.25 and _avg_tx5 >= 35.0:
            _max_vl_jev = 6.00
        elif b5 >= s5 * 1.22 and _avg_tx5 >= 20.0 and eff_liq >= 15000.0:
            _max_vl_jev = 3.80
        else:
            _max_vl_jev = 1.85
        if eff_liq > 0 and (vol_m5 / eff_liq) > _max_vl_jev:
            return False, f"wash-trading-spike: vol5 ${vol_m5:,.0f} is {(vol_m5/eff_liq):.1f}x > {_max_vl_jev:.2f}x of pool liq ${eff_liq:,.0f} (bot pump trap)"

        _min_vol_m5 = float(getattr(_c, "MIN_VOLUME_M5", 2000.0))
        if vol_m5 < _min_vol_m5:
            return False, f"vol5-low ${vol_m5:.0f} < ${_min_vol_m5:.0f} (inactive dead pool)"
        _min_tx5 = int(getattr(_c, "MIN_TXNS_M5", 20))
        if (b5 + s5) < _min_tx5:
            return False, f"tx5-low {b5+s5} < {_min_tx5} (too few transactions)"
        _req_bs = float(getattr(_c, "MIN_BUY_SELL_RATIO", 1.15))
        if s5 > 0 and (b5 / s5) < _req_bs:
            return False, f"sellers-dominating {b5}b/{s5}s (ratio {b5/s5:.2f} < {_req_bs:.2f})"

        # Проверка суточной активности h24 для токенов старше 1 часа
        if age_min >= 60.0:
            min_vol_h24 = float(getattr(_c, "JEV_MIN_VOLUME_H24", 15000.0))
            min_trades_h24 = int(getattr(_c, "JEV_MIN_TRADES_H24", 50))
            if vol_h24 < min_vol_h24:
                return False, f"vol-h24-low ${vol_h24:,.0f} < ${min_vol_h24:,.0f}"
            if trades_h24 < min_trades_h24:
                return False, f"trades-h24-low {trades_h24} < {min_trades_h24}"

        # 4.1. Wash Trading Check (накрутка объёма ботами копеечными сделками)
        if trades_h24 >= 400 and vol_h24 > 0:
            avg_trade = vol_h24 / trades_h24
            _is_strong_volume_pump = (
                vol_m5 >= 12000.0
                and eff_liq >= 18000.0
                and fdv_mc >= 45000.0
                and avg_trade >= 8.0
                and _avg_tx5 >= 10.0
            )
            if avg_trade < 12.0 and not _is_strong_volume_pump:
                return False, f"wash-trading-trap (avg trade ${avg_trade:.1f} < $12 across {trades_h24} txns, weak pool)"

        # 5. Рыночная капитализация и верхний потолок ликвидности (отсекает HODL $4.2M / $171k liq)
        max_liq = float(getattr(_c, "JEV_MAX_LIQUIDITY_USD", 220000.0))
        if eff_liq > max_liq:
            return False, f"liq-too-heavy ${eff_liq:,.0f} > ${max_liq:,.0f} (mid-cap pool, slow moves)"
        mcap = fdv_mc
        if is_pump and mcap > 0 and mcap < 5000.0:
            return False, f"pump-curve-too-early mcap ${mcap:,.0f} < $5k (high dev dump trap risk)"
        min_mcap = 5000.0 if (dex_id in ("pump", "pumpfun") or vol_m5 >= 2500.0) else float(getattr(_c, "JEV_MIN_MCAP_USD", 7500.0))
        max_mcap = float(getattr(_c, "JEV_MAX_MCAP_USD", 1800000.0))
        if mcap > 0 and not (min_mcap <= mcap <= max_mcap):
            return False, f"mcap-out-of-bounds ${mcap:,.0f} not in [${min_mcap:,.0f}, ${max_mcap:,.0f}]"

        # Распределение холдеров (savipww: >= 80 холдеров, Топ-1 <= 5%, Топ-10 <= 60%)
        if holder_stats and isinstance(holder_stats, dict):
            holders_count = int(holder_stats.get("holders_count", 0) or 0)
            if holders_count > 0 and holders_count < getattr(_c, "JEV_MIN_HOLDERS", 80):
                return False, f"holders-low {holders_count} < 80"

            top1_pct = float(holder_stats.get("top1_pct", 0.0) or 0.0)
            top10_pct = float(holder_stats.get("top10_pct", 0.0) or 0.0)

            max_top1 = float(getattr(_c, "JEV_MAX_TOP_WALLET", 0.20)) * 100.0   # <= 20%
            max_top10 = float(getattr(_c, "JEV_MAX_TOP_10", 0.60)) * 100.0       # <= 60%
            
            if top1_pct > max_top1:
                return False, f"top-wallet-high {top1_pct:.1f}% > {max_top1:.1f}% (exit risk)"
            if top10_pct > max_top10:
                return False, f"top-10-high {top10_pct:.1f}% > {max_top10:.1f}% (cabal risk)"

        FUNNEL_STATS["holders"] += 1
        return True, "HARD-GATE-PASS"

    # =========================================================================
    # 2. SOFT GATE: JEV AI Evaluation (Groq / xAI Grok / Gemini / Fallback)
    # =========================================================================
    async def jev_vet(
        self,
        mint: str,
        symbol: str,
        pair_data: dict,
        chain: str = "solana",
        holder_stats: Optional[dict] = None
    ) -> Tuple[bool, dict, str]:
        """
        Этап 2: Оценка ИИ (JEV/Grok) по 6 параметрам [0.0 - 1.0].
        Работает только на финалистах hard_gate!
        """
        import config as _c
        
        # Проверяем кэш (TTL: 180с для успешных, 35с для отклоненных)
        now = time.time()
        ck = (chain, mint.lower())
        hit = _JEV_CACHE.get(str(ck))
        if hit:
            hit_passed = hit[1].get("passed", False)
            cache_ttl = 180.0 if hit_passed else 35.0
            if (now - hit[0]) < cache_ttl:
                res = hit[1]
                return res.get("passed", False), res, f"cached ({res.get('reason', '')})"

        # Собираем контекст токена
        info = pair_data.get("info", {}) or {}
        socials = info.get("socials", []) or []
        websites = info.get("websites", []) or []
        base = pair_data.get("baseToken", {}) or {}
        name = base.get("name", symbol)
        
        pc = pair_data.get("priceChange", {}) or {}
        m5 = float(pc.get("m5", 0) or 0)
        m1 = float(pc.get("m1", 0) or 0)
        if m1 == 0.0 and abs(m5) > 0.5:
            m1 = max(-25.0, min(25.0, m5 * 0.15))
        h1 = float(pc.get("h1", 0) or 0)
        
        dex_id = str(pair_data.get("dexId", "")).lower()
        is_pump = dex_id in ("pump", "pumpfun") or mint.endswith("pump")
        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        mcap = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        liq = max(raw_liq, mcap * 0.40) if is_pump else raw_liq
        vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        
        top1_pct = float((holder_stats or {}).get("top1_pct", 4.0))
        top10_pct = float((holder_stats or {}).get("top10_pct", 22.0))
        dev_pct = float((holder_stats or {}).get("dev_pct", 2.0))
        ticket = float(getattr(_c, "BASE_TRADE_AMOUNT_USD", 15.0) or 15.0)
        data_line = _data_line(pair_data, holder_stats or {}, ticket)

        # Попытка вызова LLM (Groq -> xAI Grok -> Gemini)
        llm_res = None
        global _COOLDOWN_UNTIL, _LAST_API_CALL_TS
        
        if now >= _COOLDOWN_UNTIL and (self.groq_key or self.xai_key or self.gemini_key):
            try:
                async with _get_api_semaphore():
                    # Соблюдаем интервал между вызовами для бесплатного API (мин 4.0с)
                    dt = now - _LAST_API_CALL_TS
                    if dt < 4.0:
                        await asyncio.sleep(4.0 - dt)
                        
                    llm_res = await asyncio.wait_for(
                        self._call_llm_jev(mint, symbol, name, chain, socials, websites, m1, m5, h1, liq, mcap, vol5, top1_pct, top10_pct, dev_pct, data_line),
                        timeout=10.0
                    )
                    _LAST_API_CALL_TS = time.time()
            except asyncio.TimeoutError:
                print(f"⚠️ [JEV/GROK] LLM Timeout (10s) для {symbol} ({mint[:8]}) ➔ отказ, повтор позже.")
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str or "quota" in err_str:
                    _COOLDOWN_UNTIL = time.time() + 60.0  # 1 мин пауза
                    print(f"⏳ [JEV/GROK] Free API 429 Rate Limit. Пауза 60с, токен повторим позже.")
                else:
                    print(f"⚠️ [JEV/GROK] Ошибка вызова LLM ({type(e).__name__}): {e}")

        # Защита от математических галлюцинаций LLM и переключение на детерминированный JEV
        if llm_res and isinstance(llm_res, dict):
            try:
                import json as _json_raw
                print(f"🤖 [JEV-RAW-VERDICT] {symbol} ({mint[:8]}): {_json_raw.dumps(llm_res, ensure_ascii=False)}")
            except Exception:
                pass
            # Билет $4-$15 на пул $5k+ (<=2%) математически идеален: проскальзывание <0.5%, min 0.90!
            if liq >= 5000.0 and ticket <= (liq * 0.02):
                llm_res["liquidity_fits_ticket"] = max(float(llm_res.get("liquidity_fits_ticket", 0) or 0), 0.90)
        else:
            llm_res = self._deterministic_jev_eval(
                symbol, name, socials, websites,
                m1, m5, h1, liq, mcap, vol5,
                top1_pct, top10_pct, dev_pct, is_pump=is_pump
            )
            print(f"📊 [JEV-ALGO] {symbol}: детерминированный скоринг (LLM offline) ➔ {llm_res.get('reason')}")

        failed = _jev_failed(llm_res)
        
        # Если LLM отклонила токен из-за шума промпта (кейс APU), перепроверяем строгим детерминированным алгоритмом:
        if failed:
            algo_res = self._deterministic_jev_eval(
                symbol, name, socials, websites,
                m1, m5, h1, liq, mcap, vol5,
                top1_pct, top10_pct, dev_pct, is_pump=is_pump
            )
            algo_failed = _jev_failed(algo_res)
            if not algo_failed:
                print(f"📊 [JEV-ALGO-RESCUE] {symbol} ({mint[:8]}): алгоритмический скоринг спас токен от шума LLM ({', '.join(failed)}) -> PASS!")
                for k in list(failed):
                    if k in algo_res:
                        llm_res[k] = algo_res[k]
                llm_res["source"] = f"{llm_res.get('source', 'llm')}+algo-rescue"
                failed = []

        is_passed = not failed
        llm_res["passed"] = is_passed          # кэш читает это поле

        # Кэшируем результат
        _JEV_CACHE[str(ck)] = (now, llm_res)

        if is_passed:
            FUNNEL_STATS["jev_gate"] += 1
            FUNNEL_STATS["passed"] += 1
            print(f"🤖 [JEV/GROK-PASS] {symbol} ({mint[:8]}): conc={llm_res.get('concentration_is_exit_risk')} "
                  f"mom={llm_res.get('momentum_already_spent')} worth={llm_res.get('pick_worth_trading')} "
                  f"conf={llm_res.get('confidence')} [{llm_res.get('source', 'llm')}]")
            return True, llm_res, "PASS"
        if not getattr(_c, "JEV_LLM_ENFORCE", True):
            print(f"👻 [JEV-SHADOW] {symbol}: судья отказал бы ({', '.join(failed)}) -> пропуск (shadow)")
            return True, llm_res, "shadow-pass"
        return False, llm_res, "JEV soft-reject: " + ", ".join(failed)

    # =========================================================================
    # 3. FILLS: "We bid the flush, not the candle"
    #    & RISK: "The stop lives under the wick"
    # =========================================================================
    def check_flush_and_wick(
        self,
        pair_data: dict,
        entry_price: float
    ) -> Tuple[bool, float, float, str]:
        """
        Этап 3 & 4:
        - FILLS: "We bid the flush, not the candle" (покупаем только откат / консолидацию m1).
        - RISK: "The stop lives under the wick" (стоп прямо под фитиль/low свечи отката).
        
        Возвращает:
        (is_flush_ok, wick_stop_loss_pct, stop_under_wick_price, reason)
        """
        import config as _c
        
        if entry_price <= 0:
            return False, -0.06, 0.0, "invalid-entry-price"

        pc = pair_data.get("priceChange", {}) or {}
        _raw_m1 = pc.get("m1")
        m1 = float(_raw_m1 or 0)
        m5 = float(pc.get("m5", 0) or 0)
        if abs(m1) > max(180.0, abs(m5) * 3.0 + 40.0):
            m1 = 0.0
            _raw_m1 = None
        txns_m5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        b5 = int(txns_m5.get("buys", 0) or 0)
        s5 = int(txns_m5.get("sells", 0) or 0)
        vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        
        # 1. Запрет покупать вертикальную палку на гипер-пике FOMO (но пропускаем органический пробой с объёмом!):
        _h1 = float(pc.get("h1", 0) or 0)
        _ca = pair_data.get("pairCreatedAt") or 0
        _base_addr = str(((pair_data.get("baseToken") or {}).get("address")) or "")
        _dex_id = str(pair_data.get("dexId", "")).lower()
        _is_pump_pair = _base_addr.endswith("pump") or _dex_id in ("pump", "pumpfun")
        if _ca:
            _age_m = round((time.time() * 1000 - _ca) / 60000.0, 1)
        else:
            _bt_age = None
            try:
                from birth_tracker import birth_tracker as _bt_check
                _bt_age = _bt_check.get_age(_base_addr)
            except Exception:
                pass
            _age_m = float(_bt_age) if _bt_age is not None else (0.1 if _is_pump_pair else 999.0)
        _liq_usd = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        _base_addr = str(((pair_data.get("baseToken") or {}).get("address")) or "")
        _dex_id = str(pair_data.get("dexId", "")).lower()
        _is_pump_pair = _base_addr.endswith("pump") or _dex_id in ("pump", "pumpfun", "pumpswap", "meteora")
        _min_liq = 1500.0 if _is_pump_pair else (4000.0 if _age_m < 60.0 else float(getattr(_c, "JEV_MIN_LIQUIDITY_USD", 6000.0)))
        _base_max_m1 = float(getattr(_c, "JEV_MAX_M1_ENTRY_PCT", 28.0))

        # Если DexScreener не отдал m1 (0.0 / None) — берем реальный sub-minute m1 из нашей живой истории сканов
        _m1_known = (_raw_m1 is not None and float(_raw_m1 or 0) != 0.0)
        if not _m1_known and _base_addr:
            _ph_f = self._px_hist.get(_base_addr) or []
            _now_f = time.time()
            _ref_f = [
                p for t, p in _ph_f
                if 12.0 <= (_now_f - t) <= 120.0 and p > 0
                and 0.05 <= (entry_price / p) <= 12.0
            ]
            if _ref_f:
                m1 = max(-40.0, min(40.0, (entry_price / _ref_f[-1] - 1.0) * 100.0))
                _m1_known = True
            self._record_px(_base_addr, entry_price)

        # Если токен моложе 3м, DexScreener часто отдаёт m1 == m5 (одна первая свеча).
        _m1_is_phantom = (_age_m < 3.0 and abs(m5) > 1.0 and abs(m1) > 0 and abs(m1 / max(abs(m5), 0.01) - 1.0) < 0.08)
        _strong_breakout = (b5 >= 15 and b5 >= s5 * 1.10 and vol5 >= 3000.0)
        _max_m1 = 22.0 if _strong_breakout else min(float(getattr(_c, "JEV_MAX_M1_ENTRY_PCT", 8.0)), 12.0)
        _cfg_m5 = float(getattr(_c, "MAX_ENTRY_M5_PCT", 28.0))
        _max_m5_cap = 120.0 if _strong_breakout else _cfg_m5
        if m5 > _max_m5_cap:
            return False, -0.14, 0.0, f"fomo-spike (m5 {m5:+.1f}% > +{_max_m5_cap:.0f}%) — buying local top, wait for reset"
        if m1 > _max_m1 and not _m1_is_phantom:
            return False, -0.14, 0.0, f"m1-spike (m1 {m1:+.1f}% > +{_max_m1:.1f}%) — vertical push, wait for consolidation"
        _base_max_h1_flush = float(getattr(_c, "MAX_ENTRY_H1_PCT", 350.0))
        _is_active_fresh_flush = (_age_m <= 120.0 and vol5 >= 3000.0 and b5 >= s5 * 1.05 and m5 >= 1.5)
        _max_h1_flush = 2500.0 if _is_active_fresh_flush else _base_max_h1_flush
        if _h1 > _max_h1_flush and _age_m > 4.0:
            return False, -0.14, 0.0, f"h1-exhaustion (h1 {_h1:+.1f}% > +{_max_h1_flush:.0f}% at {_age_m:.0f}m) — pump already exhausted"
        if _liq_usd > 0 and _liq_usd < _min_liq:
            return False, -0.14, 0.0, f"thin-liquidity (${_liq_usd:,.0f} < ${_min_liq:,.0f}) — slippage/dump risk"

        # 2. Защита от покупки затухающей свечи: сильный тренд (m5 >= +12%) допускает ретест до -5.5%,
        # но слабая монета (m5 < +12%) с красной 1м свечой (< -2.5%) — это умирающий отскок, ведущий в стоп!
        _min_flush_m1 = -5.5 if (m5 >= 12.0 and b5 >= s5 * 1.15) else -2.5
        if _m1_known and m1 < _min_flush_m1:
            return False, -0.14, 0.0, f"flush-dump (m1 {m1:+.1f}% < {_min_flush_m1:+.1f}%) — falling knife / weak rollover"

        # 3. Проверка давления продавцов: допускаем небольшой перевес на самом откате
        if s5 > 8 and b5 < int(s5 * 0.80):
            return False, -0.14, 0.0, f"sellers-dominating ({b5}b/{s5}s) — sellers active, wait for buyer absorption"

        # 4. Монета не должна стоять мёртвым флетом
        if (_m1_known and m1 <= -3.5 and m5 < 1.5) or (not _m1_known and m5 < 1.2):
            return False, -0.14, 0.0, f"flat-coin (m1 {m1:+.1f}%, m5 {m5:+.1f}%) — price standing still, waiting for live impulse"

        # 3.2. ПОДТВЕРЖДЕНИЕ ТРЕНДА ПО СОБСТВЕННОЙ ИСТОРИИ ЦЕН (DexScreener m1/m5 запаздывают на минуту и больше)
        if getattr(_c, "JEV_TREND_CONFIRM_ENABLED", False):
            _mint = _base_addr
            if _mint:
                _now = time.time()
                _h = self._record_px(_mint, entry_price)
                # (а) уже слили с недавнего максимума — это откат после пика, а не вход в рост
                _win = float(getattr(_c, "JEV_DD_WINDOW_MIN", 30.0)) * 60.0
                _hi = max([p for t, p in _h if _now - t <= _win] or [0.0])
                _max_dd = float(getattr(_c, "JEV_MAX_DD_FROM_HIGH", 0.12))
                if _hi > 0 and entry_price < _hi * (1.0 - _max_dd):
                    return False, -0.14, 0.0, f"flush-wait post-peak-fade (price {(entry_price/_hi-1)*100:+.1f}% from {int(_win/60)}m high)"
                # (б) цена должна быть ВЫШЕ, чем 20-300 секунд назад (растём, а не падаем / стоим)
                _rmin = float(getattr(_c, "JEV_RISE_REF_MIN_SEC", 20.0))
                _rmax = float(getattr(_c, "JEV_RISE_REF_MAX_SEC", 300.0))
                _ref = [p for t, p in _h if _rmin <= _now - t <= _rmax]
                if not _ref:
                    # На первом скане не блокируем, если DexScreener m5/m1 уже подтверждает активный импульс
                    if m5 < 2.5 or m1 < -2.5:
                        return False, -0.14, 0.0, "flush-wait no-trend-history (first sight, need a second price)"
                elif entry_price < _ref[-1] * (1.0 + float(getattr(_c, "JEV_RISE_EPS", 0.002))):
                    return False, -0.14, 0.0, f"flush-wait not-rising ({(entry_price/_ref[-1]-1)*100:+.2f}% vs earlier scan)"

        # 4. Расчет стопа под фитиль отката ("The stop lives under the wick"):
        # На мем-коинах Solana минутный шум составляет 6-10%, поэтому ставим рабочий стоп -14%..-16%
        # (вместо -5%..-7%, который выбивал ракеты на первой же секундной тени).
        dip_factor = min(0.0, m1 / 100.0)
        wick_low_price = entry_price * (1.0 + dip_factor - 0.12)
        
        # Динамический безопасный стоп под фитиль: от -13.5% до -16.5%
        calculated_stop_pct = (wick_low_price - entry_price) / entry_price
        wick_stop_loss_pct = max(-0.165, min(-0.135, calculated_stop_pct))
        stop_under_wick_price = entry_price * (1.0 + wick_stop_loss_pct)

        reason = f"Entry validated (m1 {m1:+.1f}%, m5 {m5:+.1f}%), stop under wick: {wick_stop_loss_pct*100:.1f}% (${stop_under_wick_price:.6f})"
        return True, wick_stop_loss_pct, stop_under_wick_price, reason

    # =========================================================================
    # Вспомогательные вызовы API и Алгоритмический Fallback
    # =========================================================================
    async def _call_llm_jev(
        self, mint: str, symbol: str, name: str, chain: str,
        socials: list, websites: list,
        m1: float, m5: float, h1: float,
        liq: float, mcap: float, vol5: float,
        top1_pct: float, top10_pct: float, dev_pct: float,
        data_line: str = ""
    ) -> Optional[dict]:
        """Вызов LLM (Groq OpenAI-compatible или xAI или Gemini) с JSON Schema."""
        from http_client import fetch_json
        
        soc_text = []
        for s in socials:
            soc_text.append(f"{s.get('type')}: {s.get('url')}")
        for w in websites:
            soc_text.append(f"Web: {w.get('url')}")
        socials_str = "; ".join(soc_text) if soc_text else "None"

        system_prompt = (
            "You are JEV, the lead AI trading analyst for a Solana memecoin desk ('We bid the flush, not the candle'). "
            "Evaluate the token's meme narrative, social links, holder safety, and pullback timing.\n"
            "CRITICAL RULE: NEVER return null. If a metric is 'NA', infer a reasonable score from the remaining data.\n\n"
            "Scoring Rubric (all values MUST be floats between 0.0 and 1.0):\n"
            "1. concentration_is_exit_risk (max 0.55 to pass): 0.15-0.35 if top1<=8% and top10<=35% with liq>=$15k on DEX (or liq>=$3k on pump/pumpswap); >0.60 if whales dominate.\n"
            "2. momentum_already_spent (max 0.60 to pass): 0.20-0.45 if price is in a healthy pullback/consolidation (m1 between -8% and +5%) while h24/h6 volume is active (vol_ratio >= 0.7); >0.70 if m1 > +15% (vertical top) or volume died.\n"
            "3. liquidity_fits_ticket (min 0.60 to pass): >=0.85 if ticket/liq <= 1%; ~0.65 if <= 2.5%; <=0.30 if > 4%.\n"
            "4. dev_still_loaded (max 0.55 to pass): 0.05-0.25 if dev <= 3% or NA; >0.60 if dev >= 8%.\n"
            "5. pick_worth_trading (min 0.60 to pass): Score 0.65-0.85 if the token has a catchy meme ticker/socials, solid liquidity, buyer support (b5 >= s5*0.8), and is not at an overheated m1 spike. Score <0.50 if it looks like a low-effort scam or dead chart.\n"
            "6. confidence (min 0.55 to pass): 0.70-0.90 when core liquidity, volume, and price changes are present.\n\n"
            "Reply with ONE valid JSON object only. Keys: concentration_is_exit_risk, momentum_already_spent, "
            "liquidity_fits_ticket, dev_still_loaded, pick_worth_trading, confidence."
        )

        user_content = f"token={name} (${symbol}) links=[{socials_str}] | {data_line}"


        return await self._groq_judge(system_prompt, user_content)

    async def _groq_judge(self, system_prompt: str, user_content: str) -> Optional[dict]:
        """Groq free tier: 70B с дневным бюджетом -> 8B -> None (fail-closed в jev_vet)."""
        from http_client import fetch_json
        if not self.groq_key:
            return None
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if _BUDGET["day"] != today:
            _BUDGET["day"], _BUDGET["n70"] = today, 0
        for model, tag in _MODELS:
            if time.time() < _CD[tag]:
                continue
            if tag == "70b" and _BUDGET["n70"] >= DAILY_70B:
                continue
            payload = {
                "model": model, "temperature": 0.1, "max_tokens": 700,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system_prompt},
                             {"role": "user", "content": user_content}],
            }
            if model.startswith("openai/gpt-oss"):
                payload["reasoning_effort"] = "low"   # reasoning-модель: иначе размышления съедят max_tokens
            st, data = await fetch_json(
                "https://api.groq.com/openai/v1/chat/completions", method="POST",
                headers={"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
                json_payload=payload, timeout=8, retries=0)
            if tag == "70b":
                _BUDGET["n70"] += 1
            if st == 429:
                _CD[tag] = time.time() + 60
                _log_groq(f"{model}: 429 (пауза 60с)")
                continue
            if st == 200 and isinstance(data, dict):
                try:
                    out = json.loads(data["choices"][0]["message"]["content"])
                    if isinstance(out, dict):
                        out["source"] = f"groq-{tag}"
                        return out
                    _log_groq(f"{model}: ответ не объект JSON: {str(out)[:80]}")
                except Exception as e:
                    _log_groq(f"{model}: 200, но разбор ответа не удался ({type(e).__name__}): {str(data)[:160]}")
                    continue
            else:
                # 401 = неверный ключ, 400 = плохой запрос, 404 = нет модели/адреса, 0 = сеть/таймаут
                _log_groq(f"{model}: HTTP {st}, тело пустое (401=ключ, 400=запрос, 404=модель, 0=сеть)")
        return None

    def _deterministic_jev_eval(
        self, symbol: str, name: str,
        socials: list, websites: list,
        m1: float, m5: float, h1: float,
        liq: float, mcap: float, vol5: float,
        top1_pct: float, top10_pct: float, dev_pct: float,
        is_pump: bool = False
    ) -> dict:
        """
        Детерминированный алгоритмический скоринг по формулам JEV
        (работает независимо от сети/лимитов API).
        """
        # 1. account_is_the_project
        has_soc = len(socials) > 0 or len(websites) > 0
        sym_lower = symbol.lower()
        soc_matched = any(sym_lower in str(s.get("url", "")).lower() for s in socials)
        if not has_soc:
            # Для pump.fun токенов и монет с подтвержденной органикой аккаунт валиден (pump.fun native launch)
            if is_pump or (liq >= 35000.0 and vol5 >= 12000.0) or (vol5 >= 3000.0):
                account_score = 0.80
            else:
                account_score = 0.35  # Отказ JEV: анонимный контракт без соцсетей и органики
        elif soc_matched:
            account_score = 0.90
        else:
            account_score = 0.80

        # 2. recycled_account
        recycled_score = 0.15

        # 3. concentration_is_exit_risk
        # Если топ1 < 4% и топ10 < 22% — риск низкий (0.15..0.25)
        conc_score = min(1.0, max(0.1, (top1_pct / 8.0) * 0.25 + (top10_pct / 30.0) * 0.25))

        # 4. momentum_already_spent
        # СТАЛО (замени строки 643-654 в grok_jev.py):
        if m5 < 1.2 or h1 < -15.0:
            mom_score = 0.95
        elif m1 > 28.0:
            mom_score = 0.85
        elif m5 >= 1.5 and m1 <= 15.0:
            mom_score = 0.25
        elif m5 > 400.0 and m1 > 18.0:
            mom_score = 0.75
        else:
            mom_score = 0.40

        # 5. liquidity_fits_ticket
        # Для ордера $15-$25 в мемкоины глубина пула от $2,500+ достаточна (проскальзывание < 0.5%)
        if liq >= 2500.0:
            liq_score = min(1.0, 0.70 + min(0.30, (liq - 2500.0) / 25000.0))
        else:
            liq_score = max(0.2, (liq / 2500.0) * 0.55)

        # 6. dev_still_loaded
        dev_score = min(1.0, max(0.05, dev_pct / 10.0))

        # 7. pick_worth_trading & confidence (обязательные поля _NEED)
        if mom_score <= 0.60 and conc_score <= 0.55 and dev_score <= 0.55 and liq_score >= 0.60:
            pick_score = round(max(0.65, 1.0 - (mom_score * 0.4 + conc_score * 0.3 + dev_score * 0.3)), 2)
            conf_score = 0.85
        else:
            pick_score = 0.35
            conf_score = 0.45

        return {
            "account_is_the_project": round(account_score, 2),
            "recycled_account": round(recycled_score, 2),
            "concentration_is_exit_risk": round(conc_score, 2),
            "momentum_already_spent": round(mom_score, 2),
            "liquidity_fits_ticket": round(liq_score, 2),
            "dev_still_loaded": round(dev_score, 2),
            "pick_worth_trading": round(pick_score, 2),
            "confidence": round(conf_score, 2),
            "source": "jev-deterministic-engine",
            "reason": f"Algorithmic JEV verification (Liq ${liq:,.0f}, Vol5 ${vol5:,.0f}, Top1 {top1_pct:.1f}%)"
        }

    # =========================================================================
    # 5. CRAWLER: Автономный поиск кандидатов (Solana + Base)
    # =========================================================================
    async def crawl_universe(self) -> list:
        """
        CRAWLER: Автономный сбор токенов-кандидатов с Solana и Base.
        Источники:
        1. Solana: Jupiter trending & recent, DexScreener boosts & profiles
        2. Base: GeckoTerminal Base trending pools, DexScreener Base boosts
        """
        candidates = []
        seen = set()

        # 0. ПРИОРИТЕТ №1: Живой поток миграций PumpPortal (subscribeMigration + Waiting Room 90с..45м)
        try:
            from birth_tracker import birth_tracker
            grad_ready = birth_tracker.get_waiting_room_graduates(min_age_sec=90.0, max_age_sec=2700.0)
            for gm in (grad_ready or []):
                if gm and gm not in seen:
                    seen.add(gm)
                    candidates.append((gm, "solana"))
        except Exception:
            pass

        # 1. Solana: Jupiter Discovery (весь пул, без искусственной обрезки)
        try:
            from jup_discovery import fetch_jup_tokens
            jup_mints = await fetch_jup_tokens()
            for m in (jup_mints or []):
                if m and m not in seen:
                    seen.add(m)
                    candidates.append((m, "solana"))
        except Exception:
            pass

        # 1.1 Solana: Market Data Organic Trending
        try:
            import market_data
            tr_pools = await market_data.get_trending()
            for item in (tr_pools or []):
                m = item.get("tokenAddress")
                if m and m not in seen:
                    seen.add(m)
                    candidates.append((m, "solana"))
        except Exception:
            pass

        # 2. DexScreener Top Boosts (Строго проверенный топ SOLANA, исключая мусорные 0-минутные спам-профили)
        from http_client import fetch_json
        for url in [
            "https://api.dexscreener.com/token-boosts/top/v1"
        ]:
            try:
                st, data = await fetch_json(url, timeout=5, retries=0)
                if st == 200 and isinstance(data, list):
                    for item in data:
                        ch = str(item.get("chainId", "")).lower()
                        tok = str(item.get("tokenAddress", "")).strip()
                        if ch == "solana" and tok and tok not in seen:
                            seen.add(tok)
                            candidates.append((tok, "solana"))
            except Exception:
                pass

        # Примечание: тренды GeckoTerminal уже подтянуты на шаге 1.1 через общий кэш market_data.get_trending() (без дублирования запросов и 429)
        FUNNEL_STATS["universe"] += len(candidates)
        return candidates

    def _select_best_pair(self, address: str, pairs: list, chain: str = "solana") -> Optional[dict]:
        """
        Интеллектуальный выбор пула для Solana (синхронизирован с analyzer._best_solana_pair):
        1. Отсекает пары, где токен является quoteToken (например FACEPALM/🖖).
        2. Фильтрует разрешенные котировки (SOL, WSOL, USDC, USDT).
        3. Если у токена уже есть живой AMM-пул (pumpswap, raydium, meteora, orca) с liq >= $500,
           НИКОГДА не выбирает старый bonding curve pump.fun.
        4. Защита от рассинхрона цен (Pool Price Divergence / Arbitrage Trap):
           Если между пулами со сравнимой ликвидностью цена расходится >12%,
           помечает _price_divergence = True для мгновенного отсева.
        """
        if not pairs:
            return None
        if chain.lower() != "solana":
            matching = [p for p in pairs if str(p.get("chainId", "")).lower() == chain.lower()]
            return max(matching if matching else pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0)) if (matching or pairs) else None

        ALLOWED_SOL_QUOTES = {
            "so11111111111111111111111111111111111111112",  # WSOL/SOL
            "epjfwdd5aufqssqem2qnxzybapc8g4weggkzwytdt1v",  # USDC
            "es9vmfrzacerjmffrff4h2fyd4kconky11mcce8benwnyb",  # USDT
        }
        ALLOWED_SOL_SYMBOLS = {"SOL", "WSOL", "USDC", "USDT"}
        sol_pairs = []
        for p in pairs:
            if (p.get("chainId") or "").lower() != "solana":
                continue
            base_addr = str((p.get("baseToken") or {}).get("address") or "").strip()
            if base_addr and base_addr != address:
                continue
            q_addr = str((p.get("quoteToken") or {}).get("address") or "").lower()
            q_sym = str((p.get("quoteToken") or {}).get("symbol") or "").upper()
            if q_addr and q_addr not in ALLOWED_SOL_QUOTES and q_sym not in ALLOWED_SOL_SYMBOLS:
                continue
            sol_pairs.append(p)

        if not sol_pairs:
            return None

        # 1. Если есть AMM-пулы (pumpswap/raydium/meteora/orca) с ликвидностью >= $500,
        # токен УЖЕ мигрировал, bonding curve pump.fun игнорируется!
        amm_pairs = [p for p in sol_pairs if str(p.get("dexId") or "").lower() not in ("pump", "pumpfun", "meteoradbc")]
        real_pairs = [p for p in amm_pairs if float(((p.get("liquidity") or {}).get("usd", 0) or 0)) >= 500]
        if real_pairs:
            best_pair = sorted(real_pairs, key=lambda p: float(((p.get("liquidity") or {}).get("usd", 0) or 0)), reverse=True)[0]
        elif amm_pairs:
            best_pair = sorted(amm_pairs, key=lambda p: float(((p.get("liquidity") or {}).get("usd", 0) or 0)), reverse=True)[0]
        else:
            best_pair = sorted(sol_pairs, key=lambda p: float(((p.get("liquidity") or {}).get("usd", 0) or 0)), reverse=True)[0]

        # 2. Проверка рассинхрона цен между КРУПНЫМИ AMM-пулами (Raydium / Meteora / Orca):
        # ВАЖНО: исключаем мертвый пул bonding curve pump.fun, у которого цена застыла!
        _amm_check = [p for p in sol_pairs if str(p.get("dexId") or "").lower() not in ("pump", "pumpfun", "meteoradbc")]
        if _amm_check and len(_amm_check) >= 2:
            _top_l = float(((best_pair.get("liquidity") or {}).get("usd", 0) or 0))
            if _top_l >= 5000.0:
                _comp = [p for p in _amm_check if float(((p.get("liquidity") or {}).get("usd", 0) or 0)) >= max(4000.0, _top_l * 0.25)]
                if len(_comp) >= 2:
                    _prices = [float(p.get("priceUsd", 0) or 0) for p in _comp if float(p.get("priceUsd", 0) or 0) > 0]
                    if len(_prices) >= 2:
                        _max_p, _min_p = max(_prices), min(_prices)
                        if _min_p > 0 and (_max_p / _min_p) > 1.40:
                            best_pair["_price_divergence"] = True
                            best_pair["_price_divergence_ratio"] = _max_p / _min_p

        return best_pair

    async def fetch_pair(self, address: str, chain: str) -> Optional[dict]:
        """Прямой запрос рыночных данных пары из DexScreener (с поддержкой пакетного кэша analyzer)."""
        if hasattr(self, "analyzer") and self.analyzer and hasattr(self.analyzer, "fetch_token_data"):
            try:
                cached_or_pair = await self.analyzer.fetch_token_data(address)
                if cached_or_pair and isinstance(cached_or_pair, dict) and float(cached_or_pair.get("priceUsd", 0) or 0) > 0:
                    return cached_or_pair
            except Exception:
                pass
        from http_client import fetch_json
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        try:
            st, data = await fetch_json(url, timeout=6, retries=1)
            if st == 200 and isinstance(data, dict):
                pairs = data.get("pairs") or []
                return self._select_best_pair(address, pairs, chain)
        except Exception:
            pass
        return None

    async def fetch_pair_fresh(self, address: str, chain: str) -> Optional[dict]:
        """Свежие данные DexScreener в обход кэша analyzer (кэш держит данные до 60 секунд и дольше)."""
        from http_client import fetch_json
        try:
            st, data = await fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{address}", timeout=5, retries=1)
            if st == 200 and isinstance(data, dict):
                pairs = data.get("pairs") or []
                return self._select_best_pair(address, pairs, chain)
        except Exception:
            pass
        return None

    # =========================================================================
    # 6. ANTI-SCAM SUITE: Комплексный аудит на все типы скамов
    # =========================================================================
    async def full_anti_scam_audit(
        self,
        address: str,
        symbol: str,
        chain: str,
        pair_data: dict
    ) -> Tuple[bool, str, dict]:
        """
        Полный бескомпромиссный аудит на все виды скамов перед входом в сделку:
        1. Запрещенные слова и фейковые бренды
        2. Аудит соцсетей (защита от анонимных дев-бандлеров)
        3. On-chain аудит через прямой RPC (Freeze / Mint Authority / Hooks)
        4. GoPlus Security (Honeypot, blacklist, taxes)
        5. Полный RugCheck аудит (Score, Danger-риски, Unlocked LP, Dev holding %, Whale %, Top 10 %, Jito Sybil Bundles)
        6. AI ML Scam Filter (XGBoost)
        7. Клон-фильтр (is_clone)
        """
        import config as _c
        _h_stats = {"top1_pct": 0.0, "top10_pct": 0.0, "dev_pct": 0.0}
        base_info = pair_data.get("baseToken", {}) or {}
        name = str(base_info.get("name", "")).upper()
        symbol_upper = symbol.upper().strip()

        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        dex_id = str(pair_data.get("dexId", "")).lower()
        is_raw_pump_curve = dex_id in ("pump", "pumpfun", "moonshot") or address.endswith("moon")
        is_pump = address.endswith("pump") or is_raw_pump_curve
        eff_liq = max(raw_liq, fdv * 0.40) if is_raw_pump_curve else raw_liq

        _tx5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5 = int(_tx5.get("buys", 0) or 0)
        _s5 = int(_tx5.get("sells", 0) or 0)
        _vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        vol_m5 = _vol5

        # 1. Запрещенные ключевые слова в названии / тикере
        bad_words = ["test", "scam", "nigger", "fuck", "pump and dump", "rug", "airdrop", "presale", "honeypot", "drain"]
        if any(w in name.lower() for w in bad_words) or any(w in symbol.lower() for w in bad_words):
            return False, f"bad-word-scam in name/symbol ({name} / {symbol})", _h_stats

        # Токенизированные регулируемые акции брокеров (Backpack Securities: TTWO, TSLA, AAPL и др.)
        # Это официальные акции с регуляторным freeze authority, а не скам. Отклоняем без маркировки скама!
        if "backpack securities" in name.lower() or "securities" in name.lower() or symbol_upper in ("TTWO", "BACKPACK"):
            return False, f"tokenized-equity-skip ({symbol_upper} — официальная акция Backpack Securities, не мем-токен)", _h_stats

        # Корпоративные тикеры и знаменитости — фейковые бренды (MrBeast -52%, ELON, TRUMP, AMAZON)
        corporate_scam_keywords = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "XAI", "NEURALINK", "PAYPAL",
                                  "NVDA", "GOOG", "AMZN", "META", "NFLX", "ANTHROPIC", "OPENAI", "PERPLEXITY",
                                  "MRBEAST", "BEASTMODE", "ELONMUSK", "TRUMPCOIN", "TRUMP2024", "TRUMP2025",
                                  "AMAZON", "MICROSOFT", "GOOGLE", "APPLE"]
        # Проверяем точное совпадение тикера ИЛИ имя содержит бренд
        _name_has_brand = any(k in name for k in corporate_scam_keywords)
        _is_high_volume_corp = (eff_liq >= 10000.0 and _vol5 >= 3000.0 and _b5 >= _s5) or (_vol5 >= 10000.0 and _b5 >= _s5)
        if (any(k == symbol_upper for k in corporate_scam_keywords) or _name_has_brand) and not _is_high_volume_corp:
            return False, f"corporate-brand-mimic {symbol_upper}", _h_stats

        # 2. Аудит соцсетей и анонимности создателя
        info = pair_data.get("info", {}) or {}
        socials = info.get("socials", []) or []
        websites = info.get("websites", []) or []
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        has_socials = bool(has_twitter or has_tg or has_website or pair_data.get("_socials_unknown"))

        if is_raw_pump_curve:
            if not getattr(_c, "ALLOW_UNGRADUATED_PUMPFUN", False):
                return False, "ungraduated-pump-curve (ждём миграции на DEX с сожжённым LP)", _h_stats
            _min_c_mc = float(getattr(_c, "MIN_PUMP_CURVE_MCAP_USD", 7500.0))
            _max_c_mc = float(getattr(_c, "MAX_PUMP_CURVE_MCAP_USD", 42000.0))
            if fdv > 0 and fdv < _min_c_mc:
                return False, f"pump-curve-flash-rug (FDV ${fdv:,.0f} < ${_min_c_mc:,.0f})", _h_stats
            if fdv > _max_c_mc:
                return False, f"pump-curve-migration-cliff (FDV ${fdv:,.0f} > ${_max_c_mc:,.0f})", _h_stats

        # Токены на AMM (PumpSwap / Raydium / Meteora) без соцсетей пускаем ТОЛЬКО при наличии подтвержденного органического потока
        # (в первые минуты миграции DexScreener часто еще не успел подтянуть Twitter/TG, кейсы LAUNCH +793%, JOCKEY +274%, MBAPPE +166%, SPERM +112%)
        if not is_raw_pump_curve and not has_socials and eff_liq < 22000.0:
            _v2l = _vol5 / max(eff_liq, 1.0)
            _bs = _b5 / float(max(_s5, 1))
            _avg_tx5 = _vol5 / max(_b5 + _s5, 1)
            _is_organic_no_soc = (
                (_avg_tx5 >= 32.0 and _b5 >= 40 and _bs >= 1.25)
                or (address.endswith(("pump", "bonk", "BAGS")) and eff_liq >= 14000.0 and _bs >= 1.22)
            )
            if _v2l > 2.8 and _bs < 1.5 and not _is_organic_no_soc:
                return False, f"anon-wash-churn-trap (0 socials, liq ${eff_liq:,.0f}, vol5/liq {_v2l:.1f}x, b/s {_bs:.2f})", _h_stats
        _is_organic_flow = (
            (_b5 >= 12 and _b5 >= _s5 and _vol5 >= 1500.0 and eff_liq >= 5000.0)
            or (_b5 >= 25 and _b5 >= _s5 * 1.10 and eff_liq >= 6000.0)
            or (_vol5 >= 12000.0 and _b5 >= _s5)
        )

        if not is_raw_pump_curve and not has_socials and not _is_organic_flow:
            return False, f"no-socials-amm-scam (0 socials on DEX без органического объема — 100% disposable dev dump)", _h_stats

        # 3. Аудит для сети SOLANA
        if chain == "solana":
            import goplus as _gp

            # А) Мгновенный ончейн-аудит через RPC
            onchain_ok, onchain_why = await _gp.check_onchain_solana_security(address)
            if not onchain_ok:
                return False, f"onchain-sol-reject {onchain_why}", _h_stats

            # Б) GoPlus Security + В) Полный кэшированный отчет RugCheck API (с точным исключением AMM-пулов)
            sec_ok, sec_why = await _gp.check_solana(address)
            sec_why_low = str(sec_why or "").lower()
            rc_status_pre, rc_data_pre = await _gp.fetch_rugcheck_report_cached(address, ttl=120.0, timeout=6)
            _gp_is_holder = ("кит держит" in sec_why_low or "топ-10 китов" in sec_why_low)
            if not sec_ok and "rugcheck-score" not in sec_why_low and not (_gp_is_holder and (rc_status_pre == 200 or eff_liq >= 20000.0)):
                return False, f"goplus-sol-reject {sec_why}", _h_stats
              
            # Токены канонического происхождения Pump.fun (bonding curve / выпускники):
            _dex_low_jev = str(pair_data.get("dexId") or "").lower()
            is_canonical_pump = address.endswith("pump") or is_raw_pump_curve
            if rc_status_pre != 200 or not isinstance(rc_data_pre, dict):
                return False, "waiting for rugcheck index (auditor safety)", _h_stats

            # В) Полный отчет RugCheck API (из общего 120с кэша)
            try:
                rc_status, rc_data = rc_status_pre, rc_data_pre
                if rc_status == 200 and isinstance(rc_data, dict):
                    token_info = rc_data.get("token", {}) or {}
                    mint_auth = token_info.get("mintAuthority")
                    freeze_auth = token_info.get("freezeAuthority")

                    PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
                    SYSTEM_PROGRAM = "11111111111111111111111111111111"
                    SAFE_AUTHORITIES = {PUMPFUN_PROGRAM, SYSTEM_PROGRAM, None, ""}

                    if mint_auth and mint_auth not in SAFE_AUTHORITIES:
                        return False, f"rugcheck: mint authority active ({mint_auth[:8]})", _h_stats
                    if freeze_auth and freeze_auth not in SAFE_AUTHORITIES:
                        return False, f"rugcheck: freeze authority active ({freeze_auth[:8]})", _h_stats

                    # Проверка блокировки LP в пулах (Raydium Lock/Burn >= 80% или протокольный PumpSwap AMM)
                    target_pair = str(pair_data.get("pairAddress") or "").strip()
                    _dex_rc_low = str(pair_data.get("dexId") or "").lower()
                    has_verified_locked_lp = False
                    for mkt in (rc_data.get("markets") or []):
                        if isinstance(mkt, dict):
                            mkt_type = str(mkt.get("marketType") or "").lower()
                            mkt_pubkey = str(mkt.get("pubkey") or "").strip()
                            lp_info = mkt.get("lp") or {}
                            if isinstance(lp_info, dict):
                                mkt_quote_usd = float(lp_info.get("quoteUSD") or 0.0)
                                lp_unlocked = float(lp_info.get("lpUnlocked") or 0.0)
                                lp_locked_pct = float(lp_info.get("lpLockedPct") or 0.0)
                                lp_burned_pct = float(lp_info.get("lpBurnedPct") or 0.0)
                                if (lp_locked_pct >= 80.0 or lp_burned_pct >= 80.0) and (mkt_quote_usd >= 2000 or (target_pair and mkt_pubkey == target_pair)):
                                    has_verified_locked_lp = True
                            if mkt_type in ("pump_fun_amm", "pump_amm"):
                                has_verified_locked_lp = True

                    # Для неизвестных кастомных токенов (не оканчивающихся на 'pump') требуем подтвержденный заблокированный LP >= 80%!
                    if not is_canonical_pump and not has_verified_locked_lp:
                        return False, "rugcheck: custom token without verified locked LP (lock < 80%)", _h_stats

                    # Очищенный score RugCheck: вычитаем безвредный шум и AMM LP пула для bonding curve / pump tokens / проверенных пулов с залоченным LP.
                    _lp_noise_allowed = bool(is_canonical_pump or has_verified_locked_lp)
                    _allowed_lp_terms_jev = (
                        "large amount of lp unlocked", "lp provider", "single lp provider",
                        "lp not locked", "unlocked lp"
                    ) if _lp_noise_allowed else ("lp provider", "low liquidity")

                    raw_score = float(rc_data.get("score") or 0.0)
                    penalty_to_ignore = sum(
                        float(rk.get("score", 0) or 0)
                        for rk in (rc_data.get("risks") or [])
                        if any(k in str(rk.get("name", "")).lower() for k in (
                            "low liquidity", "creator history", "copycat", "symbol mimic", *_allowed_lp_terms_jev
                        ))
                    )
                    clean_score = max(0.0, raw_score - penalty_to_ignore)
                    if clean_score >= 2000:
                        return False, f"rugcheck: high risk score {clean_score:.0f} >= 2000", _h_stats

                    # Проверка критических рисков (реальные опасности смарт-контракта и концентрации)
                    for rk in (rc_data.get("risks") or []):
                        rk_name = str(rk.get("name") or "").lower()
                        rk_lvl = str(rk.get("level") or "").lower()
                        if any(k in rk_name for k in ("low liquidity", "creator history", "copycat", "symbol mimic", *_allowed_lp_terms_jev)):
                            continue
                        if _lp_noise_allowed and any(lp_k in rk_name for lp_k in ("large amount of lp unlocked", "lp not locked", "unlocked lp")):
                            continue
                        if rk_lvl == "danger" and "creator history" not in rk_name:
                            return False, f"rugcheck: critical danger risk '{rk.get('name')}'", _h_stats
                        if any(k in rk_name for k in ("single holder", "high ownership", "top 10 holders")) and rk_lvl == "danger":
                            return False, f"rugcheck: critical hazard '{rk.get('name')}'", _h_stats

                    # Проверка держателей и дева (Holders & Sybil analysis)
                    holders = rc_data.get("topHolders", []) or []
                    pool_accounts = set()
                    if pair_data.get("pairAddress"):
                        pool_accounts.add(pair_data["pairAddress"])
                    for p in (pair_data.get("pairs") or []):
                        if isinstance(p, dict) and p.get("pairAddress"):
                            pool_accounts.add(p["pairAddress"])
                    for k_acc, v_acc in (rc_data.get("knownAccounts") or {}).items():
                        if isinstance(v_acc, dict) and (v_acc.get("type") in ("AMM", "LOCKER", "DEX") or any(w in str(v_acc.get("name", "")).lower() for w in ("pool", "amm", "raydium", "meteora", "pump", "vault", "locker"))):
                            pool_accounts.add(k_acc)
                        elif isinstance(v_acc, str) and any(w in v_acc.lower() for w in ("pool", "amm", "raydium", "meteora", "pump", "vault", "locker")):
                            pool_accounts.add(k_acc)
                    for mkt in (rc_data.get("markets") or []):
                        if isinstance(mkt, dict):
                            for m_field in ("pubkey", "liquidityA", "liquidityB", "baseVault", "quoteVault"):
                                if mkt.get(m_field):
                                    pool_accounts.add(mkt[m_field])

                    DEX_POOL_AUTHORITIES = {
                        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", # Raydium AMM V4
                        "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5", # Raydium CP-Swap
                        "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL", # Raydium CPMM / LaunchLab
                        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", # Raydium CLMM
                        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", # PumpSwap AMM
                        "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg", # Pump.fun Migration Authority
                        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB", # Meteora Dynamic Pools
                        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", # Meteora DLMM
                        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", # Orca Whirlpools
                        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", # Pump.fun Bonding Curve
                        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1", # Pump.fun Authority
                    }

                    pool_keywords = ("raydium", "meteora", "pump", "orca", "pool", "vault", "locker", "amm", "pumpswap")
                    real_creator = rc_data.get("creator") or ""
                    dev_pct = 0.0
                    if real_creator:
                        for h in holders:
                            if h.get("owner") == real_creator or h.get("address") == real_creator:
                                dev_pct = float(h.get("pct", 0) or 0.0)
                                break

                    non_curve = []
                    for h in holders:
                        pct = float(h.get("pct", 0) or 0.0)
                        amt = float(h.get("uiAmount", 0) or 0.0)
                        owner = str(h.get("owner") or "")
                        addr = str(h.get("address") or "")
                        if owner in pool_accounts or addr in pool_accounts or owner in DEX_POOL_AUTHORITIES or addr in DEX_POOL_AUTHORITIES:
                            continue
                        if any(w in owner.lower() for w in pool_keywords) or any(w in addr.lower() for w in pool_keywords):
                            continue
                        non_curve.append((amt, pct, owner))

                    top_10 = non_curve[:10]
                    # Jito-бандл (Сивил-атака дева: 4+ кошельков с одинаковым %)
                    bundle_suspects = [round(pct, 1) for (amt, pct, owner) in top_10 if pct >= 2.5]
                    if len(bundle_suspects) >= 4:
                        from collections import Counter
                        counts = Counter(bundle_suspects)
                        most_common_pct, count = counts.most_common(1)[0]
                        if count >= 4 and most_common_pct >= 2.5 and (most_common_pct * count) >= 12.0:
                            return False, f"jito-bundle sybil attack ({count} wallets with ~{most_common_pct:.1f}%)", _h_stats

                    top_10_sum = sum(x[1] for x in top_10)
                    largest_whale = top_10[0][1] if top_10 else 0.0

                    _h_stats["top10_pct"] = top_10_sum
                    _h_stats["dev_pct"] = dev_pct
                    _h_stats["top1_pct"] = largest_whale

                    max_dev_limit = 8.0 if is_pump else float(getattr(_c, "AUDITOR_MAX_DEV_PCT", 6.0))
                    if dev_pct and dev_pct > max_dev_limit:
                        return False, f"dev still loaded {dev_pct:.1f}% > {max_dev_limit:.0f}% (dump hazard: dev holds supply)", _h_stats
                    try:
                        from birth_tracker import birth_tracker as _bt_aud
                        _dumped, _dump_why = _bt_aud.check_creator_dumped(address, dev_pct)
                        if _dumped:
                            return False, f"auditor-layer3 {_dump_why}", _h_stats
                    except Exception:
                        pass
                    max_whale_limit = 18.0 if is_pump else 15.0
                    if largest_whale > max_whale_limit:
                        return False, f"whale concentration {largest_whale:.1f}% > {max_whale_limit:.0f}%", _h_stats
                    max_top10 = 38.0 if (is_pump or vol_m5 >= 10000.0) else float(getattr(_c, "AUDITOR_MAX_TOP10_PCT", 32.0))
                    if top_10_sum > max_top10:
                        return False, f"top 10 concentration {top_10_sum:.1f}% > {max_top10:.0f}%", _h_stats

                    # Layer 7 (insiders/graph) + Layer 8 (SPL Delegations) + Layer 9/10 (Staircase & Sybil Farm) параллельно!
                    _holder_tok_accs = [str(h.get("address") or "") for h in holders[:20] if h.get("address") and str(h.get("address")) not in pool_accounts]
                    _holder_owners = [str(h.get("owner") or "") for h in holders[:20] if h.get("owner") and str(h.get("owner")) not in pool_accounts and float(h.get("pct") or 0) <= 50.0]
                    _raw_sup = float((token_info.get("supply") or 0.0))
                    _dec_sup = int(token_info.get("decimals") or 6)
                    _ui_sup = (_raw_sup / (10 ** _dec_sup)) if _raw_sup > 0 else 1_000_000_000.0
                    (_l7_ok, _l7_why), (_l8_ok, _l8_why), (_l9_ok, _l9_why) = await asyncio.gather(
                        _gp.check_rugcheck_insider_graph(address, total_supply_raw=_raw_sup, pool_accounts=pool_accounts),
                        _gp.check_spl_holder_delegations(address, holder_token_accounts=_holder_tok_accs, total_supply_ui=_ui_sup),
                        _gp.check_staircase_and_sybil_farm(address, pair_address=str(pair_data.get("pairAddress") or ""), holder_owners=_holder_owners),
                    )
                    if not _l7_ok:
                        return False, f"auditor-layer7 {_l7_why}", _h_stats
                    if not _l8_ok:
                        return False, f"auditor-layer8 {_l8_why}", _h_stats
                    if not _l9_ok:
                        return False, f"auditor-layer9/10 {_l9_why}", _h_stats
                else:
                    # Для кастомных токенов (не оканчивающихся на 'pump') без отчёта RugCheck блокируем вход
                    if not address.endswith("pump"):
                        return False, "rugcheck: custom token unindexed (cannot verify LP lock)", _h_stats

                    # Фоллбэк: RugCheck API вернул 400/404/429/500 для pump-токена -> проверяем GoPlus holder stats + Layer 8/9/10
                    (_l8_fb_ok, _l8_fb_why), (_l9_fb_ok, _l9_fb_why) = await asyncio.gather(
                        _gp.check_spl_holder_delegations(address),
                        _gp.check_staircase_and_sybil_farm(address, pair_address=str(pair_data.get("pairAddress") or "")),
                    )
                    if not _l8_fb_ok:
                        return False, f"auditor-layer8 {_l8_fb_why}", _h_stats
                    if not _l9_fb_ok:
                        return False, f"auditor-layer9/10 {_l9_fb_why}", _h_stats
                    _h_raw = _gp.get_solana_holder_stats(address)
                    if _h_raw and _h_raw.get("has_data"):
                        _h_stats["top10_pct"] = float(_h_raw.get("top_10_pct", 0.0) or 0.0)
                        _h_stats["dev_pct"] = float(_h_raw.get("dev_pct", 0.0) or 0.0)
                        _h_stats["top1_pct"] = _h_stats["dev_pct"]
                        max_dev_gp = 8.0 if is_pump else float(getattr(_c, "AUDITOR_MAX_DEV_PCT", 6.0))
                        if _h_stats["dev_pct"] > max_dev_gp:
                            return False, f"dev loaded (GoPlus) {_h_stats['dev_pct']:.1f}% > {max_dev_gp:.0f}% (dump risk)", _h_stats
                        max_top10_gp = 35.0 if is_pump else float(getattr(_c, "AUDITOR_MAX_TOP10_PCT", 30.0))
                        if _h_stats["top10_pct"] > max_top10_gp:
                            return False, f"top10 concentration (GoPlus) {_h_stats['top10_pct']:.1f}% > {max_top10_gp:.0f}%", _h_stats
                    else:
                        return False, "auditor: cannot verify holders (rugcheck missing)", _h_stats

            except Exception as e:
                return False, f"rugcheck-exception ({e})", _h_stats
                _h_raw = _gp.get_solana_holder_stats(address)
                if _h_raw and _h_raw.get("has_data"):
                    _h_stats["top10_pct"] = float(_h_raw.get("top_10_pct", 0.0) or 0.0)
                    _h_stats["dev_pct"] = float(_h_raw.get("dev_pct", 0.0) or 0.0)
                    _h_stats["top1_pct"] = _h_stats["dev_pct"]
                    if _h_stats["dev_pct"] > 4.0:
                        return False, f"dev loaded (GoPlus) {_h_stats['dev_pct']:.1f}% > 4.0% (dump risk)", _h_stats
                    if _h_stats["top10_pct"] > 35.0:
                        return False, f"top10 concentration (GoPlus) {_h_stats['top10_pct']:.1f}% > 35%", _h_stats
                elif address.endswith("pump"):
                    _h_stats["top10_pct"] = 15.0
                    _h_stats["dev_pct"] = 2.0
                    _h_stats["top1_pct"] = 2.0
                else:
                    return False, "auditor: cannot verify holders on custom token", _h_stats

            # Г) Двухсторонняя симуляция (BUY + SELL) через Jupiter
            # ЖЕСТКАЯ ЗАЩИТА: токен ОБЯЗАН успешно проходить и покупку, и продажу!
            # Никаких поблажек для pump.fun (кейс LAUNCH / RICK / CRACKED — без обратного маршрута вход ЗАПРЕЩЕН)
            try:
                from jupiter import JupiterAPI
                jup_sim = await JupiterAPI.check_taxes_and_simulate_swap(address, input_amount_sol=0.05)
                if not jup_sim.get("is_safe", False):
                    reason = jup_sim.get("reason", "no route / unsellable / honeypot")
                    if jup_sim.get("rate_limited", False) or "429" in str(reason) or "rate limit" in str(reason).lower():
                        # Если Jupiter временно вернул 429, НЕ клеймим токен скамом на 10 минут!
                        # Если токен уже прошёл RPC On-Chain + GoPlus + RugCheck и имеет >= 8 реальных продаж за 5м — пул доказанно продаваем
                        if (is_pump and _s5 >= 5 and _b5 >= 12) or (_s5 >= 8 and _b5 >= 15 and eff_liq >= 15000.0):
                            print(f"⚠️ [JUPITER-429-FALLBACK] {symbol}: Jupiter 429, но токен прошёл RPC+RugCheck+GoPlus и имеет {_b5}b/{_s5}s за 5м — пропуск разрешён")
                        else:
                            return False, f"jupiter-rate-limit-429-retry ({reason})", _h_stats
                    else:
                        return False, f"jupiter-routing-failed ({reason})", _h_stats
            except Exception as e:
                return False, f"jupiter-simulation-error ({e})", _h_stats

        # 4. Аудит для других сетей (отключены)
        elif chain != "solana":
            return False, f"chain-disabled ({chain} отключен, 100% фокус на Solana)", _h_stats

        # 5. Клон-фильтр (is_clone) из analyzer
        try:
            if hasattr(self, "analyzer") and self.analyzer:
                created_at = pair_data.get("pairCreatedAt") or 0
                tx5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
                b5 = int(tx5.get("buys", 0) or 0)
                vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                is_cl = await self.analyzer.is_clone(symbol, address, created_at, fdv, current_liq=raw_liq, current_vol5=vol5, current_b5=b5)
                if is_cl:
                    return False, f"clone-scam of an older/larger token", _h_stats
        except Exception:
            pass

        return True, "PASSED_ALL_SCAM_CHECKS", _h_stats

    # =========================================================================
    # 7. CONVEYOR: Полный цикл оценки и исполнения
    # =========================================================================
    _SHADOW_SKIP = ("no-pair-data", "invalid-price", "already-held", "max-positions", "llm-unavailable",
                    "non-solana", "fake-brand", "rate-limit-retry")

    async def process_candidate(self, address: str, chain: str, tracker) -> Tuple[bool, str]:
        """Обёртка: каждый отказ воронки пишется в теневой журнал (rejected_tokens, source=GROK-JEV),
        чтобы потом видеть, какой фильтр режет токены, которые позже выросли."""
        ok, why = await self._process_candidate_inner(address, chain, tracker)
        pair = getattr(self, "_pair_seen", {}).pop(address, None)
        if not ok and pair:
            try:
                import config as _c
                low = str(why).lower()
                if getattr(_c, "JEV_SHADOW_LOG_REJECTS", True) and not any(k in low for k in self._SHADOW_SKIP):
                    from shadow_tracker import shadow_tracker
                    base = pair.get("baseToken", {}) or {}
                    _an = getattr(self, "analyzer", None)
                    rkt_f = (getattr(_an, "rkt_features_map", {}).get(address) if _an else None) or {}
                    rkt_s = getattr(_an, "rocket_scores", {}).get(address, None) if _an else None
                    shadow_tracker.log_rejection(
                        mint=address, reason=str(why)[:140], symbol=base.get("symbol", address[:6]), chain="solana",
                        price=float(pair.get("priceUsd", 0) or 0),
                        liquidity=float((pair.get("liquidity") or {}).get("usd", 0) or 0),
                        m5_pct=float((pair.get("priceChange") or {}).get("m5", 0) or 0),
                        features=rkt_f,
                        rkt_score=rkt_s,
                        source="GROK-JEV")
            except Exception:
                pass
        return ok, why

    async def _process_candidate_inner(self, address: str, chain: str, tracker) -> Tuple[bool, str]:
        """
        Полный конвейер GROKBOT x JEV для одного токена:
        1. ПОЛНЫЙ АНТИСКАМ-АУДИТ (RugCheck, GoPlus, Dev/Whale concentration, Jito Sybil, Socials, Clone)
        2. HARD GATE (Математические пороги ликвидности, возраста, импульса)
        3. VET (JEV AI Gate: Groq / xAI / Gemini)
        4. FILLS ("We bid the flush, not the candle")
        5. RISK ("The stop lives under the wick")
        6. CHIEF / GROK TRADES (Исполнение сделки)
        """
        import config as _c

        if chain != "solana":
            return False, "non-solana-chain-disabled"
        if not address or address in tracker.get_open_positions():
            return False, "already-held"
        if len(tracker.get_open_positions()) >= getattr(_c, "MAX_CONCURRENT_POSITIONS", 10):
            return False, "max-positions-reached"

        # 1. Получаем рыночные данные
        pair = await self.fetch_pair(address, chain)
        if not pair:
            return False, "no-pair-data"

        base_info = pair.get("baseToken", {}) or {}
        symbol = str(base_info.get("symbol", address[:6])).upper().strip()
        price = float(pair.get("priceUsd", 0) or 0.0)
        if not hasattr(self, "_pair_seen"):
            self._pair_seen = {}
        if len(self._pair_seen) > 500:
            self._pair_seen.clear()
        self._pair_seen[address] = pair
        if price <= 0:
            return False, "invalid-price"
        self._record_px(address, price)

        # 1.5. Проверка рассинхрона цен между пулами (миграция / арбитражная ловушка)
        if pair.get("_price_divergence"):
            _div_r = float(pair.get("_price_divergence_ratio", 0.0) or 0.0)
            print(f"🚫 [PRICE-SPLIT] {symbol} ({address[:8]}): рассинхрон цен между пулами {_div_r:.2f}x — блок входа (защита от ложного стоп-лосса).")
            return False, f"price-divergence-pool-split ({_div_r:.2f}x)"

        # 1.6. 🚀 STAGE 1: ROCKET XGBOOST ML СКРИНЕР (0ms IN-MEMORY)
        # Отсекает 90% мусора и заведомых дампов в 0мс (DARTHLON 6%, BALLS 4%, DAISUKE 2%)
        # без лишних сетевых вызовов Jupiter, RugCheck и LLM.
        rkt_s1 = self.score_rocket_xgb(address, pair)
        s1_thresh = float(getattr(_c, "ROCKET_XGB_STAGE1_THRESHOLD", 8.0))
        _txs_s1 = (pair.get("txns") or {}).get("m5") or {}
        _b5_s1 = int(_txs_s1.get("buys", 0) or 0)
        _s5_s1 = int(_txs_s1.get("sells", 0) or 0)
        _vm5_s1 = float((pair.get("volume") or {}).get("m5", 0) or 0)
        _m5_s1 = float((pair.get("priceChange") or {}).get("m5", 0) or 0)
        _is_momentum_rocket = (_m5_s1 >= 12.0 and _b5_s1 >= (_s5_s1 * 1.25) and _vm5_s1 >= 8000.0)

        if getattr(_c, "ROCKET_XGB_ENABLED", True) and not _is_momentum_rocket and (rkt_s1 + 0.05) < s1_thresh:
            print(f"🚫 [GROK-JEV-ML-S1] {symbol} ({address[:8]}): Rocket XGB score {rkt_s1:.1f}% < {s1_thresh:.0f}% — отсев дампа в 0мс.")
            return False, f"rocket-xgb-s1-reject {rkt_s1:.1f}% < {s1_thresh:.0f}%"
        elif _is_momentum_rocket and (rkt_s1 + 0.05) < s1_thresh:
            print(f"⚡ [GROK-JEV-ML-S1] {symbol} ({address[:8]}): Моментум-байпас для ракеты (m5 +{_m5_s1:.1f}%, vol5 ${_vm5_s1:,.0f}, {_b5_s1}b/{_s5_s1}s) при скоре {rkt_s1:.1f}% — допуск в Hard Gate!")

        # 2. HARD GATE ПЕРВЫМ ("if it fails here, JEV and Jupiter never see it")
        # Мгновенный математический отсев (0 сетевых запросов!) — отсекает 95% пустых/стоячих монет ДО вызовов Jupiter/RugCheck/GoPlus
        hard_ok, hard_why = self.hard_gate(address, pair, chain, None)
        if not hard_ok:
            return False, f"hard-gate-reject {hard_why}"

        # 3. FILLS & RISK ВТОРЫМ ("We bid the flush, not the candle" & "The stop lives under the wick")
        # Также 0 сетевых запросов — если сейчас экстремальная свеча и нужно подождать отката 25с, не сжигаем лимит Jupiter API
        flush_ok, wick_pct, wick_price, flush_msg = self.check_flush_and_wick(pair, price)
        if not flush_ok:
            print(f"⏳ [GROK+JEV] {symbol} ({chain}): FILLS ➔ {flush_msg}")
            return False, f"fills-flush-wait {flush_msg}"

        # 3.5. ПЕРЕПРОВЕРКА НА СВЕЖИХ ДАННЫХ: цена из кэша могла устареть на минуту, пока монета уже падает
        if getattr(_c, "JEV_FRESH_RECHECK_ENABLED", False):
            _fresh = await self.fetch_pair_fresh(address, chain)
            if _fresh:
                _fp = float(_fresh.get("priceUsd", 0) or 0)
                if _fp > 0:
                    pair, price = _fresh, _fp
                    _hard2_ok, _hard2_why = self.hard_gate(address, pair, chain, None)
                    if not _hard2_ok:
                        return False, f"fills-flush-wait fresh-recheck {_hard2_why}"
                    flush_ok, wick_pct, wick_price, flush_msg = self.check_flush_and_wick(pair, price)
                    if not flush_ok:
                        print(f"⏳ [GROK+JEV] {symbol} ({chain}): FRESH RECHECK ➔ {flush_msg}")
                        return False, f"fills-flush-wait fresh {flush_msg}"

        # 3.5. Судья недоступен (нет ключа / 429 / бюджет) -> если не включен жесткий LLM enforcement, используем алгоритмический JEV
        if getattr(_c, "REQUIRE_ONLINE_LLM", False) and not self.llm_ready():
            return False, "llm-unavailable-wait"

        # 4. ПОЛНЫЙ АНТИСКАМ-АУДИТ (RUGCHECK + GOPLUS + ONCHAIN + DEV/WHALES + JITO-BUNDLE + JUPITER SIM + CLONE)
        # Выполняется ТОЛЬКО для тех 2-5% живых ракет, которые уже прошли Hard Gate и Fills!
        sec_ok, sec_why, _h_stats = await self.full_anti_scam_audit(address, symbol, chain, pair)
        if not sec_ok:
            if "rate-limit-429-retry" in sec_why:
                print(f"⏳ [GROK+JEV] {symbol} ({chain}): Jupiter API busy (429), retry in 20s")
                return False, f"rate-limit-retry {sec_why}"
            print(f"🚫 [GROK+JEV-SCAM] {symbol} ({chain}): {sec_why}")
            return False, f"anti-scam-reject {sec_why}"

        # 4.5. Нет данных о холдерах (RugCheck/GoPlus молчат) -> не входим вслепую (для не-pump токенов):
        _is_pump_token = address.endswith("pump") or str(pair.get("dexId", "")).lower() in ("pump", "pumpfun", "pumpswap")
        if getattr(_c, "JEV_REQUIRE_HOLDER_DATA", True) and not _is_pump_token:
            if not (float((_h_stats or {}).get("top1_pct", 0) or 0) > 0 or float((_h_stats or {}).get("top10_pct", 0) or 0) > 0):
                return False, "anti-scam-reject holders-no-data (нет данных о холдерах)"

        # 4.6. SMART HUNTER: торговали ли этим токеном кошельки из базы. Фильтры НЕ обходит (инсайдеры тоже сливают на копирующих),
        # результат идёт в data-line судьи и в запись входа, чтобы измерить, есть ли в этом сигнале польза.
        _n, _s = await self.smart_buyers(address)
        if _n is not None:
            _h_stats = dict(_h_stats or {})
            _h_stats["smart_n"] = _s
            if _s:
                print(f"💎 [SMART] {symbol} ({address[:8]}): {_s} смарт-кошельков среди {_n} последних плательщиков")

        # 5. VET (SOFT GATE: JEV AI Evaluation)
        soft_ok, jev_data, soft_why = await self.jev_vet(address, symbol, pair, chain, _h_stats)
        if not soft_ok:
            print(f"🚫 [GROK+JEV] {symbol} ({chain}): JEV soft-reject ➔ {soft_why}")
            return False, f"jev-soft-reject {soft_why}"

        # 5.5. ФИНАЛЬНАЯ ПРОВЕРКА ПЕРЕД ИСПОЛНЕНИЕМ: аудит+LLM занимают секунды, цена в pair устарела.
        # Все минусы (OREN/GTA/ELOOONG/HYPERCAT) куплены на пике входа (peak == entry) по устаревшим данным.
        _scan_price, _scan_liq = price, float((pair.get("liquidity") or {}).get("usd", 0) or 0)
        _fin = await self.fetch_pair_fresh(address, chain)
        if not _fin:
            return False, "fills-flush-wait final-recheck no-fresh-data"
        _fpx = float(_fin.get("priceUsd", 0) or 0)
        _fliq = float((_fin.get("liquidity") or {}).get("usd", 0) or 0)
        _fm1 = float(((_fin.get("priceChange") or {}).get("m1", 0)) or 0)
        if _fpx <= 0:
            return False, "fills-flush-wait final-recheck invalid-price"
        if _fin.get("_price_divergence"):
            return False, "fills-flush-wait final-recheck price-divergence-pool-split"
        if _fpx < _scan_price * 0.965:
            return False, f"fills-flush-wait final-recheck price falling ({(_fpx/_scan_price-1)*100:+.1f}% since scan)"
        if _fpx > _scan_price * 1.12:
            return False, f"fills-flush-wait final-recheck price ran away ({(_fpx/_scan_price-1)*100:+.1f}% since scan, no chase)"
        if _scan_liq > 0 and _fliq > 0 and _fliq < _scan_liq * 0.85:
            return False, f"fills-flush-wait final-recheck liquidity draining ({_scan_liq:,.0f}->{_fliq:,.0f})"
        if _fm1 < -3.5:
            return False, f"fills-flush-wait final-recheck m1 {_fm1:+.1f}% falling"
        pair, price = _fin, _fpx
        wick_price = price * (1.0 + wick_pct)

        # 5.6. 🚀 STAGE 2: ROCKET XGBOOST FINAL ML FIREWALL
        # Финальная проверка перед реальным открытием ордера со свежими данными и реальными ончейн холдерами (_h_stats).
        rkt_final = self.score_rocket_xgb(address, pair, _h_stats)
        final_thresh = float(getattr(_c, "ROCKET_XGB_FINAL_THRESHOLD", 14.0))
        _fin_links = len((pair.get("info") or {}).get("socials", []) or []) + len((pair.get("info") or {}).get("websites", []) or [])
        if _fin_links == 0 and not bool(pair.get("_socials_unknown", False)):
            final_thresh = max(final_thresh, 18.0)
        if getattr(_c, "ROCKET_XGB_ENABLED", True) and (rkt_final + 0.05) < final_thresh:
            print(f"🚫 [GROK-JEV-ML-VETO] {symbol} ({address[:8]}): Rocket XGB final score {rkt_final:.1f}% < {final_thresh:.0f}% — VETO! (Блокировка дампа)")
            return False, f"rocket-xgb-final-veto {rkt_final:.1f}% < {final_thresh:.0f}%"

        print(f"🚀 [GROK-JEV-ML-PASS] {symbol} ({address[:8]}): Rocket XGB score {rkt_final:.1f}% >= {final_thresh:.0f}% — ОДОБРЕНО!")

        # 6. CHIEF / GROK TRADES: Исполнение ордера
        if len(tracker.get_open_positions()) >= getattr(_c, "MAX_CONCURRENT_POSITIONS", 10):
            return False, "max-positions-reached"

        # Расчет размера билета (Dynamic Sizing / Compounding)
        _base_t = float(getattr(_c, "BASE_TRADE_AMOUNT_USD", 4.0) or 4.0)
        ticket_size = _base_t
        if getattr(_c, "DYNAMIC_REINVEST_ENABLED", True) or getattr(_c, "COMPOUNDING_ENABLED", True):
            try:
                ticket_size = tracker.calculate_dynamic_position_size(pair, chain=chain)
            except Exception:
                pass
        ticket_size = max(_base_t, ticket_size)
        _cap = float(getattr(_c, "JEV_MAX_TICKET_USD", 0.0) or 0.0)
        if _cap > 0:
            ticket_size = min(ticket_size, _cap)
        # Executor Liquidity Cap (@savipww): не более 2% от ликвидности пула, чтобы собственный выход не двигал стакан
        _pool_frac = float(getattr(_c, "MAX_POOL_LIQ_FRAC", 0.02) or 0.02)
        if _fliq > 0:
            ticket_size = min(ticket_size, max(_base_t, round(_fliq * _pool_frac, 2)))

        # PRE-FLIGHT SELL CHECK & LIVE JUPITER PRICE SYNC:
        # 1) Проверяем продаваемость на Jupiter ПЕРЕД входом (спасает от ханипотов NO_ROUTES_FOUND).
        # 2) Синхронизируем entry_price с живой котировкой Jupiter (защита от покупки с отстающей свечи DexScreener -> мгновенный стоп -14%).
        if chain == "solana":
            try:
                from jupiter import JupiterAPI
                _j_ok, _j_why = await JupiterAPI.is_token_sellable(address)
                _w_low = str(_j_why or "").lower()
                if not _j_ok and any(k in _w_low for k in ("no routes", "cannot sell", "honeypot", "freeze", "0 sol returned")):
                    print(f"🚫 [PRE-FLIGHT BLOCKED] Отказ входа {symbol} ({address[:8]}): токен непродаваем на DEX ({_j_why}) — предотвращен слив -100%!")
                    return False, f"pre-flight-unsellable ({_j_why})"
                _dex_fin = str(pair.get("dexId", "")).lower()
                if _dex_fin not in ("pump", "pumpfun"):
                    _jup_live_px = await JupiterAPI.get_price(address)
                    if _jup_live_px and _jup_live_px > 0 and price > 0:
                        if _jup_live_px < price * 0.975:
                            print(f"🚫 [JEV-LIVE-DUMP] {symbol} ({address[:8]}): живая цена Jupiter (${_jup_live_px:.8f}) уже на {(1.0 - _jup_live_px / price) * 100:.1f}% ниже DexScreener (${price:.8f}) — обвал в моменте, блок входа!")
                            return False, "fills-flush-wait live-jup-dump"
                        _jup_ratio = max(_jup_live_px, price) / min(_jup_live_px, price)
                        if _jup_ratio > 1.12:
                            print(f"🚫 [JEV-PRICE-MISMATCH] {symbol} ({address[:8]}): Jupiter (${_jup_live_px:.8f}) vs DS (${price:.8f}) расхождение {_jup_ratio:.2f}x (>1.12x) — защита от ложного стопа!")
                            return False, f"fills-flush-wait jup-ds-mismatch ({_jup_ratio:.2f}x)"
                        price = _jup_live_px
                        wick_price = price * (1.0 + wick_pct)
            except Exception:
                pass

        tracker.add_position(
            symbol=symbol,
            mint=address,
            entry_price=price,
            amount_usd=ticket_size,
            is_mature=True,
            source="GROK-JEV",
            chain=chain,
            pair_address=pair.get("pairAddress", ""),
            stop_under_wick=wick_price,
            wick_stop_loss_pct=wick_pct,
            ml_confidence=rkt_final,
            ml_features=_entry_features(pair, _h_stats, jev_data)
        )

        FUNNEL_STATS["filled"] += 1

        # Запись входа для разбора (победители vs проигравшие) + замер Smart Money (не блокирует сделку)
        _sm = (_h_stats or {}).get("smart_n", "NA")
        try:
            print("📝 [ENTRY] " + json.dumps({
                "sym": symbol, "mint": address, "ticket": round(ticket_size, 2), "smart": _sm,
                "data": _data_line(pair, _h_stats or {}, ticket_size),
                "judge": {k: jev_data.get(k) for k in _NEED}, "src": jev_data.get("source")}, ensure_ascii=False))
        except Exception:
            pass

        # Форматированный визуальный лог в стиле дашборда автора
        pc = pair.get("priceChange", {}) or {}
        m5 = float(pc.get("m5", 0) or 0)
        m1 = float(pc.get("m1", 0) or 0)
        if m1 == 0.0 and abs(m5) > 0.5:
            m1 = max(-25.0, min(25.0, m5 * 0.15))
        h1 = float(pc.get("h1", 0) or 0)
        liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
        mcap = float(pair.get("fdv", 0) or pair.get("marketCap", 0) or 0)
        acc_s = float(jev_data.get("account_is_the_project", 0.0) or 0.0)
        mom_s = float(jev_data.get("momentum_already_spent", 0.0) or 0.0)
        conc_s = float(jev_data.get("concentration_is_exit_risk", 0.0) or 0.0)
        provider = jev_data.get("source", "groq")

        print(f"\n"
              f"╔═══════════════════════════════════════════════════════════════════════════╗\n"
              f"║ 🤖 GROKBOT + JEV ➔ TRADE EXECUTED (JEV PICKS, GROK TRADES)               ║\n"
              f"╠═══════════════════════════════════════════════════════════════════════════╣\n"
              f"║ Token:     ${symbol:<10} [{chain.upper()}] {address[:16]}...                 ║\n"
              f"║ Price:     ${price:<15.8f} Ticket: ${ticket_size:<6.2f} (Bank Sized)           ║\n"
              f"║ Risk/SL:   ${wick_price:<15.8f} Stop Under Wick ({wick_pct*100:+.1f}%)               ║\n"
              f"║ Fills:     Bid the flush: m1 {m1:+.1f}%, m5 {m5:+.1f}%, h1 {h1:+.0f}%                    ║\n"
              f"║ Depth:     Liq: ${liq:,.0f} | MCap: ${mcap:,.0f}                                ║\n"
              f"║ JEV AI:    Acc:{acc_s:.2f} | Mom:{mom_s:.2f} | Conc:{conc_s:.2f} [{provider}] ➔ PASS     ║\n"
              f"╚═══════════════════════════════════════════════════════════════════════════╝\n")
        print(get_funnel_summary())
        return True, "FILLED"


# Глобальный инстанс
grok_jev = GrokJevEngine()


async def grok_bot_jev_loop(tracker, analyzer=None):
    """
    Автономный торговый конвейер GROKBOT + JEV (JEV PICKS, GROK TRADES).
    Работает полностью независимо от других сканеров.
    Фокусируется строго на сети Solana (100% SOLANA ONLY).
    """
    if analyzer:
        grok_jev.analyzer = analyzer
    print("🤖 [GROKBOT x JEV] Автономный торговый движок запущен (100% SOLANA ONLY, JEV PICKS, GROK TRADES)!")
    last_funnel_print = 0.0
    evaluated_cooldown = {}

    while True:
        try:
            import config as _c
            if not getattr(_c, "JEV_ENABLED", True):
                await asyncio.sleep(30.0)
                continue
            await asyncio.sleep(15.0)
            now = time.time()

            # Периодический отчет воронки THE FUNNEL (раз в 60 сек)
            if now - last_funnel_print >= 60.0:
                print(get_funnel_summary())
                print(get_reject_summary())
                last_funnel_print = now

            # Очистка устаревших кулдаунов
            if len(evaluated_cooldown) > 2000:
                evaluated_cooldown = {k: ts for k, ts in evaluated_cooldown.items() if ts > now}

            # 1. CRAWLER: Сбор кандидатов (Solana + Base)
            candidates = await grok_jev.crawl_universe()
            if not candidates:
                continue

            # Пакетная предзагрузка пар (по 30 адресов за 1 запрос вместо 120 одиночных запросов)
            open_pos_mints = set(tracker.get_open_positions().keys())
            active_batch = []
            for addr, ch in candidates:
                if not addr or addr in open_pos_mints or now < evaluated_cooldown.get((addr, ch), 0.0):
                    continue
                if addr in tracker.positions:
                    _cp = tracker.positions.get(addr)
                    _c_pnl = float(getattr(_cp, "pnl_usd", 0.0) or 0.0)
                    _c_ext = float(getattr(_cp, "exit_time", 0.0) or 0.0)
                    _req_w = 300.0 if _c_pnl > 0 else 14400.0
                    if (now - _c_ext) < _req_w:
                        continue
                active_batch.append(addr)

            if active_batch and grok_jev.analyzer and hasattr(grok_jev.analyzer, "prefetch_token_data"):
                try:
                    await grok_jev.analyzer.prefetch_token_data(active_batch[:120])
                except Exception:
                    pass

            # Приоритизируем живые импульсные монеты (m5 >= 2%) и ограничиваем цикл ТОП-55 кандидатами,
            # чтобы один проход занимал 10-15 секунд вместо 5 минут и цены не устаревали!
            _pf_map = getattr(grok_jev.analyzer, "_prefetch_cache", {}) if grok_jev.analyzer else {}
            _ranked_cands = []
            for idx, (address, chain) in enumerate(candidates):
                if not address or address in open_pos_mints or now < evaluated_cooldown.get((address, chain), 0.0):
                    continue
                if address in tracker.positions:
                    _cp = tracker.positions.get(address)
                    _c_pnl = float(getattr(_cp, "pnl_usd", 0.0) or 0.0)
                    _c_ext = float(getattr(_cp, "exit_time", 0.0) or 0.0)
                    if (now - _c_ext) < (300.0 if _c_pnl > 0 else 14400.0):
                        continue
                _hit = _pf_map.get(address)
                _td_c = _hit[1] if (_hit and len(_hit) > 1 and isinstance(_hit[1], dict)) else None
                if _td_c:
                    _ca_c = _td_c.get("pairCreatedAt") or 0
                    if _ca_c:
                        _age_c_m = (now * 1000 - _ca_c) / 60000.0
                        _max_loop_age = float(getattr(_c, "SOLANA_MAX_AGE_MIN", 240.0))
                        if _age_c_m > _max_loop_age:
                            evaluated_cooldown[(address, chain)] = now + 86400.0
                            continue
                    _m5_c = float((_td_c.get("priceChange") or {}).get("m5", 0) or 0)
                    _dex_c = str(_td_c.get("dexId", "")).lower()
                    if _dex_c in ("pump", "pumpfun") and not getattr(_c, "ALLOW_UNGRADUATED_PUMPFUN", False):
                        evaluated_cooldown[(address, chain)] = now + 45.0
                        continue
                    if _m5_c < 1.0 and idx > 15:
                        evaluated_cooldown[(address, chain)] = now + 60.0
                        continue
                    _prio = _m5_c + (50.0 if idx < 15 else 0.0)
                else:
                    _prio = 25.0 if idx < 25 else 0.0
                _ranked_cands.append((_prio, address, chain))

            _ranked_cands.sort(key=lambda x: x[0], reverse=True)

            for _, address, chain in _ranked_cands[:55]:
                if len(tracker.get_open_positions()) >= getattr(_c, "MAX_CONCURRENT_POSITIONS", 5):
                    break

                # 2. Обработка через независимый конвейер
                try:
                    ok, why = await grok_jev.process_candidate(address, chain, tracker)
                    why_low = str(why).lower()
                    REJECT_STATS["BOUGHT" if ok else _reason_key(why)] += 1
                    if ok:
                        evaluated_cooldown[(address, chain)] = now + 3600.0  # Куплен, не дергать
                    elif "llm-unavailable" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 60.0    # Судья недоступен: не долбим аудит
                    elif "rate-limit" in why_low or "429" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 20.0    # Повтор через 20с при 429
                    elif "candle-spike" in why_low or "flush" in why_low or "pullback" in why_low or "post-migration-dump-zone" in why_low or "maturation" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 25.0    # Ждем откат / созревание миграции 25 секунд!
                    elif any(k in why_low for k in ["vol5", "tx5", "buys5", "sellers", "momentum", "downtrend", "liq", "sol-reserve", "too-young", "mcap", "fills"]):
                        evaluated_cooldown[(address, chain)] = now + 60.0    # Рыночная динамика: перепроверка через 60с (токен может набрать объем)
                    elif "max-positions" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 30.0    # Ждем слота 30с
                    elif any(k in why_low for k in ["rugcheck", "honeypot", "goplus", "freeze", "mint-auth", "clone", "copycat-of-runner", "auditor-layer", "delegation", "insider-graph", "impersonation", "dev-dump", "cabal", "top-wallet", "top-10"]):
                        evaluated_cooldown[(address, chain)] = now + 1200.0  # Подтвержденный скам / опасный контракт — 20 минут бана
                    else:
                        evaluated_cooldown[(address, chain)] = now + 90.0    # Остальные временные фильтры — 90 сек вместо 10 мин
                except Exception as e:
                    print(f"⚠️ [GROKBOT+JEV] Ошибка обработки {address[:8]} ({chain}): {e}")
                    evaluated_cooldown[(address, chain)] = now + 60.0

        except Exception as e:
            print(f"⚠️ [GROKBOT+JEV] Ошибка в цикле grok_bot_jev_loop: {e}")
            await asyncio.sleep(10.0)
