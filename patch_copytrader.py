import re

with open('copytrader.py', 'r') as f:
    content = f.read()

# Мы добавим ротацию RPC/WSS в copytrader.py
new_listen = '''
    async def listen(self):
        # Список бесплатных публичных WSS
        wss_endpoints = [
            self.wss_url, # Helius
            "wss://api.mainnet-beta.solana.com/",
            "wss://solana-rpc.publicnode.com/"
        ]
        endpoint_idx = 0
        
        while True:
            current_wss = wss_endpoints[endpoint_idx % len(wss_endpoints)]
            try:
                async with websockets.connect(current_wss) as ws:
                    print(f"👥 Копитрейдер: Подключен к {current_wss.split('.')[0]}... Слушаем {len(self.wallets)} китов")
                    
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
                            asyncio.create_task(self.process_transaction(signature))
                                
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg:
                    print(f"⚠️ Лимит запросов 429 на {current_wss}. Переключаемся на следующий сервер...")
                    endpoint_idx += 1
                    await asyncio.sleep(2)
                else:
                    print(f"Ошибка Копитрейдера ({current_wss}): {err_msg}. Переподключение...")
                    await asyncio.sleep(5)
'''

content = re.sub(r'    async def listen\(self\):.*', new_listen.strip(), content, flags=re.DOTALL)

with open('copytrader.py', 'w') as f:
    f.write(content)
