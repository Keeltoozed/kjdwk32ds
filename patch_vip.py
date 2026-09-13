import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

# Изменяем evaluate_and_enter(state) -> evaluate_and_enter(state, is_vip=False)
content = content.replace("async def evaluate_and_enter(self, state: TokenTrackerState):", "async def evaluate_and_enter(self, state: TokenTrackerState, is_vip: bool = False):")

# Изменяем порог Confidence
old_conf_logic = '''        import config
        threshold = getattr(config, "AI_CONFIDENCE_THRESHOLD", 85.0)'''
        
new_conf_logic = '''        import config
        threshold = 70.0 if is_vip else getattr(config, "AI_CONFIDENCE_THRESHOLD", 85.0)
        
        if is_vip:
            print(f"🔥 [VIP BYPASS] {state.symbol}: Используем сниженный порог XGBoost ({threshold}%) для Гипер-Ракеты!")'''

content = content.replace(old_conf_logic, new_conf_logic)

# Добавляем вызов VIP проверки перед 60% прогрессом
old_loop = '''                            # Если достигли 60% (Proof of Traction ~ $12k MC), оцениваем ИИ
                            if not state.is_ai_evaluated and progress >= 60.0 and len(state.trades) > 5:
                                await self.evaluate_and_enter(state)'''

new_loop = '''                            
                            # --- HYPER-ROCKET BYPASS LOGIC ---
                            time_alive = time.time() - state.start_time
                            if not state.is_ai_evaluated and time_alive <= 30.0 and len(state.trades) > 5:
                                buys = sum(1 for t in state.trades if t['type'] == 'buy')
                                
                                # $30,000 это примерно 200 SOL. Если за первые секунды залили > 50 SOL (скорость $30k/m) и > 25 покупок
                                current_curve = sol_amount
                                initial_curve = 30.0 # Базовая стартовая кривая Pump.fun
                                injected_sol = current_curve - initial_curve
                                
                                if injected_sol > 40.0 and buys > 25:
                                    print(f"🚀🚀🚀 [HYPER-ROCKET DETECTED] {state.symbol}! Влито {injected_sol:.1f} SOL, {buys} покупок за {time_alive:.1f} сек!")
                                    await self.evaluate_and_enter(state, is_vip=True)
                                    continue
                            # ---------------------------------
                            
                            # Стандартный Quarantine: Если достигли 60% (Proof of Traction ~ $12k MC), оцениваем ИИ
                            if not state.is_ai_evaluated and progress >= 60.0 and len(state.trades) > 5:
                                # Standard filter (if you want to add unique_buyers back)
                                if len(state.unique_buyers) < 15:
                                    # print(f"🚫 [LOW TRACTION] {state.symbol}: Всего {len(state.unique_buyers)} покупателей при 60% Bonding Curve. Ждем дальше...")
                                    continue
                                await self.evaluate_and_enter(state, is_vip=False)'''

content = content.replace(old_loop, new_loop)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
