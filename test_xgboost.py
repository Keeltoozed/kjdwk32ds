import asyncio
from analyzer import Analyzer

async def test():
    analyzer = Analyzer()
    # Find a pump fun token
    tokens = await analyzer.fetch_latest_tokens()
    pump_tokens = [t.get("tokenAddress") for t in tokens if t.get("tokenAddress") and t.get("tokenAddress").endswith("pump")]
    
    if pump_tokens:
        test_mint = pump_tokens[0]
        print(f"Testing mint: {test_mint}")
        try:
            res = await analyzer.analyze_token_xgboost(test_mint)
            print(f"Result: {res}")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("No pump tokens found")

asyncio.run(test())
