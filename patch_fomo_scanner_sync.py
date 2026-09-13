import re

with open('fomo_scanner.py', 'r') as f:
    content = f.read()

old_logic = '''                            from sol_price import fetch_bulk_prices_sync
                            live_prices = fetch_bulk_prices_sync([mint])'''

new_logic = '''                            from sol_price import fetch_bulk_prices_sync
                            import asyncio
                            # Запускаем синхронную функцию в пуле потоков, чтобы не блокировать весь event loop бота!
                            live_prices = await asyncio.to_thread(fetch_bulk_prices_sync, [mint])'''

content = content.replace(old_logic, new_logic)

with open('fomo_scanner.py', 'w') as f:
    f.write(content)
