"""Модуль мониторинга новостей, X (Twitter) и нарративного снайпинга по ключевым словам.

Логика:
1. NewsNarrativeRadar:
   - Хранит активные ключевые слова (постоянный список + слова от юзера + авто-сбор из крипто-новостей и X).
   - Парсит бесплатные RSS-потоки (Cointelegraph, Decrypt, Google News Crypto/Elon/Trump) каждые 60-90 сек.
   - Извлекает горячие слова, тикеры ($TICKER) и закавыченные термины ('STARSHIP', "PEACE").
   - Сохраняет список в target_keywords.json, чтобы слова не терялись при перезапуске.

2. Сопоставление (match_token):
   - Мгновенное сопоставление (0ms) имени и символа появляющегося токена с активными словами.
   - Не требует блокировки LP (для Pump.fun кёрвов ликвидность в контракте), но проверяет процент дева.
"""

import asyncio
import aiohttp
import re
import time
import json
import os
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Set


class NewsNarrativeRadar:
    STOP_WORDS = {
        "THE", "AND", "FOR", "THAT", "THIS", "WITH", "FROM", "HAVE", "ARE", "NOT", "WAS", "BUT",
        "THEY", "OUT", "NEW", "ALL", "HOW", "WHY", "WHO", "CAN", "GET", "JUST", "NOW", "SEE", "TOP",
        "ONE", "TWO", "DAY", "WEEK", "YEAR", "CRYPTO", "BITCOIN", "ETHEREUM", "SOLANA", "TOKEN", "COIN",
        "AFTER", "BEFORE", "WILL", "MORE", "OVER", "INTO", "SAYS", "ABOUT", "PRICE", "MARKET", "FIRST",
        "WHAT", "WHEN", "WHERE", "COULD", "WOULD", "SHOULD", "THEIR", "THERE", "HERE", "THEN", "THAN",
        "USD", "USDC", "USDT", "BTC", "ETH", "SOL", "SEC", "ETF", "FED", "WSJ", "PBS", "CNN", "BBC",
        "FOX", "CEO", "CFO", "CTO", "COO", "API", "USA", "UK", "EU", "POST", "UPDATE", "LIVE", "BREAKING"
    }

    RSS_FEEDS = [
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("Decrypt", "https://decrypt.co/feed"),
        ("Google News Breaking", "https://news.google.com/rss/search?q=when:1h+(elon+OR+trump+OR+crypto+OR+memecoin+OR+solana)&hl=en-US&gl=US&ceid=US:en"),
        ("Google News Tweets", "https://news.google.com/rss/search?q=when:1h+site:twitter.com+(elonmusk+OR+realDonaldTrump)&hl=en-US&gl=US&ceid=US:en"),
    ]

    def __init__(self, storage_path: str = None):
        if not storage_path:
            storage_path = os.path.join(os.path.dirname(__file__), "target_keywords.json")
        self.storage_path = storage_path
        self.keywords: Dict[str, dict] = {}
        self.seen_headlines: Set[str] = set()
        self.last_news_items = []
        self._load_keywords()

    def _load_keywords(self):
        # Базовые вирусные нарративы по умолчанию
        defaults = [
            "GROK", "STARSHIP", "DOGE", "MARS", "CYBER",
            "TRUMP", "TARIFF", "PEACE", "MUSK", "OPENAI", "DEEPSEEK"
        ]
        now = time.time()
        for w in defaults:
            self.keywords[w] = {
                "source": "DEFAULT_CORE",
                "added_at": now,
                "expires_at": now + 86400 * 30,  # 30 дней
                "headline": "Core viral narrative"
            }
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, dict):
                                self.keywords[k.upper().strip()] = v
            except Exception as e:
                print(f"⚠️ Ошибка загрузки target_keywords.json: {e}")

    def _save_keywords(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.keywords, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def add_keyword(self, word: str, source: str = "USER", ttl_sec: float = 7200.0, headline: str = "") -> bool:
        w = word.upper().strip()
        w = re.sub(r'[^A-Z0-9_]', '', w)
        if not w or len(w) < 2 or w in self.STOP_WORDS:
            return False
        now = time.time()
        self.keywords[w] = {
            "source": source,
            "added_at": now,
            "expires_at": now + ttl_sec,
            "headline": headline or f"Added by {source}"
        }
        self._save_keywords()
        print(f"🎯 [NARRATIVE-RADAR] Добавлено ключевое слово '{w}' (источник: {source}, TTL: {ttl_sec/60:.0f}м)!", flush=True)
        return True

    def remove_keyword(self, word: str) -> bool:
        w = word.upper().strip()
        if w in self.keywords:
            del self.keywords[w]
            self._save_keywords()
            print(f"🗑️ [NARRATIVE-RADAR] Удалено ключевое слово '{w}'!", flush=True)
            return True
        return False

    def get_active_keywords(self) -> Dict[str, dict]:
        now = time.time()
        active = {}
        for k, v in list(self.keywords.items()):
            if v.get("expires_at", 0) > now:
                active[k] = v
        return active

    def match_token(self, symbol: str, name: str, desc: str = "") -> Optional[dict]:
        """Мгновенное сопоставление нового токена с активными ключевыми словами."""
        active = self.get_active_keywords()
        if not active:
            return None

        sym_clean = re.sub(r'[^A-Z0-9]', '', (symbol or "").upper().strip())
        name_clean = (name or "").upper()
        desc_clean = (desc or "").upper()

        for kw, meta in active.items():
            # 1. Точное совпадение тикера ($GROK == GROK)
            if sym_clean == kw:
                return {"word": kw, "match_type": "exact_symbol", "meta": meta}

            # 2. Слово в имени токена как отдельное слово ("SpaceX Starship" -> STARSHIP)
            if re.search(rf'\b{re.escape(kw)}\b', name_clean):
                return {"word": kw, "match_type": "word_in_name", "meta": meta}

            # 3. Тикер содержит ключевое слово и близок по длине (GROKAI, CYBERDOGE)
            if kw in sym_clean and (len(kw) >= 4 or len(sym_clean) <= len(kw) + 3):
                return {"word": kw, "match_type": "symbol_contains", "meta": meta}

        return None

    def extract_keywords_from_text(self, text: str) -> Set[str]:
        found = set()
        if not text:
            return found

        # 1. Закавыченные термины: "STARSHIP", 'PEACE'
        quotes = re.findall(r'["\']([A-Za-z0-9_]{3,12})["\']', text)
        for q in quotes:
            q_clean = q.upper().strip()
            if q_clean not in self.STOP_WORDS and len(q_clean) >= 3 and not q_clean.isdigit():
                found.add(q_clean)

        # 2. Тикеры со знаком $: $DOGE, $GROK
        tickers = re.findall(r'\$([A-Za-z0-9_]{3,10})', text)
        for t in tickers:
            t_clean = t.upper().strip()
            if t_clean not in self.STOP_WORDS and len(t_clean) >= 3 and not t_clean.isdigit():
                found.add(t_clean)

        # 3. Слова заглавными буквами 3-10 символов (только буквы)
        words = re.findall(r'\b[A-Z]{3,10}\b', text)
        for w in words:
            w_clean = w.upper().strip()
            if w_clean not in self.STOP_WORDS and not w_clean.isdigit():
                found.add(w_clean)

        return found

    async def fetch_rss_feed(self, session: aiohttp.ClientSession, url: str, name: str):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    root = ET.fromstring(text)
                    items = root.findall(".//item")
                    for it in items[:15]:
                        t_el = it.find("title")
                        if t_el is not None and t_el.text:
                            headline = t_el.text.strip()
                            if headline not in self.seen_headlines:
                                self.seen_headlines.add(headline)
                                if len(self.seen_headlines) > 1000:
                                    self.seen_headlines.clear()

                                kws = self.extract_keywords_from_text(headline)
                                if kws:
                                    self.last_news_items.insert(0, {
                                        "ts": time.time(),
                                        "source": name,
                                        "title": headline,
                                        "keywords": list(kws)
                                    })
                                    if len(self.last_news_items) > 50:
                                        self.last_news_items.pop()

                                    for kw in kws:
                                        # Авто-добавление слова на 2 часа
                                        self.add_keyword(kw, source=f"NEWS:{name}", ttl_sec=7200.0, headline=headline[:120])
        except Exception:
            pass

    async def run_news_monitor(self):
        """Фоновый цикл периодического опроса новостных лент каждые 75 сек."""
        print("📰 [NARRATIVE-RADAR] Запуск мониторинга X (Twitter) и новостей по ключевым словам...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*"
        }
        while True:
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    tasks = [self.fetch_rss_feed(session, u, n) for n, u in self.RSS_FEEDS]
                    await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                pass
            await asyncio.sleep(75.0)


# Синглтон радара
news_radar = NewsNarrativeRadar()

