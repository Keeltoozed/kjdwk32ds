import re

with open('jupiter.py', 'r') as f:
    content = f.read()

# Update check_taxes_and_simulate_swap slippage
content = content.replace("slippageBps=500", "slippageBps=300")

old_swap = '''                # Для экстренных продаж (Stop Loss) ставим priority fee на 'VeryHigh'
                # Для покупок ставим 'High'
                priority_level = "VeryHigh" if is_sell else "High"
                
                payload = {
                    "quoteResponse": quote_response,
                    "userPublicKey": "YOUR_WALLET_PUBLIC_KEY", # Placeholder для интеграции
                    "wrapAndUnwrapSol": True,
                    "computeUnitPriceMicroLamports": priority_level, # Автоматический динамический fee от Юпитера
                    "dynamicComputeUnitLimit": True
                }'''

new_swap = '''                # ИНТЕГРАЦИЯ JITO & PRIORITY FEES
                # Для покупок (снайпинга) и экстренных продаж ставим Jito Tip и VeryHigh priority
                jito_tip = 150000 if is_sell else 100000
                priority_level = "veryHigh"
                
                payload = {
                    "quoteResponse": quote_response,
                    "userPublicKey": "YOUR_WALLET_PUBLIC_KEY", # Placeholder для интеграции
                    "wrapAndUnwrapSol": True,
                    "dynamicComputeUnitLimit": True,
                    "prioritizationFeeLamports": {
                        "jitoTipLamports": jito_tip,
                        "priorityLevelWithMaxLamports": {
                            "maxLamports": 2000000,
                            "priorityLevel": priority_level
                        }
                    }
                }'''

content = content.replace(old_swap, new_swap)

with open('jupiter.py', 'w') as f:
    f.write(content)
