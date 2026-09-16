import aiohttp
from datetime import datetime, timezone
import time
import config
from sentiment import analyze_sentiment
from ta_tools import TATools
import math

try:
    import numpy as np
    import xgboost as xgb
    class ScamFilter:
        def __init__(self, path='scam_filter_model.json'):
            try:
                self.model = xgb.XGBClassifier()
                self.model.load_model(path)
                self.enabled = True
                print('AI Scam Filter: загружен (XGBoost native, без sklearn-pickle)')
            except Exception as e:
                self.enabled = False
                print(f'AI Scam Filter: пропущен ({e})')
        def is_scam(self, data) -> tuple:
            if not self.enabled:
                return False, 0.0
            try:
                feat = np.array([[data.get('dev_holding_pct', 0), data.get('tx_velocity_1m', 0),
                                  data.get('volume_to_liq_ratio', 0), data.get('funded_from_cex', 0)]])
                return bool(self.model.predict(feat)[0]), float(self.model.predict_proba(feat)[0][1])
            except Exception:
                return False, 0.0
    SCAM_FILTER = ScamFilter()
except Exception as e:
    SCAM_FILTER = None
    print(f'AI Filter ошибка: {e}')


class Analyzer:
    def __init__(self):
        self.session = None
        
    async def get_session(self):
        import aiohttp
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def fetch_latest_tokens(self) -> list:
        tokens = []
        session = await self.get_session()
        if True:
            # 1. Сканируем топовые (Boosted) монеты
            try:
                async with session.get(config.DEXSCREENER_LATEST, timeout=5) as response:
                    if response.status == 200:
                        tokens.extend(await response.json())
            except Exception as e:
                print(f"Dexscreener boosts fetch error: {e}")
                
            # 2. Сканируем новые профили, чтобы не пропускать свежие ракеты
            try:
                async with session.get(config.DEXSCREENER_PROFILES, timeout=5) as response:
                    if response.status == 200:
                        tokens.extend(await response.json())
            except Exception as e:
                print(f"Dexscreener profiles fetch error: {e}")
                
        # Возвращаем уникальные токены (по tokenAddress)
        seen = set()
        unique_tokens = []
        for t in tokens:
            addr = t.get("tokenAddress")
            chain = t.get("chainId")
            if addr and addr not in seen and chain == "solana":
                seen.add(addr)
                unique_tokens.append(t)
        return unique_tokens
                
    async def fetch_token_data(self, mint: str) -> dict:
        url = f"{config.DEXSCREENER_SEARCH}{mint}"
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                            if sol_pairs:
                                return sorted(sol_pairs, key=lambda x: x.get("liquidity", {}).get("usd", 0), reverse=True)[0]
                    return await self.fetch_token_data_gecko(mint)
            except Exception as e:
                print(f"Dexscreener token data error: {type(e).__name__} {e}")
                return await self.fetch_token_data_gecko(mint)

    async def fetch_token_data_gecko(self, mint: str) -> dict:
        session = await self.get_session()
        try:
            url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1"
            async with session.get(url, timeout=8, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    return {}
                data = await response.json()
                pools = data.get("data", [])
                if not pools:
                    return {}
                best = max(pools, key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd", 0) or 0))
                a = best.get("attributes", {})
                pc = a.get("price_change_percentage") or {}
                tx = a.get("transactions") or {}
                vu = a.get("volume_usd") or {}
                created = a.get("pool_created_at")
                created_ms = 0
                if created:
                    from datetime import datetime
                    created_ms = int(datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() * 1000)
                symbol = (a.get("name") or "UNKNOWN").split("/")[0].strip()

                def _tx(key):
                    t = tx.get(key) or {}
                    return {"buys": int(t.get("buys", 0) or 0), "sells": int(t.get("sells", 0) or 0)}

                print(f"🦎 GeckoTerminal fallback для {mint[:8]}: пул найден.")
                return {
                    "baseToken": {"symbol": symbol, "name": symbol},
                    "priceUsd": str(a.get("base_token_price_usd") or 0),
                    "priceChange": {"m5": float(pc.get("m5") or 0), "m1": 0.0,
                                    "h1": float(pc.get("h1") or 0), "h24": float(pc.get("h24") or 0)},
                    "txns": {"m5": _tx("m5"), "h1": _tx("h1"), "h24": _tx("h24")},
                    "volume": {"m5": float(vu.get("m5") or 0), "h1": float(vu.get("h1") or 0),
                               "h24": float(vu.get("h24") or 0)},
                    "liquidity": {"usd": float(a.get("reserve_in_usd") or 0)},
                    "fdv": float(a.get("fdv_usd") or a.get("market_cap_usd") or 0),
                    "pairCreatedAt": created_ms,
                    "dexId": "pump" if mint.endswith("pump") else "raydium",
                    "info": {"socials": [], "websites": []},
                }
        except Exception as e:
            print(f"GeckoTerminal token data error: {type(e).__name__} {e}")
            return {}

    async def is_clone(self, symbol: str, current_mint: str, current_created_at: int, current_fdv: float) -> bool:
        """Проверяет, является ли этот токен дешевой копией (клоном) более старого или крупного оригинала."""
        if not symbol or len(symbol) <= 2:
            return False 
            
        url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        pairs = data.get("pairs", [])
                        
                        for p in pairs:
                            if p.get("chainId") == "solana":
                                p_symbol = p.get("baseToken", {}).get("symbol", "").upper()
                                p_mint = p.get("baseToken", {}).get("address", "")
                                
                                if p_symbol == symbol.upper() and p_mint != current_mint:
                                    p_created_at = p.get("pairCreatedAt", float('inf'))
                                    p_fdv = p.get("fdv", 0)
                                    
                                    # Если мы нашли другой токен с таким же именем, который был создан РАНЬШЕ нас
                                    # и имеет какую-то капитализацию (не мертвый с 0 fdv), то наш токен - фейк.
                                    if p_created_at < current_created_at and p_fdv > 5000:
                                        return True
                                        
                                    # Либо если другой токен имеет огромную капу (в 10 раз больше нашей),
                                    # значит он - оригинал, а мы клон.
                                    if p_fdv > (current_fdv * 10) and p_fdv > 50000:
                                        return True
            except Exception as e:
                pass
        return False

    async def check_rugcheck(self, mint: str) -> bool:
        url = config.RUGCHECK_API.format(mint=mint)
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        score = data.get("score", 1000)
                        # Защита от моментальных дампов (-37%). Строгий фильтр скама.
                        if score >= 150: # было 300, сделали 150 (очень строго). Отсекает монеты, где у одного кошелька >20% саплая.
                            return False
                            
                        token_info = data.get("token", {})
                        if token_info.get("mintAuthority") is not None:
                            return False
                        if token_info.get("freezeAuthority") is not None:
                            return False
                        
                        # Фильтр по названию токена
                        name = token_info.get("name", "").lower()
                        symbol = token_info.get("symbol", "").lower()
                        bad_words = ["test", "scam", "fuck", "nigger", "pump and dump", "rug"]
                        if any(w in name for w in bad_words) or any(w in symbol for w in bad_words):
                            return False
                            
                        # Индекс Херфиндаля-Хиршмана (HHI) для выявления скрытых монополий (как Bubble Map)
                        top_holders = data.get("topHolders", [])
                        hhi_index = sum([(h.get("pct", 0) * 100) ** 2 for h in top_holders[:15] if not h.get("isContract", False)])
                        
                        top_10_pct = sum([h.get("pct", 0) for h in top_holders[:10] if not h.get("isContract", False)])
                        
                        # Если HHI высокий (>2000), значит кошельки сильно сконцентрированы (пузыри)
                        if top_10_pct >= 30 or hhi_index > 2500:
                            return False
                            
                        return True
                    print(f"⚠️ RugCheck HTTP {response.status} для {mint}. Fail-Open: решаю по остальным фильтрам.")
                    return True
            except Exception as e:
                print(f"⚠️ RugCheck fetch error ({type(e).__name__}): {e}. Fail-Open: решаю по остальным фильтрам.")
                return True

    async def get_helius_transaction_metrics(self, mint: str) -> tuple:
        """ Возвращает (unique_buyers_m5, smart_money_inflow) """
        unique_buyers = 0
        smart_money_inflow = 0
        
        # Читаем smart wallets
        smart_wallets = set()
        import os
        if os.path.exists("smart_wallets.txt"):
            with open("smart_wallets.txt", "r") as f:
                smart_wallets = {line.strip() for line in f if line.strip()}
                
        payload_sigs = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": 30}]
        }
        
        import aiohttp
        session = await self.get_session()
        if True:
            try:
                # 1. Получаем сигнатуры
                async with session.post(config.HELIUS_RPC_URL, json=payload_sigs, timeout=3) as resp:
                    data = await resp.json()
                    signatures = [item["signature"] for item in data.get("result", [])]
                
                if not signatures:
                    return 0, 0
                    
                # 2. Получаем детали транзакций
                payload_txs = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransactions",
                    "params": [signatures, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                }
                async with session.post(config.HELIUS_RPC_URL, json=payload_txs, timeout=5) as resp:
                    tx_data = await resp.json()
                    transactions = tx_data.get("result", [])
                    
                    buyers = set()
                    for tx in transactions:
                        if not tx or not tx.get("transaction"):
                            continue
                            
                        account_keys = tx["transaction"]["message"]["accountKeys"]
                        for acc in account_keys:
                            if acc.get("signer"):
                                pubkey = acc.get("pubkey")
                                buyers.add(pubkey)
                                if pubkey in smart_wallets:
                                    smart_money_inflow += 1
                                break 
                                
                    unique_buyers = len(buyers)
                    
            except Exception as e:
                print(f"⚠️ Ошибка Helius RPC при парсинге транзакций: {e}")
                
        return unique_buyers, smart_money_inflow

    def check_hyper_rocket_momentum(self, pair_data: dict) -> bool:
        """
        VIP-полоса для Гипер-Ракет:
        Ищет аномальные всплески покупок (>50 покупок) и объема (>$30,000) в первые 5 минут.
        Позволяет пропустить стандартные жесткие фильтры.
        """
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        volume_m5 = pair_data.get("volume", {}).get("m5", 0)
        
        # > 50 покупок И > $30k объема в 5-минутном окне
        if buys_m5 >= 50 and volume_m5 >= 30000:
            return True
        return False
        
    async def analyze_token(self, mint: str) -> bool:
        # Smart Router
        
        # 1. Сначала жесткий фильтр скама (RugCheck). Если это скам - даже не тратим лимиты.
        is_safe = await self.check_rugcheck(mint)
        if not is_safe:
            print(f"🚫 Скам-фильтр: {mint} не прошел проверку RugCheck (Риск дампа/MintAuthority).")
            return False
            
        pair_data = await self.fetch_token_data(mint)
        if not pair_data:
            print(f"⚠️ Пропуск: DexScreener не вернул данные для {mint} (Rate Limit или токен слишком новый).")
            return None
            
        # Блэклист тикеров и названий (Защита от фейковых токенов)
        base_token = pair_data.get("baseToken", {})
        name = base_token.get("name", "").upper()
        symbol = base_token.get("symbol", "").upper()
        
        scam_keywords = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "NVDA", "GOOG", "AMZN", "META", "NFLX", 
                         "PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN"]
                         
        if any(keyword in symbol for keyword in scam_keywords) or any(keyword in name for keyword in scam_keywords):
            print(f"🚫 Мусор: Токен {symbol} мимикрирует под известный бренд/мем. Это 100% scam.")
            return False
            
        is_vip = self.check_hyper_rocket_momentum(pair_data)

        # === VIP OVERHEAT GUARD: не покупаем вершину вертикали ===
        if is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            _m1 = _pc.get("m1", 0) or 0
            if _m5 > getattr(config, "VIP_MAX_M5_PCT", 0.40) * 100:
                print(f"🚫 [VIP OVERHEAT] {mint}: m5 {_m5:+.0f}% — вертикаль уже прошла, вход = вершина.")
                return False
            if _m1 < 0:
                print(f"🚫 [VIP REVERSAL] {mint}: m1 {_m1:+.1f}% — всплеск откатывает, ждём pullback.")
                return False

        # === PULLBACK ENTRY (не-VIP): входим в ОТКАТ после импульса, не в вершину ===
        _lottery = False
        if not is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            _m1 = _pc.get("m1", 0) or 0
            _h1 = _pc.get("h1", 0) or 0
            _tx = (pair_data.get("txns") or {}).get("h1", {}) or {}
            _b, _s = _tx.get("buys", 0) or 0, _tx.get("sells", 0) or 0
            _v24 = (pair_data.get("volume") or {}).get("h24", 0) or 0
            _txm5_pre = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _vm5_pre = (pair_data.get("volume") or {}).get("m5", 0) or 0
            _created_pre = pair_data.get("pairCreatedAt") or 0
            _age_min_pre = (time.time() * 1000 - _created_pre) / 60000.0 if _created_pre else 999
            if _age_min_pre < 10 and (_txm5_pre.get("buys", 0) + _txm5_pre.get("sells", 0)) == 0 and _vm5_pre == 0:
                print(f"⏳ [ENTRY] {mint}: возраст {_age_min_pre:.1f} мин, m5-окно API ещё пустое — повторю позже, снайпер ведёт его по WSS.")
                return None
            if _m5 >= getattr(config, "LOTTERY_MIN_M5_PCT", 1.0) * 100:
                if _m1 < 0:
                    print(f"🚫 [LOTTERY] {mint}: m5 {_m5:+.0f}%, но m1 {_m1:+.1f}% — вертикаль откатывает, это вершина.")
                    return False
                print(f"🎰 [LOTTERY] {mint}: вертикаль m5 {_m5:+.0f}%, m1 {_m1:+.1f}% — вход уменьшенным сайзом.")
                _lottery = True
            else:
                if _m5 < getattr(config, "PULLBACK_MIN_M5_PCT", 0.08) * 100:
                    print(f"🚫 [ENTRY] {mint}: m5 {_m5:+.1f}% < импульса не было, пропуск.")
                    return False
                if _m1 > getattr(config, "PULLBACK_M1_MAX_PCT", 0.05) * 100:
                    print(f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% — вертикаль в процессе, купим вершину. Ждём откат.")
                    return False
                if _m1 < getattr(config, "PULLBACK_M1_MIN_PCT", -0.10) * 100:
                    print(f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% — импульс схлопнулся, это дамп, не откат.")
                    return False
                if _h1 > getattr(config, "PULLBACK_MAX_H1_PCT", 1.5) * 100:
                    print(f"🚫 [ENTRY] {mint}: h1 {_h1:+.0f}% — уже улетел, поздно.")
                    return False
            if _s > 0 and _b < _s * 1.1:
                print(f"🚫 [ENTRY] {mint}: buys {_b} / sells {_s} — нет давления покупателей.")
                return False
            if _v24 < 20000:
                print(f"🚫 [ENTRY] {mint}: vol24h ${_v24:,.0f} < $20k — нет объёма.")
                return False
            _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _b5, _s5 = _txm5.get("buys", 0) or 0, _txm5.get("sells", 0) or 0
            if (_b5 + _s5) < 50:
                print(f"🚫 [VELOCITY] {mint}: txns m5 {_b5 + _s5} < 50 — нет скорости торгов.")
                return False
            if _s5 > 0:
                mult = 1.2 if _lottery else 1.5
                if _b5 < _s5 * mult:
                    print(f"🚫 [VELOCITY] {mint}: buy/sell m5 {_b5}/{_s5} < {mult}x — {'(лотерея, ослаблено)' if _lottery else 'нет буфера покупателей'}")
                    return False
            _socials = (pair_data.get("info") or {}).get("socials") or []
            _created = pair_data.get("pairCreatedAt") or 0
            if _created:
                _age_h = (time.time() * 1000 - _created) / 3.6e6
                if _age_h < 6 and isinstance(_socials, list) and len(_socials) == 0 and not _lottery:
                    print(f"🚫 [SOCIAL] {mint}: нет соцсетей при возрасте {_age_h:.1f}ч — высокий скам-риск.")
                    return False
            print(f"✅ [ENTRY] {mint}: PULLBACK — импульс m5 {_m5:+.1f}%, откат m1 {_m1:+.1f}%, h1 {_h1:+.0f}%, b/s {_b}/{_s}. Вход.")
        
        # Защита от микро-пулов (Scam сетки типа Fly)
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        if liquidity < 15000 and not is_vip and pair_data.get("dexId") != "pump":
            print(f"📉 Изоляция: {symbol} имеет микро-пул (${liquidity:.0f} < $15k). Риск 100% проскальзывания.")
            return False
            
        if is_vip:
            print(f"🚀 [VIP] {symbol}: Пропуск проверок клонов и соцсетей из-за гипер-моментума!")
            
        # 1.5 Защита от вторичных клонов (Copycat Filter)
        current_created_at = pair_data.get("pairCreatedAt", 0)
        current_fdv = pair_data.get("fdv", 0)
        if not is_vip and await self.is_clone(symbol, mint, current_created_at, current_fdv):
            print(f"🚫 Мусор: Токен {symbol} является клоном! На DexScreener найден более старый/крупный оригинал.")
            return False
            
        # 2. Обязательное наличие соцсетей (Proof of Effort: Twitter + Website/TG)
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        
        # Смягченный фильтр: достаточно хотя бы одной соцсети или сайта
        if not (has_twitter or has_tg or has_website):
            print(f"🚫 Мусор: У {mint} вообще нет ни одной соцсети или сайта.")
            return False
            
        if is_vip or _lottery:
            print(f"🚀 [FAST TRACK] {symbol}: гейты пройдены с подтверждением объёма — вход без ML-вето.")
            return True

        dex_id = pair_data.get("dexId")
        created_at = pair_data.get("pairCreatedAt", 0)
        age_minutes = (time.time() * 1000 - created_at) / (1000 * 60) if created_at else 999
        
        if dex_id == "pump" and age_minutes <= 15:
            return await self.analyze_token_xgboost(mint, pair_data)
        else:
            return await self.analyze_token_raydium(mint, pair_data)
        
    async def analyze_token_xgboost(self, mint: str, pair_data: dict) -> bool:
        if not pair_data:
            return False
            
        # 1. Проверяем, что токен все еще на Pump.fun (не ушел на Raydium)
        if pair_data.get("dexId") != "pump":
            return False
            
        is_vip = self.check_hyper_rocket_momentum(pair_data)
        if is_vip:
            print(f"🚀🚀🚀 [HYPER-ROCKET BYPASS] Токен {mint} летит в космос! Игнорируем карантин возраста и соцсетей.")
            
        # 2. Проверяем возраст токена (только для обычных монет)
        created_at = pair_data.get("pairCreatedAt")
        if created_at and not is_vip:
            age_minutes = (time.time() * 1000 - created_at) / (1000 * 60)
            if age_minutes > 15:  # Игнорируем токены старше 15 минут
                return False
                
        import pandas as pd
        import joblib
        from pump_fun_sniper import PumpFunSniper
        
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
            
        tx_velocity_1m = (buys_m5 + sells_m5) / 5.0
        
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        has_socials = 1 if (has_twitter and (has_website or has_tg)) else 0
        
        # Get holders via Helius RPC
        dev_holding_pct, top_10_holding_pct = 0.0, 0.0
        rpc_url = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint]
        }
        try:
            import aiohttp
            session = await self.get_session()
            if True:
                async with session.post(rpc_url, json=payload, timeout=5) as resp:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    total_supply = 1_000_000_000
                    if accounts:
                        # Exclude bonding curve account which holds ~80% initially
                        # We just sum the remaining top 9 accounts
                        non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts if float(acc["uiAmount"]) < 800_000_000]
                        top_10_amounts = non_curve_accounts[:10]
                        top_10_holding_pct = (sum(top_10_amounts) / total_supply) * 100
                        if top_10_amounts:
                            dev_holding_pct = (top_10_amounts[0] / total_supply) * 100 
                        
                        # Защита от Jito-бандлов (Sybil-атаки):
                        # Скаммеры часто раскидывают одинаковые суммы по свежим кошелькам.
                        # Если 3 и более кошельков в топе имеют одинаковый баланс (с погрешностью) - это бандл.
                        if len(top_10_amounts) >= 3:
                            rounded_amounts = [round(amt, -4) for amt in top_10_amounts if amt > 1000000]
                            if rounded_amounts:
                                # Ищем самый частый баланс
                                from collections import Counter
                                counts = Counter(rounded_amounts)
                                if counts.most_common(1)[0][1] >= 3:
                                    if not is_vip:
                                        print(f"🚫 Мусор: Обнаружен Jito-бандл (Sybil attack) у {mint}.")
                                        return False
                                    else:
                                        print(f"⚠️ ВНИМАНИЕ: {mint} имеет Jito-бандл, но пропускается по VIP-квоте (Hyper-Rocket)!")
        except Exception as e:
            print(f"Helius RPC error: {e}")
        
        # Funded from CEX (simplified)
        funded_from_cex = 0
        
        features = pd.DataFrame([{
            "dev_holding_pct": dev_holding_pct,
            "top_10_holding_pct": top_10_holding_pct,
            "tx_velocity_1m": tx_velocity_1m,
            "has_socials": has_socials,
            "funded_from_cex": funded_from_cex
        }])
        
        import xgboost as xgb
        model = xgb.XGBClassifier()
        model.load_model("pump_model.json")
        prob = model.predict_proba(features)[0][1]
        conf = prob * 100
        print(f"🤖 XGBoost [DEX Poller]: {mint} | Score: {conf:.1f}%")
        import config
        threshold = 75.0 if getattr(config, "AI_MODE", "sniper") == "sniper" else 65.0
        if is_vip:
            threshold = 70.0 # Снижаем порог уверенности для ракет
            print(f"🔥 [VIP] Порог XGBoost снижен до {threshold}%")
            
        return conf >= threshold

    async def analyze_token_raydium(self, mint: str, pair_data: dict) -> bool:
        # Безлимитный режим: используем ТОЛЬКО данные DexScreener
        if not pair_data:
            return False
            
        import pandas as pd
        import joblib
        
        # Извлекаем признаки
        txns_h24 = pair_data.get("txns", {}).get("h24", {})
        buys_h24 = txns_h24.get("buys", 0)
        sells_h24 = txns_h24.get("sells", 0)
        
        volume_h24 = pair_data.get("volume", {}).get("h24", 0)
        price_change_h24 = pair_data.get("priceChange", {}).get("h24", 0)
        
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        fdv = pair_data.get("fdv", 0)
        
        buy_sell_ratio = buys_h24 / (sells_h24 + 1)
        vol_to_liq = volume_h24 / (liquidity + 1)
        
        # Формируем DataFrame для XGBoost
        features = ['price_change_h24', 'volume_h24', 'buys_h24', 'sells_h24', 'liquidity', 'fdv', 'buy_sell_ratio', 'vol_to_liq']
        df = pd.DataFrame([{
            'price_change_h24': price_change_h24,
            'volume_h24': volume_h24,
            'buys_h24': buys_h24,
            'sells_h24': sells_h24,
            'liquidity': liquidity,
            'fdv': fdv,
            'buy_sell_ratio': buy_sell_ratio,
            'vol_to_liq': vol_to_liq
        }])
        
        try:
            import xgboost as xgb
            model = xgb.XGBClassifier()
            model.load_model("raydium_model_dex.json")
            prob = model.predict_proba(df)[0][1]
            conf = prob * 100
            
            # --- ИНТЕГРАЦИЯ LUNARCRUSH ---
            symbol = pair_data.get("baseToken", {}).get("symbol", "")
            if symbol:
                lc_data = await self.fetch_lunarcrush_sentiment(symbol)
                if lc_data:
                    interactions = lc_data.get("interactions", 0)
                    sentiment = lc_data.get("sentiment", 50)
                    print(f"🌕 [LunarCrush] {symbol}: Interactions: {interactions}, Sentiment: {sentiment}%")
                    
                    if sentiment >= 70 and interactions > 500:
                        conf += 15.0 # Бустим уверенность ИИ за счет сильного социального хайпа!
                        print(f"📈 [LunarCrush] Хайп подтвержден! Буст +15% к Score.")
                    elif sentiment < 30:
                        conf -= 20.0
                        print(f"📉 [LunarCrush] Негативный сентимент! Штраф -20% к Score.")
            # -------------------------------
            
            print(f"🧠 Raydium XGBoost (Безлимит): {mint} | Score: {conf:.1f}%")
            import config; threshold = 75.0 if getattr(config, "AI_MODE", "sniper") == "sniper" else 65.0
            
            is_buy = conf >= threshold
            
            if not is_buy:
                try:
                    from shadow_tracker import ShadowTracker
                    shadow = ShadowTracker()
                    price = float(pair_data.get("priceUsd", 0))
                    # Пишем Raydium FOMO-монеты в ту же таблицу shadow_log для дальнейшего анализа
                    shadow.log_rejection(
                        mint=mint,
                        reason=f"FOMO XGBoost low score: {conf:.1f}%",
                        score=conf,
                        price=price,
                        features=df.iloc[0].to_dict()
                    )
                except Exception as e:
                    print(f"Ошибка записи в ShadowTracker (Raydium): {e}")
                    
            return is_buy
        except Exception as e:
            print(f"⚠️ Ошибка XGBoost (analyze_token_raydium) для {mint}: {e}")
            return False
    async def analyze_token_ws(self, ws_data: dict) -> bool:
        mint = ws_data.get("mint")
        symbol = ws_data.get("symbol", "UNKNOWN")
        name = ws_data.get("name", "Unknown")
        
        # Читаем соцсети прямо из смарт-контракта (создатель обязан их указать при деплое на Pump.fun)
        has_twitter = bool(ws_data.get("twitter"))
        has_telegram = bool(ws_data.get("telegram"))
        has_website = bool(ws_data.get("website"))
        socials_count = sum([has_twitter, has_telegram, has_website])
        
        # Расчет стартовой ликвидности из кривой Bonding Curve
        v_sol = ws_data.get("vSolInBondingCurve", 30.0)
        from sol_price import get_sol_price_sync
        sol_price = get_sol_price_sync()
        liq_usd = v_sol * sol_price
        
        initial_buy = ws_data.get("initialBuy", 0)
        
        safety_score = 40
        momentum_score = 40 if initial_buy > 0 else 20
        
        # Social Score теперь зависит от того, сколько ссылок создатель прикрепил к контракту
        social_score = 10
        if has_twitter: social_score += 30
        if has_telegram: social_score += 30
        if has_website: social_score += 30
        social_score = min(100, social_score)
        
        alpha_score = int((safety_score * 0.35) + (momentum_score * 0.40) + (social_score * 0.25))
        
        self._save_scanned_token({
            "symbol": symbol,
            "mint": mint,
            "score": alpha_score,
            "safety": safety_score,
            "momentum": momentum_score,
            "social": social_score,
            "liquidity": liq_usd,
            "vol_24h": 0,
            "buys": 1 if initial_buy > 0 else 0,
            "sells": 0,
            "m5_change": 0,
            "age_mins": "0m (WSS)",
            "time": time.time()
        })
        
        print(f"📡 [WSS SNIPER] Пойман токен: {name} (${symbol}) | Liq: ${liq_usd:.0f} | Socials: {socials_count}")
        
        # СТРОГИЕ ФИЛЬТРЫ ДЛЯ 0-СЕКУНДНЫХ МОНЕТ
        
        # 1. Защита от ленивых скаммеров (мусор без соцсетей)
        if socials_count == 0:
            print(f"🚫 [WSS] Отказ: Создатель {symbol} даже не прикрепил соцсети. 100% мусор.")
            return False
            
        # 2. Skin in the game & Анти-монополия (Initial Buy)
        # PumpPortal отдает initialBuy в SOL. Требуем от 0.1 до 5 SOL.
        if initial_buy < 0.1:
            print(f"🚫 [WSS] Отказ: Создатель вкинул слишком мало ({initial_buy} SOL). У него нет 'шкуры на кону'.")
            return False
        if initial_buy > 5.0:
            print(f"🚫 [WSS] Отказ: Создатель выкупил слишком много токенов ({initial_buy} SOL). Высокий риск монопольного дампа.")
            return False
            
        # 3. Проверка кода (RugCheck)
        if not await self.check_rugcheck(mint):
            print(f"🚫 [WSS] Отказ: {symbol} не прошел стартовый RugCheck.")
            return False
            
        # 4. Проверка кошелька разработчика (Helius RPC) и метаданных IPFS
        trader_pubkey = ws_data.get("traderPublicKey")
        uri = ws_data.get("uri")
        
        dev_balance_sol = 0
        description = ""
        
        import aiohttp
        session = await self.get_session()
        if True:
            # Запрос баланса к Helius
            if trader_pubkey:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [trader_pubkey, {"encoding": "jsonParsed"}]
                }
                try:
                    async with session.post(config.HELIUS_RPC_URL, json=payload, timeout=2) as resp:
                        data = await resp.json()
                        lamports = data.get("result", {}).get("value", {}).get("lamports", 0) if data.get("result", {}).get("value") else 0
                        dev_balance_sol = lamports / 1e9
                except Exception as e:
                    print(f"⚠️ Ошибка RPC баланса: {e}")
                    
            # Загрузка метаданных IPFS
            if uri:
                try:
                    async with session.get(uri, timeout=2) as resp:
                        meta = await resp.json()
                        description = meta.get("description", "").lower()
                except Exception:
                    pass
                    
        if dev_balance_sol < 0.1:
            print(f"🚫 [WSS] Отказ: Кошелек разработчика пуст ({dev_balance_sol:.2f} SOL). Скаммер-однодневка.")
            return False
            
        bad_words = ["test", "scam", "fuck", "shit", "nigger", "pump and dump", "rug"]
        if any(word in description for word in bad_words) or len(description) < 5:
            print(f"🚫 [WSS] Отказ: Мусорное описание на IPFS (спам/короткое).")
            return False
            
        print(f"🚀 [WSS СИГНАЛ] Входим в токен {symbol} на нулевой секунде! (Dev Wallet: {dev_balance_sol:.2f} SOL, Socials: {socials_count})")
        
        # Эмуляция цены (записываем цену в usd в словарь, чтобы main.py мог ее взять)
        v_tok = ws_data.get("vTokensInBondingCurve", 1073000000.0)
        ws_data["priceUsd"] = (v_sol / v_tok) * sol_price
        return True

    async def fetch_lunarcrush_sentiment(self, symbol: str) -> dict:
        """
        Проверяет хайп (Social Sentiment) монеты в Twitter через LunarCrush.
        """
        import config
        api_key = getattr(config, "LUNARCRUSH_API_KEY", "")
        if not api_key:
            return {}
            
        url = f"https://lunarcrush.com/api4/public/coins/{symbol}/v1"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, headers=headers, timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        coin_data = data.get("data", {})
                        
                        return {
                            "social_volume": coin_data.get("social_volume_24h", 0),
                            "interactions": coin_data.get("interactions_24h", 0),
                            "sentiment": coin_data.get("sentiment", 50)
                        }
            except Exception as e:
                pass
        return {}
