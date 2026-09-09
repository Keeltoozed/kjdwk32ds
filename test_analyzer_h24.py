import asyncio
from analyzer import Analyzer

async def main():
    analyzer = Analyzer()
    # Test with some token (e.g. random pump/raydium token)
    res = await analyzer.analyze_token_raydium("7bgLbV11vCBwVK6N62LqR1x3BGEE1Z1fK5jA3Fpump")
    print("Result:", res)

asyncio.run(main())
