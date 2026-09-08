import asyncio
from pump_fun_sniper import PumpFunSniper
async def main():
    sniper = PumpFunSniper()
    print("Connecting...")
    # We will just run it for 5 seconds to see if it receives anything
    task = asyncio.create_task(sniper.connect_and_listen())
    await asyncio.sleep(5)
    task.cancel()
    print("Done")

asyncio.run(main())
