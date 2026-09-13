import numpy as np
import json
import random

def run():
    with open("real_trades.json", "r") as f:
        real_trades = json.load(f)
    
    real_pnls = [t["pnl"] for t in real_trades if t.get("pnl") is not None]
    
    print("=" * 60)
    print("📊 АНАЛИЗ: ЧТО ИМЕННО КАЖДЫЙ ФИКС МЕНЯЕТ В РАСПРЕДЕЛЕНИИ")
    print("=" * 60)
    
    # Парсим реальные причины выхода
    reasons = {}
    for t in real_trades:
        r = t.get("exit_reason", "unknown")
        if r not in reasons:
            reasons[r] = []
        if t.get("pnl") is not None:
            reasons[r].append(t["pnl"])
    
    print("\nТЕКУЩЕЕ распределение (из Supabase):")
    total_drag = 0
    for r, pnls in sorted(reasons.items(), key=lambda x: sum(x[1])):
        avg = np.mean(pnls)
        total = sum(pnls)
        total_drag += total
        print(f"  {r}: {len(pnls)}x | avg: {avg:+.1f}% | total drag: {total:+.1f}%")
    print(f"\n  ИТОГО матожидание: {total_drag/len(real_pnls):+.2f}% на сделку")
    
    # ======================================================
    # МОДЕЛИРУЕМ ФИКСЫ
    # ======================================================
    print("\n" + "=" * 60)
    print("🔧 МОДЕЛИРУЕМ КАК ФИКСЫ МЕНЯЮТ КАЖДУЮ КАТЕГОРИЮ")
    print("=" * 60)
    
    fixed_pnls = []
    
    for t in real_trades:
        pnl = t.get("pnl")
        reason = t.get("exit_reason", "")
        if pnl is None:
            continue
            
        # ФИК 1: Hard Stop Loss (-35%) → (-15%)
        # Было: 11 сделок с avg -66.2% и 8 сделок с avg -28.6%
        # С Jito + стоп на -15%, реальный выход будет ~-18% (3% slippage)
        if "Hard Stop Loss" in reason:
            fixed_pnls.append(-18.0)
            continue
            
        # ФИКС 2: Lock Profit и Break-even УБРАНЫ
        # Было: Lock Profit avg -0.6%, Break-even avg -8.1%/-23.7%
        # Без этих выходов, монета продолжала бы лететь.
        # 50% из них стали бы иксами (они ведь БЫЛИ в профите!), 
        # 50% упали бы к стопу -15%→-18%
        if "Lock Profit" in reason or "Break-even" in reason:
            if random.random() < 0.50:
                # Монета полетела! Даём ей дойти до +80% и трейлим
                fixed_pnls.append(np.random.uniform(40.0, 200.0))
            else:
                # Монета упала обратно, вышли по стопу
                fixed_pnls.append(-18.0)
            continue
            
        # ФИКС 3: Time Exit 30 мин → 10 мин
        # Было: 26 сделок с avg -7.3%
        # За 10 мин вместо 30 убыток будет меньше (~-3%)
        if "Time-based Exit" in reason:
            fixed_pnls.append(pnl * 0.4)  # ~40% от текущего убытка (меньше времени = меньше потерь)
            continue
        
        # ФИКС 4: Micro-Trailing (7%/12%/15%) УБРАН
        # Было: 16 сделок с avg +12-25%
        # Без раннего тейка, часть долетит до иксов, часть упадет к стопу
        if "Micro-Trailing" in reason:
            if random.random() < 0.40:
                # Монета дала бы икс если не резать
                fixed_pnls.append(np.random.uniform(80.0, 400.0))
            else:
                # Без раннего тейка, монета вернулась к стопу
                fixed_pnls.append(-18.0)
            continue
        
        # Все остальные (Moonbag, Take Profit, Rug Pull) — без изменений
        fixed_pnls.append(pnl)
    
    print(f"\nПОСЛЕ ФИКСОВ:")
    avg_fixed = np.mean(fixed_pnls)
    print(f"  Новое матожидание: {avg_fixed:+.2f}% на сделку (было -4.87%)")
    
    wins = len([p for p in fixed_pnls if p > 0])
    losses = len([p for p in fixed_pnls if p <= 0])
    print(f"  Win Rate: {wins/len(fixed_pnls)*100:.1f}% (было 34.8%)")
    
    # Новое распределение по бакетам
    buckets_fixed = {
        "Катастрофа (< -50%)": [p for p in fixed_pnls if p <= -50],
        "Тяжелый убыток (-50/-25%)": [p for p in fixed_pnls if -50 < p <= -25],
        "Средний убыток (-25/-10%)": [p for p in fixed_pnls if -25 < p <= -10],
        "Около нуля (-10/+10%)": [p for p in fixed_pnls if -10 < p <= 10],
        "Профит (+10/+50%)": [p for p in fixed_pnls if 10 < p <= 50],
        "Большой профит (+50/+100%)": [p for p in fixed_pnls if 50 < p <= 100],
        "ИКСЫ (> +100%)": [p for p in fixed_pnls if p > 100],
    }
    
    print("\nНовое распределение:")
    bucket_params = []
    for name, bucket in buckets_fixed.items():
        pct = len(bucket) / len(fixed_pnls)
        avg = np.mean(bucket) if bucket else 0
        print(f"  {name}: {len(bucket)} ({pct*100:.1f}%) | avg: {avg:+.1f}%")
        bucket_params.append((pct, avg))
    
    # ======================================================
    # СИМУЛЯЦИЯ ПОРТФЕЛЯ С НОВЫМ РАСПРЕДЕЛЕНИЕМ
    # ======================================================
    print("\n" + "=" * 60)
    print("💎 БЭКТЕСТ НА 30 ДНЕЙ С ИСПРАВЛЕННЫМ РАСПРЕДЕЛЕНИЕМ")
    print("=" * 60)
    
    START = 20.0
    capital = START
    peak = START
    max_dd = 0
    wins_count = 0
    trades_done = 0
    
    TRADES_PER_DAY = 15  # Реалистично: 15 сделок/день (MAX_CONCURRENT=15)
    DAYS = 30
    
    for i in range(TRADES_PER_DAY * DAYS):
        if capital < 4.0:
            break
        
        trade_size = max(4.0, min(100.0, capital * 0.10))
        
        roll = random.random()
        cumulative = 0
        pnl_pct = 0
        for prob, avg_pnl in bucket_params:
            cumulative += prob
            if roll <= cumulative:
                pnl_pct = avg_pnl * np.random.uniform(0.7, 1.3)
                break
        
        if pnl_pct > 0: wins_count += 1
        trades_done += 1
        
        capital += trade_size * (pnl_pct / 100.0) - 0.075
        if capital > peak: peak = capital
        dd = (peak - capital) / peak
        if dd > max_dd: max_dd = dd
    
    net = capital - START
    
    print(f"  💰 Старт:          ${START:.2f}")
    print(f"  💰 Итого:          ${capital:,.2f}")
    if net >= 0:
        print(f"  🟢 Прибыль:        ${net:,.2f} (+{net/START*100:,.1f}%)")
    else:
        print(f"  🔴 Убыток:         ${abs(net):,.2f} ({net/START*100:,.1f}%)")
    print(f"  📉 Max Drawdown:   {max_dd*100:.1f}%")
    print(f"  📈 Сделок:         {trades_done}")
    print(f"  ⚔️  Win Rate:       {wins_count/trades_done*100:.1f}%")
    print(f"  📐 Матожидание:    {avg_fixed:+.2f}% на сделку")

if __name__ == "__main__":
    random.seed(42)
    run()
