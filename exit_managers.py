class MatureExitManager:
    """
    Risk Management для зрелых мем-коинов (Raydium, Swing Trading).
    Допускает глубокие просадки, широкие трейлинги и макро-цели.
    """
    
    @staticmethod
    def evaluate_exit(position, current_price: float) -> str:
        pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd
        max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd
        drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd



        # 2. Широкий Trailing Stop (Защита иксов)
        if max_pnl_pct >= 1.0: # Если сделали >100%
            if drop_from_max >= 0.25: # Откат 25% от пика (нормальный шум для свинг-трейда)
                return "Wide Trailing Stop (25% drop from ATH)"
        elif max_pnl_pct >= 0.50: # Если сделали >50%
            if drop_from_max >= 0.20: # Откат 20%
                return "Wide Trailing Stop (20% drop from ATH)"

        # Расчет минимальной маржи для покрытия фиксированной сети Solana
        priority_fee_usd = 0.075 if position.amount_usd < 10.0 else 0.45
        min_fee_pct = (priority_fee_usd + 0.02 * position.amount_usd) / position.amount_usd
        safe_be = min_fee_pct + 0.02 # Безубыток + 2% чистыми

        # 3. Swing Break-even (Безубыток на долгосрок)
        # Переводим в БУ только после мощного роста (от +40%)
        if max_pnl_pct >= 0.40 and pnl_pct <= safe_be:
            return f"Swing Break-even (+{safe_be*100:.1f}%)"

        # 4. Fee-Adjusted Stop Loss (Упреждающий стоп)
        if pnl_pct <= -0.15: # Триггер на -15%, чтобы с проскальзыванием вышло около -20%
            return "Swing Stop Loss (-15%)"

        return "" # Продолжаем держать
