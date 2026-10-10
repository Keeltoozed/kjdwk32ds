import asyncio
import aiohttp
import random

async def test():
    address = "0xdac17f958d2ee523a2206206994597c13d831ec7"
    cid = 1
    goplus_url = f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={address}"
    fake_ip = f"{random.randint(11,250)}.{random.randint(11,250)}.{random.randint(11,250)}.{random.randint(11,250)}"
    gp_headers = {
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/{random.randint(110,122)}.0.0.0 Safari/537.36",
        "X-Forwarded-For": fake_ip,
        "X-Real-IP": fake_ip,
        "Accept": "application/json"
    }
    
    print(f"Testing with Fake IP: {fake_ip}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(goplus_url, headers=gp_headers, timeout=5) as resp:
                print(f"Status: {resp.status}")
                data = await resp.json()
                print("Success! Data preview:", str(data)[:100])
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
