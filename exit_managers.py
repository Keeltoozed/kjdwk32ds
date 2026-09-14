class MatureExitManager:
    @staticmethod
    def evaluate_exit(position, current_price):
        if not hasattr(position, 'entry_price_usd') or position.entry_price_usd <= 0:
            return None
            
        pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd
        
        if not hasattr(position, 'max_price_usd') or position.max_price_usd <= 0:
            position.max_price_usd = current_price
        elif current_price > position.max_price_usd:
            position.max_price_usd = current_price
            
        minutes_held = (time.time() - position.entry_time) / 60 if hasattr(position, 'entry_time') else 0
        
        import config
        
        # Trailing stop for mature coins
        if hasattr(config, 'TRAILING_ACTIVATION_PCT') and hasattr(config, 'TRAILING_DISTANCE_PCT'):
            if pnl_pct >= config.TRAILING_ACTIVATION_PCT:
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                if drop_from_max >= config.TRAILING_DISTANCE_PCT:
                    return f"Mature Trailing (+{pnl_pct*100:.0f}% peak)"
        
        # Hard stop loss
        if hasattr(config, 'STOP_LOSS_PCT') and pnl_pct <= config.STOP_LOSS_PCT:
            return f"Mature Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)"
            
        # Time exit
        if hasattr(config, 'TIME_EXIT_MINUTES') and hasattr(config, 'TIME_EXIT_PROFIT_REQ'):
            if minutes_held >= config.TIME_EXIT_MINUTES and pnl_pct < config.TIME_EXIT_PROFIT_REQ:
                return "Mature Time Exit"
                
        return None

import time
import config