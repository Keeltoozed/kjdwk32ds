import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

# 1. Unique buyers filter before evaluate_and_enter
old_trigger = '''                            # Если достигли 60% (Proof of Traction ~ $12k MC)
                            if not state.is_ai_evaluated and progress >= 60.0 and len(state.trades) > 5:
                                await self.evaluate_and_enter(state)'''

new_trigger = '''                            # Если достигли 60% (Proof of Traction ~ $12k MC)
                            if not state.is_ai_evaluated and progress >= 60.0 and len(state.trades) > 5:
                                if len(state.unique_buyers) < 15:
                                    print(f"🚫 [LOW TRACTION] {state.symbol}: Всего {len(state.unique_buyers)} уник. покупателей. Нужно минимум 15 (Защита от искусственного пампа)!")
                                    state.is_ai_evaluated = True
                                    continue
                                await self.evaluate_and_enter(state)'''

content = content.replace(old_trigger, new_trigger)

# 2. Buy/Sell ratio filter inside evaluate_and_enter
old_eval = '''        try:
            df = pd.DataFrame(state.trades)'''

new_eval = '''        try:
            buy_count = sum(1 for t in state.trades if t['type'] == 'buy')
            sell_count = sum(1 for t in state.trades if t['type'] == 'sell')
            if sell_count > buy_count:
                print(f"🚫 [TREND DEAD] {state.symbol}: Продаж ({sell_count}) больше, чем покупок ({buy_count}). Пропуск (падающий нож)!")
                state.is_ai_evaluated = True
                return
                
            df = pd.DataFrame(state.trades)'''

content = content.replace(old_eval, new_eval)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
