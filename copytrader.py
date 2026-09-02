import asyncio
import websockets
import json
import time
import aiohttp

class CopyTrader:
    def __init__(self, tracker):
        self.tracker = tracker
        # Настоящие on-chain кошельки трейдеров с FOMO (без signer-пустышек)
        self.wallets = {
            "5FGoPPj1nL8LCnfVnpTmreqQtqLuMXXAwuS1uahMrp8V": "@DumbCrayonEater",
            "2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V": "@Salem1299534",
            "DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39": "@Natan_benish",
            "GFRjGNXY8JrGSPC46inqrH4XPdUFMDLkE1oNm1nXiPsJ": "@brrrgrrrz",
            "7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9": "@notanicecat69"
        }
        
    async def get_sol_price(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112") as resp:
                    data = await resp.json()
                    return float(data['data']['So11111111111111111111111111111111111111112']['price'])
        except:
            return 140.0 # fallback

    async def listen(self):
        uri = "wss://pumpportal.fun/api/data"
        while True:
            try:
                async with websockets.connect(uri) as ws:
                    payload = {
                        "method": "subscribeAccountTrade",
                        "keys": list(self.wallets.keys())
                    }
                    await ws.send(json.dumps(payload))
                    print(f"👥 Копитрейдер запущен! Следим за {len(self.wallets)} китами с FOMO...")
                    
                    async for message in ws:
                        data = json.loads(message)
                        tx_type = data.get("txType")
                        mint = data.get("mint")
                        trader = data.get("traderPublicKey")
                        
                        if tx_type == "buy" and mint and trader in self.wallets:
                            trader_name = self.wallets[trader]
                            sol_amount = data.get("solAmount", 0)
                            print(f"🚨 COPYTRADE СИГНАЛ: {trader_name} только что купил {mint} на {sol_amount} SOL!")
                            
                            if len(self.tracker.get_open_positions()) >= 5: # config.MAX_CONCURRENT_POSITIONS
                                print("🚫 Лимит позиций. Пропускаем копитрейд.")
                                continue
                                
                            v_sol = data.get("vSolInBondingCurve")
                            v_tokens = data.get("vTokensInBondingCurve")
                            if v_sol and v_tokens:
                                price_sol = v_sol / v_tokens
                                sol_usd = await self.get_sol_price()
                                price_usd = price_sol * sol_usd
                                
                                symbol = f"COPY_{trader_name[1:5].upper()}"
                                
                                # Копитрейдинг покупает СРАЗУ, в обход анализатора, 
                                # так как мы полностью доверяем этим китам.
                                self.tracker.add_position(symbol, mint, price_usd, amount_usd=5.0)
                                print(f"✅ Успешно скопировали сделку {trader_name}!")
                                
            except Exception as e:
                print(f"Ошибка Копитрейдера: {e}. Переподключение...")
                await asyncio.sleep(5)
