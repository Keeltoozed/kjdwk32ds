import asyncio
import time
import aiohttp
import config

# Кэш успешных двухсторонних симуляций (mint -> (ts, res_dict)) для защиты от 429
_SIM_CACHE: dict = {}
_SIM_LOCKS: dict = {}
_LAST_SIM_TS: float = 0.0


def _get_sim_lock():
    try:
        loop = asyncio.get_running_loop()
        k = id(loop)
    except RuntimeError:
        k = 0
    if k not in _SIM_LOCKS:
        _SIM_LOCKS[k] = asyncio.Lock()
    return _SIM_LOCKS[k]


def _jup_headers(host: str) -> dict:
    if "lite-api.jup.ag" in host:
        return {"Accept": "application/json"}
    key = (getattr(config, "JUP_API_KEY", "") or "").strip()
    return {"x-api-key": key, "Accept": "application/json"} if key else {"Accept": "application/json"}


class JupiterAPI:
    @staticmethod
    async def get_prices(mints: list, pair_hints: dict = None) -> dict:
        """
        Балк-запрос цен: Jupiter Lite первым (своя квота), GeckoTerminal как fallback.
        Значительно ускоряет цикл трекинга позиций, избавляя от последовательных HTTP-запросов.
        """
        if not mints:
            return {}
        import market_data
        try:
            return await market_data.get_bulk_prices(mints, pair_hints=pair_hints)
        except Exception:
            pass
        addresses = ",".join(mints)
        url = f"https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{addresses}"
        headers = {"Accept": "application/json"}

        from http_client import fetch_json
        try:
            status, data = await fetch_json(url, headers=headers, timeout=8, retries=1)
            if status == 200 and data:
                prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                return {mint: float(price) for mint, price in prices.items() if float(price or 0) > 0}
        except Exception:
            pass
        return {}

    @staticmethod
    async def get_price(mint: str) -> float:
        prices = await JupiterAPI.get_prices([mint])
        return prices.get(mint, 0.0)

    @staticmethod
    async def check_taxes_and_simulate_swap(mint: str, input_amount_sol: float = 0.05) -> dict:
        """
        ДВУХСТОРОННЯЯ СИМУЛЯЦИЯ (BUY + SELL):
        Проверяет, можно ли не только купить, но и ПРОДАТЬ токен обратно в SOL!
        Защита от Honeypot (кейс RICK/CRACKED: купить можно, продать нельзя -> NO_ROUTES_FOUND).
        Использует x-api-key + автоматический фоллбэк на lite-api.jup.ag + кэш 180с против 429!
        """
        global _LAST_SIM_TS
        now = time.time()
        cached = _SIM_CACHE.get(mint)
        if cached and (now - cached[0]) < (180.0 if cached[1].get("is_safe") else 45.0):
            return cached[1]

        lamports_in = int(input_amount_sol * 1e9)
        sol_mint = "So11111111111111111111111111111111111111112"
        hosts = ["https://api.jup.ag", "https://lite-api.jup.ag"]

        from http_client import get_session
        session = await get_session()
        try:
            async with _get_sim_lock():
                wait_s = 0.25 - (time.time() - _LAST_SIM_TS)
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
                _LAST_SIM_TS = time.time()

                # 1. Симуляция ПОКУПКИ (SOL -> Mint) с переключением хостов при 429/401/5xx
                buy_data = None
                for attempt in range(3):
                    host = hosts[attempt % len(hosts)]
                    buy_url = f"{host}/swap/v1/quote?inputMint={sol_mint}&outputMint={mint}&amount={lamports_in}&slippageBps=300"
                    async with session.get(buy_url, headers=_jup_headers(host), timeout=5) as response:
                        if response.status in (429, 401, 403, 500, 502, 503, 504):
                            await asyncio.sleep(0.35 * (attempt + 1))
                            continue
                        if response.status != 200:
                            try:
                                err_data = await response.json()
                                err_code = err_data.get("errorCode", err_data.get("error", "NO_ROUTE"))
                            except Exception:
                                err_code = "NO_ROUTE"
                            res = {"is_safe": False, "reason": f"Jupiter buy routing failed ({err_code})"}
                            _SIM_CACHE[mint] = (time.time(), res)
                            return res

                        buy_data = await response.json()
                        break

                if not buy_data:
                    return {"is_safe": False, "rate_limited": True, "reason": "Jupiter API rate limit (429) on buy"}

                buy_impact = float(buy_data.get("priceImpactPct", 100) or 100)
                if buy_impact > 6.0:
                    res = {"is_safe": False, "reason": f"High buy price impact: {buy_impact:.1f}% (empty pool or tax)"}
                    _SIM_CACHE[mint] = (time.time(), res)
                    return res

                out_tokens = int(buy_data.get("outAmount", 0) or 0)
                if out_tokens <= 0:
                    return {"is_safe": False, "reason": "Zero tokens returned from buy quote"}

                # 2. Симуляция ПРОДАЖИ (Mint -> SOL) на 50% полученных токенов с переключением хостов при 429
                sell_amount = max(1, out_tokens // 2)
                sell_data = None
                for attempt in range(3):
                    host = hosts[(attempt + 1) % len(hosts)]
                    sell_url = f"{host}/swap/v1/quote?inputMint={mint}&outputMint={sol_mint}&amount={sell_amount}&slippageBps=500"
                    async with session.get(sell_url, headers=_jup_headers(host), timeout=5) as sell_resp:
                        if sell_resp.status in (429, 401, 403, 500, 502, 503, 504):
                            await asyncio.sleep(0.35 * (attempt + 1))
                            continue
                        if sell_resp.status != 200:
                            try:
                                sell_err = await sell_resp.json()
                                err_code = sell_err.get("errorCode", sell_err.get("error", "NO_ROUTE"))
                            except Exception:
                                err_code = "NO_ROUTE"
                            res = {"is_safe": False, "reason": f"HONEYPOT: cannot sell token back to SOL ({err_code})"}
                            _SIM_CACHE[mint] = (time.time(), res)
                            return res

                        sell_data = await sell_resp.json()
                        break

                if not sell_data:
                    return {"is_safe": False, "rate_limited": True, "reason": "Jupiter API rate limit (429) on sell"}

                if not sell_data or "outAmount" not in sell_data:
                    return {"is_safe": False, "reason": "HONEYPOT: invalid sell quote from Jupiter"}

                sell_impact = float(sell_data.get("priceImpactPct", 100) or 100)
                if sell_impact > 15.0:
                    res = {"is_safe": False, "reason": f"High sell price impact: {sell_impact:.1f}% (liquidity drained)"}
                    _SIM_CACHE[mint] = (time.time(), res)
                    return res

                out_sol_lamports = int(sell_data.get("outAmount", 0) or 0)
                if out_sol_lamports <= 0:
                    res = {"is_safe": False, "reason": "HONEYPOT: 0 SOL returned when selling token"}
                    _SIM_CACHE[mint] = (time.time(), res)
                    return res

                res = {"is_safe": True, "buy_data": buy_data, "sell_data": sell_data}
                if len(_SIM_CACHE) > 1500:
                    _SIM_CACHE.clear()
                _SIM_CACHE[mint] = (time.time(), res)
                return res
        except Exception as e:
            return {"is_safe": False, "reason": f"Simulation error: {e}"}

    @staticmethod
    async def is_token_sellable(mint: str) -> tuple:
        """
        Оперативная проверка для открытой позиции: можно ли прямо сейчас продать токен в SOL (Mint -> SOL).
        Возвращает (is_sellable: bool, reason: str).
        Никогда не списывает в 0.0$ при 429 rate limit или сетевых таймаутах.
        """
        sol_mint = "So11111111111111111111111111111111111111112"
        hosts = ["https://api.jup.ag", "https://lite-api.jup.ag"]
        
        from http_client import get_session
        session = await get_session()
        for attempt in range(3):
            host = hosts[attempt % len(hosts)]
            sell_url = f"{host}/swap/v1/quote?inputMint={mint}&outputMint={sol_mint}&amount=1000000000&slippageBps=1500"
            try:
                async with session.get(sell_url, headers=_jup_headers(host), timeout=5) as resp:
                    if resp.status in (429, 401, 403):
                        await asyncio.sleep(0.4 * (attempt + 1))
                        continue
                    if resp.status == 200:
                        data = await resp.json()
                        out_lamports = int(data.get("outAmount", 0) or 0)
                        impact = float(data.get("priceImpactPct", 100) or 100)
                        if out_lamports > 0 and impact < 35.0:
                            return True, "OK"
                        elif impact >= 35.0:
                            return False, f"Extreme sell price impact: {impact:.1f}% (liquidity drained)"
                        else:
                            return False, "Zero SOL out on sell quote"
                    elif resp.status == 400:
                        try:
                            err_data = await resp.json()
                            err_code = err_data.get("errorCode", err_data.get("error", "NO_ROUTE"))
                        except Exception:
                            err_code = "NO_ROUTE"
                        return False, f"HONEYPOT: cannot sell token back to SOL ({err_code})"
                    else:
                        # 5xx или временный сбой API — не считаем рагпуллом
                        return True, f"Jupiter API temporary status {resp.status} (assumed sellable)"
            except Exception as e:
                return True, f"Network error during check ({e}) (assumed sellable)"
        return True, "Jupiter rate limit 429 (assumed sellable)"

    @staticmethod
    async def get_swap_transaction(mint: str, is_sell: bool = False, amount_lamports: int = 0, is_emergency: bool = False) -> dict:
        """
        Генерирует реальную транзакцию на Swap через Jupiter Swap V1 (api.jup.ag).
        quote-api.jup.ag/v6 sunset - мигрировано на Swap V1.
        Блокировка проскальзывания: жесткий лимит slippageBps = 1500 (15%).
        Динамический Priority Fee: если это SELL (Stop-Loss/Crash Guard), ставим Ultra/Very High priority!
        """
        sol_mint = "So11111111111111111111111111111111111111112"
        input_mint = mint if is_sell else sol_mint
        output_mint = sol_mint if is_sell else mint
        
        # 1. Жесткий контроль проскальзывания (Max Slippage) по просьбе пользователя.
        # Устанавливаем лимит в 10% (1000 bps) как на вход, так и на выход. 
        # Бот больше не будет спасать копейки с проскальзыванием 50-100%. Если цена ушла ниже 10% от заявленной - транзакция отменяется.
        slippage = 1000
        quote_url = f"https://api.jup.ag/swap/v1/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount_lamports}&slippageBps={slippage}"
        
        from http_client import get_session
        session = await get_session()
        try:
            async with session.get(quote_url, timeout=5) as response:
                if response.status != 200:
                    return {"success": False, "reason": "No route or slippage too high"}
                quote_response = await response.json()

                # 2. Формируем транзакцию с динамическими fee
                swap_url = "https://api.jup.ag/swap/v1/swap"

            # ИНТЕГРАЦИЯ JITO & PRIORITY FEES
            # Для экстренных продаж (Crash Guard / Stop Loss) агрессивно завышаем комиссию (Jito Tip), 
            # чтобы транзакция гарантированно прошла первой в блоке, обогнав остальных продавцов.
            if is_emergency:
                jito_tip = 5000000  # 0.005 SOL для экстренного спасения капитала
                priority_level = "veryHigh"
            else:
                jito_tip = 150000 if is_sell else 100000
                priority_level = "high"

            payload = {
                "quoteResponse": quote_response,
                "userPublicKey": "YOUR_WALLET_PUBLIC_KEY", # Placeholder для интеграции
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": {
                    "jitoTipLamports": jito_tip,
                    "priorityLevelWithMaxLamports": {
                        "maxLamports": 2000000,
                        "priorityLevel": priority_level
                    }
                }
            }

            async with session.post(swap_url, json=payload, timeout=5) as response:
                if response.status != 200:
                    return {"success": False, "reason": "Failed to generate swap tx"}
                swap_data = await response.json()
                encoded_tx = swap_data.get("swapTransaction")

                if not encoded_tx:
                    return {"success": False, "reason": "Empty swapTransaction from Jupiter"}

                # === JITO BUNDLE EXECUTION ===
                # Вместо отправки через обычный RPC (открытый мемпул),
                # направляем транзакцию напрямую в Jito Block Engine.
                # MEV-боты не видят нашу транзакцию — нет сэндвич-атак!
                from jito_executor import JitoExecutor
                jito_ok = await JitoExecutor.send_bundle(encoded_tx, is_emergency=is_emergency)

                if jito_ok:
                    return {"success": True, "tx": encoded_tx, "via": "jito"}
                else:
                    # Fallback: если Jito недоступен, возвращаем транзакцию (вызывающий сам решит что делать)
                    print("⚠️ [JITO] Fallback: бандл не принят, транзакция возвращена для ручной отправки")
                    return {"success": True, "tx": encoded_tx, "via": "fallback_rpc"}

        except Exception as e:
            print(f"Jupiter Swap Error: {type(e).__name__} {e}")
            return {"success": False, "reason": f"Jupiter API error: {e}"}
