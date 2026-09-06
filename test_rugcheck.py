import asyncio
from analyzer import Analyzer

async def run():
    analyzer = Analyzer()
    # Safe token example:
    print(await analyzer.check_rugcheck("8H5yfL1GoDETLDaLYZzrgQuZs37eiKJjdfP21b6ypump"))

asyncio.run(run())
