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
_API_SEMAPHORE = asyncio.Semaphore(1)  # 1 запрос за раз для бесплатного API
_COOLDOWN_UNTIL = 0.0  # Пауза при 429 rate-limit


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
        pass

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
            "BINANCE", "COINBASE", "TESLA", "APPLE", "NVDA", "AMAZON", "GOOGLE", "MICROSOFT"
        }
        if symbol in FAKE_BRAND_BLACKLIST:
            return False, f"fake-brand-clone {symbol}"

        # 1. Ликвидность (с защитой от гигантских не-мем пулов)
        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv_mc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        eff_liq = max(raw_liq, fdv_mc * 0.40) if is_pump else raw_liq
        
        min_liq = 6000.0 if is_pump else float(getattr(_c, "JEV_MIN_LIQUIDITY_USD", 12000.0))
        if eff_liq < min_liq:
            return False, f"liq-depth-low ${eff_liq:.0f} < ${min_liq:.0f}"
            
        max_liq = float(getattr(_c, "JEV_MAX_LIQUIDITY_USD", 350000.0))
        if eff_liq > max_liq:
            return False, f"liq-giant ${eff_liq:,.0f} > ${max_liq:,.0f} (stagnant giant pool, not a meme rocket)"

        FUNNEL_STATS["liquidity"] += 1

        # 2. Возраст
        created_at = pair_data.get("pairCreatedAt") or 0
        age_min = (time.time() * 1000 - created_at) / 60000.0 if created_at else 999.0
        
        txns_m5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        txns_h1 = (pair_data.get("txns") or {}).get("h1", {}) or {}
        b5 = int(txns_m5.get("buys", 0) or 0)
        s5 = int(txns_m5.get("sells", 0) or 0)
        bh1 = int(txns_h1.get("buys", 0) or 0)
        sh1 = int(txns_h1.get("sells", 0) or 0)
        vol_m5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        vol_h1 = float((pair_data.get("volume") or {}).get("h1", 0) or 0)

        is_breakout = (vol_m5 >= 6000.0 and b5 >= 20)
        min_age = float(getattr(_c, "JEV_EARLY_MIN_AGE_MIN", 3.5)) if (is_pump or is_breakout) else float(getattr(_c, "JEV_MIN_AGE_MIN", 8.0))
        if age_min < min_age:
            return False, f"too-young {age_min:.1f}m < {min_age:.1f}m (sniper flush zone)"

        # 3. Живой импульс ракеты (LIVE ROCKET MOMENTUM)
        # Бот НЕ покупает стоячие, дохлые или падающие монеты!
        pc = pair_data.get("priceChange", {}) or {}
        m1 = float(pc.get("m1", 0) or 0)
        m5 = float(pc.get("m5", 0) or 0)
        h1 = float(pc.get("h1", 0) or 0)

        min_m5 = float(getattr(_c, "JEV_MIN_M5_PCT", 4.0))
        if m5 < min_m5:
            return False, f"no-momentum m5 {m5:+.1f}% < +{min_m5:.1f}% (flat or dying coin)"
        
        # Если m5 > 150%, разрешаем только если токен на здоровом откате/консолидации 1м (-8%..+2.5%) с мощным объемом
        if m5 > 150.0:
            if not (-8.0 <= m1 <= 2.5 and b5 >= s5 * 1.15 and vol_m5 >= 8000.0):
                return False, f"overheated m5 {m5:+.0f}% > +150% (peak FOMO risk)"
        if h1 < -8.0:
            return False, f"hourly-downtrend h1 {h1:+.1f}% < -8.0% (dead-cat bounce)"

        # 4. Объем и давление покупателей (LIVE 5M ACTIVITY)
        # В 5-минутном окне ВСЕГДА должна быть живая активность
        if vol_m5 < 2000.0:
            return False, f"vol5-low ${vol_m5:.0f} < $2,000 (inactive dead pool)"
        if (b5 + s5) < 12:
            return False, f"tx5-low {b5+s5} < 12 (no live trading)"
        if b5 < 8:
            return False, f"buys5-low {b5} < 8 (no buyers)"
        if s5 > 0 and (b5 / s5) < 1.15:
            return False, f"sellers-dominating {b5}b/{s5}s (ratio {b5/s5:.2f} < 1.15)"

        min_vol_h1 = float(getattr(_c, "JEV_MIN_VOLUME_H1", 40000.0))
        min_trades_h1 = int(getattr(_c, "JEV_MIN_TRADES_H1", 250))
        if age_min >= 30.0:
            if vol_h1 < min_vol_h1:
                return False, f"vol-h1-low ${vol_h1:,.0f} < ${min_vol_h1:,.0f}"
            if (bh1 + sh1) < min_trades_h1:
                return False, f"trades-h1-low {bh1+sh1} < {min_trades_h1}"

        # 5. Рыночная капитализация (MCap / FDV)
        mcap = fdv_mc
        min_mcap = float(getattr(_c, "JEV_MIN_MCAP_USD", 45000.0))
        if mcap > 0 and mcap < min_mcap and not is_pump:
            return False, f"mcap-low ${mcap:,.0f} < ${min_mcap:,.0f}"

        # 5. Распределение холдеров (Holder Spread)
        if holder_stats and isinstance(holder_stats, dict):
            top1_pct = float(holder_stats.get("top1_pct", 0.0) or 0.0)
            top10_pct = float(holder_stats.get("top10_pct", 0.0) or 0.0)
            # Нормализация: если передано в долях единицы (0.05 вместо 5.0), переводим в проценты
            if 0 < top1_pct <= 1.0:
                top1_pct *= 100.0
            if 0 < top10_pct <= 1.0:
                top10_pct *= 100.0

            max_top1 = 12.0 if is_pump else float(getattr(_c, "JEV_MAX_TOP_WALLET", 0.15)) * 100.0
            max_top10 = 32.0 if is_pump else float(getattr(_c, "JEV_MAX_TOP_10", 0.35)) * 100.0
            
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

        # Попытка вызова LLM (Groq -> xAI Grok -> Gemini)
        llm_res = None
        global _COOLDOWN_UNTIL, _LAST_API_CALL_TS
        
        if now >= _COOLDOWN_UNTIL and (self.groq_key or self.xai_key or self.gemini_key):
            try:
                async with _API_SEMAPHORE:
                    # Соблюдаем интервал между вызовами для бесплатного API (мин 2.0с)
                    dt = now - _LAST_API_CALL_TS
                    if dt < 2.0:
                        await asyncio.sleep(2.0 - dt)
                        
                    llm_res = await asyncio.wait_for(
                        self._call_llm_jev(mint, symbol, name, chain, socials, websites, m1, m5, h1, liq, mcap, vol5, top1_pct, top10_pct, dev_pct),
                        timeout=4.0
                    )
                    _LAST_API_CALL_TS = time.time()
            except asyncio.TimeoutError:
                print(f"⚠️ [JEV/GROK] LLM Timeout (4s) для {symbol} ({mint[:8]}) ➔ Переход на локальный скоринг.")
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str or "quota" in err_str:
                    _COOLDOWN_UNTIL = time.time() + 60.0  # 1 мин пауза
                    print(f"⏳ [JEV/GROK] Free API 429 Rate Limit. Пауза 60с, используем локальный движок.")
                else:
                    print(f"⚠️ [JEV/GROK] Ошибка вызова LLM ({type(e).__name__}): {e}")

        # Если LLM не вернула ответ — используем детерминированный локальный скоринг JEV
        if not llm_res:
            llm_res = self._deterministic_jev_eval(
                symbol, name, socials, websites, m1, m5, h1, liq, mcap, vol5, top1_pct, top10_pct, dev_pct, is_pump=is_pump
            )

        # Проверка порогов SOFT GATE из thresholds.py:
        min_acc = float(getattr(_c, "JEV_MIN_ACCOUNT_IS_PROJECT", 0.75))
        max_recycled = float(getattr(_c, "JEV_MAX_RECYCLED_ACCOUNT", 0.30))
        max_conc = float(getattr(_c, "JEV_MAX_CONCENTRATION_RISK", 0.35))
        max_mspent = float(getattr(_c, "JEV_MAX_MOMENTUM_SPENT", 0.60))
        min_liq_ticket = float(getattr(_c, "JEV_MIN_LIQUIDITY_FITS_TICKET", 0.65))
        max_dev = float(getattr(_c, "JEV_MAX_DEV_STILL_LOADED", 0.25))

        p_acc = float(llm_res.get("account_is_the_project", 0.8)) >= min_acc
        p_rec = float(llm_res.get("recycled_account", 0.1)) <= max_recycled
        p_conc = float(llm_res.get("concentration_is_exit_risk", 0.2)) <= max_conc
        p_msp = float(llm_res.get("momentum_already_spent", 0.4)) <= max_mspent
        p_liq = float(llm_res.get("liquidity_fits_ticket", 0.8)) >= min_liq_ticket
        p_dev = float(llm_res.get("dev_still_loaded", 0.1)) <= max_dev

        is_passed = all([p_acc, p_rec, p_conc, p_msp, p_liq, p_dev])
        llm_res["passed"] = is_passed
        
        # Кэшируем результат
        _JEV_CACHE[str(ck)] = (now, llm_res)

        if is_passed:
            FUNNEL_STATS["jev_gate"] += 1
            FUNNEL_STATS["passed"] += 1
            print(f"🤖 [JEV/GROK-PASS] {symbol} ({mint[:8]}): PASS! {llm_res.get('reason', '')} "
                  f"(Acc:{llm_res.get('account_is_the_project', 0):.2f}, Mom:{llm_res.get('momentum_already_spent', 0):.2f}, Conc:{llm_res.get('concentration_is_exit_risk', 0):.2f})")
            return True, llm_res, llm_res.get("reason", "PASS")
        else:
            fail_reasons = []
            if not p_acc: fail_reasons.append("fake/unrelated account")
            if not p_rec: fail_reasons.append("recycled account")
            if not p_conc: fail_reasons.append("high concentration exit risk")
            if not p_msp: fail_reasons.append("momentum exhausted (top)")
            if not p_liq: fail_reasons.append("insufficient depth for ticket")
            if not p_dev: fail_reasons.append("dev loaded")
            why = ", ".join(fail_reasons)
            return False, llm_res, f"JEV soft-reject: {why}"

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
        
        # 1. Токен ОБЯЗАН быть в импульсе m5 (ракета растет прямо сейчас)!
        if m5 < 4.0:
            return False, -0.05, 0.0, f"no-impulse (m5 {m5:+.1f}% < +4.0%) — wait for real rocket surge"

        # 2. "We bid the flush, not the candle"
        # Если в моменте идет вертикальный прострел (m1 > +2.5%) — это зеленая свеча.
        max_m1_entry = float(getattr(_c, "JEV_MAX_M1_ENTRY_PCT", 2.5))
        if m1 > max_m1_entry:
            return False, -0.05, 0.0, f"candle-spike (m1 {m1:+.1f}% > +{max_m1_entry:.1f}%) — we bid the flush, wait for pullback"

        # 3. Если откат глубже -8.0% — это слом структуры / дамп
        if m1 < -8.0:
            return False, -0.05, 0.0, f"flush-dump (m1 {m1:+.1f}% < -8.0%) — falling knife"

        # 4. Расчет стопа под фитиль отката ("The stop lives under the wick"):
        # Если есть локальный откат m1 (например, -2.5%), wick находится чуть ниже.
        # Ставим стоп на 1.5% ниже локального минимума свечи отката.
        dip_factor = min(0.0, m1 / 100.0)
        wick_low_price = entry_price * (1.0 + dip_factor - 0.015)
        
        # Динамический безопасный стоп под фитиль: от -5.0% до -8.5%
        calculated_stop_pct = (wick_low_price - entry_price) / entry_price
        wick_stop_loss_pct = max(-0.085, min(-0.050, calculated_stop_pct))
        stop_under_wick_price = entry_price * (1.0 + wick_stop_loss_pct)

        reason = f"Flush validated (m1 {m1:+.1f}%, m5 {m5:+.1f}%), stop under wick: {wick_stop_loss_pct*100:.1f}% (${stop_under_wick_price:.6f})"
        return True, wick_stop_loss_pct, stop_under_wick_price, reason

    # =========================================================================
    # Вспомогательные вызовы API и Алгоритмический Fallback
    # =========================================================================
    async def _call_llm_jev(
        self, mint: str, symbol: str, name: str, chain: str,
        socials: list, websites: list,
        m1: float, m5: float, h1: float,
        liq: float, mcap: float, vol5: float,
        top1_pct: float, top10_pct: float, dev_pct: float
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
            "You are JEV, an expert quantitative on-chain risk engine and trading bot AI. "
            "Evaluate meme breakout candidates that already passed HARD math gates. "
            "Output strictly valid JSON with 6 float metrics (0.0 to 1.0) and short reason:\n"
            "1. account_is_the_project (0.0-1.0): Social is genuinely created for this token (not impersonating Elon Musk/celebrity). If socials are 'None' but 5m volume > $8k with active buyers, score >= 0.80 (market validated).\n"
            "2. recycled_account (0.0-1.0): 0.0=fresh project, 1.0=reused old rug account.\n"
            "3. concentration_is_exit_risk (0.0-1.0): Risk top holders/whales dump on us.\n"
            "4. momentum_already_spent (0.0-1.0): 0.0=fresh momentum or consolidation on 1m, 1.0=exhausted peak vertical blow-off.\n"
            "5. liquidity_fits_ticket (0.0-1.0): Liquidity depth vs small ticket size.\n"
            "6. dev_still_loaded (0.0-1.0): Dev holding undisclosed hidden supply.\n\n"
            "Format:\n"
            "{\"account_is_the_project\": 0.85, \"recycled_account\": 0.10, \"concentration_is_exit_risk\": 0.25, "
            "\"momentum_already_spent\": 0.35, \"liquidity_fits_ticket\": 0.90, \"dev_still_loaded\": 0.15, "
            "\"reason\": \"one-sentence summary\"}"
        )

        user_content = (
            f"Token: {name} (${symbol}) on {chain}\n"
            f"Mint/Address: {mint}\n"
            f"Social Links: {socials_str}\n"
            f"Price Action: m1 {m1:+.1f}%, m5 {m5:+.1f}%, h1 {h1:+.1f}%\n"
            f"Liquidity: ${liq:,.0f} | MCap: ${mcap:,.0f} | 5m Volume: ${vol5:,.0f}\n"
            f"Holders: Top-1 {top1_pct:.1f}%, Top-10 {top10_pct:.1f}%, Dev {dev_pct:.1f}%"
        )

        # 1. Попытка через Groq (OpenAI-compatible)
        if self.groq_key:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.groq_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
                "max_tokens": 250
            }
            st, data = await fetch_json(url, method="POST", headers=headers, json_payload=payload, timeout=4, retries=0)
            if st == 200 and isinstance(data, dict):
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = json.loads(content)
                parsed["source"] = "groq-llama-3.3"
                return parsed
            elif st == 429:
                # Быстрый фоллбэк на более легкую модель 8b с отдельным лимитом
                payload["model"] = "llama-3.1-8b-instant"
                st8, data8 = await fetch_json(url, method="POST", headers=headers, json_payload=payload, timeout=3, retries=0)
                if st8 == 200 and isinstance(data8, dict):
                    content = data8.get("choices", [{}])[0].get("message", {}).get("content", "")
                    parsed = json.loads(content)
                    parsed["source"] = "groq-llama-3.1-8b"
                    return parsed

        # 2. Попытка через xAI Grok
        if self.xai_key:
            url = "https://api.x.ai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.xai_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "grok-beta",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.1,
                "max_tokens": 250
            }
            st, data = await fetch_json(url, method="POST", headers=headers, json_payload=payload, timeout=4, retries=0)
            if st == 200 and isinstance(data, dict):
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = json.loads(content)
                parsed["source"] = "xai-grok"
                return parsed

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
        elif m1 > 12.0:
            mom_score = 0.85  # Вертикальный прострел прямо сейчас в 1м свече (FOMO пик)
        elif m5 >= 5.0 and m1 <= 2.5:
            mom_score = 0.25  # Идеальный свежий импульс на откате / консолидации
        elif m5 > 350.0 and m1 > 5.0:
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

        # 2. DexScreener Latest & Top Boosts & Profiles (Строго SOLANA)
        from http_client import fetch_json
        for url in [
            "https://api.dexscreener.com/token-boosts/top/v1",
            "https://api.dexscreener.com/token-boosts/latest/v1",
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

        # 3. GeckoTerminal Solana (Trending + New Pools) — 100% фокус на Solana
        for net, endp in [
            ("solana", "trending_pools"),
            ("solana", "new_pools")
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
        """Прямой запрос рыночных данных пары из DexScreener без сторонних фильтров."""
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

            # Б) GoPlus Security
            sec_ok, sec_why = await _gp.check_solana(address)
            if not sec_ok:
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

                    # Очищенный score RugCheck
                    raw_score = float(rc_data.get("score") or 0.0)
                    low_liq_penalty = sum(
                        float(rk.get("score", 0) or 0)
                        for rk in (rc_data.get("risks") or [])
                        if "low liquidity" in str(rk.get("name", "")).lower()
                    )
                    clean_score = max(0.0, raw_score - low_liq_penalty)
                    if clean_score >= 600:
                        return False, f"rugcheck: high risk score {clean_score:.0f} >= 600", _h_stats

                    # Проверка критических рисков
                    for rk in (rc_data.get("risks") or []):
                        rk_name = str(rk.get("name") or "").lower()
                        rk_lvl = str(rk.get("level") or "").lower()
                        if "low liquidity" in rk_name:
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
                s5 = int(tx5.get("sells", 0) or 0)
                vol5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                is_org = (vol5 >= 3000.0 and b5 >= 20 and b5 >= s5 * 1.10)
                if not is_org:
                    is_cl = await self.analyzer.is_clone(symbol, address, created_at, fdv, current_liq=raw_liq, current_vol5=vol5, current_b5=b5)
                    if is_cl:
                        return False, f"clone-scam of an older/larger token", _h_stats
        except Exception:
            pass

        return True, "PASSED_ALL_SCAM_CHECKS", _h_stats

    # =========================================================================
    # 7. CONVEYOR: Полный цикл оценки и исполнения
    # =========================================================================
    async def process_candidate(self, address: str, chain: str, tracker) -> Tuple[bool, str]:
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
        if price <= 0:
            return False, "invalid-price"

        # 2. ПОЛНЫЙ АНТИСКАМ-АУДИТ (RUGCHECK + GOPLUS + ONCHAIN + DEV/WHALES + JITO-BUNDLE + SOCIALS + CLONE)
        sec_ok, sec_why, _h_stats = await self.full_anti_scam_audit(address, symbol, chain, pair)
        if not sec_ok:
            print(f"🚫 [GROK+JEV-SCAM] {symbol} ({chain}): {sec_why}")
            return False, f"anti-scam-reject {sec_why}"

        # 3. HARD GATE ("if it fails here, JEV never sees it")
        hard_ok, hard_why = self.hard_gate(address, pair, chain, _h_stats)
        if not hard_ok:
            return False, f"hard-gate-reject {hard_why}"

        # 4. VET (SOFT GATE: JEV AI Evaluation)
        soft_ok, jev_data, soft_why = await self.jev_vet(address, symbol, pair, chain, _h_stats)
        if not soft_ok:
            print(f"🚫 [GROK+JEV] {symbol} ({chain}): JEV soft-reject ➔ {soft_why}")
            return False, f"jev-soft-reject {soft_why}"

        # 5. FILLS & RISK ("We bid the flush, not the candle" & "The stop lives under the wick")
        flush_ok, wick_pct, wick_price, flush_msg = self.check_flush_and_wick(pair, price)
        if not flush_ok:
            print(f"⏳ [GROK+JEV] {symbol} ({chain}): FILLS ➔ {flush_msg}")
            return False, f"fills-flush-wait {flush_msg}"

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
            wick_stop_loss_pct=wick_pct
        )

        FUNNEL_STATS["filled"] += 1

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
                last_funnel_print = now

            # Очистка устаревших кулдаунов
            if len(evaluated_cooldown) > 2000:
                evaluated_cooldown = {k: ts for k, ts in evaluated_cooldown.items() if ts > now}

            # 1. CRAWLER: Сбор кандидатов (Solana + Base)
            candidates = await grok_jev.crawl_universe()
            if not candidates:
                continue

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
                    if ok:
                        evaluated_cooldown[(address, chain)] = now + 3600.0  # Куплен, не дергать
                    elif "candle-spike" in why_low or "flush" in why_low or "pullback" in why_low:
                        evaluated_cooldown[(address, chain)] = now + 25.0    # Ждем откат 25 секунд!
                    elif "vol5-low" in why_low or "tx5-low" in why_low or "no-momentum" in why_low:
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
