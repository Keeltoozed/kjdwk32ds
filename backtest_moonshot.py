"""MOONSHOT Strategy — Strategy C (гибрид раннего входа + AI фильтр + частичный тейк)."""
import pandas as pd, numpy as np, sys, bisect
sys.path.insert(0, '.')
import config
from exit_managers import MatureExitManager

MOONSHOT_CONFIG = {
    'name': 'MOONSHOT_V1',
    'max_age_hours': 6,
    'min_age_minutes': 30,
    'min_liquidity_usd': 8000,
    'min_volume_24h': 2000,
    'max_market_cap': 500000,
    'position_size_pct': 0.12,
    'max_concurrent_positions': 4,
    'stop_loss_pct': -0.75,
    'take_profit_levels': [{'pct': 5.0, 'sell_pct': 0.30}, {'pct': 20.0, 'sell_pct': 0.40}, {'pct': 100.0, 'sell_pct': 1.0}],
    'trailing_stop_after_tp': 0.40,
}

df_ref = pd.read_csv("moonshot_dataset.csv")
moon_ref = df_ref[df_ref.is_moonshot == True]

print(f"MOONSHOT ref: min_liq={moon_ref['liquidity_usd'].min()}, max_mc={moon_ref['market_cap'].max()}")
print(f"Config: {MOONSHOT_CONFIG}")

# === ПРАКТИЧЕСКАЯ РЕАЛИЗАЦИЯ MOONSHOT НА ДАННЫХ DUNE ===
if __name__ == "__main__":
    print("=== MOONSHOT V1 — Strategy C (Гибрид) ===")
    print("Параметры:")
    for k, v in MOONSHOT_CONFIG.items():
        if isinstance(v, float) or isinstance(v, int) or isinstance(v, str):
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: {v}")
    
    # Загружаем dune_data.csv для сравнения
    import config
    df = pd.read_csv("dune_data.csv")
    print(f"\nДанные: {len(df)} свечей, {len(df.groupby('mint'))} токенов")
    print("Стратегия C: вход на 2-6 минуте, частичный тейк 5x/20x/100x, трейлинг 40%, стоп -75%")
    print("Это гибрид между SNIPER (ранний вход) и MATURE (выходная логика).")
