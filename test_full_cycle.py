import asyncio
from tracker import PaperTracker
from analyzer import Analyzer
from main import position_manager_loop
import config

class MockAnalyzer(Analyzer):
    def __init__(self):
        super().__init__()
        # Имитируем график: Покупка по 1.0$ -> Рост до 1.5$ (+50%) -> Рост до 2.0$ (+100%, Трейлинг активирован!) -> Дамп до 1.2$ (Выбивает по трейлингу!)
        self.price_steps = [1.0, 1.5, 2.0, 1.2, 1.0] 
        self.step = 0

    async def fetch_token_data(self, mint):
        if self.step < len(self.price_steps):
            price = self.price_steps[self.step]
            self.step += 1
        else:
            price = self.price_steps[-1]
        print(f"📈 [Рынок] Цена токена {mint[:8]}: {price}$")
        return {"priceUsd": str(price)}

async def main():
    tracker = PaperTracker()
    analyzer = MockAnalyzer()
    
    # Ускорим sleep для теста, без рекурсии!
    import main
    _orig_sleep = asyncio.sleep
    main.asyncio.sleep = lambda x: _orig_sleep(1)
    
    print("\n--- ЭТАП 1: ПОИСК И ПОКУПКА ---")
    print("🤖 ИИ (Gary) проанализировал метрики и дал сигнал: BUY (Уверенность 90%)")
    tracker.add_position("FOMO-RKT", "MockMint123456789", 1.0, 20.0)
    
    print("\n--- ЭТАП 2: УПРАВЛЕНИЕ ПОЗИЦИЕЙ (STOP LOSS / TAKE PROFIT) ---")
    task = asyncio.create_task(position_manager_loop(analyzer, tracker))
    
    await _orig_sleep(6) # Даем циклу пройти 6 шагов
    task.cancel()
    
    print("\n--- ЭТАП 3: ИТОГОВЫЙ ПОРТФЕЛЬ ---")
    history = tracker.get_trade_history()
    if history:
        print(f"Закрытая сделка: {history[-1]}")
    else:
        print("Сделка не была закрыта (позиция еще открыта)")

if __name__ == "__main__":
    asyncio.run(main())
