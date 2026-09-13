import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

old_eval = '''            buy_count = sum(1 for t in state.trades if t['type'] == 'buy')
            sell_count = sum(1 for t in state.trades if t['type'] == 'sell')
            if sell_count > buy_count:
                print(f"🚫 [TREND DEAD] {state.symbol}: Продаж ({sell_count}) больше, чем покупок ({buy_count}). Пропуск (падающий нож)!")
                state.is_ai_evaluated = True
                return'''

new_eval = '''            buys = [t for t in state.trades if t['type'] == 'buy']
            sells = [t for t in state.trades if t['type'] == 'sell']
            buy_count = len(buys)
            sell_count = len(sells)
            
            if sell_count > buy_count:
                print(f"🚫 [TREND DEAD] {state.symbol}: Продаж ({sell_count}) больше, чем покупок ({buy_count}). Пропуск (падающий нож)!")
                state.is_ai_evaluated = True
                return
                
            # ДЕТЕКЦИЯ РАСПРЕДЕЛЕНИЯ (Smart Money Exit)
            avg_buy = sum(abs(t.get('curve_sol_diff', 0)) for t in buys) / buy_count if buy_count else 0
            avg_sell = sum(abs(t.get('curve_sol_diff', 0)) for t in sells) / sell_count if sell_count else 0
            if avg_sell > (avg_buy * 1.5):
                print(f"🚫 [DISTRIBUTION] {state.symbol}: Киты разгружаются об толпу! Средний Sell > Средний Buy в 1.5 раза. Блокируем сделку!")
                state.is_ai_evaluated = True
                return'''

content = content.replace(old_eval, new_eval)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
