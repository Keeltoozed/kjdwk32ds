import asyncio
import aiohttp

async def test_mint():
    mint = "GtNcDuiXbEpprdPxnmu7z2L5eoRUEfvCjfPRxaSipump"
    rpc_url = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
    fallback_rpcs = [
        "https://rpc.ankr.com/solana",
        "https://solana-rpc.publicnode.com",
        "https://api.mainnet-beta.solana.com"
    ]
    
    mint_info_payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "getAccountInfo",
        "params": [mint, {"encoding": "jsonParsed"}]
    }
    
    mint_data = None
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(rpc_url, json=mint_info_payload, timeout=10) as resp:
                if resp.status == 200:
                    mint_data = await resp.json(content_type=None)
                    print("✅ Успех через Helius")
                else:
                    raise Exception(f"HTTP {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"Helius упал: {e}. Пробуем fallbacks...")
            for fallback_url in fallback_rpcs:
                print(f"Пробуем {fallback_url} ...")
                try:
                    async with session.post(fallback_url, json=mint_info_payload, timeout=10) as resp:
                        if resp.status == 200:
                            mint_data = await resp.json(content_type=None)
                            print(f"✅ Успех через {fallback_url}")
                            break
                        else:
                            print(f"Ошибка {resp.status} от {fallback_url}: {await resp.text()}")
                except Exception as ex:
                    print(f"Исключение {fallback_url}: {ex}")
                    continue

    if mint_data:
        print("Получили данные!")
    else:
        print("❌ Все RPC недоступны!")

asyncio.run(test_mint())
