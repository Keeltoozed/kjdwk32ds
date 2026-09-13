import re

with open('analyzer.py', 'r') as f:
    content = f.read()

old_liq = '''        # Защита от микро-пулов (Scam сетки типа Fly)
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        if liquidity < 15000:
            print(f"📉 Изоляция: {symbol} имеет микро-пул (${liquidity:.0f} < $15k). Риск 100% проскальзывания.")
            return False'''

new_liq = '''        is_vip = self.check_hyper_rocket_momentum(pair_data)
        
        # Защита от микро-пулов (Scam сетки типа Fly)
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        if liquidity < 15000 and not is_vip:
            print(f"📉 Изоляция: {symbol} имеет микро-пул (${liquidity:.0f} < $15k). Риск 100% проскальзывания.")
            return False
            
        if is_vip:
            print(f"🚀 [VIP] {symbol}: Пропуск проверок клонов и соцсетей из-за гипер-моментума!")'''

content = content.replace(old_liq, new_liq)

old_social = '''        if not (has_twitter or has_tg or has_website):
            print(f"🚫 Опасно: У {symbol} вообще нет соцсетей. Обычно это rugpull за 5 минут.")
            return False'''

new_social = '''        if not (has_twitter or has_tg or has_website) and not is_vip:
            print(f"🚫 Опасно: У {symbol} вообще нет соцсетей. Обычно это rugpull за 5 минут.")
            return False'''

content = content.replace(old_social, new_social)

old_clone = '''        # 1.5 Защита от вторичных клонов (Copycat Filter)
        current_created_at = pair_data.get("pairCreatedAt", 0)
        current_fdv = pair_data.get("fdv", 0)
        if await self.is_clone(symbol, mint, current_created_at, current_fdv):
            print(f"🚫 Мусор: Токен {symbol} является клоном! На DexScreener найден более старый/крупный оригинал.")
            return False'''
            
new_clone = '''        # 1.5 Защита от вторичных клонов (Copycat Filter)
        current_created_at = pair_data.get("pairCreatedAt", 0)
        current_fdv = pair_data.get("fdv", 0)
        if not is_vip and await self.is_clone(symbol, mint, current_created_at, current_fdv):
            print(f"🚫 Мусор: Токен {symbol} является клоном! На DexScreener найден более старый/крупный оригинал.")
            return False'''

content = content.replace(old_clone, new_clone)

with open('analyzer.py', 'w') as f:
    f.write(content)
