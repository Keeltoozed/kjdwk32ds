import re

with open('jupiter.py', 'r') as f:
    content = f.read()

old_quote = '''        # 1. Запрашиваем роут с жестким slippage=500
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount_lamports}&slippageBps=300"'''

new_quote = '''        # 1. Динамическое проскальзывание: Вход строгий (3%), Выход агрессивный (15%), чтобы не застрять в падающей монете!
        slippage = 1500 if is_sell else 300
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount_lamports}&slippageBps={slippage}"'''

content = content.replace(old_quote, new_quote)

with open('jupiter.py', 'w') as f:
    f.write(content)
