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
        self.positions[mint] = VirtualPosition(
            symbol=symbol,
            mint=mint,
            entry_price_usd=entry_price,
            amount_usd=amount_usd,
            entry_time=time.time(),
            max_price_usd=entry_price,
            current_price_usd=entry_price
        )
        self.save_portfolio()
        print(f"📝 PAPER BUY: {symbol} ({mint}) | Amount: ${amount_usd} | Price: ${entry_price}")

    def close_position(self, mint: str, exit_price: float, reason: str):
        pos = self.positions.get(mint)
        if pos and pos.status == "open":
            pos.status = "closed"
            pos.exit_price_usd = exit_price
            pos.exit_reason = reason
            pnl_pct = (exit_price - pos.entry_price_usd) / pos.entry_price_usd if pos.entry_price_usd > 0 else 0
            pos.pnl_usd = pos.amount_usd * pnl_pct
            self.save_portfolio()
            print(f"🔒 PAPER SELL: {pos.symbol} ({mint}) | Reason: {reason} | PnL: {pnl_pct*100:.2f}% (${pos.pnl_usd:.2f})")
            
            # === СОХРАНЕНИЕ ОПЫТА ДЛЯ ИИ (Continuous Learning) ===
            try:
                import sqlite3
                import json
                with sqlite3.connect("trade_journal.db") as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS trades (
                            mint TEXT PRIMARY KEY,
                            entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            features TEXT,
                            confidence REAL,
                            pnl REAL DEFAULT NULL,
                            exit_reason TEXT DEFAULT NULL,
                            status TEXT DEFAULT 'OPEN'
                        )
                    """)
                    conn.execute(
                        "INSERT OR REPLACE INTO trades (mint, features, confidence, pnl, exit_reason, status) VALUES (?, ?, ?, ?, ?, 'CLOSED')",
                        (pos.mint, json.dumps(pos.ml_features), pos.ml_confidence, pnl_pct, pos.exit_reason)
                    )
                print(f"🧠 Сделка {pos.symbol} сохранена в БД опыта (PnL: {pnl_pct*100:.2f}%)")
            except Exception as e:
                print(f"⚠️ Ошибка сохранения опыта: {e}")

    def can_open_new_position(self, max_concurrent: int) -> bool:
        return len(self.get_open_positions()) < max_concurrent
