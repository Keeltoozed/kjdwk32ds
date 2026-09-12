import re

with open('main.py', 'r') as f:
    content = f.read()

# Мы закомментируем запуск birdeye_loop
content = content.replace("asyncio.create_task(birdeye_loop(analyzer, tracker))", "# asyncio.create_task(birdeye_loop(analyzer, tracker)) # Отключено, т.к. лимиты API исчерпаны, а FOMO модуль теперь умнее")

with open('main.py', 'w') as f:
    f.write(content)
