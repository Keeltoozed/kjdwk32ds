import re

with open('tracker.py', 'r') as f:
    content = f.read()

old_logic = '''        if daily_pnl <= -config.MAX_DAILY_LOSS_USD:
            print(f"🛑 [KILL SWITCH] Превышен дневной лимит потерь: ${daily_pnl:.2f}. Торговля остановлена!")
            return False
        return True'''

new_logic = '''        # Динамический Kill-Switch: Либо статический лимит, либо % от текущего депозита
        max_loss_allowed = getattr(config, "MAX_DAILY_LOSS_USD", 10.0)
        max_loss_pct = getattr(config, "MAX_DAILY_LOSS_PCT", 0.25)
        
        capital = self.get_total_capital()
        dynamic_loss_limit = max(max_loss_allowed, capital * max_loss_pct)
        
        if daily_pnl <= -dynamic_loss_limit:
            print(f"🛑 [KILL SWITCH] Дневной убыток ${daily_pnl:.2f} превысил лимит ${dynamic_loss_limit:.2f}. Торговля остановлена!")
            return False
        return True'''

content = content.replace(old_logic, new_logic)

with open('tracker.py', 'w') as f:
    f.write(content)
