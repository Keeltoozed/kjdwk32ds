import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

# Убираем хардкод-фильтры
to_remove = '''            buy_count = sum(1 for t in state.trades if t['type'] == 'buy')
            sell_count = sum(1 for t in state.trades if t['type'] == 'sell')
            if sell_count > buy_count:
                print(f"🚫 [TREND DEAD] {state.symbol}: Продаж ({sell_count}) больше, чем покупок ({buy_count}). Пропуск (падающий нож)!")
                state.is_ai_evaluated = True
                return
                
            df = pd.DataFrame(state.trades)
            df['curve_sol_diff'] = df['curve_sol'].diff().fillna(0)
            
            # ДЕТЕКЦИЯ РАСПРЕДЕЛЕНИЯ (Smart Money Exit)
            avg_buy = df[df['type'] == 'buy']['curve_sol_diff'].abs().mean()
            avg_sell = df[df['type'] == 'sell']['curve_sol_diff'].abs().mean()
            
            if avg_sell > (avg_buy * 1.5):
                print(f"🚫 [DISTRIBUTION] {state.symbol}: Киты разгружаются об толпу! Средний Sell ({avg_sell:.2f} SOL) > Средний Buy ({avg_buy:.2f} SOL). Блокируем сделку!")
                state.is_ai_evaluated = True
                return'''

new_start = '''            df = pd.DataFrame(state.trades)
            df['curve_sol_diff'] = df['curve_sol'].diff().fillna(0)'''

content = content.replace(to_remove, new_start)

# Добавляем новые ML-фичи в конец блока DataFrame
old_features_end = '''            df['momentum_15m'] = df['price'].pct_change(min(15, len(df)-1)).fillna(0)
            df['tx_count'] = 1'''

new_features = '''            df['momentum_15m'] = df['price'].pct_change(min(15, len(df)-1)).fillna(0)
            df['tx_count'] = 1
            
            # --- DEEP FEATURE ENGINEERING (Micro-structure) ---
            buy_mask = df['type'] == 'buy'
            sell_mask = df['type'] == 'sell'
            
            buys_count = buy_mask.sum()
            sells_count = sell_mask.sum()
            df['sell_buy_count_ratio'] = sells_count / (buys_count + 1)
            
            total_buy_vol = df.loc[buy_mask, 'curve_sol_diff'].abs().sum()
            total_sell_vol = df.loc[sell_mask, 'curve_sol_diff'].abs().sum()
            df['sell_buy_vol_ratio'] = total_sell_vol / (total_buy_vol + 1e-9)
            
            df['trade_size_variance'] = df['curve_sol_diff'].abs().var()
            df['trade_size_variance'] = df['trade_size_variance'].fillna(0)
            
            # Time diffs
            df['time_diff_ms'] = df['timestamp'].diff() * 1000
            df['avg_time_between_trades_ms'] = df['time_diff_ms'].mean()
            df['avg_time_between_trades_ms'] = df['avg_time_between_trades_ms'].fillna(0)
            
            # Whale activity
            top_5_vol = df['curve_sol_diff'].abs().nlargest(5).sum()
            df['whale_activity_index'] = top_5_vol / (df['total_vol'].iloc[-1] + 1e-9) if len(df) > 0 else 0
            
            # Price acceleration (2nd derivative)
            df['price_vel'] = df['price'].diff().fillna(0)
            df['price_acceleration'] = df['price_vel'].diff().fillna(0)
            # ------------------------------------------------'''

content = content.replace(old_features_end, new_features)

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
