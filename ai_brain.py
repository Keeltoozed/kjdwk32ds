import aiohttp
import json
import config

OPENROUTER_API_KEY = "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"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Бесплатные модели на OpenRouter (пробуем по очереди, если одна упала)
FREE_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "minimax/minimax-m3:free",
]

async def ask_ai_oracle(token_context: dict) -> dict:
    """
    Отправляет данные о монете в бесплатную нейросеть через OpenRouter
    для финального вердикта: Скам или Ракета.
    """
    
    prompt = f"""Ты — элитный AI-агент "Gary", встроенный в торгового бота PhantBot. Твоя цель: максимизировать профит с учетом риска (Risk-Adjusted) на мемкоинах Solana.
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
Верни СТРОГО JSON и ничего больше. Никакого текста до или после JSON.
Формат: {{"decision":"BUY"|"SKIP","confidence":80,"reason":"твоя причина до 150 символов"}}"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://phantbot.app",
        "X-Title": "PhantBot AI Scanner"
    }

    for model in FREE_MODELS:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 200,
            "response_format": {"type": "json_object"}
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=20.0) as response:
                    if response.status == 200:
                        data = await response.json()
                        text_response = data['choices'][0]['message']['content']
                        
                        # Очищаем ответ от маркдауна
                        text_response = text_response.replace("```json", "").replace("```", "").strip()
                        
                        result = json.loads(text_response)
                        print(f"✅ AI ({model.split('/')[-1]}): {result.get('decision')} | {result.get('reason', '')[:80]}")
                        return result
                    else:
                        error_text = await response.text()
                        print(f"⚠️ OpenRouter ({model}): HTTP {response.status}")
                        continue  # Пробуем следующую модель
        except json.JSONDecodeError as e:
            print(f"⚠️ AI вернул невалидный JSON ({model}): {text_response[:100]}")
            continue
        except Exception as e:
            print(f"⚠️ Ошибка AI ({model}): {type(e).__name__} - {e}")
            continue
        
    # Если ВСЕ модели упали — НЕ ПОКУПАЕМ
    print("🚫 Все AI-модели недоступны. Пропускаем сделку для безопасности.")
    return {"decision": "SKIP", "confidence": 0, "reason": "Все AI-модели недоступны"}
