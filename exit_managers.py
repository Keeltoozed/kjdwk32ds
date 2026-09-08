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

        # 1. Macro Take Profit (Свинг-цели)
        # На зрелой ликвидности мы можем ждать х2-х3
        if pnl_pct >= 2.0: # +200%
            return "Swing Take Profit (+200%)"

        # 2. Широкий Trailing Stop (Защита иксов)
        if max_pnl_pct >= 1.0: # Если сделали >100%
            if drop_from_max >= 0.25: # Откат 25% от пика (нормальный шум для свинг-трейда)
                return "Wide Trailing Stop (25% drop from ATH)"
        elif max_pnl_pct >= 0.50: # Если сделали >50%
            if drop_from_max >= 0.20: # Откат 20%
                return "Wide Trailing Stop (20% drop from ATH)"

        # 3. Swing Break-even (Безубыток на долгосрок)
        # Переводим в БУ только после мощного роста (от +40%)
        if max_pnl_pct >= 0.40 and pnl_pct <= 0.05:
            return "Swing Break-even (+5%)"

        # 4. Wide Stop Loss (Пересиживаем обычный рыночный шум)
        if pnl_pct <= -0.25: # -25% вместо скальперских -15%
            return "Swing Stop Loss (-25%)"

        return "" # Продолжаем держать
