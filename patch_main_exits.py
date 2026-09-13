import re

with open('main.py', 'r') as f:
    content = f.read()

# Replace Lock Profit logic
old_lock = '''                safe_lock = max(0.10, min_fee_pct + 0.05) # Минимум +5% чистыми
                if max_pnl_pct >= safe_lock + 0.15 and pnl_pct <= safe_lock:
                    tracker.close_position(mint, current_price, f"Lock Profit (+{safe_lock*100:.1f}%)")
                    continue'''
new_lock = '''                # Lock Profit removed to let runners run. We only trailing stop now.'''

content = content.replace(old_lock, new_lock)

# Replace trailing stop logic
old_trail_micro = '''                    if max_pnl_pct >= 0.80:
                        if drop_from_max >= 0.15: 
                            tracker.close_position(mint, current_price, "Micro-Trailing (15% drop)")
                            continue
                    elif max_pnl_pct >= 0.40:
                        if drop_from_max >= 0.20: 
                            tracker.close_position(mint, current_price, "Micro-Trailing (20% drop)")
                            continue
                    elif max_pnl_pct >= 0.15: 
                        if drop_from_max >= 0.15: 
                            tracker.close_position(mint, current_price, "Micro-Trailing (15% drop)")
                            continue'''

new_trail_micro = '''                    # Только иксы: начинаем трейлить, только когда профит достигает +80%
                    if max_pnl_pct >= 0.80:
                        if drop_from_max >= 0.25: 
                            tracker.close_position(mint, current_price, "Diamond Hand Trailing (25% drop)")
                            continue'''

content = content.replace(old_trail_micro, new_trail_micro)

old_trail = '''                    if max_pnl_pct >= 0.30: 
                        if drop_from_max >= 0.15: 
                            tracker.close_position(mint, current_price, "Trailing Stop (15% drop)")
                            continue
                    elif max_pnl_pct >= 0.15: 
                        if drop_from_max >= 0.10: 
                            tracker.close_position(mint, current_price, "Trailing Stop (10% drop)")
                            continue'''

new_trail = '''                    # Обычный трейлинг тоже отключаем до +80%
                    if max_pnl_pct >= 0.80:
                        if drop_from_max >= 0.25:
                            tracker.close_position(mint, current_price, "Trailing Stop (25% drop)")
                            continue'''

content = content.replace(old_trail, new_trail)

with open('main.py', 'w') as f:
    f.write(content)
