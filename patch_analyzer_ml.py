import re

with open('analyzer.py', 'r') as f:
    content = f.read()

old_block = '''        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
        
        if sells_m5 > buys_m5:
            print(f"🚫 [TREND DEAD] Токен {mint}: Продаж ({sells_m5}) больше, чем покупок ({buys_m5}) за последние 5 минут. Пропуск (падающий нож).")
            return False
            
        tx_velocity_1m = (buys_m5 + sells_m5) / 5.0'''

new_block = '''        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
            
        tx_velocity_1m = (buys_m5 + sells_m5) / 5.0'''

content = content.replace(old_block, new_block)

with open('analyzer.py', 'w') as f:
    f.write(content)
