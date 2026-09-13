import re

with open('fomo_scanner.py', 'r') as f:
    content = f.read()

content = content.replace('print(f"Ошибка получения трендов GeckoTerminal: {e}")', 'print(f"Ошибка получения трендов GeckoTerminal: {type(e).__name__} - {e}")')
content = content.replace('print(f"Ошибка получения топ-монет Pump.fun: {e}")', 'print(f"Ошибка получения топ-монет Pump.fun: {type(e).__name__} - {e}")')
content = content.replace('print(f"Ошибка получения FOMO токенов: {e}")', 'print(f"Ошибка получения FOMO токенов: {type(e).__name__} - {e}")')

with open('fomo_scanner.py', 'w') as f:
    f.write(content)
