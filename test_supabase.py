import asyncio
from trade_logger import trade_logger

async def test():
    print("Writing entry...")
    await trade_logger.log_entry("TEST_MINT_123", {"feature": 1}, 80.0)
    await asyncio.sleep(2)
    print("Writing exit...")
    await trade_logger.log_exit("TEST_MINT_123", 10.5, "Take Profit")

asyncio.run(test())
