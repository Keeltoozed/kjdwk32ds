import re

with open('config.py', 'r') as f:
    content = f.read()

content = content.replace('MAX_DAILY_LOSS_USD = 10.0 # Глобальный Kill-Switch. Если убыток за сегодня > 10$, бот останавливается',
                          'MAX_DAILY_LOSS_USD = 10.0 # Минимальный порог в долларах\\nMAX_DAILY_LOSS_PCT = 0.25 # Глобальный Kill-Switch: 25% от текущего депозита за день')

with open('config.py', 'w') as f:
    f.write(content)
