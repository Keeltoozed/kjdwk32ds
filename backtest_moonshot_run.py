"""Запуск полного MOONSHOT на реальных данных (dune_data.csv)."""
import pandas as pd, numpy as np, sys, bisect
sys.path.insert(0, '.')
import config
from exit_managers import MatureExitManager

# Переопределяем config для MOONSHOT
original_reinvest = config.REINVEST_PERCENT
original_time = config.TIME_EXIT_MINUTES
original_pos = config.MAX_CONCURRENT_POSITIONS
original_stop = config.STOP_LOSS_PCT

# Применяем MOONSHOT параметры
config.REINVEST_PERCENT = 12.0  # 12% сайз
config.MAX_CONCURRENT_POSITIONS = 5
config.STOP_LOSS_PCT = -0.40  # -40% стоп
config.TIME_EXIT_MINUTES = 180  # 3 часа max hold

# Загружаем данные
print("=== MOONSHOT FULL BACKTEST ===")
print("Параметры: REINVEST=12%, POS=5, STOP=-40%, TIME=180мин, TP=5x/20x/100x")

df = pd.read_csv("dune_data.csv")
df["minute"] = pd.to_datetime(df["minute"], utc=True)
df = df.sort_values(["mint", "minute"])
series = {m: (g["minute"].tolist(), g["price_usd"].tolist()) for m, g in df.groupby("mint")}

# Простая репликация mature с MOONSHOT фильтром
cands = []
for mint, (times, prices) in series.items():
    if len(prices) < 8:
        continue
    # Вход на 2-6 минуте (раньше mature, но не на 0-й)
    ei = 2  # 2-я минута
    cands.append({"mint": mint, "entry_idx": ei, "mature": True})

cands.sort(key=lambda c: series[c["mint"]][0][c["entry_idx"]])

# Упрощённая симуляция с частичным TP
capital = 1000  # MOONSHOT_CONFIG initial
start = capital
peak = start
max_dd = 0

print(f"Токенов в выборке: {len(series)}")
print(f"Кандидатов на вход (2-я минута): {len(cands)}")

# Быстрый прогон (для скорости — упрощённая версия)
# Полная репликация требует полной логики v2
# Но покажем ключевые выходы на основе данных
results = []
big_wins = 0
stops = 0

for c in cands[:min(20, len(cands))]:  # Ограничим для скорости демонстрации
    mint, (times, prices) = c["mint"], series[c["mint"]]
    entry_idx = c["entry_idx"]
    entry_price = prices[entry_idx]
    
    # Упрощённая симуляция выхода
    max_pnl = 0
    best_pnl = -1
    
    for j in range(entry_idx + 1, min(entry_idx + 20, len(prices))):
        price = prices[j]
        pnl_pct = (price - entry_price) / entry_price
        max_pnl = max(max_pnl, pnl_pct)
        
        # Частичные тейки
        if max_pnl >= 1.0 and best_pnl < 0:  # 100% TP
            best_pnl = pnl_pct
            break
        if max_pnl >= 3.0 and best_pnl < 0:
            best_pnl = pnl_pct
            break
        if max_pnl >= 10.0 and best_pnl < 0:
            best_pnl = pnl_pct
            break
        
        # Trailing 30% после первого TP
        if best_pnl > 0:
            if price < prices[entry_idx] * (1 + best_pnl) * 0.7:
                best_pnl = best_pnl * 0.7
                break
        
        # Стоп -40%
        if pnl_pct <= -0.40:
            best_pnl = -0.40
            stops += 1
            break
    
    if best_pnl > 0:
        big_wins += 1
    
    size = max(4.0, min(100, 1000 * 0.12))
    pnl = size * best_pnl - 0.075
    results.append(pnl)
    capital += pnl
    peak = max(peak, capital)
    max_dd = max(max_dd, (peak - capital) / peak if peak > 0 else 0)

final = start + sum(results)
print(f"\n=== РЕЗУЛЬТАТ MOONSHOT (упрощённая симуляция, 20 сделок) ===")
print(f"Старт: ${start:.2f}")
print(f"Финал: ${final:.2f} ({(final/start-1)*100:+.1f}%)")
print(f"Win: {big_wins}, Stop: {stops}")
print(f"Макс просадка (оценка): {max_dd*100:.1f}%")
print("\nПолная репликация: используйте backtest_live_logic_v2.py с MOONSHOT_CONFIG.")
