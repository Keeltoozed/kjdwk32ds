import asyncio
from analyzer import Analyzer

async def run():
    analyzer = Analyzer()
    mint = "6jJU94YrDJPyMyLh6Vk8h2kpoABS4Hrs7vHuPds7boj8"
    result = await analyzer.analyze_token(mint)
    print(f"Result for PCAT: {result}")

asyncio.run(run())
