"""TG-слушатель живых коллов: каналы -> fomo_signals.txt -> fomo_signal_loop.
Ловит Solana-минты (base58 32-44) и EVM-адреса (0x + 40 hex).
Формат строк: 'sol:<mint>' или 'evm:<addr>' (сеть EVM определяется при обработке).
Требует env TG_API_ID / TG_API_HASH (бесплатно: https://my.telegram.org).
Первый запуск - локально (Telegram спросит код, создастся .session файл).
"""
from telethon import TelegramClient, events
import re
import os
import asyncio

SOLANA_MINT_REGEX = r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"
EVM_ADDR_REGEX = r"\b0x[a-fA-F0-9]{40}\b"


def _extract_signals(text: str) -> list:
    out = []
    for m in set(re.findall(SOLANA_MINT_REGEX, text or "")):
        out.append(f"sol:{m}")
    for a in set(re.findall(EVM_ADDR_REGEX, text or "")):
        out.append(f"evm:{a}")
    return out


async def tg_listener_loop(*_args, **_kwargs):
    import config
    if not getattr(config, "TG_ENABLED", True):
        return
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    channels = list(getattr(config, "TG_CHANNELS", []) or [])

    if not api_id or not api_hash:
        print("⚠️ Telegram-парсер отключен (нет TG_API_ID / TG_API_HASH в env)")
        return
    if not channels:
        print("⚠️ Telegram-парсер: пуст TG_CHANNELS в config.py")
        return

    client = TelegramClient(getattr(config, "TG_SESSION", "sniper_session"),
                            int(api_id), api_hash)

    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        try:
            text = event.message.message or ""
        except Exception:
            return
        if not text:
            return
        print(f"\n[TG] Сигнал ({len(text)} симв): {text[:120]}...")
        sigs = _extract_signals(text)
        if sigs:
            print(f"🎯 TG-контракты: {sigs}")
            with open('fomo_signals.txt', 'a') as f:
                for s in sigs:
                    f.write(s + "\n")

    while True:
        try:
            print(f"🚀 TG-парсер: подключаюсь, каналы: {channels}...")
            await client.start()
            print("✅ TG слушает живые коллы!")
            await client.run_until_disconnected()
        except Exception as e:
            print(f"❌ Ошибка Telegram: {type(e).__name__} {e}. Ретраю через 30с...")
            await asyncio.sleep(30)
