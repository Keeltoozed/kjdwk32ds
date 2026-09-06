import asyncio
from main import async_main

async def run_for_a_bit():
    print("Starting bot...")
    task = asyncio.create_task(async_main())
    await asyncio.sleep(15)
    print("Test finished successfully.")
    task.cancel()

asyncio.run(run_for_a_bit())
