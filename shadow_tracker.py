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
        self._peak_memory = {}  # {mint: (max_price, max_liq, ts)} — память пиков для защиты от покупки слитых токенов
        
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

    def record_observation(self, mint: str, price: float = 0.0, liquidity: float = 0.0):
        """Обновляет максимальную виденную цену и ликвидность токена в оперативной памяти (TTL 90 мин)."""
        if not mint:
            return
        try:
            now_t = time.time()
            px = float(price or 0.0)
            lq = float(liquidity or 0.0)
            old = self._peak_memory.get(mint)
            if old and (now_t - old[2]) <= 5400.0:
                px = max(px, old[0])
                lq = max(lq, old[1])
            if px > 0 or lq > 0:
                self._peak_memory[mint] = (px, lq, now_t)
            if len(self._peak_memory) > 4000:
                cutoff = now_t - 5400.0
                self._peak_memory = {k: v for k, v in self._peak_memory.items() if v[2] >= cutoff}
        except Exception:
            pass

    def get_peak(self, mint: str) -> tuple:
        """Возвращает (max_price, max_liq), виденные ботом за последние 90 минут."""
        if not mint:
            return 0.0, 0.0
        old = self._peak_memory.get(mint)
        if not old or (time.time() - old[2]) > 5400.0:
            return 0.0, 0.0
        return old[0], old[1]

    def init_db(self):
        """Создает локальную SQLite таблицу как гарантированный fallback и применяет миграции колонок."""
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
                        check_5m_done INTEGER DEFAULT 0,
                        check_15m_done INTEGER DEFAULT 0,
                        check_1h_done INTEGER DEFAULT 0,
                        check_4h_done INTEGER DEFAULT 0,
                        check_24h_done INTEGER DEFAULT 0,
                        time_to_ath_min REAL DEFAULT 0.0,
                        current_pnl REAL DEFAULT 0.0,
                        outcome TEXT DEFAULT 'PENDING',
                        features TEXT DEFAULT '{}',
                        last_checked_at TIMESTAMP
                    )
                ''')
                # Безопасная миграция колонок для существующей таблицы
                existing_cols = {col[1] for col in conn.execute("PRAGMA table_info(rejected_tokens)").fetchall()}
                for col_name, col_type in [
                    ("check_5m_done", "INTEGER DEFAULT 0"),
                    ("check_15m_done", "INTEGER DEFAULT 0"),
                    ("time_to_ath_min", "REAL DEFAULT 0.0"),
                    ("current_pnl", "REAL DEFAULT 0.0"),
                    ("outcome", "TEXT DEFAULT 'PENDING'")
                ]:
                    if col_name not in existing_cols:
                        try:
                            conn.execute(f"ALTER TABLE rejected_tokens ADD COLUMN {col_name} {col_type}")
                        except Exception:
                            pass
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
        self.record_observation(mint, price, liquidity)
            
        # Защита от спама: если этот токен уже логировали за последние 10 минут (было 30м) — скип.
        # 10 минут достаточно, чтобы не спамить, но не упустить монету, если через 10-15м в ней начался памп.
        now_ts = time.time()
        if mint in self._cooldown and (now_ts - self._cooldown[mint]) < 600:
            return
            
        # Не логируем нулевые пустышки без ликвидности и токены до миграции с фейковыми ценами 5e-12
        if liquidity < 3000 or native_price < 1e-7:
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
                "check_5m_done": 0,
                "check_15m_done": 0,
                "check_1h_done": 0,
                "check_4h_done": 0,
                "check_24h_done": 0,
                "time_to_ath_min": 0.0,
                "current_pnl": 0.0,
                "outcome": "PENDING",
                "features": json.dumps(features_dict) if isinstance(features_dict, dict) else str(features_dict)
            }

            if self.use_supabase and self.supabase:
                try:
                    # Пытаемся записать в rejected_tokens со всеми новыми колонками
                    self.supabase.table(self._target_table).upsert(data).execute()
                except Exception as sb_err:
                    err_str = str(sb_err).lower()
                    if "rejected_tokens" in err_str and ("does not exist" in err_str or "not found" in err_str or "relation" in err_str):
                        self._target_table = "shadow_log"
                    else:
                        # Если в Supabase еще не созданы колонки (check_5m_done и т.д.), фолбэк на базовую схему
                        try:
                            compat_data = dict(data)
                            for k in ["check_5m_done", "check_15m_done", "time_to_ath_min", "current_pnl", "outcome"]:
                                compat_data.pop(k, None)
                            self.supabase.table(self._target_table).upsert(compat_data).execute()
                        except Exception:
                            pass

            # Локальная запись в SQLite (всегда работает независимо от сети)
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute('''
                        INSERT OR REPLACE INTO rejected_tokens (
                            mint, symbol, chain, rejected_at, reason, score, rkt_score,
                            m5_pct, price_at_rejection, liquidity, source, ath_price,
                            hypothetical_pnl, is_missed_rocket, check_5m_done, check_15m_done,
                            check_1h_done, check_4h_done, check_24h_done, time_to_ath_min,
                            current_pnl, outcome, features
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        mint, clean_sym, clean_chain, data["rejected_at"], clean_reason,
                        native_score, native_rkt, native_m5, native_price, native_liq,
                        clean_source, native_price, 0.0, 0, 0, 0, 0, 0, 0, 0.0, 0.0, "PENDING", data["features"]
                    ))
            except Exception as sql_e:
                pass

            print(f"👻 [Shadow Logger] Отказ сохранён: {clean_sym} ({clean_chain}) | {clean_reason} | ${native_price:.6f}")
        except Exception as e:
            print(f"⚠️ Ошибка Shadow Logger: {e}")

    async def _fetch_prices_batch(self, mints: list) -> dict:
        """
        Батч-запрос цен с DexScreener для любых сетей (Solana, BSC, Base, Robinhood, Ethereum).
        Берём цену только из живого пула с МАКСИМАЛЬНОЙ ликвидностью (>= $2,500) и реальными продажами,
        чтобы опустошённые пулы ($0.82 ликвы как LILEDGE) и 0-sell ханиподы не считались «ракетами».
        """
        if not mints:
            return {}
        prices = {}
        max_liq = {}
        from http_client import fetch_json
        addresses = ",".join(mints[:30])
        url = f"https://api.dexscreener.com/latest/dex/tokens/{addresses}"
        try:
            status, data = await fetch_json(url, timeout=8, retries=1)
            if status == 200 and data:
                all_pairs = data.get("pairs") or []
                # Если токен уже мигрировал на AMM (pumpswap/raydium/meteora/orca),
                # его старый bonding curve на pumpfun заморожен на $0.000042 ($42k FDV) — игнорируем его!
                migrated_mints = set()
                for pair in all_pairs:
                    base = pair.get("baseToken", {}) or {}
                    m = (base.get("address") or "").lower()
                    dex_id = str(pair.get("dexId") or "").lower()
                    raw_liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0.0)
                    if m and dex_id not in ("pump", "pumpfun") and raw_liq >= 100.0:
                        migrated_mints.add(m)

                for pair in all_pairs:
                    base = pair.get("baseToken", {}) or {}
                    m = (base.get("address") or "").lower()
                    p = float(pair.get("priceUsd", 0) or 0.0)
                    liq = float((pair.get("liquidity") or {}).get("usd", 0) or 0.0)
                    fdv = float(pair.get("fdv", 0) or 0.0)
                    dex_id = str(pair.get("dexId") or "").lower()
                    if dex_id in ("pump", "pumpfun"):
                        if m in migrated_mints:
                            continue  # Никогда не берём замороженную кривую pumpfun для мигрировавших монет!
                        if liq == 0:
                            liq = fdv * 0.40
                    # Игнорируем эксплойты с раздутым supply (FDV > $500M при микро-ликвидности, кейсы IRYS/BTW)
                    if fdv > 500_000_000 and liq < 250_000:
                        continue
                    tx24 = ((pair.get("txns") or {}).get("h24") or {})
                    sells24 = int(tx24.get("sells", 0) or 0)
                    if not m or p <= 0 or liq < 2500.0 or sells24 < 5:
                        continue
                    if liq > max_liq.get(m, -1.0):
                        max_liq[m] = liq
                        prices[m] = p
                        orig_m = base.get("address")
                        if orig_m:
                            prices[orig_m] = p
        except Exception as e:
            print(f"⚠️ [Shadow] DexScreener Fetch Error: {e}")
        return prices

    async def price_watcher_loop(self):
        """
        Фоновый цикл проверки цен отклонённых токенов (Missed Rockets & Flash Pump Tracker).
        Проверяет монеты на отметках 5m, 15m, 1h, 4h, 24h и ведет непрерывный трекинг
        свежих токенов (< 60 минут), чтобы точно знать: дала ли монета ракету через 5 минут,
        а затем ушла в 0 через час.
        """
        print("👁️ [Shadow Watcher] Запущен фоновый мониторинг отклонённых токенов (5m/15m/1h/4h/24h + Flash Rockets)...")
        await asyncio.sleep(30)  # Даём боту 30 секунд на старт
        
        while True:
            try:
                now = datetime.utcnow()
                updates = []
                mints_to_check = []
                table_to_use = self._target_table
                
                rows = []
                if self.use_supabase and self.supabase:
                    try:
                        res = self.supabase.table(table_to_use).select("mint, rejected_at, initial_price, ath_price, check_5m_done, check_15m_done, check_1h_done, check_4h_done, check_24h_done").eq("check_24h_done", 0).order("rejected_at", desc=True).limit(50).execute()
                        rows = res.data or []
                    except Exception as e:
                        if table_to_use != "shadow_log":
                            try:
                                res = self.supabase.table("shadow_log").select("mint, rejected_at, initial_price, ath_price, check_5m_done, check_15m_done, check_1h_done, check_4h_done, check_24h_done").eq("check_24h_done", 0).order("rejected_at", desc=True).limit(50).execute()
                                rows = res.data or []
                                table_to_use = "shadow_log"
                            except Exception:
                                rows = []
                
                if not rows:
                    try:
                        with sqlite3.connect(self.db_path) as conn:
                            conn.row_factory = sqlite3.Row
                            c = conn.cursor()
                            c.execute("SELECT * FROM rejected_tokens WHERE check_24h_done = 0 ORDER BY rejected_at DESC LIMIT 200")
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
                        elapsed_min = elapsed.total_seconds() / 60.0
                        
                        check_5m = row.get("check_5m_done", 0)
                        check_15m = row.get("check_15m_done", 0)
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
                        elif elapsed > timedelta(minutes=15) and not check_15m:
                            needs_check, check_15m = True, 1
                        elif elapsed > timedelta(minutes=5) and not check_5m:
                            needs_check, check_5m = True, 1
                        elif elapsed_min < 60.0:
                            # Для токенов в первый час жизни: мониторим в реальном времени,
                            # чтобы поймать всплеск на 3-й, 5-й или 10-й минуте до слива в 0!
                            last_chk = row.get("last_checked_at")
                            if not last_chk:
                                needs_check = True
                            else:
                                try:
                                    last_dt = datetime.fromisoformat(last_chk.split(".")[0].replace("Z", ""))
                                    if (now - last_dt).total_seconds() >= 60.0:
                                        needs_check = True
                                except Exception:
                                    needs_check = True

                        if needs_check:
                            m = row.get("mint")
                            mints_to_check.append(m)
                            updates.append({
                                "mint": m,
                                "price_at_rejection": float(row.get("price_at_rejection", 0) or 0),
                                "old_ath": float(row.get("ath_price", 0) or 0),
                                "old_time_to_ath": float(row.get("time_to_ath_min", 0.0) or 0.0),
                                "check_5m": check_5m,
                                "check_15m": check_15m,
                                "check_1h": check_1h,
                                "check_4h": check_4h,
                                "check_24h": check_24h,
                                "symbol": row.get("symbol", m[:6]),
                                "elapsed_min": elapsed_min
                            })
                    except Exception:
                        continue

                # Батч-проверка цен через DexScreener (до 30 токенов за запрос)
                for i in range(0, len(mints_to_check), 30):
                    batch_mints = mints_to_check[i:i+30]
                    cur_prices = await self._fetch_prices_batch(batch_mints)
                    
                    for upd in updates:
                        m = upd["mint"]
                        if m in batch_mints:
                            p_now = cur_prices.get(m, 0) or cur_prices.get(m.lower(), 0)
                            p_entry = upd["price_at_rejection"]
                            old_ath = upd["old_ath"]
                            old_time = upd["old_time_to_ath"]
                            elapsed_m = upd["elapsed_min"]

                            # Если токен дал новый пик цены — фиксируем новый ATH и точную минуту
                            if p_now > old_ath:
                                new_ath = p_now
                                time_to_ath = round(elapsed_m, 1)
                            else:
                                new_ath = old_ath if old_ath > 0 else p_now
                                time_to_ath = old_time
                            if new_ath > 0:
                                self.record_observation(m, new_ath, 0.0)

                            ath_pnl = ((new_ath - p_entry) / p_entry * 100.0) if p_entry >= 1e-7 and new_ath > 0 else 0.0
                            cur_pnl = ((p_now - p_entry) / p_entry * 100.0) if p_entry >= 1e-7 and p_now > 0 else (-100.0 if p_now == 0 else 0.0)
                            is_rocket = bool(ath_pnl >= 50.0)

                            # Категоризация исхода монеты:
                            if ath_pnl >= 50.0 and cur_pnl <= 0.0:
                                outcome = f"🚀 Ракета +{ath_pnl:.0f}% на {time_to_ath:.0f}м -> Слив в 0 ({cur_pnl:.0f}%)"
                            elif ath_pnl >= 50.0 and cur_pnl > 0.0:
                                outcome = f"💎 Ракета летит (пик +{ath_pnl:.0f}%, сейчас {cur_pnl:+.0f}%)"
                            elif ath_pnl < 15.0 and cur_pnl <= -40.0:
                                outcome = "☠️ Мгновенный раг (фильтр спас депозит)"
                            elif ath_pnl >= 20.0 and cur_pnl <= 0.0:
                                outcome = f"⚠️ Памп +{ath_pnl:.0f}% на {time_to_ath:.0f}м -> Откат в {cur_pnl:.0f}%"
                            else:
                                outcome = "😴 Флет / Без движения"

                            if is_rocket and ath_pnl > (upd.get("old_ath_pnl", 0) or 0):
                                print(f"🚀 [MISSED ROCKET!] {upd['symbol']}: {outcome} (ATH ${new_ath:.6f} vs вход ${p_entry:.6f})")

                            # Обновляем в Supabase
                            if self.use_supabase and self.supabase:
                                try:
                                    update_payload = {
                                        "ath_price": new_ath,
                                        "hypothetical_pnl": round(ath_pnl, 1),
                                        "check_5m_done": upd["check_5m"],
                                        "check_15m_done": upd["check_15m"],
                                        "check_1h_done": upd["check_1h"],
                                        "check_4h_done": upd["check_4h"],
                                        "check_24h_done": upd["check_24h"],
                                        "time_to_ath_min": time_to_ath,
                                        "current_pnl": round(cur_pnl, 1),
                                        "outcome": outcome,
                                        "last_checked_at": datetime.utcnow().isoformat()
                                    }
                                    if table_to_use == "rejected_tokens":
                                        update_payload["is_missed_rocket"] = is_rocket
                                    try:
                                        self.supabase.table(table_to_use).update(update_payload).eq("mint", m).execute()
                                    except Exception as col_err:
                                        # Если колонки check_5m_done/outcome еще не созданы в Supabase, шлем базовый payload
                                        base_payload = {
                                            "ath_price": new_ath,
                                            "hypothetical_pnl": round(ath_pnl, 1),
                                            "check_1h_done": upd["check_1h"],
                                            "check_4h_done": upd["check_4h"],
                                            "check_24h_done": upd["check_24h"],
                                            "last_checked_at": datetime.utcnow().isoformat()
                                        }
                                        if table_to_use == "rejected_tokens":
                                            base_payload["is_missed_rocket"] = is_rocket
                                        self.supabase.table(table_to_use).update(base_payload).eq("mint", m).execute()
                                except Exception as u_err:
                                    pass
                                    
                            # Обновляем в SQLite
                            try:
                                with sqlite3.connect(self.db_path) as conn:
                                    conn.execute('''
                                        UPDATE rejected_tokens 
                                        SET ath_price = ?, hypothetical_pnl = ?, is_missed_rocket = ?,
                                            check_5m_done = ?, check_15m_done = ?,
                                            check_1h_done = ?, check_4h_done = ?, check_24h_done = ?,
                                            time_to_ath_min = ?, current_pnl = ?, outcome = ?,
                                            last_checked_at = ?
                                        WHERE mint = ?
                                    ''', (
                                        new_ath, round(ath_pnl, 1), 1 if is_rocket else 0,
                                        upd["check_5m"], upd["check_15m"],
                                        upd["check_1h"], upd["check_4h"], upd["check_24h"],
                                        time_to_ath, round(cur_pnl, 1), outcome,
                                        datetime.utcnow().isoformat(), m
                                    ))
                            except Exception:
                                pass

                    await asyncio.sleep(0.5)

            except Exception as e:
                print(f"⚠️ Ошибка Shadow Watcher: {e}")
                
            await asyncio.sleep(60)  # Быстрая проверка раз в минуту для отслеживания 5m/15m ракет


# Глобальный синглтон для простого импорта
shadow_tracker = ShadowTracker()
