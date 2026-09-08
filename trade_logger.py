import sqlite3
import json
import asyncio
from datetime import datetime
import config

try:
    from supabase import create_client, Client
except ImportError:
    pass

class TradeLogger:
    """
    Trade Journal (Сборщик опыта).
    Сохраняет фичи и уверенность модели при входе.
    Обновляет запись реальным PnL и причиной выхода.
    """
    def __init__(self, db_path="trade_journal.db"):
        self.db_path = db_path
        self.use_supabase = bool(getattr(config, 'SUPABASE_URL', None) and getattr(config, 'SUPABASE_KEY', None))
        
        if self.use_supabase:
            self.supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            print("☁️ [TradeLogger] Подключен к Supabase PostgreSQL")
        else:
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
            if self.use_supabase:
                data = {
                    "mint": mint,
                    "features": json.dumps(features),
                    "confidence": confidence,
                    "status": "OPEN",
                    "entry_time": datetime.utcnow().isoformat()
                }
                self.supabase.table("trades").upsert(data).execute()
            else:
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
            if self.use_supabase:
                data = {
                    "pnl": pnl_pct,
                    "exit_reason": exit_reason,
                    "status": "CLOSED"
                }
                self.supabase.table("trades").update(data).eq("mint", mint).execute()
            else:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE trades SET pnl = ?, exit_reason = ?, status = 'CLOSED' WHERE mint = ?",
                        (pnl_pct, exit_reason, mint)
                    )
        await asyncio.to_thread(_update)
        print(f"🧠 [TradeLogger] Опыт закрыт для {mint}. PnL: {pnl_pct}% | Причина: {exit_reason}")

# Глобальный инстанс для использования в проекте
trade_logger = TradeLogger()
