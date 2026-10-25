import json
import os
import time
from typing import Dict, Tuple
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
    price_updated_at: float = 0.0 # Время свежего обновления цены
    price_checked_at: float = 0.0 # Для Crash Guard
    wss_price: float = 0.0 # Лайв цена из WebSocket (pump.fun)
    wss_price_ts: float = 0.0 # Время тика из WebSocket (pump.fun)
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
    pair_address: str = "" # Адрес пула DexScreener для мгновенного балка без 429
    # --- GROK BOT x JEV RISK ENGINE ("The stop lives under the wick") ---
    stop_under_wick: float = 0.0 # Динамический стоп-уровень под фитиль отката
    wick_stop_loss_pct: float = 0.0 # % стопа от входа (-0.05..-0.08)

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
                # Санация отравленного пика (кейс RAY: max +1624120% с битого тика).
                # Больше x500 от входа за всю жизнь сделки не бывает даже у SHCAT
                # (x12) — это фантом, он валит трейлинги. Сбрасываем на текущую.
                try:
                    _e = float(v.get("entry_price_usd", 0) or 0)
                    _mx = float(v.get("max_price_usd", 0) or 0)
                    if _e > 0 and _mx / _e > 500:
                        print(f"🧹 Санация пика {k[:12]}: x{_mx / _e:.0f} → сброс на текущую.")
                        v["max_price_usd"] = float(v.get("current_price_usd", 0) or _e)
                except Exception:
                    pass
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
        # Грузим ОБА слепка (облако + файл) и сливаем через merge_states:
        # апсерты в Supabase идут каждые ~2с и могут лагать — stale-облако
        # без свежих открытых позиций больше не затирает локальную правду.
        cloud, local = {}, {}
        sb = self._supabase()
        if sb is not None:
            try:
                _snap_key = getattr(config, "PORTFOLIO_SNAPSHOT_MINT", "PORTFOLIO_STATE_V3")
                res = sb.table("trades_pump").select("features").eq("mint", _snap_key).execute()
                if res.data and res.data[0].get("features"):
                    cloud = json.loads(res.data[0]["features"]) or {}
            except Exception as e:
                print(f"⚠️ Не удалось загрузить портфель из Supabase: {e}.")
        try:
            with open(self.filename, 'r') as f:
                local = json.load(f) or {}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            print(f"⚠️ Не удалось прочитать локальный портфель: {e}")

        data = self.merge_states(cloud, local)
        if data:
            _src = "облако+файл" if (cloud and local) else ("Supabase" if cloud else "локальный файл")
            print(f"✅ Портфель загружен ({_src}): {len(data)} слотов.")
            self._parse_portfolio_data(data)
            # Синхронизируем merged-версию в файл для дашборда
            try:
                with open(self.filename, 'w') as f:
                    json.dump(data, f, indent=4)
            except Exception:
                pass
            return

        # Данных нигде нет — стартуем пустыми, но НИЧЕГО НЕ ПИШЕМ,
        # чтобы случайно не затереть облачный слепок пустым словарём.
        print("🧹 Локальных и облачных данных нет — начинаем с чистого листа (в памяти, без записи).")

    def save_portfolio(self, force_cloud: bool = False):
        data = {k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}
        
        # 1. Сохраняем локально (для Streamlit) — мгновенно и без задержек
        try:
            with open(self.filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass
            
        # 2. Сохраняем в Supabase (Render-proof).
        # Троттлинг: не спамим Supabase синхронными HTTP-запросами каждую секунду.
        # В облако пишем немедленно при открытии/закрытии (force_cloud=True) или не чаще раза в 4 секунды.
        if not data:
            return
        now = time.time()
        last_sb = getattr(self, "_last_sb_save", 0.0)
        if not force_cloud and (now - last_sb) < 4.0:
            return
        self._last_sb_save = now
        sb = self._supabase()
        if sb is None:
            return
        try:
            _snap_key = getattr(config, "PORTFOLIO_SNAPSHOT_MINT", "PORTFOLIO_STATE_V3")
            sb.table("trades_pump").upsert({
                "mint": _snap_key,
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
    def _norm_symbol(s) -> str:
        """Тикер к каноническому виду для символьного кулдауна."""
        try:
            return (str(s) or "").strip().upper()
        except Exception:
            return ""

    @staticmethod
    def merge_states(cloud: dict, local: dict) -> dict:
        """Слияние облачного и локального слепков портфеля (анти-«пропадание» сделок).
        Проблема: сейвы идут каждые ~2с, апсерты в Supabase могут лагать/падать —
        тогда дашборд (или бот после рестарта) видел stale-облако без свежих
        открытых позиций. Правила: ключ из любого источника сохраняется;
        open+closed → closed побеждает (терминальное событие); open+open →
        свежее по price_updated_at; closed+closed → позднее по exit_time."""
        cloud = cloud if isinstance(cloud, dict) else {}
        local = local if isinstance(local, dict) else {}
        if not cloud:
            return dict(local)
        if not local:
            return dict(cloud)
        out = dict(local)
        for k, cv in cloud.items():
            if k not in out:
                out[k] = cv
                continue
            lv = out[k]
            if not isinstance(cv, dict) or not isinstance(lv, dict):
                continue
            cs, ls = cv.get("status"), lv.get("status")
            if cs != ls:
                # Терминальное состояние (closed/quarantined) важнее висящего open
                if cs != "open" and ls == "open":
                    out[k] = cv
                # иначе оставляем local (свежие правки трекинга идут в файл первым)
                continue
            if cs == "open":
                try:
                    cv_t = max(float(cv.get("price_checked_at", 0) or 0), float(cv.get("price_updated_at", 0) or 0))
                    lv_t = max(float(lv.get("price_checked_at", 0) or 0), float(lv.get("price_updated_at", 0) or 0))
                    if cv_t > lv_t:
                        out[k] = cv
                except Exception:
                    pass
            else:
                try:
                    if float(cv.get("exit_time", 0) or 0) > float(lv.get("exit_time", 0) or 0):
                        out[k] = cv
                except Exception:
                    pass
        return out

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

    def calculate_dynamic_position_size(self, pair_data: dict = None, chain: str = "base") -> float:
        """
        Умный ступенчатый реинвест прибыли с защитой от сдвига цены (Price Impact Guard):
        1. Проверка свободного кэша:
           Свободный кэш = Общий капитал - Стоимость открытых позиций.
           Если свободного кэша меньше базового входа ($15) -> 0.0 (не открываем).
        2. Базовый пол: $15.0.
        3. Жесткий потолок: $25.0 (не более $25 в один мемкоин).
        4. Ступени от чистой прибыли (не рискуем всей прибылью!):
           - Прибыль < $20: $15
           - Прибыль $20..$39: $17 (риск из прибыли всего +$2)
           - Прибыль $40..$69: $19 (риск из прибыли всего +$4)
           - Прибыль $70..$99: $22 (риск из прибыли всего +$7)
           - Прибыль >= $100: $25 (риск из прибыли всего +$10)
        5. Защита ликвидности пула:
           - Если пул < $15,000 USD -> сайз строго залочен на базовые $15.0 (защита от сдвига цены).
           - Если пул >= $15,000 USD -> разрешен увеличенный сайз ($17-$25).
           - В любом случае сайз не может превышать 0.5% ликвидности пула (liq * 0.0025).
        6. Округление: строго целое число долларов (int(round(...))). Никаких бесконечных копеек!
        """
        base_size = float(getattr(config, "BASE_TRADE_AMOUNT_USD", 15.0) or 15.0)
        hard_cap = float(getattr(config, "MAX_POSITION_SIZE_USD", 25.0) or 25.0)
        min_liq = float(getattr(config, "REINVEST_MIN_POOL_LIQ_USD", 15000.0) or 15000.0)

        # 0. Проверка защиты от серии минусов
        is_cooling, reason = self.is_streak_cooldown_active()
        if is_cooling:
            print(reason)
            return 0.0

        capital = self.get_total_capital()
        if capital < 5.0:
            return 0.0

        open_positions = self.get_open_positions()
        open_cost = sum(p.amount_usd for p in open_positions.values())
        free_cash = max(0.0, capital - open_cost)

        if free_cash < base_size:
            print(f"⚠️ [REINVEST] Недостаточно свободного кэша: ${free_cash:.1f} < базового входа ${base_size:.1f}")
            return 0.0

        initial_balance = float(getattr(config, "INITIAL_BALANCE_USD", 100.0) or 100.0)
        net_profit = capital - initial_balance

        # Стратегия экспоненциального разгона (@savipww):
        # 20-25% от текущего капитала в одну сделку (фиксированная доля капитала).
        if getattr(config, "COMPOUNDING_ENABLED", True):
            comp_pct = float(getattr(config, "COMPOUNDING_PCT", 0.22) or 0.22)
            target = max(base_size, capital * comp_pct)
        else:
            if net_profit < 20.0:
                target = 15.0
            elif net_profit < 40.0:
                target = 17.0
            elif net_profit < 70.0:
                target = 19.0
            elif net_profit < 100.0:
                target = 22.0
            else:
                target = 25.0

        liq_usd = 0.0
        if isinstance(pair_data, dict):
            liq_usd = float((pair_data.get("liquidity") or {}).get("usd", 0.0) or 0.0)

        if liq_usd > 0 and liq_usd < min_liq:
            if target > base_size:
                print(f"💧 [REINVEST] Пул ${liq_usd:,.0f} < ${min_liq:,.0f}: сброс сайза с ${target:.0f} на базовые ${base_size:.0f} (защита цены)")
            target = base_size
        elif liq_usd > 0:
            # Не более 1.0% от пула (для глубоких пулов $100k+ ордер может быть $1,000 без проскальзывания)
            max_by_pool = max(base_size, liq_usd * 0.010)
            if target > max_by_pool:
                target = max_by_pool

        target = min(target, hard_cap)

        # Троттлинг по просадке: чем глубже откат от пика реализованного PnL, тем меньше билет.
        # >= START$ просадки -> половина размера, >= 2*START$ -> базовый (скаутский) билет.
        if getattr(config, "DD_THROTTLE_ENABLED", True):
            _dd = self.get_realized_drawdown()
            _dd0 = float(getattr(config, "DD_THROTTLE_START_USD", 15.0))
            if _dd >= 2 * _dd0:
                target = base_size
            elif _dd >= _dd0:
                target = max(base_size, target * 0.5)
            if _dd >= _dd0:
                print(f"📉 [DD THROTTLE] просадка ${_dd:.1f} от пика -> сайз ${target:.0f}")

        if target > free_cash:
            target = free_cash

        final_size = float(int(round(target)))
        if final_size < base_size:
            final_size = base_size if free_cash >= base_size else 0.0

        if chain == "robinhood":
            rob_cap = float(getattr(config, "ROB_MAX_SIZE_USD", hard_cap) or hard_cap)
            final_size = min(final_size, rob_cap)
        elif chain == "bsc":
            bsc_cap = float(getattr(config, "BSC_MAX_SIZE_USD", hard_cap) or hard_cap)
            final_size = min(final_size, bsc_cap)

        print(f"💰 [COMPOUNDING] Вход: ${final_size:.0f} (Капитал: ${capital:.1f}, Свободно: ${free_cash:.1f}, Профит: {net_profit:+.1f}, Пул: ${liq_usd:,.0f})")
        return final_size

    def add_position(self, symbol, mint, entry_price, amount_usd=5.0, ml_features=None, ml_confidence=0.0, is_mature=False, source="", chain="solana", pair_address="", stop_under_wick=0.0, wick_stop_loss_pct=0.0):
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
        if not (0.5 <= amount_usd <= 50000.0):
            print(f"🚫 Отказ: сайз ${amount_usd:.2f} вне [0.5, 50000] для {symbol}.")
            return
        # Защита от серии минусов (Streak Circuit Breaker)
        is_cooling, reason = self.is_streak_cooldown_active()
        if is_cooling:
            print(f"🚫 Отказ входа {symbol}: {reason}")
            return
        # СИМВОЛЬНЫЙ КУЛДАУН (кейс VRAX: +5% → перезаход в клона → -32%).
        # Все старые гарды keyed by mint (адрес), а клоны выходят под тем же
        # тикером с другим адресом. Баним тикер целиком: открыт такой же —
        # отказ (не удваиваемся в тот же памп); закрыт недавно — отказ
        # (не покупаем вершину, с которой только вышли, и не ловим нож).
        # История закрытых живёт в positions (включая архив *_old_*), поэтому
        # переживает рестарты через файл/Supabase.
        _sym = self._norm_symbol(symbol)
        if _sym:
            _cd = float(getattr(config, "SYMBOL_REBUY_COOLDOWN_SEC", 4 * 3600) or 0)
            _now = time.time()
            for _k, _p in self.positions.items():
                if _k == mint:
                    continue  # свой же адрес ведёт mint-ветка ниже
                try:
                    if self._norm_symbol(_p.symbol) != _sym:
                        continue
                except Exception:
                    continue
                if _p.status == "open":
                    print(f"🚫 Отказ: {symbol} уже в рынке (другой контракт, {_k[:10]}). Не удваиваемся в тот же тикер.")
                    return
                if _p.status == "closed" and _cd > 0:
                    _xt = float(getattr(_p, "exit_time", 0) or 0)
                    _pnl = float(getattr(_p, "pnl_usd", 0.0) or 0.0)
                    _was_profitable = _pnl > 0 and not any(k in str(getattr(_p, "exit_reason", "")).upper() for k in ("CRASH", "RUG", "HONEYPOT", "EMERGENCY"))
                    _eff_cd = (5 * 60) if _was_profitable else _cd
                    if _xt and (_now - _xt) < _eff_cd:
                        _left = (_eff_cd - (_now - _xt)) / 60 if _was_profitable else (_eff_cd - (_now - _xt)) / 3600
                        _unit = "м" if _was_profitable else "ч"
                        print(f"⏳ Символьный кулдаун: {symbol} закрыт {_p.pnl_usd:+.2f}$ недавно ({getattr(_p, 'exit_reason', '')}). "
                              f"Повтор через {_left:.1f}{_unit} — не покупаем свой же выход.")
                        return
        # БЛОКИРОВКА ПОВТОРНОГО ВХОДА С УМНЫМ КУЛДАУНОМ
        if mint in self.positions:
            pos = self.positions[mint]
            if pos.status == "open":
                print(f"⚠️ Позиция {symbol} уже открыта. Отмена повторного входа.")
                return
                
            # Умный кулдаун:
            # 1. Если сделка закрылась в плюс (TP / Trailing) и это не скам/раг:
            #    даём 5 минут на окончание первого отката. Если монета снова растёт и сканер
            #    подтверждает импульс — разрешаем повторный вход на вторую волну!
            # 2. Если сделка закрылась в минус (Stop-Loss / Cut / Crash) — кулдаун 4 часа,
            #    чтобы гарантированно не ловить падающий нож в умирающем токене.
            _pnl = float(getattr(pos, "pnl_usd", 0.0) or 0.0)
            _exit_time = float(getattr(pos, "exit_time", 0.0) or pos.entry_time)
            time_since_exit = time.time() - _exit_time
            _was_profitable = _pnl > 0 and not any(k in str(getattr(pos, "exit_reason", "")).upper() for k in ("CRASH", "RUG", "HONEYPOT", "EMERGENCY"))
            
            _req_cd = (5 * 60) if _was_profitable else (4 * 3600)
            if time_since_exit < _req_cd:
                if _was_profitable:
                    print(f"⏳ Кулдаун профитной монеты: {symbol} закрыт в +${_pnl:.2f}. Ждем еще {int(_req_cd - time_since_exit)}с перед возможным перезаходом во 2-ю волну.")
                else:
                    print(f"⏳ Кулдаун: {symbol} закрыт с минусом. Ждем еще {(4*3600 - time_since_exit)/3600:.1f}ч перед входом.")
                return
                
            # Если кулдаун прошел, архивируем старую сделку, чтобы не потерять ее из истории PnL
            archive_key = f"{mint}_old_{int(time.time())}"
            self.positions[archive_key] = pos
            print(f"🔄 Кулдаун прошел! Разрешен повторный вход в {symbol} ({'Вторая волна профитного токена' if _was_profitable else 'CTO'}).")

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
            chain=chain,
            pair_address=pair_address,
            stop_under_wick=float(stop_under_wick or 0.0),
            wick_stop_loss_pct=float(wick_stop_loss_pct or 0.0)
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
            pos.amount_usd = max(0.0, pos.amount_usd - amount_sold_usd)
            if "MOONBAG" in reason.upper() or "RISK FREE" in reason.upper() or "200%" in reason:
                pos.is_moonbag = True
            self.save_portfolio()

    def close_position(self, mint: str, exit_price: float, reason: str):
        pos = self.positions.get(mint)
        if pos and pos.status == "open":
            try:
                exit_price = float(exit_price or 0)
            except Exception:
                exit_price = 0.0
            
            # 1. Жесткий скам / Honeypot / Rugpull: честное списание по 0.0 (-100% реальный исход)
            _r_up = str(reason or "").upper()
            _is_hard_scam = any(k in _r_up for k in ("HONEYPOT", "RUG", "SCAM", "CANNOT SELL", "UNSELLABLE", "NO LIQUIDITY"))
            if _is_hard_scam:
                exit_price = 0.0
            elif not (exit_price > 0):
                # Stale-выход по 0 (сбой API котировок): закрываем по цене входа (0% PnL - комиссии)
                exit_price = pos.entry_price_usd
                reason = f"{reason} [stale→entry]"

            # Фантом единиц цены: exit в разы от entry+пика = битые данные, не пишем миллион.
            if not _is_hard_scam:
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

            # ХАНИПОД / РАГ / UNSELLABLE: на реальном блокчейне частичные тейки (TP1/TP2)
            # физически не исполнились бы! Списываем 100% от ИЗНАЧАЛЬНОГО депозита без иллюзий.
            if "HONEYPOT" in _r or "UNSELLABLE" in _r or "RUG" in _r:
                pos.pnl_usd = -original_amount - priority_fee_usd
                pos.exit_price_usd = 0.0
            else:
                # Добавляем профит от закрытия финального остатка к тому, что уже зафиксировано
                final_pnl = (pos.amount_usd * price_diff_pct) - priority_fee_usd
                pos.pnl_usd += final_pnl

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

    def _closed_sorted(self):
        """Закрытые сделки (без фантомного PnL) по времени выхода, старые первыми."""
        closed = [p for p in self.positions.values()
                  if p.status == "closed" and not self._is_suspicious_pnl(p)]
        return sorted(closed, key=lambda p: getattr(p, "exit_time", 0.0) or getattr(p, "entry_time", 0.0))

    def get_realized_drawdown(self) -> float:
        """Просадка реализованного PnL от своего максимума, в $ (>= 0)."""
        cum = peak = 0.0
        for p in self._closed_sorted():
            cum += float(p.pnl_usd or 0.0)
            peak = max(peak, cum)
        return max(0.0, peak - cum)

    def is_rolling_breaker_active(self) -> Tuple[bool, str]:
        """Пауза входов, если сумма PnL последних N закрытых сделок <= -LOSS_USD.
        Пауза считается от выхода последней сделки. На данных 22.10: пик +67$ в 15:19,
        дальше 13 минусов из 14 (-35$) при паузе стрик-брейкера всего 3 мин."""
        if not getattr(config, "ROLLING_BREAKER_ENABLED", True):
            return False, ""
        n = int(getattr(config, "ROLLING_BREAKER_WINDOW", 8))
        loss = float(getattr(config, "ROLLING_BREAKER_LOSS_USD", 8.0))
        pause = float(getattr(config, "ROLLING_BREAKER_PAUSE_MIN", 45.0))
        closed = self._closed_sorted()
        if len(closed) < n:
            return False, ""
        last = closed[-n:]
        total = sum(float(p.pnl_usd or 0.0) for p in last)
        if total > -loss:
            return False, ""
        last_exit = getattr(last[-1], "exit_time", 0.0) or getattr(last[-1], "entry_time", 0.0) or 0.0
        elapsed = (time.time() - last_exit) / 60.0
        if elapsed < pause:
            return True, (f"🛑 [ROLLING BREAKER] Последние {n} сделок: ${total:.2f}. "
                          f"Пауза ещё {pause - elapsed:.0f} мин.")
        return False, ""

    def is_streak_cooldown_active(self) -> Tuple[bool, str]:
        """
        Проверка защиты от серии минусов (Streak Circuit Breaker):
        Если последние N закрытых сделок (по умолчанию 3) были в минус,
        активируется пауза на STREAK_COOLDOWN_MINUTES (по умолчанию 30 минут)
        с момента закрытия последнего минуса.
        """
        # Скользящий брейкер по сумме PnL (ловит «медленное кровотечение», которое серия
        # из N крупных минусов подряд не видит): срабатывает раньше и держит паузу дольше.
        _rb, _rb_msg = self.is_rolling_breaker_active()
        if _rb:
            return True, _rb_msg

        if not getattr(config, "STREAK_COOLDOWN_ENABLED", True):
            return False, ""

        max_losses = int(getattr(config, "STREAK_MAX_CONSECUTIVE_LOSSES", 3) or 3)
        cooldown_min = float(getattr(config, "STREAK_COOLDOWN_MINUTES", 30.0) or 30.0)

        closed = [p for p in self.positions.values() if p.status == "closed" and not self._is_suspicious_pnl(p)]
        if not closed:
            return False, ""

        sorted_closed = sorted(
            closed,
            key=lambda p: getattr(p, "exit_time", 0.0) or getattr(p, "entry_time", 0.0),
            reverse=True
        )

        # Если прямо сейчас открыта прибыльная позиция (например, CALL +$23.91):
        # общий портфель в плюсе, торговля не блокируется!
        _open_pnl = sum(getattr(p, "current_pnl_usd", 0.0) or 0.0 for p in self.positions.values() if p.status == "open")
        if _open_pnl > 3.0:
            return False, ""

        loss_streak = 0
        last_loss_exit_time = 0.0
        for p in sorted_closed:
            # Учитываем только реальные минусы (микро-скретчи вроде Starman -$0.32 не являются скам-волной)
            if p.pnl_usd <= -1.50:
                loss_streak += 1
                if last_loss_exit_time == 0.0:
                    last_loss_exit_time = getattr(p, "exit_time", 0.0) or getattr(p, "entry_time", 0.0) or time.time()
            elif p.pnl_usd > 0.50:
                break

        if loss_streak >= max_losses:
            elapsed_min = (time.time() - last_loss_exit_time) / 60.0
            if elapsed_min < cooldown_min:
                rem = cooldown_min - elapsed_min
                msg = f"🛑 [STREAK COOLDOWN] Серия из {loss_streak} минусов подряд! Пауза на {rem:.1f} мин для защиты от скам-волны."
                return True, msg

        return False, ""

    def is_trading_allowed(self) -> bool:
        """Проверка глобального Kill-Switch и кулдауна серии минусов"""
        is_cooling, reason = self.is_streak_cooldown_active()
        if is_cooling:
            print(reason)
            return False

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
        if len(self.get_open_positions()) >= max_concurrent:
            return False
        # Проверка свободного кэша: если осталось меньше базового ордера ($15),
        # блокируем открытие новой позиции (защита от входа на деньги, которых нет)
        capital = self.get_total_capital()
        open_cost = sum(p.amount_usd for p in self.get_open_positions().values())
        free_cash = capital - open_cost
        base_size = float(getattr(config, "BASE_TRADE_AMOUNT_USD", 15.0) or 15.0)
        if free_cash < base_size:
            print(f"🛑 [CASH GUARD] Свободный кэш ${free_cash:.1f} < базового входа ${base_size:.1f}. Вход заблокирован.")
            return False
        return True
