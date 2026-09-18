import asyncio
import aiohttp

async def run():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": ["8BBNS7hURKU4whJ9L7BpjnAR8n9bKFiPA4ZKcYs76qHu"]}
    fake_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://explorer.solana.com",
        "Referer": "https://explorer.solana.com/"
    }
    url = "https://solana-mainnet.g.alchemy.com/v2/alch_wwSmrv5RZmrq66-lSNekM"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=fake_headers, timeout=10) as resp:
                print(resp.status)
                print(await resp.text())
        except Exception as e:
            print("ERROR", e)

asyncio.run(run())
