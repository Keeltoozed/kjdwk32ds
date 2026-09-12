import re

with open('jupiter.py', 'r') as f:
    content = f.read()

new_tx = '''
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
            try:
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
            except Exception as e:
                print(f"Jupiter Swap Error: {type(e).__name__} {e}")
                return {"success": False, "reason": f"Jupiter API error: {e}"}
'''

content = re.sub(r'    @staticmethod\n    async def get_swap_transaction\(mint: str, is_sell: bool = False, amount_lamports: int = 0\) -> dict:.*?(?=\n    @staticmethod|\Z)', new_tx.strip() + "\n", content, flags=re.DOTALL)

with open('jupiter.py', 'w') as f:
    f.write(content)
