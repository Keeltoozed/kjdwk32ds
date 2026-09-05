import aiohttp
import json
import config

async def ask_ai_oracle(token_context: dict) -> dict:
    """
    Отправляет собранные данные о монете в нейросеть Gemini 2.5 Flash
    для финального вердикта: Скам или Ракета.
    """
    if not hasattr(config, 'GEMINI_API_KEY') or not config.GEMINI_API_KEY or config.GEMINI_API_KEY == "ТВОЙ_КЛЮЧ_ЗДЕСЬ":
        print("⚠️ Gemini API ключ не настроен. AI-фильтр пропущен.")
        return {"decision": "BUY", "confidence": 100, "reason": "AI отключен"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={config.GEMINI_API_KEY}"
    
    prompt = f"""
    Ты — элитный AI-агент "Gary", встроенный в торгового бота PhantBot. Твоя цель: максимизировать профит с учетом риска (Risk-Adjusted) на мемкоинах Solana.
    Тебе поступают сырые данные с блокчейна и сканеров:
    {json.dumps(token_context, indent=2, ensure_ascii=False)}
    
    ИНСТРУКЦИИ И ЛОГИКА (Прочитай внимательно):
    1. Микроструктура рынка: В мемкоинах стартовый "минус" 2-5% — это НОРМАЛЬНО (из-за комиссий сети и slippage). Не бойся небольшого недостатка ликвидности, если моментум огромный (киты выкупают стакан).
    2. Тренд и Свечи: Оценивай баланс m5_buys и m5_sells. Если преобладают покупки и price_change_m5_pct > 0, это может быть зарождением "ракеты".
    3. Защита от Honeypot (RugPull): Если HHI > 2500 (монополия) или у монеты нет соцсетей при мизерной ликвидности (< $2000) — это почти 100% скам. SKIP.
    4. 🎓 Эффект Выпускного (Graduation): На Pump.fun токены переходят на Raydium при достижении капитализации ~$65,000. Если ликвидность/капа близка к этому значению и идет шквал покупок — это сильнейший сигнал на BUY (FOMO миграции).
    5. Перегрев: Если индикатор RSI > 75, монета локально перегрета. Жди отката, не покупай на пике. SKIP.
    6. Режимы Риска (Degen vs Safe): Сейчас бот работает в режиме "Medium". Допускай небольшие огрехи в контракте (например, freeze_authority может быть включен на PumpFun до миграции), если моментум железобетонный.
    
    Возможные вердикты:
    - BUY: Монета во флэте перед взлетом, идет уверенный бычий тренд ИЛИ токен летит к Graduation.
    - SKIP: Скам, дамп, сильная монополия (пузыри), или перегрев (RSI > 75).
    
    ВЫВОД:
    Верни СТРОГО минифицированный JSON (в одну строку, без ```json, без форматирования).
    Формат: {{"decision":"BUY"|"SKIP","confidence":80,"reason":"твоя причина до 150 символов"}}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            # Даем ИИ максимум 15 секунд на раздумья
            async with session.post(url, json=payload, timeout=15.0) as response:
                if response.status == 200:
                    data = await response.json()
                    text_response = data['candidates'][0]['content']['parts'][0]['text']
                    
                    # Очищаем ответ от маркдауна (если ИИ добавил ```json ... ```)
                    text_response = text_response.replace("```json", "").replace("```", "").strip()
                    
                    return json.loads(text_response)
                else:
                    print(f"AI API Error: {response.status}")
                    error_text = await response.text()
                    print(f"Details: {error_text}")
    except Exception as e:
        print(f"⚠️ Ошибка вызова AI: {type(e).__name__} - {e}")
        
    # Если ИИ сломался (например, неверный ключ), мы разрешаем сделку, 
    # так как монета УЖЕ прошла все жесткие технические фильтры в analyzer.py
    return {"decision": "BUY", "confidence": 50, "reason": "AI Error (Fallback to Technicals)"}
