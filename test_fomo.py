import asyncio
from tracker import PaperTracker
from analyzer import Analyzer
from fomo_scanner import fomo_loop

async def main():
    tracker = PaperTracker()
    analyzer = Analyzer()
    print("Запуск тестового прогона FOMO радара...")
    task = asyncio.create_task(fomo_loop(analyzer, tracker))
    await asyncio.sleep(5)
    task.cancel()
    print("Тест завершен.")

if __name__ == "__main__":
    asyncio.run(main())
