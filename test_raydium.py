import asyncio
from analyzer import Analyzer

async def test():
    analyzer = Analyzer()
    # Find a raydium token
    tokens = await analyzer.fetch_latest_tokens()
    ray_tokens = [t.get("tokenAddress") for t in tokens if t.get("tokenAddress") and t.get("dexId") == "raydium"]
    
    if ray_tokens:
        test_mint = ray_tokens[0]
        print(f"Testing Raydium mint: {test_mint}")
        try:
            res = await analyzer.analyze_token_raydium(test_mint)
            print(f"Result: {res}")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("No raydium tokens found")

asyncio.run(test())
