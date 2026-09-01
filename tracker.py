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

    def add_position(self, symbol: str, mint: str, entry_price: float, amount_usd: float):
        if mint not in self.positions or self.positions[mint].status == "closed":
            self.positions[mint] = VirtualPosition(
                symbol=symbol,
                mint=mint,
                entry_price_usd=entry_price,
                amount_usd=amount_usd,
                entry_time=time.time(),
                max_price_usd=entry_price
            )
            self.save_portfolio()
            print(f"📝 PAPER BUY: {symbol} ({mint}) | Amount: ${amount_usd} | Price: ${entry_price}")

    def close_position(self, mint: str, exit_price: float, reason: str):
        pos = self.positions.get(mint)
        if pos and pos.status == "open":
            pos.status = "closed"
            pos.exit_price_usd = exit_price
            pnl_pct = (exit_price - pos.entry_price_usd) / pos.entry_price_usd
            pos.pnl_usd = pos.amount_usd * pnl_pct
            self.save_portfolio()
            print(f"🔒 PAPER SELL: {pos.symbol} ({mint}) | Reason: {reason} | PnL: {pnl_pct*100:.2f}% (${pos.pnl_usd:.2f})")

    def can_open_new_position(self, max_concurrent: int) -> bool:
        return len(self.get_open_positions()) < max_concurrent
