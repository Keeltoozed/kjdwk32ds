import asyncio
from analyzer import Analyzer

async def main():
    a = Analyzer()
    tokens = await a.fetch_latest_tokens()
    print(f"Fetched {len(tokens)} tokens")
    for t in tokens[:5]:
        mint = t.get("tokenAddress")
        res = await a.analyze_token(mint)
        print(f"Mint: {mint}, result: {res}")

asyncio.run(main())
