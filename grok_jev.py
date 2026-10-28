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
    top1 = hs.get("top1_pct") or "NA"       # 0.0 в _h_stats = данных нет
    top10 = hs.get("top10_pct") or "NA"
    hold = hs.get("holders_count") or "NA"
    return (f"age={age}m liq={(pair.get('liquidity') or {}).get('usd', 'NA')} "
            f"mcap={pair.get('fdv') or pair.get('marketCap') or 'NA'} holders={hold} "
            f"top1={top1} top10={top10} dev={hs.get('dev_pct', 'NA')} "
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

    def __init__(self):
        # mint -> [(ts, price), ...] за последние 30 мин: нужна, чтобы отличать растущую монету от падающей
        self._px_hist: Dict[str, list] = {}

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
            from http_client import fetch_json
            wallets = _smart_wallets()
            key = (os.getenv("HELIUS_API_KEY") or getattr(_c, "HELIUS_API_KEY", "") or "").strip()
            if not key or not wallets:
                return None, None
            st, data = await asyncio.wait_for(fetch_json(
                f"https://api.helius.xyz/v0/addresses/{mint}/transactions",
                params={"api-key": key, "limit": 50}, timeout=5, retries=0), timeout=7.0)
            if st != 200 or not isinstance(data, list):
                _log_groq(f"helius history {mint[:6]}: HTTP {st}")
                return None, None
            payers = {str(t.get("feePayer") or "") for t in data if isinstance(t, dict)} - {""}
            return len(payers), len(payers & wallets)
        except Exception:
            return None, None

    def llm_ready(self) -> bool:
        """Есть ли шанс получить ответ судьи прямо сейчас (ключ, пауза 429, дневной бюджет)."""
        now = time.time()
        if not self.groq_key or now < _COOLDOWN_UNTIL:
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
        
        if not pair_data or not isinstance(pair_data, dict):
            return False, "no-pair-data"
            
        base = pair_data.get("baseToken", {}) or {}
        symbol = str(base.get("symbol", "")).upper().strip()
        dex_id = str(pair_data.get("dexId", "")).lower()
        is_pump = dex_id in ("pump", "pumpfun")
        
        # 0. Защита от фейковых клонов брендов и системных тикеров
        FAKE_BRAND_BLACKLIST = {
            "BASE", "SP500", "S&P500", "USDC", "USDT", "SOL", "ETH", "BTC", "SOLANA",
            "BINANCE", "COINBASE", "TESLA", "APPLE", "NVDA", "AMAZON", "GOOGLE", "MICROSOFT",
            "NINTENDO", "PLAYSTATION", "SONY", "DISNEY", "MARVEL", "POKEMON", "SEGA", "XBOX",
            "STEAM", "VALVE", "ROBLOX", "TIKTOK", "NETFLIX", "SPOTIFY", "PEPSI", "COCACOLA", "MCDONALDS"
        }
        if symbol in FAKE_BRAND_BLACKLIST or any(b in symbol for b in ("NINTENDO", "POKEMON", "PLAYSTATION", "DISNEY")):
            return False, f"fake-brand-clone {symbol}"

        # 1. Ликвидность (с защитой от гигантских не-мем пулов и фантомной ликвидности)
        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv_mc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        eff_liq = raw_liq if raw_liq > 0 else (fdv_mc * 0.40 if is_pump else 0.0)
        
        # ЗАЩИТА ОТ ФАНТОМНОЙ ЛИКВИДНОСТИ И ОДНОМИНУТНЫХ ДЕВ-ДАМПОВ:
        # В пуле ОБЯЗАН быть реальный резерв SOL (>= 20 SOL), иначе это пустой рагпулл.
        liq_dict = pair_data.get("liquidity") or {}
        quote_token_symbol = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
        quote_amount = float(liq_dict.get("quote", 0.0) or 0.0)
        if quote_token_symbol in ("SOL", "WSOL") and quote_amount > 0 and quote_amount < 20.0:
            return False, f"phantom-liq-trap: real pool SOL {quote_amount:.2f} < 20.0 SOL (dump risk like KAT/Nintendo)"

        # Жесткий порог ликвидности: настоящие ракеты (GTA $51k, KIMCHI $35k, TRUMP $40k) имеют глубину >= $14,000!
        min_liq = float(getattr(_c, "JEV_MIN_LIQUIDITY_USD", 14000.0))
        if eff_liq < min_liq:
            return False, f"liq-depth-low ${eff_liq:.0f} < ${min_liq:.0f} (micro-cap dump trap)"
            
        max_liq = float(getattr(_c, "JEV_MAX_LIQUIDITY_USD", 350000.0))
        if eff_liq > max_liq:
            return False, f"liq-giant ${eff_liq:,.0f} > ${max_liq:,.0f} (stagnant giant pool, not a meme rocket)"

        FUNNEL_STATS["liquidity"] += 1

        # 2. Возраст: не входить в первые 3.5 минуты (зона дампа создателя, кейс KAT на 0m!)        # 2. Возраст: строго 15 мин - 72 ч (как у savipww)
        created_at = pair_data.get("pairCreatedAt") or 0
        age_min = (time.time() * 1000 - created_at) / 60000.0 if created_at else 999.0

        txns_m5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        txns_h24 = (pair_data.get("txns") or {}).get("h24", {}) or {}
        b5 = int(txns_m5.get("buys", 0) or 0)
        s5 = int(txns_m5.get("sells", 0) or 0)
        vol_m5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        vol_h24 = float((pair_data.get("volume") or {}).get("h24", 0) or 0)
        trades_h24 = int(txns_h24.get("buys", 0) or 0) + int(txns_h24.get("sells", 0) or 0)

        # 3. Живой импульс ракеты (LIVE ROCKET MOMENTUM)
        pc = pair_data.get("priceChange", {}) or {}
        m1 = float(pc.get("m1", 0) or 0)
        m5 = float(pc.get("m5", 0) or 0)
        h1 = float(pc.get("h1", 0) or 0)

        # 2. Возраст: молодые пробои с перевесом покупок пускаем от 1.0м
        _early_min_age = float(getattr(_c, "JEV_EARLY_MIN_AGE_MIN", 1.0))
        _std_min_age = float(getattr(_c, "JEV_MIN_AGE_MIN", 1.5))
        min_age = _early_min_age if (b5 >= s5 and m5 >= 5.0) else _std_min_age
        max_age = float(getattr(_c, "JEV_MAX_AGE_MIN", 72.0 * 60.0))  # 72 часа
        if not (min_age <= age_min <= max_age):
            return False, f"age-out-of-bounds {age_min:.1f}m not in [{min_age:.1f}m, {max_age:.0f}m]"

        # Убрано требование m5 >= 4.0%: берем откаты на устоявшихся монетах, а не хаи
        if m5 > 180.0:
            return False, f"overheated-m5 {m5:+.0f}% > +180% (peak FOMO risk)"

        if m5 > 180.0:
            if not (-8.0 <= m1 <= 22.0 and b5 >= s5 * 1.15 and vol_m5 >= 5000.0 and m5 <= 300.0):
                return False, f"overheated m5 {m5:+.0f}% > +180% (peak FOMO risk)"
        _min_h1 = float(getattr(_c, "JEV_MIN_H1_PCT", -8.0))
        if h1 < _min_h1:
            return False, f"hourly-downtrend h1 {h1:+.1f}% < {_min_h1:+.1f}% (dead-cat bounce)"
        _h6_raw = pc.get("h6")
        if _h6_raw is not None:
            _min_h6 = float(getattr(_c, "JEV_MIN_H6_PCT", -1000.0))
            if float(_h6_raw or 0) < _min_h6:
                return False, f"h6-downtrend {float(_h6_raw or 0):+.1f}% < {_min_h6:+.1f}% (monet is bleeding)"

        # 4. Объем и давление покупателей (LIVE 5M ACTIVITY)
        # В 5-минутном окне ВСЕГДА должна быть живая активность
        if vol_m5 < 4000.0:
            return False, f"vol5-low ${vol_m5:.0f} < $4,000 (inactive dead pool)"
        if (b5 + s5) < 12:
            return False, f"tx5-low {b5+s5} < 12 (no live trading)"
        if b5 < 8:
            return False, f"buys5-low {b5} < 8 (no buyers)"
        _req_bs = float(getattr(_c, "MIN_BUY_SELL_RATIO", 1.15))
        if (b5 + s5) >= 60 or vol_m5 >= 15000.0:
            _req_bs = 1.02
        if s5 > 0 and (b5 / s5) < _req_bs:
            return False, f"sellers-dominating {b5}b/{s5}s (ratio {b5/s5:.2f} < {_req_bs:.2f})"

        # Проверка суточной активности h24 вместо h1 (vol >= $40k, trades >= 150)
# ЗАМЕНИ НА (для молодых токенов не требуем суточную историю):
        if age_min >= 60.0:  # Проверяем h24 ТОЛЬКО для старых токенов старше 1 часа
            min_vol_h24 = float(getattr(_c, "JEV_MIN_VOLUME_H24", 30000.0))
            min_trades_h24 = int(getattr(_c, "JEV_MIN_TRADES_H24", 100))
            if vol_h24 < min_vol_h24:
                return False, f"vol-h24-low ${vol_h24:,.0f} < ${min_vol_h24:,.0f}"
            if trades_h24 < min_trades_h24:
                return False, f"trades-h24-low {trades_h24} < {min_trades_h24}"

        # 4.1. Wash Trading Check (накрутка объёма ботами копеечными сделками)
        # 4.1. Wash Trading Check (накрутка объёма ботами копеечными сделками по h24)
        if trades_h24 >= 400 and vol_h24 > 0:
            avg_trade = vol_h24 / trades_h24
            if avg_trade < 12.0:
                return False, f"wash-trading-trap (avg trade ${avg_trade:.1f} < $12 across {trades_h24} txns)"

        # 5. Рыночная капитализация (MCap / FDV): минимум $48,000 для всех токенов
        # Отсекает микро-скамы pump.fun ($5k-$20k), где создатель держит 30% и сливает на входе (KAT)
        # Капитализация строго в коридоре $60,000 - $8,000,000
        mcap = fdv_mc
        min_mcap = float(getattr(_c, "JEV_MIN_MCAP_USD", 60000.0))
        max_mcap = float(getattr(_c, "JEV_MAX_MCAP_USD", 8000000.0))
        if mcap > 0 and not (min_mcap <= mcap <= max_mcap):
            return False, f"mcap-out-of-bounds ${mcap:,.0f} not in [${min_mcap:,.0f}, ${max_mcap:,.0f}]"

        # Распределение холдеров (savipww: >= 80 холдеров, Топ-1 <= 5%, Топ-10 <= 60%)
        if holder_stats and isinstance(holder_stats, dict):
            holders_count = int(holder_stats.get("holders_count", 0) or 0)
            if holders_count > 0 and holders_count < getattr(_c, "JEV_MIN_HOLDERS", 80):
                return False, f"holders-low {holders_count} < 80"

            top1_pct = float(holder_stats.get("top1_pct", 0.0) or 0.0)
            top10_pct = float(holder_stats.get("top10_pct", 0.0) or 0.0)
            if 0 < top1_pct <= 1.0:
                top1_pct *= 100.0
            if 0 < top10_pct <= 1.0:
                top10_pct *= 100.0

            max_top1 = float(getattr(_c, "JEV_MAX_TOP_WALLET", 0.05)) * 100.0   # <= 5%
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
            cache_ttl = 300.0 if hit_passed else 900.0
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
        m1 = float(pc.get("m1", 0) or 0)
        m5 = float(pc.get("m5", 0) or 0)
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

        # LLM не ответила -> отказ (fail-closed). "rate-limit" в причине: цикл повторит через 20с.
        if not llm_res:
            if not getattr(_c, "JEV_LLM_ENFORCE", True):
                print(f"👻 [JEV-SHADOW] {symbol}: судья молчит -> пропуск (shadow)")
                return True, {}, "shadow-no-llm"
            return False, {}, "llm-unavailable-retry"

        failed = _jev_failed(llm_res)
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
        m1 = float(pc.get("m1", 0) or 0)
        m5 = float(pc.get("m5", 0) or 0)
        txns_m5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        b5 = int(txns_m5.get("buys", 0) or 0)
        s5 = int(txns_m5.get("sells", 0) or 0)
        vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        
        # 1. Запрет покупать зеленую свечу на пике: вход ТОЛЬКО в откат или консолидацию m1
        if m1 > 1.5:
            return False, -0.12, 0.0, f"candle-spike (m1 {m1:+.1f}% > +1.5%) — bid the flush, wait for pullback"

        # 2. Откат не должен быть ножом падения (падение глубже -8% отсекается)
        if m1 < -8.0:
            return False, -0.12, 0.0, f"flush-dump (m1 {m1:+.1f}% < -8.0%) — falling knife"

        # 3. Если откат глубже -8.0% — это слом структуры / дамп
        if m1 < -8.0:
            return False, -0.14, 0.0, f"flush-dump (m1 {m1:+.1f}% < -8.0%) — falling knife"

        # 3.1. НЕ ПОКУПАЕМ КРАСНУЮ МИНУТУ: раньше пускало до m1 -8% ("bid the flush"), и бот входил в падающий нож.
        _m1_floor = float(getattr(_c, "JEV_MIN_M1_ENTRY_FLOOR_PCT", -8.0))
        if m1 < _m1_floor:
            return False, -0.14, 0.0, f"flush-wait red-minute (m1 {m1:+.1f}% < {_m1_floor:+.1f}%) — price is falling right now"

        # 3.2. ПОДТВЕРЖДЕНИЕ ТРЕНДА ПО СОБСТВЕННОЙ ИСТОРИИ ЦЕН (DexScreener m1/m5 запаздывают на минуту и больше)
        if getattr(_c, "JEV_TREND_CONFIRM_ENABLED", False):
            _mint = str(((pair_data.get("baseToken") or {}).get("address")) or "")
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
                    return False, -0.14, 0.0, "flush-wait no-trend-history (first sight, need a second price)"
                _eps = float(getattr(_c, "JEV_RISE_EPS", 0.002))
                if entry_price < _ref[-1] * (1.0 + _eps):
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
            "1. concentration_is_exit_risk (max 0.55 to pass): 0.15-0.35 if top1<=5% and top10<=50% with liq>=$15k; >0.60 if whales dominate.\n"
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
        # Если m5 < 4.0% или h1 < -8.0% — импульса нет, токен дохлый или в сливе (риск 0.95 = REJECT!)
        if m5 < 4.0 or h1 < -8.0:
            mom_score = 0.95
        elif m1 > 22.0:
            mom_score = 0.85  # Вертикальный прострел прямо сейчас в 1м свече (FOMO пик)
        elif m5 >= 5.0 and m1 <= 8.0:
            mom_score = 0.25  # Идеальный свежий импульс на откате / консолидации / старте
        elif m5 > 350.0 and m1 > 12.0:
            mom_score = 0.75  # Сильный перегрев
        else:
            mom_score = 0.40

        # 5. liquidity_fits_ticket
        # Для ордера $15-$50 ликвидность >= $7k дает оценку 0.70+ (глубина пула полностью достаточна)
        liq_score = min(1.0, max(0.4, liq / 10000.0))

        # 6. dev_still_loaded
        dev_score = min(1.0, max(0.05, dev_pct / 10.0))

        return {
            "account_is_the_project": round(account_score, 2),
            "recycled_account": round(recycled_score, 2),
            "concentration_is_exit_risk": round(conc_score, 2),
            "momentum_already_spent": round(mom_score, 2),
            "liquidity_fits_ticket": round(liq_score, 2),
            "dev_still_loaded": round(dev_score, 2),
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

        # 2. DexScreener Top Boosts & Profiles (Строго SOLANA, без мусорных latest бустов)
        from http_client import fetch_json
        for url in [
            "https://api.dexscreener.com/token-boosts/top/v1",
            "https://api.dexscreener.com/token-profiles/latest/v1"
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

        # 3. GeckoTerminal Solana (Trending Pools) — 100% фокус на проверенных трендах
        for net, endp in [
            ("solana", "trending_pools")
        ]:
            try:
                url_gt = f"https://api.geckoterminal.com/api/v2/networks/{net}/{endp}"
                st_gt, data_gt = await fetch_json(url_gt, headers={"Accept": "application/json"}, timeout=5, retries=0)
                if st_gt == 200 and isinstance(data_gt, dict):
                    pools = data_gt.get("data", []) or []
                    for p in pools:
                        rel = p.get("relationships", {}) or {}
                        base_tok = (rel.get("base_token", {}) or {}).get("data", {}) or {}
                        tok_id = str(base_tok.get("id", "")).replace(f"{net}_", "").strip()
                        if tok_id and tok_id not in seen:
                            seen.add(tok_id)
                            candidates.append((tok_id, "solana"))
            except Exception:
                pass

        FUNNEL_STATS["universe"] += len(candidates)
        return candidates

    async def fetch_pair(self, address: str, chain: str) -> Optional[dict]:
        """Прямой запрос рыночных данных пары из DexScreener (с поддержкой пакетного кэша analyzer)."""
        if hasattr(self, "analyzer") and self.analyzer and hasattr(self.analyzer, "get_token_data"):
            try:
                cached_or_pair = await self.analyzer.get_token_data(address, chain=chain)
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
                if not pairs:
                    return None
                matching = [p for p in pairs if str(p.get("chainId", "")).lower() == chain.lower()]
                target = matching if matching else pairs
                best = max(target, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
                return best
        except Exception:
            pass
        return None

    async def fetch_pair_fresh(self, address: str, chain: str) -> Optional[dict]:
        """Свежие данные DexScreener в обход кэша analyzer (кэш держит данные до 60 секунд и дольше)."""
        from http_client import fetch_json
        try:
            st, data = await fetch_json(f"https://api.dexscreener.com/latest/dex/tokens/{address}", timeout=5, retries=1)
            if st == 200 and isinstance(data, dict):
                pairs = [p for p in (data.get("pairs") or []) if str(p.get("chainId", "")).lower() == chain.lower()]
                if pairs:
                    return max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
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
        _h_stats = {"top1_pct": 0.0, "top10_pct": 0.0, "dev_pct": 0.0}
        base_info = pair_data.get("baseToken", {}) or {}
        name = str(base_info.get("name", "")).upper()
        symbol_upper = symbol.upper().strip()

        # 1. Запрещенные ключевые слова в названии / тикере
        bad_words = ["test", "scam", "nigger", "fuck", "pump and dump", "rug", "airdrop", "presale", "honeypot", "drain"]
        if any(w in name.lower() for w in bad_words) or any(w in symbol.lower() for w in bad_words):
            return False, f"bad-word-scam in name/symbol ({name} / {symbol})", _h_stats

        corporate_scam_keywords = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "XAI", "NEURALINK", "PAYPAL",
                                  "NVDA", "GOOG", "AMZN", "META", "NFLX", "ANTHROPIC", "OPENAI", "PERPLEXITY"]
        if any(k == symbol_upper for k in corporate_scam_keywords):
            return False, f"corporate-brand-mimic {symbol_upper}", _h_stats

        # 2. Аудит соцсетей и анонимности создателя
        info = pair_data.get("info", {}) or {}
        socials = info.get("socials", []) or []
        websites = info.get("websites", []) or []
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        has_socials = bool(has_twitter or has_tg or has_website)

        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        dex_id = str(pair_data.get("dexId", "")).lower()
        is_pump = dex_id in ("pump", "pumpfun")
        eff_liq = max(raw_liq, fdv * 0.40) if is_pump else raw_liq

        # Анонимные токены без соцсетей при пуле < $35k — это скам-бандлер, если нет подтвержденного органического потока или pump.fun
        _tx5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5 = int(_tx5.get("buys", 0) or 0)
        _s5 = int(_tx5.get("sells", 0) or 0)
        _vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        _is_organic_flow = (_b5 >= 20 and _b5 >= _s5 and _vol5 >= 3000.0)
        _is_pump_token = is_pump or address.endswith("pump")
        if not has_socials and eff_liq < 35000.0 and not _is_pump_token and not _is_organic_flow:
            return False, f"no-socials-anon-rug (0 socials with <$35k liq — 100% anon dev dump)", _h_stats

        # 3. Аудит для сети SOLANA
        if chain == "solana":
            import goplus as _gp

            # А) Мгновенный ончейн-аудит через RPC
            onchain_ok, onchain_why = await _gp.check_onchain_solana_security(address)
            if not onchain_ok:
                return False, f"onchain-sol-reject {onchain_why}", _h_stats

            # Б) GoPlus Security (пропускаем сырой rugcheck-score из goplus.py, т.к. ниже идёт детальный разбор RugCheck)
            sec_ok, sec_why = await _gp.check_solana(address)
            if not sec_ok and "rugcheck-score" not in str(sec_why).lower():
                return False, f"goplus-sol-reject {sec_why}", _h_stats
              
            # В) Полный отчет RugCheck API
            try:
                from http_client import fetch_json
                rc_url = f"https://api.rugcheck.xyz/v1/tokens/{address}/report"
                rc_status, rc_data = await fetch_json(rc_url, timeout=5, retries=1)
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

                    # Очищенный score RugCheck: для pump.fun бондинг-кривой вычитаем штраф за ликвидность,
                    # но для мигрировавших DEX токенов низкая ликвидность — это смертельный признак рагпулла!
                    raw_score = float(rc_data.get("score") or 0.0)
                    if is_pump:
                        low_liq_penalty = sum(
                            float(rk.get("score", 0) or 0)
                            for rk in (rc_data.get("risks") or [])
                            if "low liquidity" in str(rk.get("name", "")).lower()
                        )
                        clean_score = max(0.0, raw_score - low_liq_penalty)
                    else:
                        clean_score = raw_score
                    if clean_score >= 600:
                        return False, f"rugcheck: high risk score {clean_score:.0f} >= 600", _h_stats

                    # Проверка критических рисков
                    for rk in (rc_data.get("risks") or []):
                        rk_name = str(rk.get("name") or "").lower()
                        rk_lvl = str(rk.get("level") or "").lower()
                        if is_pump and "low liquidity" in rk_name:
                            continue
                        if rk_lvl == "danger":
                            return False, f"rugcheck: critical danger risk '{rk.get('name')}'", _h_stats
                        if "unlocked" in rk_name and "lp" in rk_name:
                            return False, f"rugcheck: unlocked LP risk", _h_stats

                    # Проверка пулов и блокировки LP для не-pump токенов
                    target_pair = str(pair_data.get("pairAddress") or "").strip()
                    has_verified_locked_lp = False
                    for mkt in (rc_data.get("markets") or []):
                        if isinstance(mkt, dict):
                            mkt_pubkey = str(mkt.get("pubkey") or "").strip()
                            lp_info = mkt.get("lp") or {}
                            if isinstance(lp_info, dict):
                                mkt_quote_usd = float(lp_info.get("quoteUSD") or 0.0)
                                lp_unlocked = float(lp_info.get("lpUnlocked") or 0.0)
                                lp_locked_pct = float(lp_info.get("lpLockedPct") or 0.0)
                                if lp_locked_pct >= 80.0 and (mkt_quote_usd >= 2000 or (target_pair and mkt_pubkey == target_pair)):
                                    has_verified_locked_lp = True
                                if (target_pair and mkt_pubkey == target_pair) or mkt_quote_usd >= 2000:
                                    if lp_unlocked > 0 and lp_locked_pct < 80.0:
                                        return False, f"rugcheck: LP not locked ({lp_locked_pct:.1f}%)", _h_stats
                    if not address.endswith("pump") and not has_verified_locked_lp:
                        return False, "rugcheck: non-pump token without verified locked LP", _h_stats

                    # Проверка держателей и дева (Holders & Sybil analysis)
                    holders = rc_data.get("topHolders", []) or []
                    pool_accounts = set()
                    if pair_data.get("pairAddress"):
                        pool_accounts.add(pair_data["pairAddress"])
                    for p in (pair_data.get("pairs") or []):
                        if isinstance(p, dict) and p.get("pairAddress"):
                            pool_accounts.add(p["pairAddress"])
                    for k_acc, v_acc in (rc_data.get("knownAccounts") or {}).items():
                        if isinstance(v_acc, dict) and v_acc.get("type") in ("AMM", "LOCKER"):
                            pool_accounts.add(k_acc)
                    for mkt in (rc_data.get("markets") or []):
                        if isinstance(mkt, dict):
                            for m_field in ("pubkey", "liquidityA", "liquidityB"):
                                if mkt.get(m_field):
                                    pool_accounts.add(mkt[m_field])

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
                        if owner in pool_accounts or addr in pool_accounts or owner == "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1":
                            continue
                        if any(w in owner.lower() for w in pool_keywords):
                            continue
                        if pct > 80.0:
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
                    if not real_creator and top_10:
                        dev_pct = largest_whale

                    _h_stats["top10_pct"] = top_10_sum
                    _h_stats["dev_pct"] = dev_pct
                    _h_stats["top1_pct"] = largest_whale

                    if dev_pct > 10.0:
                        return False, f"dev still loaded {dev_pct:.1f}% > 10.0% (dump risk)", _h_stats
                    if largest_whale > 15.0:
                        return False, f"whale concentration {largest_whale:.1f}% > 15.0%", _h_stats
                    max_top10 = 30.0 if is_pump else 35.0
                    if top_10_sum > max_top10:
                        return False, f"top 10 concentration {top_10_sum:.1f}% > {max_top10:.0f}%", _h_stats

            except Exception as e:
                # Фоллбэк: если RugCheck недоступен, проверяем GoPlus holder stats
                _h_raw = _gp.get_solana_holder_stats(address)
                if _h_raw and _h_raw.get("has_data"):
                    _h_stats["top10_pct"] = float(_h_raw.get("top_10_pct", 0.0) or 0.0)
                    _h_stats["dev_pct"] = float(_h_raw.get("dev_pct", 0.0) or 0.0)
                    _h_stats["top1_pct"] = _h_stats["dev_pct"]
                    if _h_stats["dev_pct"] > 10.0:
                        return False, f"dev loaded (GoPlus) {_h_stats['dev_pct']:.1f}% > 10%", _h_stats
                    if _h_stats["top10_pct"] > 35.0:
                        return False, f"top10 concentration (GoPlus) {_h_stats['top10_pct']:.1f}% > 35%", _h_stats

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
                    shadow_tracker.log_rejection(
                        mint=address, reason=str(why)[:140], symbol=base.get("symbol", address[:6]), chain="solana",
                        price=float(pair.get("priceUsd", 0) or 0),
                        liquidity=float((pair.get("liquidity") or {}).get("usd", 0) or 0),
                        m5_pct=float((pair.get("priceChange") or {}).get("m5", 0) or 0),
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
        if not address or address in tracker.positions:
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

        # 3.5. Судья недоступен (нет ключа / 429 / бюджет) -> не жжём RugCheck/Jupiter впустую
        if getattr(_c, "JEV_LLM_ENFORCE", True) and not self.llm_ready():
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

        # 4.5. Нет данных о холдерах (RugCheck/GoPlus молчат) -> не входим вслепую: все сливы за сутки прошли именно так
        if getattr(_c, "JEV_REQUIRE_HOLDER_DATA", True):
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

        # 6. CHIEF / GROK TRADES: Исполнение ордера
        if len(tracker.get_open_positions()) >= getattr(_c, "MAX_CONCURRENT_POSITIONS", 10):
            return False, "max-positions-reached"

        # Расчет размера билета (Dynamic Sizing / Compounding)
        ticket_size = float(getattr(_c, "BASE_TRADE_AMOUNT_USD", 15.0) or 15.0)
        if getattr(_c, "DYNAMIC_REINVEST_ENABLED", True) or getattr(_c, "COMPOUNDING_ENABLED", True):
            try:
                ticket_size = tracker.calculate_dynamic_position_size(pair, chain=chain)
            except Exception:
                pass
        ticket_size = max(10.0, ticket_size)
        _cap = float(getattr(_c, "JEV_MAX_TICKET_USD", 15.0) or 0.0)
        if _cap > 0:
            ticket_size = min(ticket_size, _cap)   # растущий билет ($31) умножал убыток на каждом сливе

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
        m1 = float(pc.get("m1", 0) or 0)
        m5 = float(pc.get("m5", 0) or 0)
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
            active_batch = [
                addr for addr, ch in candidates
                if addr and addr not in tracker.positions and now >= evaluated_cooldown.get((addr, ch), 0.0)
            ]
            if active_batch and grok_jev.analyzer and hasattr(grok_jev.analyzer, "prefetch_token_data"):
                try:
                    await grok_jev.analyzer.prefetch_token_data(active_batch, chain="solana")
                except Exception:
                    pass

            for address, chain in candidates:
                if not address or address in tracker.positions:
                    continue
                # Адаптивный кулдаун
                retry_until = evaluated_cooldown.get((address, chain), 0.0)
                if now < retry_until:
                    continue

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
                    elif "candle-spike" in why_low or "flush" in why_low or "pullback" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 25.0    # Ждем откат 25 секунд!
                    elif "vol5-low" in why_low or "tx5-low" in why_low or "no-momentum" in why_low or "downtrend" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 45.0    # Набор объема, проверка через 45с
                    elif "max-positions" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 30.0    # Ждем слота 30с
                    else:
                        evaluated_cooldown[(address, chain)] = now + 600.0   # Скамы/непроход - 10 мин
                except Exception as e:
                    print(f"⚠️ [GROKBOT+JEV] Ошибка обработки {address[:8]} ({chain}): {e}")
                    evaluated_cooldown[(address, chain)] = now + 60.0

        except Exception as e:
            print(f"⚠️ [GROKBOT+JEV] Ошибка в цикле grok_bot_jev_loop: {e}")
            await asyncio.sleep(10.0)
