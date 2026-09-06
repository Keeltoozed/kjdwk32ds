import asyncio
from analyzer import Analyzer

async def run():
    analyzer = Analyzer()
    tokens = await analyzer.fetch_latest_tokens()
    print(f"Found {len(tokens)} tokens.")
    for t in tokens[:15]:
        mint = t.get('tokenAddress')
        if not mint: continue
        res = await analyzer.analyze_token(mint)
        print(f"Token {mint} -> {res}")

asyncio.run(run())
