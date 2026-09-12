import re

with open('copytrader.py', 'r') as f:
    content = f.read()

new_fetch = '''
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
        
        rpc_endpoints = [
            self.rpc_url,
            "https://api.mainnet-beta.solana.com/",
            "https://solana-rpc.publicnode.com/"
        ]
        
        async with aiohttp.ClientSession() as session:
            for rpc in rpc_endpoints:
                try:
                    async with session.post(rpc, json=payload, timeout=5) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        elif resp.status == 429:
                            continue # Пробуем следующий RPC
                except:
                    continue
        return None
'''

content = re.sub(r'    async def fetch_transaction\(self, signature\):.*?return None', new_fetch.strip(), content, flags=re.DOTALL)

with open('copytrader.py', 'w') as f:
    f.write(content)
