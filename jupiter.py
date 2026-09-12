import aiohttp

class JupiterAPI:
    @staticmethod
    async def get_price(mint: str) -> float:
        """
        Получает кристально точную цену токена в USD через Jupiter Price API v2.
        Это позволяет моментально реагировать на изменение цены для TP/SL.
        """
        url = f"https://api.jup.ag/price/v2?ids={mint}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        price_str = data.get("data", {}).get(mint, {}).get("price")
                        if price_str:
                            return float(price_str)
            except Exception as e:
                print(f"Jupiter API Error: {type(e).__name__} {e}")
        return 0.0

    @staticmethod
    async def check_taxes_and_simulate_swap(mint: str, input_amount_sol: float = 0.1) -> dict:
        """
        Симулирует маршрут через Jupiter. 
        Если Price Impact аномален для маленькой суммы, значит пул пустой или это Honeypot с Tax-fee.
        """
        # 1 SOL = 1e9 lamports
        lamports_in = int(input_amount_sol * 1e9)
        # Input: SOL
        url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={mint}&amount={lamports_in}&slippageBps=500"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status != 200:
                        return {"is_safe": False, "reason": "Jupiter routing failed (No liquidity or rug)"}
                    
                    data = await response.json()
                    price_impact = float(data.get("priceImpactPct", 100))
                    
                    # Если impact > 5% на микрообъеме 0.1 SOL — в пуле нет денег, либо стоит заградительный налог
                    if price_impact > 5.0:
                        return {"is_safe": False, "reason": f"High price impact: {price_impact}%"}
                    
                    return {"is_safe": True, "data": data}
            except Exception as e:
                 return {"is_safe": False, "reason": f"Error: {e}"}

    @staticmethod
    async def get_swap_transaction(mint: str, is_sell: bool = False, amount_lamports: int = 0) -> dict:
        """
        Генерирует реальную транзакцию на Swap через Jupiter API v6.
        Блокировка проскальзывания: жесткий лимит slippageBps = 500 (5%).
        Динамический Priority Fee: если это SELL (Stop-Loss), ставим Very High priority!
        """
        sol_mint = "So11111111111111111111111111111111111111112"
        input_mint = mint if is_sell else sol_mint
        output_mint = sol_mint if is_sell else mint
        
        # 1. Запрашиваем роут с жестким slippage=500
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount_lamports}&slippageBps=500"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(quote_url, timeout=5) as response:
                if response.status != 200:
                    return {"success": False, "reason": "No route or slippage too high"}
                quote_response = await response.json()
                
            # 2. Формируем транзакцию с динамическими fee
            swap_url = "https://quote-api.jup.ag/v6/swap"
            
            # Для экстренных продаж (Stop Loss) ставим priority fee на 'VeryHigh'
            # Для покупок ставим 'High'
            priority_level = "VeryHigh" if is_sell else "High"
            
            payload = {
                "quoteResponse": quote_response,
                "userPublicKey": "YOUR_WALLET_PUBLIC_KEY", # Placeholder для интеграции
                "wrapAndUnwrapSol": True,
                "computeUnitPriceMicroLamports": priority_level, # Автоматический динамический fee от Юпитера
                "dynamicComputeUnitLimit": True
            }
            
            async with session.post(swap_url, json=payload, timeout=5) as response:
                if response.status != 200:
                    return {"success": False, "reason": "Failed to generate swap tx"}
                swap_data = await response.json()
                
                return {"success": True, "tx": swap_data.get("swapTransaction")}
