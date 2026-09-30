import json
import os
import time
from typing import Dict
from pydantic import BaseModel, ConfigDict
import config

class VirtualPosition(BaseModel):
    # extra='allow' — страховка от "опять нет цен": любое новое поле
    # трекинга (price_stale_n, tp1_done...) не должно ронять весь цикл
    # с ошибкой "object has no field".
    model_config = ConfigDict(extra="allow")

    symbol: str
    mint: str
    entry_price_usd: float
    amount_usd: float
    original_amount_usd: float = 0.0  # изначальный сайз (не меняется partials) — база для pnl%
    entry_time: float
    status: str = "open"  # "open" or "closed"
    exit_price_usd: float = 0.0
    pnl_usd: float = 0.0
    max_price_usd: float = 0.0  # Отслеживаем максимальную цену для трейлинга
    peak_time: float = 0.0      # Время, когда была достигнута максимальная цена (ракета)
    current_price_usd: float = 0.0 # Для отображения в интерфейсе
    current_pnl_usd: float = 0.0 # Для отображения в интерфейсе
    exit_reason: str = "" # Причина выхода
    ml_features: dict = {} # Фичи, по которым ИИ принял решение
    ml_confidence: float = 0.0 # Уверенность ИИ (0-100)
    is_mature: bool = False # Флаг для разделения логики (Swing vs Scalp)
    is_moonbag: bool = False # Флаг, что мы уже зафиксировали 50% прибыли
    source: str = "" # Кто открыл: SCANNER VIP RAY-XGB 98%, FOMO, COPY_xxx, ROBINHOOD rule 72%...
    chain: str = "solana" # solana | robinhood
    price_updated_at: float = 0.0 # Время свежего обновления цены из WSS
    price_checked_at: float = 0.0 # Для Crash Guard
    exit_time: float = 0.0 # Для дневного kill-switch
    # --- поля трекинга (раньше их не было в модели → трек падал с
    # '"VirtualPosition" object has no field "price_stale_n"' и цены вставали) ---
    price_stale_n: int = 0 # счётчик циклов без живой цены (EVM)
    tp1_done: bool = False # Tier-1 +25% уже зафиксирован
    stagnant_graces: int = 0 # грейсы живого флета
    first_zero_ts: float = 0.0 # когда цена впервые стала 0 (Solana Stale Price)
    # --- трекинг просадки раннеров (чтобы видеть, какой dip они пережили) ---
    min_price_usd: float = 0.0 # минимальная цена с момента входа
    max_dd_pct: float = 0.0 # максимальная просадка от входа (0..-1, напр. -0.15)

class PaperTracker:
    def __init__(self):
        self.filename = config.PAPER_PORTFOLIO_FILE
        self.positions: Dict[str, VirtualPosition] = {}
        self._sb = None  # ленивый Supabase-клиент
        self.load_portfolio()

    @staticmethod
    def _fees_for(chain: str):
        """Комиссии сети: (small, big, emergency, cap_frac).
        Ethereum L1 в разы дороже L2/Solana — считать его по $0.075 значит врать в плюс."""
        try:
            fees = getattr(config, "EVM_CHAIN_FEES", {})
            if isinstance(fees, dict) and chain in fees:
                return tuple(fees[chain])
        except Exception:
            pass
        return (0.075, 0.45, 0.75, 0.05)

    def _supabase(self):
        """Возвращает Supabase-клиент или None, если нет настроек."""
        if self._sb is not None:
            return self._sb
        url = getattr(config, 'SUPABASE_URL', None)
        key = getattr(config, 'SUPABASE_KEY', None)
        if not url or not key:
            return None
        try:
            from supabase import create_client
            self._sb = create_client(url, key)
            return self._sb
        except Exception as e:
            print(f"⚠️ Не удалось создать Supabase-клиент: {e}")
            return None

    def _parse_portfolio_data(self, data):
        for k, v in data.items():
            if isinstance(v, dict):
                if "max_price_usd" not in v:
                    v["max_price_usd"] = v.get("entry_price_usd", 0)
                if "is_mature" not in v:
                    v["is_mature"] = False
                if "is_moonbag" not in v:
                    v["is_moonbag"] = False
                # Миграция старых сделок: восстанавливаем original_amount_usd.
                # TP1 продаёт 20% (остаток 0.8), Moonbag 50% остатка (0.4 при обоих).
                if not v.get("original_amount_usd"):
                    _rem = float(v.get("amount_usd", 0) or 0)
                    _tp1 = bool(v.get("tp1_done", False))
                    _moon = bool(v.get("is_moonbag", False))
                    if _tp1 and _moon:
                        v["original_amount_usd"] = _rem / 0.4 if _rem else _rem
                    elif _moon:
                        v["original_amount_usd"] = _rem / 0.5 if _rem else _rem
                    elif _tp1:
                        v["original_amount_usd"] = _rem / 0.8 if _rem else _rem
                    else:
                        v["original_amount_usd"] = _rem
                try:
                    self.positions[k] = VirtualPosition(**v)
                except Exception as e:
                    print(f"⚠️ Битый слот {k[:12]} пропущен: {e}")
                    continue

    def load_portfolio(self):
        # 1. Пытаемся загрузить из Supabase (чтобы не терять данные при перезагрузке Render)
        sb = self._supabase()
        if sb is not None:
            try:
                res = sb.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE_V3").execute()
                if res.data and res.data[0].get("features"):
                    data = json.loads(res.data[0]["features"])
                    if data:
                        print("✅ Портфель успешно загружен из Supabase!")
                        self._parse_portfolio_data(data)
                        # Синхронизируем с локальным файлом для дашборда
                        try:
                            with open(self.filename, 'w') as f:
                                json.dump(data, f, indent=4)
                        except Exception:
                            pass
                        return
                    print("ℹ️ В Supabase пустой слепок портфеля, пробуем локальный файл...")
            except Exception as e:
                print(f"⚠️ Не удалось загрузить портфель из Supabase: {e}. Пробуем локальный файл...")
            
        # 2. Fallback: загружаем из локального файла
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                if data:
                    print("✅ Портфель загружен из локального файла!")
                    self._parse_portfolio_data(data)
                    return
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            print(f"⚠️ Не удалось прочитать локальный портфель: {e}")
            
        # 3. Данных нигде нет — стартуем пустыми, но НИЧЕГО НЕ ПИШЕМ,
        # чтобы случайно не затереть облачный слепок пустым словарём.
        print("🧹 Локальных и облачных данных нет — начинаем с чистого листа (в памяти, без записи).")

    def save_portfolio(self):
        data = {k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}
        
        # 1. Сохраняем локально (для Streamlit)
        try:
            with open(self.filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass
            
        # 2. Сохраняем в Supabase (Render-proof).
        # ЗАЩИТА ОТ WIPE: пустой словарь в облако никогда не пишем —
        # пустая память + живой слепок в облаке = не трогаем облако.
        if not data:
            return
        sb = self._supabase()
        if sb is None:
            return
        try:
            sb.table("trades_pump").upsert({
                "mint": "PORTFOLIO_STATE_V3",
                "features": json.dumps(data),
                "confidence": 0,
                "status": "SYSTEM"
            }).execute()
        except Exception as e:
            print(f"⚠️ Ошибка сохранения портфеля в Supabase: {e}")

    def get_open_positions(self) -> Dict[str, VirtualPosition]:
        return {k: v for k, v in self.positions.items() if v.status == "open"}

    def last_loss_pct(self, mint: str):
        """Худший % закрытых сделок по монете (включая архивные *_old_*).
        None — не торговали. Считается по ценам входа/выхода."""
        worst = None
        for k, pos in self.positions.items():
            if k != mint and not k.startswith(mint + "_old_"):
                continue
            if pos.status != "closed" or not pos.entry_price_usd:
                continue
            pct = (pos.exit_price_usd - pos.entry_price_usd) / pos.entry_price_usd
            if worst is None or pct < worst:
                worst = pct
        return worst

    @staticmethod
    def _is_suspicious_pnl(pos) -> bool:
        """Фантомный PnL (как NUTFLEX +$1.6M при +14%): stored pnl не бьётся
        с ценовым. Такие сделки исключаем из капитала/тотала, чиним ремонтом."""
        try:
            entry = float(pos.entry_price_usd or 0)
            exitp = float(pos.exit_price_usd or 0)
            amt = float(getattr(pos, "original_amount_usd", 0) or pos.amount_usd or 0)
            pnl = float(pos.pnl_usd or 0)
            if not entry or not amt:
                return abs(pnl) > 100
            if amt <= 0 or amt > 1000:
                return True
            # ожидаемый порядок: |pnl| <= amt * (|exit/entry-1| + 0.05 комиссий) + $2
            # + запас x3 на partials/округления. Превышение в разы = битые единицы цены.
            price_move = abs(exitp / entry - 1) if entry > 0 and exitp >= 0 else 1.0
            expected_max = amt * (price_move + 0.05) + 2.0
            if expected_max <= 0:
                return abs(pnl) > 50
            return abs(pnl) > expected_max * 3 and abs(pnl) > 50
        except Exception:
            return False

    def get_total_capital(self) -> float:
        # Считаем изначальный капитал + сумма PnL всех закрытых позиций.
        # Битые фантомы (pnl не бьётся с ценами) из капитала исключаем,
        # иначе один +$1.6M ломает весь сайзинг.
        total_pnl = 0.0
        for pos in self.positions.values():
            if pos.status != "closed":
                continue
            if self._is_suspicious_pnl(pos):
                print(f"⚠️ Suspicious PnL исключён из капитала: {pos.symbol} ${pos.pnl_usd:.2f} "
                      f"(entry {pos.entry_price_usd}, exit {pos.exit_price_usd})")
                continue
            total_pnl += pos.pnl_usd
        real_capital = config.INITIAL_BALANCE_USD + total_pnl
        
        if real_capital < 5.0:
            print(f"🛑 [KILL SWITCH] Капитал критически низкий: ${real_capital:.2f}. Торговля невозможна!")
            return 0.0  # Вернуть 0 → position_size будет 0 → сделка не откроется
        
        return real_capital

    def add_position(self, symbol, mint, entry_price, amount_usd=5.0, ml_features=None, ml_confidence=0.0, is_mature=False, source="", chain="solana"):
        # Валидация входа: мусорный entry ~0 давал фантомы +586М% и $1.6M.
        try:
            entry_price = float(entry_price or 0)
            amount_usd = float(amount_usd or 0)
        except Exception:
            print(f"🚫 Отказ: битая цена/сайз {symbol} {mint[:8]}.")
            return
        if not (entry_price > 0) or entry_price > 1e6:
            print(f"🚫 Отказ: entry_price ${entry_price} вне диапазона для {symbol}.")
            return
        if not (0.5 <= amount_usd <= 100.0):
            print(f"🚫 Отказ: сайз ${amount_usd:.2f} вне [0.5, 100] для {symbol}.")
            return
        # БЛОКИРОВКА ПОВТОРНОГО ВХОДА С УМНЫМ КУЛДАУНОМ
        if mint in self.positions:
            pos = self.positions[mint]
            if pos.status == "open":
                print(f"⚠️ Позиция {symbol} уже открыта. Отмена повторного входа.")
                return
                
            # Если позиция закрыта, проверяем кулдаун (4 часа)
            time_since_entry = time.time() - pos.entry_time
            if time_since_entry < (4 * 3600):
                print(f"⏳ Кулдаун: {symbol} уже торговался недавно. Ждем еще {(4*3600 - time_since_entry)/3600:.1f}ч перед входом.")
                return
                
            # Если кулдаун прошел, архивируем старую сделку, чтобы не потерять ее из истории PnL
            archive_key = f"{mint}_old_{int(time.time())}"
            self.positions[archive_key] = pos
            print(f"🔄 Кулдаун прошел! Разрешен повторный вход в {symbol} (CTO/Вторая волна).")

        print(f"✅ Открыта PAPER сделка: {symbol} по цене ${entry_price} [{chain}|{source}]")
        
        ml_features_dict = ml_features if ml_features is not None else {}
        
        self.positions[mint] = VirtualPosition(
            symbol=symbol,
            mint=mint,
            entry_price_usd=entry_price,
            amount_usd=amount_usd,
            original_amount_usd=amount_usd,
            entry_time=time.time(),
            max_price_usd=entry_price,
            min_price_usd=entry_price,
            current_price_usd=entry_price,
            ml_features=ml_features_dict,
            ml_confidence=ml_confidence,
            is_mature=is_mature,
            source=source,
            chain=chain
        )
        self.save_portfolio()
        print(f"📝 PAPER BUY: {symbol} ({mint}) | Amount: ${amount_usd} | Price: ${entry_price} | Src: {source} | Chain: {chain}")
        
        # === СОХРАНЕНИЕ В SUPABASE (ENTRY) ===
        # Сохраняем опыт в базу
        try:
            from trade_logger import TradeLogger
            import asyncio
            logger = TradeLogger()
            asyncio.create_task(logger.log_entry(mint, ml_features_dict, ml_confidence, is_mature))
        except Exception as e:
            print(f"⚠️ Ошибка логирования входа: {e}")

    def partial_close_position(self, mint: str, exit_price: float, sell_pct: float, reason: str):
        """Частичная фиксация позиции (Moonbags)"""
        pos = self.positions.get(mint)
        if pos and pos.status == "open":
            try:
                exit_price = float(exit_price or 0)
                sell_pct = float(sell_pct or 0)
            except Exception:
                return
            if not (exit_price > 0) or not (0 < sell_pct <= 1.0):
                print(f"🚫 Partial отказ: битая цена/доля {pos.symbol}.")
                return
            if not getattr(pos, "original_amount_usd", 0):
                pos.original_amount_usd = pos.amount_usd
            amount_sold_usd = pos.amount_usd * sell_pct
            real_entry_price = pos.entry_price_usd * 1.01
            real_exit_price = exit_price * 0.99

            price_diff_pct = (real_exit_price - real_entry_price) / real_entry_price if real_entry_price > 0 else 0
            # Защита от фантома единиц цены: +5000% на partial = битые данные, не фиксируем
            if abs(price_diff_pct) > 50:
                print(f"🚫 Partial отказ: фантом {price_diff_pct*100:.0f}% {pos.symbol} (entry {pos.entry_price_usd}, exit {exit_price}).")
                return
            _f_small, _f_big, _f_emg, _f_cap = self._fees_for(getattr(pos, "chain", "solana"))
            priority_fee_usd = _f_small if pos.amount_usd < 10.0 else _f_big
            priority_fee_usd = min(priority_fee_usd, amount_sold_usd * _f_cap)

            # PnL от проданной части
            realized_pnl_usd = (amount_sold_usd * price_diff_pct) - priority_fee_usd

            print(f"🚀 [Moonbag] Частичная фиксация {sell_pct*100}% {pos.symbol}: Профит +${realized_pnl_usd:.2f} ({reason})")

            # Сохраняем этот профит в общую копилку монеты!
            pos.pnl_usd += realized_pnl_usd

            # Уменьшаем позицию на проданный процент
            pos.amount_usd -= amount_sold_usd
            pos.is_moonbag = True
            self.save_portfolio()

    def close_position(self, mint: str, exit_price: float, reason: str):
        pos = self.positions.get(mint)
        if pos and pos.status == "open":
            try:
                exit_price = float(exit_price or 0)
            except Exception:
                exit_price = 0.0
            # Stale-выход по 0: закрываем по entry (0% - комиссии), а не -100% фантомом.
            # Иначе одна слепая сделка даёт -100% и тянет статистику в ад.
            if not (exit_price > 0):
                exit_price = pos.current_price_usd or pos.max_price_usd or pos.entry_price_usd
                if not (exit_price > 0):
                    exit_price = pos.entry_price_usd
                reason = f"{reason} [stale→entry]"
            # Фантом единиц цены: exit в разы от entry+пика = битые данные, не пишем миллион.
            try:
                _ref = max(pos.entry_price_usd, pos.max_price_usd or 0) or pos.entry_price_usd
                if _ref > 0 and exit_price / _ref > 50:
                    print(f"🚫 Close отказ-фантом: exit {exit_price} >> entry/peak {_ref} ({pos.symbol}). Закрываю по пику.")
                    exit_price = pos.max_price_usd or pos.entry_price_usd
                    reason = f"{reason} [bad-price→peak]"
            except Exception:
                pass
            pos.status = "closed"
            pos.exit_price_usd = exit_price
            pos.exit_reason = reason
            pos.exit_time = time.time()

            # РЕАЛЬНЫЙ РАСЧЕТ PnL С УЧЕТОМ КОМИССИЙ (1% вход, 1% выход + 0.003 SOL сеть)
            real_entry_price = pos.entry_price_usd * 1.01
            real_exit_price = exit_price * 0.99

            # Считаем изменение цены актива (процент)
            price_diff_pct = (real_exit_price - real_entry_price) / real_entry_price if real_entry_price > 0 else 0
            
            # 2. ДИНАМИЧЕСКИЕ МИКРО-КОМИССИИ JITO (Micro-Tips)
            # Аварийные выходы дороже: широкая проверка по смыслу, а не двум строкам
            # (иначе Emergency Cap / ROB/BSC/GROWTH-стопы считались по дешёвому тарифу)
            _r = reason.upper()
            _f_small, _f_big, _f_emg, _f_cap = self._fees_for(getattr(pos, "chain", "solana"))
            if "CRASH" in _r or "STOP" in _r or "CAP" in _r or "GUARD" in _r:
                priority_fee_usd = _f_emg
            else:
                priority_fee_usd = _f_small if pos.amount_usd < 10.0 else _f_big

            # Защита математики дашборда: комиссия не может превышать cap_frac от микро-позиции,
            # иначе тестовые входы на $4 будут показывать -50% убытка только из-за комиссии.
            # (Для Ethereum cap 20%: газ честно виден, L1-микроскальпы показывают реальный минус.)
            priority_fee_usd = min(priority_fee_usd, pos.amount_usd * _f_cap)
            
            # Добавляем профит от закрытия финального остатка к тому, что уже зафиксировано
            final_pnl = (pos.amount_usd * price_diff_pct) - priority_fee_usd
            pos.pnl_usd += final_pnl

            # Реальный итоговый процент инвестиции — от ИЗНАЧАЛЬНОГО сайза.
            # Баг: остаток/0.5 занижал базу при связке TP1 20% + Moonbag 50%
            # (остаток 0.4×orig → делили на 0.5 = 0.8×orig, +% завышался на 25%).
            original_amount = float(getattr(pos, "original_amount_usd", 0) or 0)
            if not original_amount:
                _rem = pos.amount_usd
                _tp1 = bool(getattr(pos, "tp1_done", False))
                _moon = bool(getattr(pos, "is_moonbag", False))
                if _tp1 and _moon:
                    original_amount = _rem / 0.4 if _rem else _rem
                elif _moon:
                    original_amount = _rem / 0.5 if _rem else _rem
                elif _tp1:
                    original_amount = _rem / 0.8 if _rem else _rem
                else:
                    original_amount = _rem
                pos.original_amount_usd = original_amount
            pnl_pct = pos.pnl_usd / original_amount if original_amount > 0 else 0
            
            self.save_portfolio()
            print(f"🔒 PAPER SELL: {pos.symbol} ({mint}) | Reason: {reason} | PnL: {pnl_pct*100:.2f}% (${pos.pnl_usd:.2f})")
            # Структурный лог сделки (JSONL): точная статистика по exit_reason без grep-гаданий
            try:
                import json as _json
                _dur = (pos.exit_time - pos.entry_time) if pos.entry_time else 0
                _mp = ((pos.max_price_usd - pos.entry_price_usd) / pos.entry_price_usd) \
                    if pos.entry_price_usd else 0
                with open("trades_log.jsonl", "a") as _f:
                    _f.write(_json.dumps({
                        "ts": pos.exit_time, "mint": mint, "symbol": pos.symbol,
                        "chain": pos.chain, "exit_reason": reason,
                        "entry": pos.entry_price_usd, "exit": exit_price,
                        "peak": pos.max_price_usd, "pnl_pct": round(pnl_pct * 100, 2),
                        "pnl_usd": round(pos.pnl_usd, 2),
                        "peak_pct": round(_mp * 100, 1), "held_sec": round(_dur),
                    }) + "\n")
            except Exception:
                pass
            
            # === СОХРАНЕНИЕ ОПЫТА ДЛЯ ИИ (Continuous Learning) ===
            try:
                from trade_logger import TradeLogger
                import asyncio
                logger = TradeLogger()
                asyncio.create_task(logger.log_exit(pos.mint, pnl_pct * 100, pos.exit_reason, pos.is_mature))
            except Exception as e:
                print(f"⚠️ Ошибка сохранения опыта: {e}")

    def is_trading_allowed(self) -> bool:
        """Проверка глобального Kill-Switch"""
        if not getattr(config, "KILL_SWITCH_ENABLED", True):
            return True
        if not hasattr(config, "MAX_DAILY_LOSS_USD"):
            return True
            
        import datetime
        today = datetime.datetime.utcnow().date()
        daily_pnl = 0.0
        
        for pos in self.positions.values():
            if pos.status == "closed":
                if self._is_suspicious_pnl(pos):
                    continue
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
