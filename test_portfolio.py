import json
import asyncio
from tracker import PaperTracker
from main import position_manager_loop
from analyzer import Analyzer

async def mock_get_price(mint):
    return 0.5

async def run_test():
    tracker = PaperTracker()
    analyzer = Analyzer()
    tracker.add_position("TEST", "mint123", 1.0, 10.0)
    
    from jupiter import JupiterAPI
    JupiterAPI.get_price = mock_get_price
    
    task = asyncio.create_task(position_manager_loop(analyzer, tracker))
    await asyncio.sleep(1)
    task.cancel()
    
    pos = tracker.positions.get("mint123")
    if pos:
        print(f"Status: {pos.status}, Reason: {pos.exit_reason}")
    else:
        print("Position not found")

if __name__ == "__main__":
    asyncio.run(run_test())
