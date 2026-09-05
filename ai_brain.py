"""
🧠 AI Brain v2 — Встроенный Интеллект (Zero-API)
Все правила "Agent Gary" закодированы в математическую модель.
Без ключей, без API, без задержек. Работает мгновенно.
"""
import math


def ask_ai_oracle_sync(token_context: dict) -> dict:
    """
    Локальный ИИ-анализатор. Оценивает монету по 7 критериям,
    генерирует score от 0 до 100 и выносит вердикт BUY/SKIP.
    """
    symbol = token_context.get("symbol", "???")
    age = token_context.get("age_minutes", 0)
    liq = token_context.get("liquidity_usd", 0)
    vol = token_context.get("volume_24h_usd", 0)
    buys = token_context.get("m5_buys", 0)
    sells = token_context.get("m5_sells", 0)
    m5_pct = token_context.get("price_change_m5_pct", 0)
    h1_pct = token_context.get("price_change_h1_pct", 0)
    socials = token_context.get("social_networks_count", 0)
    rsi = token_context.get("rsi_14")
    macd_hist = token_context.get("macd_histogram")
    safety = token_context.get("safety_score", 0)

    score = 50  # Нейтральная отправная точка
    reasons = []

    # ============================
    # 1. КРИТИЧЕСКИЕ СТОП-ФИЛЬТРЫ (мгновенный SKIP)
    # ============================

    # Honeypot / Скам: нет соцсетей + мизерная ликвидность
    if socials == 0 and liq < 5000:
        return _skip(5, "Нет соцсетей + микро-ликвидность = 99% скам")

    # Жёсткий даунтренд
    if h1_pct < -25:
        return _skip(8, f"Дамп -{ abs(h1_pct):.0f}% за час. Ловить нож опасно")

    # RSI экстремумы
    if rsi is not None and not math.isnan(rsi):
        if rsi > 80:
            return _skip(10, f"RSI {rsi:.0f} — экстремальный перегрев. Откат неизбежен")
        if rsi < 20:
            return _skip(10, f"RSI {rsi:.0f} — мёртвый даунтренд. Монета сдохла")

    # Низкий safety score (RugCheck забраковал)
    if safety < 30:
        return _skip(12, f"Safety Score {safety}/100 — высокий риск скама")

    # ============================
    # 2. МОМЕНТУМ (до +30 баллов)
    # ============================
    buy_sell_ratio = buys / max(sells, 1)

    if buy_sell_ratio >= 3.0:
        score += 25
        reasons.append(f"Мощный моментум: покупки x{buy_sell_ratio:.1f}")
    elif buy_sell_ratio >= 2.0:
        score += 18
        reasons.append(f"Хороший моментум: покупки x{buy_sell_ratio:.1f}")
    elif buy_sell_ratio >= 1.3:
        score += 10
        reasons.append(f"Умеренный моментум: x{buy_sell_ratio:.1f}")
    elif buy_sell_ratio < 0.7:
        score -= 20
        reasons.append(f"Продажи доминируют: x{buy_sell_ratio:.1f}")
    elif buy_sell_ratio < 1.0:
        score -= 10
        reasons.append(f"Слабый моментум: x{buy_sell_ratio:.1f}")

    # Объём покупок за 5 минут
    if buys >= 50:
        score += 10
        reasons.append(f"Шквал покупок ({buys} за 5м)")
    elif buys >= 20:
        score += 5

    # ============================
    # 3. ЦЕНОВОЙ ТРЕНД (до +20 баллов)
    # ============================
    if 0 < m5_pct <= 15:
        score += 12
        reasons.append(f"Здоровый рост +{m5_pct:.1f}% за 5м")
    elif 15 < m5_pct <= 40:
        score += 8
        reasons.append(f"Быстрый рост +{m5_pct:.1f}% (осторожно)")
    elif m5_pct > 40:
        score -= 5
        reasons.append(f"Слишком быстрый рост +{m5_pct:.1f}% — риск отката")
    elif m5_pct < -10:
        score -= 15
        reasons.append(f"Падение {m5_pct:.1f}% за 5м")

    if 0 < h1_pct <= 50:
        score += 8
    elif h1_pct > 100:
        score -= 5
        reasons.append("Рост >100% за час — поздно входить")

    # ============================
    # 4. RSI / MACD (до +15 баллов)
    # ============================
    if rsi is not None and not math.isnan(rsi):
        if 40 <= rsi <= 60:
            score += 10
            reasons.append(f"RSI {rsi:.0f} — идеальная зона входа")
        elif 60 < rsi <= 75:
            score += 5
            reasons.append(f"RSI {rsi:.0f} — бычий, но ещё не перегрет")
        elif 30 <= rsi < 40:
            score += 3
            reasons.append(f"RSI {rsi:.0f} — возможный отскок")

    if macd_hist is not None:
        if macd_hist > 0:
            score += 8
            reasons.append("MACD бычий (гистограмма > 0)")
        elif macd_hist < 0:
            score -= 5

    # ============================
    # 5. 🎓 GRADUATION DETECTOR (до +20 баллов)
    # ============================
    # Pump.fun → Raydium миграция при капе ~$65k
    if 40000 <= liq <= 80000 and buy_sell_ratio >= 1.5:
        score += 20
        reasons.append("🎓 Близко к Graduation! FOMO-зона Pump→Raydium")
    elif 25000 <= liq < 40000 and buy_sell_ratio >= 2.0:
        score += 12
        reasons.append("Подход к Graduation с сильным моментумом")

    # ============================
    # 6. ЛИКВИДНОСТЬ И БЕЗОПАСНОСТЬ (до +10 баллов)
    # ============================
    if liq >= 50000:
        score += 8
    elif liq >= 20000:
        score += 5
    elif liq < 5000:
        score -= 10
        reasons.append(f"Микро-ликвидность ${liq:.0f} — высокий риск проскальзывания")

    if socials >= 3:
        score += 5
        reasons.append("Полный набор соцсетей")
    elif socials >= 1:
        score += 2

    if safety >= 70:
        score += 5
    elif safety >= 50:
        score += 2

    # ============================
    # 7. ВОЗРАСТ (штраф за слишком старые монеты)
    # ============================
    if age > 4320:  # >3 дня
        score -= 5
        reasons.append("Монета старше 3 дней — хайп мог пройти")

    # ============================
    # ВЕРДИКТ
    # ============================
    score = max(0, min(100, score))  # Ограничиваем 0-100

    if score >= 65:
        decision = "BUY"
        main_reason = " | ".join(reasons[:3]) if reasons else "Совокупность факторов положительная"
    else:
        decision = "SKIP"
        main_reason = " | ".join(reasons[:3]) if reasons else "Недостаточно сигналов для входа"

    print(f"🧠 AI Brain v2: {decision} (Score: {score}/100) | {main_reason[:120]}")
    return {"decision": decision, "confidence": score, "reason": main_reason[:150]}


def _skip(score: int, reason: str) -> dict:
    """Быстрый отказ с объяснением"""
    print(f"🧠 AI Brain v2: SKIP (Score: {score}/100) | {reason}")
    return {"decision": "SKIP", "confidence": score, "reason": reason}


# Обёртка для совместимости с async кодом в analyzer.py
async def ask_ai_oracle(token_context: dict) -> dict:
    """Async обёртка. ИИ работает локально, задержка = 0 мс."""
    return ask_ai_oracle_sync(token_context)
