"""Бесключевой сборщик TG-коллов через публичное превью t.me/s/CHANNEL.

Без Telethon, без API-ключей: обычный HTTP-опрос открытых каналов.
Находит Solana-минты (base58 32-44) и EVM-адреса (0x+40hex), новые пишет
в fomo_signals.txt как 'sol:'/'evm:' (тот же формат, что tg_listener).
Дальше их забирает fomo_signal_loop -> analyze_token (фильтры!) -> покупка.

Проверено живьём: pumpfunmemecalls отдаёт контракты в превью.
solanamemeradar закрылся (HEO, без превью) - не использовать.
"""
import asyncio
import html
import re
import time

import config
from http_client import fetch_json  # noqa: F401 (сессия через get_session ниже)

SOLANA_MINT_REGEX = r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"
EVM_ADDR_REGEX = r"\b0x[a-fA-F0-9]{40}\b"

_seen = set()  # (channel, post_id)


def _extract_signals(text: str) -> list:
    out = []
    for m in set(re.findall(SOLANA_MINT_REGEX, text or "")):
        out.append(f"sol:{m}")
    for a in set(re.findall(EVM_ADDR_REGEX, text or "")):
        out.append(f"evm:{a}")
    return out


async def _fetch_preview(channel: str):
    """Возвращает [(post_id, text), ...] свежие сверху. Никогда не падает."""
    from http_client import get_session
    url = f"https://t.me/s/{channel}"
    try:
        session = await get_session()
        async with session.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html"}, timeout=15) as r:
            if r.status != 200:
                return None
            page = await r.text()
    except Exception as e:
        print(f"📡 TG preview {channel}: {type(e).__name__}")
        return None
    posts = []
    # посты: data-post="channel/123" + блок текста (два варианта разметки)
    blocks = re.split(r'data-post="[^"]+/(\d+)"', page)
    # blocks[0] мусор, дальше чередуются id, html-кусок
    for i in range(1, len(blocks) - 1, 2):
        pid, chunk = blocks[i], blocks[i + 1]
        m = re.search(r'js-message_text[^>]*>(.*?)</div\s*>', chunk, re.S)
        if not m:
            m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div\s*>', chunk, re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))) if m else ""
        posts.append((pid, text))
    return posts


async def tg_preview_loop(*_args, **_kwargs):
    channels = list(getattr(config, "TG_PREVIEW_CHANNELS", ["pumpfunmemecalls"]) or [])
    if not getattr(config, "TG_PREVIEW_ENABLED", True):
        return
    if not channels:
        print("⚠️ TG-превью: пуст TG_PREVIEW_CHANNELS")
        return
    interval = int(getattr(config, "TG_PREVIEW_INTERVAL", 90))
    print(f"📡 TG-превью запущен (без ключей): {channels}, опрос каждые {interval}с")
    first = True
    while True:
        try:
            for ch in channels:
                posts = await _fetch_preview(ch)
                if posts is None:
                    continue
                if first:
                    for pid, _t in posts:
                        _seen.add((ch, pid))
                    print(f"📡 TG {ch}: запомнил {len(posts)} старых постов, жду новые коллы")
                    continue
                fresh = [(pid, t) for pid, t in posts if (ch, pid) not in _seen]
                for pid, t in fresh:
                    _seen.add((ch, pid))
                sigs = []
                for _pid, t in fresh:
                    sigs.extend(_extract_signals(t))
                sigs = sorted(set(sigs))
                if sigs:
                    print(f"🎯 TG-колл @{ch}: {sigs}")
                    with open("fomo_signals.txt", "a") as f:
                        for s in sigs:
                            f.write(s + "\n")
                if len(_seen) > 5000:
                    _seen.clear()
        except Exception as e:
            print(f"Ошибка TG-превью: {type(e).__name__} {e}")
        first = False
        await asyncio.sleep(interval)


if __name__ == "__main__":
    async def _t():
        posts = await _fetch_preview("pumpfunmemecalls")
        print("постов:", len(posts or []))
        for pid, t in (posts or [])[:5]:
            print(pid, _extract_signals(t))
    asyncio.run(_t())
