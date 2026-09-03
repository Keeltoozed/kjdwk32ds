import asyncio
import websockets
import json
import time
import aiohttp

class CopyTrader:
    def __init__(self, tracker):
        self.tracker = tracker
        self.wallets = {
            "5FGoPPj1nL8LCnfVnpTmreqQtqLuMXXAwuS1uahMrp8V": "@DumbCrayonEater",
            "2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V": "@Salem1299534",
            "DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39": "@Natan_benish",
            "GFRjGNXY8JrGSPC46inqrH4XPdUFMDLkE1oNm1nXiPsJ": "@brrrgrrrz",
            "7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9": "@notanicecat69"
        }
        self.HELIUS_API_KEY = "9efda6f4-fddb-42d3-a2b1-098bbbecd299"
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}"
        self.wss_url = f"wss://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}"
        self.processed_sigs = set()
        
    async def get_token_price(self, mint):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}") as resp:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        return float(pairs[0].get("priceUsd", 0))
        except:
            pass
        return 0.0001

    async def fetch_transaction(self, signature):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.rpc_url, json=payload) as resp:
                    return await resp.json()
        except:
            return None

    async def process_transaction(self, signature):
        if signature in self.processed_sigs: return
        self.processed_sigs.add(signature)
        if len(self.processed_sigs) > 1000: self.processed_sigs.clear()
        
        # Даем RPC ноде 1.5 секунды на индексацию
        await asyncio.sleep(1.5)
        
        tx_data = await self.fetch_transaction(signature)
        if not tx_data or "result" not in tx_data or not tx_data["result"]:
            await asyncio.sleep(1.5)
            tx_data = await self.fetch_transaction(signature)
            if not tx_data or "result" not in tx_data or not tx_data["result"]: return

        meta = tx_data["result"].get("meta", {})
        if not meta or meta.get("err"): return # Ошибка транзакции (Failed)

        pre_bals = meta.get("preTokenBalances", [])
        post_bals = meta.get("postTokenBalances", [])

        # Проверяем транзакцию для каждого из 5 китов
        for wallet, trader_name in self.wallets.items():
            pre_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in pre_bals if b.get("owner") == wallet}
            post_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in post_bals if b.get("owner") == wallet}

            for mint, post_amt in post_dict.items():
                if not mint.endswith("pump"): continue
                
                pre_amt = pre_dict.get(mint, 0.0)
                if post_amt > pre_amt: # Баланс вырос = ПОКУПКА
                    print(f"🚨 COPYTRADE СИГНАЛ: {trader_name} только что купил {mint}!")
                    
                    if len(self.tracker.get_open_positions()) >= 5:
                        print("🚫 Лимит позиций. Пропускаем копитрейд.")
                        return
                        
                    price_usd = await self.get_token_price(mint)
                    symbol = f"COPY_{trader_name[1:5].upper()}"
                    
                    self.tracker.add_position(symbol, mint, price_usd, amount_usd=5.0)
                    print(f"✅ Успешно скопировали сделку {trader_name} через Helius!")

    async def listen(self):
        while True:
            try:
                async with websockets.connect(self.wss_url) as ws:
                    print(f"👥 Helius Копитрейдер запущен! Слушаем {len(self.wallets)} китов (Free API)...")
                    
                    req_id = 1
                    for wallet in self.wallets.keys():
                        payload = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "method": "logsSubscribe",
                            "params": [{"mentions": [wallet]}, {"commitment": "processed"}]
                        }
                        await ws.send(json.dumps(payload))
                        req_id += 1
                        
                    async for message in ws:
                        data = json.loads(message)
                        if "method" in data and data["method"] == "logsNotification":
                            result = data["params"]["result"]
                            signature = result["value"]["signature"]
                            
                            # Не блокируем сокет, отправляем на асинхронную расшифровку
                            asyncio.create_task(self.process_transaction(signature))
                                
            except Exception as e:
                print(f"Ошибка Helius Копитрейдера: {e}. Переподключение через 5с...")
                await asyncio.sleep(5)
