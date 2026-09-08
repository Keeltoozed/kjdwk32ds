import asyncio
from analyzer import Analyzer
async def test():
    a = Analyzer()
    tokens = await a.fetch_latest_tokens()
    print(f"Found {len(tokens)} tokens")
    for t in tokens[:5]:
        mint = t.get("tokenAddress")
        res = await a.analyze_token(mint)
        print(f"{mint}: {res}")
asyncio.run(test())
