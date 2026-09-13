import pandas as pd
import xgboost as xgb
import numpy as np
import json
import random
from collections import Counter

def load_real_distribution():
    """Загружаем реальное распределение PnL после фиксов из Supabase"""
    with open("real_trades.json", "r") as f:
        real_trades = json.load(f)
    
    fixed_pnls = []
    for t in real_trades:
        pnl = t.get("pnl")
        reason = t.get("exit_reason", "")
        if pnl is None:
            continue
        
        # Применяем те же фиксы что и в backtest_after_fix.py
        if "Hard Stop Loss" in reason:
            fixed_pnls.append(-18.0)
            continue
        if "Lock Profit" in reason or "Break-even" in reason:
            if random.random() < 0.50:
                fixed_pnls.append(np.random.uniform(40.0, 200.0))
            else:
                fixed_pnls.append(-18.0)
            continue
        if "Time-based Exit" in reason:
            fixed_pnls.append(pnl * 0.4)
            continue
        if "Micro-Trailing" in reason:
            if random.random() < 0.40:
                fixed_pnls.append(np.random.uniform(80.0, 400.0))
            else:
                fixed_pnls.append(-18.0)
            continue
        fixed_pnls.append(pnl)
    
    return fixed_pnls

def build_buckets(pnls):
    buckets = []
    ranges = [(-999, -50), (-50, -25), (-25, -10), (-10, 10), (10, 50), (50, 100), (100, 9999)]
    for lo, hi in ranges:
        subset = [p for p in pnls if lo < p <= hi] if lo != -999 else [p for p in pnls if p <= hi]
        if lo == 100:
            subset = [p for p in pnls if p > lo]
        pct = len(subset) / len(pnls) if pnls else 0
        avg = np.mean(subset) if subset else 0
        buckets.append((pct, avg))
    return buckets

def simulate_one_run(buckets, seed, days=30, trades_per_day=15):
    random.seed(seed)
    np.random.seed(seed)
    
    START = 20.0
    capital = START
    peak = START
    max_dd = 0
    
    for i in range(trades_per_day * days):
        if capital < 4.0:
            break
        
        trade_size = max(4.0, min(100.0, capital * 0.10))
        
        roll = random.random()
        cumulative = 0
        pnl_pct = 0
        for prob, avg_pnl in buckets:
            cumulative += prob
            if roll <= cumulative:
                pnl_pct = avg_pnl * np.random.uniform(0.7, 1.3)
                break
        
        capital += trade_size * (pnl_pct / 100.0) - 0.075
        if capital > peak: peak = capital
        dd = (peak - capital) / peak
        if dd > max_dd: max_dd = dd
    
    return capital, max_dd

def run():
    print("=" * 60)
    print("🎲 МОНТЕ-КАРЛО: 1000 СИМУЛЯЦИЙ × 30 ДНЕЙ")
    print("   (Каждая симуляция — другая вселенная рынка)")
    print("=" * 60)
    
    # Базовое распределение (после фиксов)
    random.seed(42)
    base_pnls = load_real_distribution()
    base_buckets = build_buckets(base_pnls)
    
    # ========================================
    # 1. МОНТЕ-КАРЛО: 1000 прогонов
    # ========================================
    results = []
    drawdowns = []
    
    for seed in range(1000):
        capital, max_dd = simulate_one_run(base_buckets, seed)
        results.append(capital)
        drawdowns.append(max_dd)
    
    results = np.array(results)
    drawdowns = np.array(drawdowns)
    
    profitable = np.sum(results > 20.0)
    bankrupt = np.sum(results < 4.0)
    
    print(f"\n📊 РЕЗУЛЬТАТЫ 1000 СИМУЛЯЦИЙ (Старт: $20)")
    print(f"  Прибыльных:     {profitable}/1000 ({profitable/10:.1f}%)")
    print(f"  Убыточных:      {1000-profitable}/1000 ({(1000-profitable)/10:.1f}%)")
    print(f"  Слив депозита:  {bankrupt}/1000 ({bankrupt/10:.1f}%)")
    print(f"\n  Медиана:        ${np.median(results):,.2f}")
    print(f"  Среднее:        ${np.mean(results):,.2f}")
    print(f"  ЛУЧШИЙ случай:  ${np.max(results):,.2f}")
    print(f"  ХУДШИЙ случай:  ${np.min(results):,.2f}")
    print(f"  10-й перцентиль: ${np.percentile(results, 10):,.2f}")
    print(f"  25-й перцентиль: ${np.percentile(results, 25):,.2f}")
    print(f"  75-й перцентиль: ${np.percentile(results, 75):,.2f}")
    print(f"  90-й перцентиль: ${np.percentile(results, 90):,.2f}")
    print(f"\n  Средняя просадка:  {np.mean(drawdowns)*100:.1f}%")
    print(f"  Макс. просадка:    {np.max(drawdowns)*100:.1f}%")
    
    # ========================================
    # 2. СТРЕСС-ТЕСТЫ (Плохие рынки)
    # ========================================
    print(f"\n{'='*60}")
    print("🔥 СТРЕСС-ТЕСТЫ: ЭКСТРЕМАЛЬНЫЕ РЫНОЧНЫЕ УСЛОВИЯ")
    print(f"{'='*60}")
    
    # Сценарий A: Волна рагпулов (катастрофы x3)
    stress_a = list(base_buckets)
    stress_a[0] = (stress_a[0][0] * 3, stress_a[0][1])  # Катастрофы x3
    stress_a[6] = (stress_a[6][0] * 0.3, stress_a[6][1])  # Иксы / 3
    # Нормализуем
    total_p = sum(p for p, _ in stress_a)
    stress_a = [(p/total_p, a) for p, a in stress_a]
    
    stress_a_results = [simulate_one_run(stress_a, s)[0] for s in range(200)]
    profitable_a = sum(1 for r in stress_a_results if r > 20)
    print(f"\n  🟥 Волна рагпулов (катастроф x3, иксов /3):")
    print(f"     Прибыльных: {profitable_a}/200 ({profitable_a/2:.1f}%)")
    print(f"     Медиана: ${np.median(stress_a_results):,.2f}")
    print(f"     Худший: ${np.min(stress_a_results):,.2f}")
    
    # Сценарий B: Медвежий рынок (все пампы слабее)
    stress_b = list(base_buckets)
    stress_b[4] = (stress_b[4][0], stress_b[4][1] * 0.5)  # Профиты /2
    stress_b[5] = (stress_b[5][0], stress_b[5][1] * 0.5)  # Большие /2
    stress_b[6] = (stress_b[6][0] * 0.5, stress_b[6][1] * 0.5)  # Иксы /2 и реже
    stress_b[2] = (stress_b[2][0] * 1.5, stress_b[2][1])  # Больше средних убытков
    total_p = sum(p for p, _ in stress_b)
    stress_b = [(p/total_p, a) for p, a in stress_b]
    
    stress_b_results = [simulate_one_run(stress_b, s)[0] for s in range(200)]
    profitable_b = sum(1 for r in stress_b_results if r > 20)
    print(f"\n  🟧 Медвежий рынок (пампы x0.5, убытков больше):")
    print(f"     Прибыльных: {profitable_b}/200 ({profitable_b/2:.1f}%)")
    print(f"     Медиана: ${np.median(stress_b_results):,.2f}")
    print(f"     Худший: ${np.min(stress_b_results):,.2f}")
    
    # Сценарий C: Полная скам-мета (80% токенов — скам)
    stress_c = list(base_buckets)
    stress_c[0] = (0.15, -86.0)   # 15% катастроф
    stress_c[1] = (0.30, -33.7)   # 30% тяжёлых
    stress_c[2] = (0.25, -19.0)   # 25% средних
    stress_c[3] = (0.15, -2.5)    # 15% около нуля
    stress_c[4] = (0.08, 33.2)    # 8% профитов
    stress_c[5] = (0.04, 60.7)    # 4% больших
    stress_c[6] = (0.03, 247.9)   # 3% иксов
    
    stress_c_results = [simulate_one_run(stress_c, s)[0] for s in range(200)]
    profitable_c = sum(1 for r in stress_c_results if r > 20)
    print(f"\n  🟫 Скам-мета (80% токенов — скам/раг):")
    print(f"     Прибыльных: {profitable_c}/200 ({profitable_c/2:.1f}%)")
    print(f"     Медиана: ${np.median(stress_c_results):,.2f}")
    print(f"     Худший: ${np.min(stress_c_results):,.2f}")
    
    # ========================================
    # 3. РАЗБИВКА ПО «МЕСЯЦАМ» (4 недели)
    # ========================================
    print(f"\n{'='*60}")
    print("📅 ПОНЕДЕЛЬНАЯ РАЗБИВКА (4 недели из медианного прогона)")
    print(f"{'='*60}")
    
    random.seed(42)
    np.random.seed(42)
    capital = 20.0
    
    for week in range(1, 5):
        week_start = capital
        for day in range(7):
            for trade in range(15):
                if capital < 4.0:
                    break
                trade_size = max(4.0, min(100.0, capital * 0.10))
                roll = random.random()
                cumulative = 0
                pnl_pct = 0
                for prob, avg_pnl in base_buckets:
                    cumulative += prob
                    if roll <= cumulative:
                        pnl_pct = avg_pnl * np.random.uniform(0.7, 1.3)
                        break
                capital += trade_size * (pnl_pct / 100.0) - 0.075
        
        week_pnl = capital - week_start
        sign = "🟢" if week_pnl >= 0 else "🔴"
        print(f"  Неделя {week}: ${week_start:>10,.2f} → ${capital:>10,.2f}  {sign} {'+' if week_pnl>=0 else ''}{week_pnl:,.2f}")

    # ========================================
    # ВЫВОД
    # ========================================
    print(f"\n{'='*60}")
    print("⚠️  ЧЕСТНЫЙ ВЕРДИКТ")
    print(f"{'='*60}")
    ruin_pct = bankrupt / 10
    profitable_pct = profitable / 10
    print(f"  Шанс заработать за 30 дней: {profitable_pct:.1f}%")
    print(f"  Шанс слить депозит:         {ruin_pct:.1f}%")
    print(f"  Медианный результат:        ${np.median(results):,.2f}")
    
if __name__ == "__main__":
    run()
