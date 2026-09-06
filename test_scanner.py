import asyncio
from analyzer import Analyzer
from tracker import PaperTracker
from main import scanner_loop

async def run_test():
    analyzer = Analyzer()
    tracker = PaperTracker()
    task = asyncio.create_task(scanner_loop(analyzer, tracker))
    await asyncio.sleep(25)
    task.cancel()

if __name__ == "__main__":
    asyncio.run(run_test())
