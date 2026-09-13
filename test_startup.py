import config
from tracker import PaperTracker
import asyncio

async def main():
    tracker = PaperTracker()
    print("Testing load_portfolio()...")
    tracker.load_portfolio()
    print("Loaded open positions:", tracker.get_open_positions())
    
    print("Testing save_portfolio()...")
    tracker.save_portfolio()
    print("Save successful!")

asyncio.run(main())
