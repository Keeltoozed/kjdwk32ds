import sqlite3
import json
import asyncio
from datetime import datetime

class TradeLogger:
    """
    Trade Journal (Сборщик опыта).
    Сохраняет фичи и уверенность модели при входе.
    Обновляет запись реальным PnL и причиной выхода.
    """
    def __init__(self, db_path="trade_journal.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Создает таблицу, если ее нет."""
        with sqlite3.connect(self.db_path) as conn:
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

    async def log_entry(self, mint: str, features: dict, confidence: float):
        """
        Асинхронная запись входа в сделку. Не блокирует event loop.
        """
        def _insert():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO trades (mint, features, confidence) VALUES (?, ?, ?)",
                    (mint, json.dumps(features), confidence)
                )
        await asyncio.to_thread(_insert)
        print(f"🧠 [TradeLogger] Записан опыт входа для {mint} (Conf: {confidence}%)")

    async def log_exit(self, mint: str, pnl_pct: float, exit_reason: str):
        """
        Асинхронное обновление сделки после выхода.
        """
        def _update():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE trades SET pnl = ?, exit_reason = ?, status = 'CLOSED' WHERE mint = ?",
                    (pnl_pct, exit_reason, mint)
                )
        await asyncio.to_thread(_update)
        print(f"🧠 [TradeLogger] Опыт закрыт для {mint}. PnL: {pnl_pct}% | Причина: {exit_reason}")

# Глобальный инстанс для использования в проекте
trade_logger = TradeLogger()
