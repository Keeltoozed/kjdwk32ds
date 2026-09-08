import json
import os
import time
from typing import Dict
from pydantic import BaseModel
import config

class VirtualPosition(BaseModel):
    symbol: str
    mint: str
    entry_price_usd: float
    amount_usd: float
    entry_time: float
    status: str = "open"  # "open" or "closed"
    exit_price_usd: float = 0.0
    pnl_usd: float = 0.0
    max_price_usd: float = 0.0  # Отслеживаем максимальную цену для трейлинга
    current_price_usd: float = 0.0 # Для отображения в интерфейсе
    current_pnl_usd: float = 0.0 # Для отображения в интерфейсе
    exit_reason: str = "" # Причина выхода
    ml_features: dict = {} # Фичи, по которым ИИ принял решение
    ml_confidence: float = 0.0 # Уверенность ИИ (0-100)

class PaperTracker:
    def __init__(self):
        self.filename = config.PAPER_PORTFOLIO_FILE
        self.positions: Dict[str, VirtualPosition] = {}
        self.load_portfolio()

    def load_portfolio(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    data = json.load(f)
                    for k, v in data.items():
                        # Поддержка старых записей без max_price_usd
                        if "max_price_usd" not in v:
                            v["max_price_usd"] = v["entry_price_usd"]
                        self.positions[k] = VirtualPosition(**v)
            except Exception as e:
                print(f"Error loading portfolio: {e}")

    def save_portfolio(self):
        with open(self.filename, 'w') as f:
            json.dump({k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}, f, indent=4)

    def get_open_positions(self) -> Dict[str, VirtualPosition]:
        return {k: v for k, v in self.positions.items() if v.status == "open"}

    def get_total_capital(self) -> float:
        # Считаем изначальный капитал + сумма PnL всех закрытых и открытых позиций
        total_pnl = sum(pos.pnl_usd for pos in self.positions.values() if pos.status == "closed")
        return max(config.INITIAL_BALANCE_USD + total_pnl, 10.0)

    def add_position(self, symbol, mint, entry_price, amount_usd=5.0, ml_features=None, ml_confidence=0.0):
        # БЛОКИРОВКА ПОВТОРНОГО ВХОДА:
        # Если мы уже торговали этой монетой (даже если она closed), мы в нее больше не лезем!
        if mint in self.positions:
            print(f"⚠️ Попытка повторного входа в {symbol} заблокирована. Мы торгуем щитком только 1 раз.")
            return

        print(f"✅ Открыта PAPER сделка: {symbol} по цене ${entry_price}")
        
        ml_features_dict = ml_features if ml_features is not None else {}
        
        self.positions[mint] = VirtualPosition(
            symbol=symbol,
            mint=mint,
            entry_price_usd=entry_price,
            amount_usd=amount_usd,
            entry_time=time.time(),
            max_price_usd=entry_price,
            current_price_usd=entry_price,
            ml_features=ml_features_dict,
            ml_confidence=ml_confidence
        )
        self.save_portfolio()
        print(f"📝 PAPER BUY: {symbol} ({mint}) | Amount: ${amount_usd} | Price: ${entry_price}")
        
        # === СОХРАНЕНИЕ В SUPABASE (ENTRY) ===
        try:
            from trade_logger import TradeLogger
            import asyncio
            logger = TradeLogger()
            asyncio.create_task(logger.log_entry(mint, ml_features_dict, ml_confidence))
        except Exception as e:
            print(f"⚠️ Ошибка логирования входа: {e}")

    def close_position(self, mint: str, exit_price: float, reason: str):
        pos = self.positions.get(mint)
        if pos and pos.status == "open":
            pos.status = "closed"
            pos.exit_price_usd = exit_price
            pos.exit_reason = reason
            
            # РЕАЛЬНЫЙ РАСЧЕТ PnL С УЧЕТОМ КОМИССИЙ (1% вход, 1% выход + 0.003 SOL сеть)
            real_entry_price = pos.entry_price_usd * 1.01
            real_exit_price = exit_price * 0.99
            
            # Считаем изменение цены актива (процент)
            price_diff_pct = (real_exit_price - real_entry_price) / real_entry_price if real_entry_price > 0 else 0
            
            # Считаем итоговый PnL в долларах с вычетом сетевой комиссии (Priority Fee ~ $0.45)
            priority_fee_usd = 0.45
            pos.pnl_usd = (pos.amount_usd * price_diff_pct) - priority_fee_usd
            
            # Реальный итоговый процент инвестиции
            pnl_pct = pos.pnl_usd / pos.amount_usd if pos.amount_usd > 0 else 0
            
            self.save_portfolio()
            print(f"🔒 PAPER SELL: {pos.symbol} ({mint}) | Reason: {reason} | PnL: {pnl_pct*100:.2f}% (${pos.pnl_usd:.2f})")
            
            # === СОХРАНЕНИЕ ОПЫТА ДЛЯ ИИ (Continuous Learning) ===
            try:
                from trade_logger import TradeLogger
                import asyncio
                logger = TradeLogger()
                asyncio.create_task(logger.log_exit(pos.mint, pnl_pct * 100, pos.exit_reason))
            except Exception as e:
                print(f"⚠️ Ошибка сохранения опыта: {e}")

    def is_trading_allowed(self) -> bool:
        """Проверка глобального Kill-Switch"""
        if not hasattr(config, "MAX_DAILY_LOSS_USD"):
            return True
            
        import datetime
        today = datetime.datetime.utcnow().date()
        daily_pnl = 0.0
        
        for pos in self.positions.values():
            if pos.status == "closed":
                pos_date = datetime.datetime.fromtimestamp(pos.entry_time).date()
                if pos_date == today:
                    daily_pnl += pos.pnl_usd
                    
        if daily_pnl <= -config.MAX_DAILY_LOSS_USD:
            print(f"🛑 [KILL SWITCH] Превышен дневной лимит потерь: ${daily_pnl:.2f}. Торговля остановлена!")
            return False
        return True

    def can_open_new_position(self, max_concurrent: int) -> bool:
        if not self.is_trading_allowed():
            return False
        return len(self.get_open_positions()) < max_concurrent
