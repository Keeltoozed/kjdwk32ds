import asyncio
from fomo_scanner import fetch_fomo_family_trending, fetch_dexscreener_trending
from analyzer import Analyzer

async def test_logic():
    print("🚀 --- ТЕСТ 1: Проверка FOMO Радара (Ловец Ракет) ---")
    try:
        fomo_mints = await fetch_fomo_family_trending()
        print(f"✅ Успешно получено {len(fomo_mints)} трендовых токенов с fomo.family (Solana + EVM)")
        if fomo_mints:
            print(f"Примеры: {fomo_mints[:3]}")
    except Exception as e:
        print(f"❌ Ошибка FOMO: {e}")

    print("\n🚀 --- ТЕСТ 2: Проверка Helius Anti-Scam (Тот самый баг с payload) ---")
    try:
        import aiohttp
        from http_client import get_session
        session = await get_session()
        # Имитируем проверку холдеров (как в починенном снайпере)
        mint_to_test = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R" # Raydium token (RAY)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint_to_test]
        }
        rpc_url = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
        async with session.post(rpc_url, json=payload, timeout=10) as resp:
            data = await resp.json()
            if "result" in data:
                accounts = data["result"].get("value", [])
                print(f"✅ Helius RPC работает! Найдено {len(accounts)} крупнейших холдеров.")
                if accounts:
                    print(f"Топ холдер баланс: {accounts[0]['uiAmount']}")
            else:
                print(f"⚠️ Helius вернул ошибку: {data}")
    except Exception as e:
        print(f"❌ Ошибка Helius: {e}")

asyncio.run(test_logic())
