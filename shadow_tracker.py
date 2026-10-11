import asyncio
import aiohttp
import sqlite3
import json
import time
from datetime import datetime, timedelta
import config

try:
    from supabase import create_client, Client
except ImportError:
    create_client, Client = None, None


class ShadowTracker:
    """
    Теневой трекер отклонённых токенов (Missed Rockets & Rejection Logger).
    Сохраняет токены, которые бот отклонил по разным причинам (фильтры, скор, вето),
    и в фоне отслеживает их максимальную цену (ATH) через 1ч, 4ч, 24ч.
    Позволяет в Supabase видеть: какие ракеты бот упустил и по какой причине.
    """
    def __init__(self, db_path="shadow_trades.db"):
        self.db_path = db_path
        self._rate_limit_lock = asyncio.Lock()
        self._cooldown = {}  # {mint: timestamp} — дедупликация, чтобы не спамить Supabase
        
        self.use_supabase = bool(
            getattr(config, 'SUPABASE_URL', None) and 
            getattr(config, 'SUPABASE_KEY', None) and 
            create_client is not None
        )
        self.supabase = None
        self._target_table = "rejected_tokens"
        
        if self.use_supabase:
            try:
                self.supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
                print("☁️ [ShadowTracker] Подключен к Supabase PostgreSQL (таблица rejected_tokens)")
            except Exception as e:
                print(f"⚠️ [ShadowTracker] Ошибка подключения к Supabase: {e}")
                self.use_supabase = False
        
        self.init_db()

    def init_db(self):
        """Создает локальную SQLite таблицу как гарантированный fallback."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS rejected_tokens (
                        mint TEXT PRIMARY KEY,
                        symbol TEXT,
                        chain TEXT,
                        rejected_at TIMESTAMP,
                        reason TEXT,
                        score REAL DEFAULT 0.0,
                        rkt_score REAL DEFAULT NULL,
                        m5_pct REAL DEFAULT 0.0,
                        price_at_rejection REAL DEFAULT 0.0,
                        liquidity REAL DEFAULT 0.0,
                        source TEXT,
                        ath_price REAL DEFAULT 0.0,
                        hypothetical_pnl REAL DEFAULT 0.0,
                        is_missed_rocket INTEGER DEFAULT 0,
                        check_1h_done INTEGER DEFAULT 0,
                        check_4h_done INTEGER DEFAULT 0,
                        check_24h_done INTEGER DEFAULT 0,
                        features TEXT DEFAULT '{}',
                        last_checked_at TIMESTAMP
                    )
                ''')
        except Exception as e:
            print(f"⚠️ [ShadowTracker] SQLite init err: {e}")

    def log_rejection(self, mint: str, reason: str, score: float = 0.0, price: float = 0.0,
                      features: dict = None, symbol: str = "", chain: str = "solana",
                      m5_pct: float = 0.0, rkt_score: float = None, liquidity: float = 0.0,
                      source: str = ""):
        """
        Логирует отклонённый токен в Supabase (rejected_tokens) и SQLite.
        Включает защиту от спама (дедупликация по mint на 30 минут) и порог ликвидности.
        """
        if not mint:
            return
            
        # Защита от спама: если этот токен уже логировали за последние 30 минут — скип
        now_ts = time.time()
        if mint in self._cooldown and (now_ts - self._cooldown[mint]) < 1800:
            return
            
        # Не логируем нулевые пустышки без ликвидности (спам ботов)
        if liquidity > 0 and liquidity < 3000:
            return
            
        self._cooldown[mint] = now_ts

        try:
            native_score = float(score or 0.0)
            native_price = float(price or 0.0)
            native_m5 = float(m5_pct or 0.0)
            native_liq = float(liquidity or 0.0)
            native_rkt = float(rkt_score) if rkt_score is not None else None
            clean_sym = str(symbol or mint[:6]).upper()
            clean_chain = str(chain or "solana").lower()
            clean_reason = str(reason or "Filtered")
            clean_source = str(source or "SCANNER")
            features_dict = features or {}

            data = {
                "mint": mint,
                "symbol": clean_sym,
                "chain": clean_chain,
                "rejected_at": datetime.utcnow().isoformat(),
                "reason": clean_reason,
                "score": native_score,
                "rkt_score": native_rkt,
                "m5_pct": native_m5,
                "price_at_rejection": native_price,
                "liquidity": native_liq,
                "source": clean_source,
                "ath_price": native_price,
                "hypothetical_pnl": 0.0,
                "is_missed_rocket": False,
                "check_1h_done": 0,
                "check_4h_done": 0,
                "check_24h_done": 0,
                "features": json.dumps(features_dict) if isinstance(features_dict, dict) else str(features_dict)
            }

            if self.use_supabase and self.supabase:
                try:
                    # Пытаемся записать в rejected_tokens
                    self.supabase.table(self._target_table).upsert(data).execute()
                except Exception as sb_err:
                    err_str = str(sb_err).lower()
                    if "rejected_tokens" in err_str and ("does not exist" in err_str or "not found" in err_str or "relation" in err_str):
                        # Фолбэк на старую таблицу shadow_log, если rejected_tokens еще не создана
                        self._target_table = "shadow_log"
                        legacy_data = {
                            "mint": mint,
                            "rejected_at": data["rejected_at"],
                            "reason": f"[{clean_chain.upper()}] {clean_reason}",
                            "score": native_score,
                            "price_at_rejection": native_price,
                            "features": data["features"],
                            "ath_price": native_price,
                            "hypothetical_pnl": 0.0,
                            "check_1h_done": 0,
                            "check_4h_done": 0,
                            "check_24h_done": 0
                        }
                        try:
                            self.supabase.table("shadow_log").upsert(legacy_data).execute()
                        except Exception:
                            pass
                    else:
                        print(f"⚠️ [ShadowTracker] Supabase upsert err: {sb_err}")

            # Локальная запись в SQLite (всегда работает независимо от сети)
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute('''
                        INSERT OR REPLACE INTO rejected_tokens (
                            mint, symbol, chain, rejected_at, reason, score, rkt_score,
                            m5_pct, price_at_rejection, liquidity, source, ath_price,
                            hypothetical_pnl, is_missed_rocket, check_1h_done, check_4h_done,
                            check_24h_done, features
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        mint, clean_sym, clean_chain, data["rejected_at"], clean_reason,
                        native_score, native_rkt, native_m5, native_price, native_liq,
                        clean_source, native_price, 0.0, 0, 0, 0, 0, data["features"]
                    ))
            except Exception as sql_e:
                pass

            print(f"👻 [Shadow Logger] Отказ сохранён: {clean_sym} ({clean_chain}) | {clean_reason} | ${native_price:.6f}")
        except Exception as e:
            print(f"⚠️ Ошибка Shadow Logger: {e}")

    async def _fetch_prices_batch(self, mints: list) -> dict:
        """
        Батч-запрос цен с DexScreener для любых сетей (Solana, BSC, Base, Robinhood, Ethereum).
        DexScreener API поддерживает до 30 токенов через запятую.
        """
        if not mints:
            return {}
        prices = {}
        from http_client import fetch_json
        addresses = ",".join(mints[:30])
        url = f"https://api.dexscreener.com/latest/dex/tokens/{addresses}"
        try:
            status, data = await fetch_json(url, timeout=8, retries=1)
            if status == 200 and data:
                for pair in data.get("pairs") or []:
                    base = pair.get("baseToken", {}) or {}
                    m = (base.get("address") or "").lower()
                    p = float(pair.get("priceUsd", 0) or 0.0)
                    if m and p > prices.get(m, 0):
                        prices[m] = p
                        # Сохраняем и оригинальный кейс
                        orig_m = base.get("address")
                        if orig_m:
                            prices[orig_m] = p
        except Exception as e:
            print(f"⚠️ [Shadow] DexScreener Fetch Error: {e}")
        return prices

    async def price_watcher_loop(self):
        """
        Фоновый цикл проверки цен отклонённых токенов (Price Watcher).
        Раз в 5 минут проверяет монеты на 1h, 4h, 24h отметках.
        Если монета дала +50% и выше от отказа — помечает как упущенную ракету (is_missed_rocket=True).
        """
        print("👁️ [Shadow Watcher] Запущен фоновый мониторинг отклонённых токенов (Missed Rockets Tracker)...")
        await asyncio.sleep(60)  # Даём боту 1 минуту на старт
        
        while True:
            try:
                now = datetime.utcnow()
                updates = []
                mints_to_check = []
                table_to_use = self._target_table
                
                rows = []
                if self.use_supabase and self.supabase:
                    try:
                        res = self.supabase.table(table_to_use).select("*").eq("check_24h_done", 0).execute()
                        rows = res.data or []
                    except Exception as e:
                        if table_to_use != "shadow_log":
                            try:
                                res = self.supabase.table("shadow_log").select("*").eq("check_24h_done", 0).execute()
                                rows = res.data or []
                                table_to_use = "shadow_log"
                            except Exception:
                                rows = []
                
                if not rows:
                    try:
                        with sqlite3.connect(self.db_path) as conn:
                            conn.row_factory = sqlite3.Row
                            c = conn.cursor()
                            c.execute("SELECT * FROM rejected_tokens WHERE check_24h_done = 0 LIMIT 150")
                            rows = [dict(r) for r in c.fetchall()]
                    except Exception:
                        rows = []

                for row in rows:
                    try:
                        rej_str = row.get("rejected_at") or ""
                        if not rej_str:
                            continue
                        rej_clean = rej_str.split(".")[0].replace("Z", "")
                        rejected_at = datetime.fromisoformat(rej_clean)
                        elapsed = now - rejected_at
                        
                        check_1h = row.get("check_1h_done", 0)
                        check_4h = row.get("check_4h_done", 0)
                        check_24h = row.get("check_24h_done", 0)
                        needs_check = False

                        if elapsed > timedelta(hours=24) and not check_24h:
                            needs_check, check_24h = True, 1
                        elif elapsed > timedelta(hours=4) and not check_4h:
                            needs_check, check_4h = True, 1
                        elif elapsed > timedelta(hours=1) and not check_1h:
                            needs_check, check_1h = True, 1

                        if needs_check:
                            m = row.get("mint")
                            mints_to_check.append(m)
                            updates.append({
                                "mint": m,
                                "price_at_rejection": float(row.get("price_at_rejection", 0) or 0),
                                "old_ath": float(row.get("ath_price", 0) or 0),
                                "check_1h": check_1h,
                                "check_4h": check_4h,
                                "check_24h": check_24h,
                                "symbol": row.get("symbol", m[:6])
                            })
                    except Exception:
                        continue

                # Батч-проверка цен через DexScreener
                for i in range(0, len(mints_to_check), 30):
                    batch_mints = mints_to_check[i:i+30]
                    cur_prices = await self._fetch_prices_batch(batch_mints)
                    
                    for upd in updates:
                        m = upd["mint"]
                        if m in batch_mints:
                            p_now = cur_prices.get(m, 0) or cur_prices.get(m.lower(), 0)
                            p_entry = upd["price_at_rejection"]
                            old_ath = upd["old_ath"]
                            new_ath = max(old_ath, p_now)
                            
                            pnl_pct = ((new_ath - p_entry) / p_entry * 100.0) if p_entry > 0 and new_ath > 0 else 0.0
                            is_rocket = bool(pnl_pct >= 50.0)
                            
                            if is_rocket:
                                print(f"🚀 [MISSED ROCKET!] {upd['symbol']}: вырос на +{pnl_pct:.0f}% (ATH ${new_ath:.6f} vs вход ${p_entry:.6f}) после отсева!")

                            # Обновляем в Supabase
                            if self.use_supabase and self.supabase:
                                try:
                                    update_payload = {
                                        "ath_price": new_ath,
                                        "hypothetical_pnl": round(pnl_pct, 1),
                                        "check_1h_done": upd["check_1h"],
                                        "check_4h_done": upd["check_4h"],
                                        "check_24h_done": upd["check_24h"],
                                        "last_checked_at": datetime.utcnow().isoformat()
                                    }
                                    if table_to_use == "rejected_tokens":
                                        update_payload["is_missed_rocket"] = is_rocket
                                    self.supabase.table(table_to_use).update(update_payload).eq("mint", m).execute()
                                except Exception as u_err:
                                    pass
                                    
                            # Обновляем в SQLite
                            try:
                                with sqlite3.connect(self.db_path) as conn:
                                    conn.execute('''
                                        UPDATE rejected_tokens 
                                        SET ath_price = ?, hypothetical_pnl = ?, is_missed_rocket = ?,
                                            check_1h_done = ?, check_4h_done = ?, check_24h_done = ?,
                                            last_checked_at = ?
                                        WHERE mint = ?
                                    ''', (
                                        new_ath, round(pnl_pct, 1), 1 if is_rocket else 0,
                                        upd["check_1h"], upd["check_4h"], upd["check_24h"],
                                        datetime.utcnow().isoformat(), m
                                    ))
                            except Exception:
                                pass

                    await asyncio.sleep(1.0)

            except Exception as e:
                print(f"⚠️ Ошибка Shadow Watcher: {e}")
                
            await asyncio.sleep(300)  # Пауза 5 минут


# Глобальный синглтон для простого импорта
shadow_tracker = ShadowTracker()
