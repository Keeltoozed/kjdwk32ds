import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import async_main

if __name__ == "__main__":
    print("🚀 [PHANTBOT] Запуск автономного торгового бота (100% Solana Only)...")
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n🛑 [PHANTBOT] Бот остановлен.")
    except Exception as e:
        print(f"\n❌ [PHANTBOT] Ошибка: {e}")
