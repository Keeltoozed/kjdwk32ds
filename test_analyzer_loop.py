import asyncio
from analyzer import Analyzer

async def main():
    a = Analyzer()
    print("Starting continuous observation...")
    for _ in range(3): # Run 3 iterations
        tokens = await a.fetch_latest_tokens()
        print(f"\n--- Fetched {len(tokens)} unique tokens from Dexscreener ---")
        
        for t in tokens[:15]: # check top 15 each time
            mint = t.get("tokenAddress")
            symbol = t.get("symbol", "UNKNOWN")
            print(f"\nAnalyzing: {symbol} ({mint})")
            res = await a.analyze_token(mint)
            if res:
                print(f"✅ PASSED AND BOUGHT: {symbol}")
            else:
                print(f"❌ REJECTED: {symbol}")
                
        await asyncio.sleep(10)
    print("Observation complete.")

if __name__ == "__main__":
    asyncio.run(main())
