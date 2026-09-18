import asyncio
import aiohttp

async def run():
    payload = {"jsonrpc":"2.0","id":1,"method":"getTokenLargestAccounts","params":["8BBNS7hURKU4whJ9L7BpjnAR8n9bKFiPA4ZKcYs76qHu"]}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://explorer.solana.com",
        "Referer": "https://explorer.solana.com/"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://solana-rpc.publicnode.com", json=payload, headers=headers) as resp:
            print(await resp.text())

asyncio.run(run())
