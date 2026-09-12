import re

with open('main.py', 'r') as f:
    content = f.read()

debug_logic = '''
                # 1. Пытаемся получить цену из Юпитера
                current_price = await JupiterAPI.get_price(mint)
                # DEBUG PRINT
                print(f"DEBUG: {position.symbol} Jupiter Price: {current_price}")
'''

content = content.replace("# 1. Пытаемся получить цену из Юпитера\n                current_price = await JupiterAPI.get_price(mint)", debug_logic.strip())

with open('main.py', 'w') as f:
    f.write(content)
