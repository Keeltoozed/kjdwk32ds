import aiohttp
from datetime import datetime, timezone
import time
import config
from sentiment import analyze_sentiment
from ta_tools import TATools
import math
import contextvars

try:
    import numpy as np
    import xgboost as xgb
    class ScamFilter:
        def __init__(self, path='scam_filter_model.json'):
            try:
                self.model = xgb.XGBClassifier()
                self.model.load_model(path)
                self.enabled = True
                print('AI Scam Filter: загружен (XGBoost native, без sklearn-pickle)')
            except Exception as e:
                self.enabled = False
                print(f'AI Scam Filter: пропущен ({e})')
        def is_scam(self, data) -> tuple:
            if not self.enabled:
                return False, 0.0
            try:
                feat = np.array([[data.get('dev_holding_pct', 0), data.get('tx_velocity_1m', 0),
                                  data.get('volume_to_liq_ratio', 0), data.get('funded_from_cex', 0)]])
                return bool(self.model.predict(feat)[0]), float(self.model.predict_proba(feat)[0][1])
            except Exception:
                return False, 0.0
    SCAM_FILTER = ScamFilter()
except Exception as e:
    SCAM_FILTER = None
    print(f'AI Filter ошибка: {e}')


def _ctx_attr(name, default=0.0):
    """Task-local атрибут анализатора для параллельного разбора токенов (ScanWindow)."""
    key = "_cv_" + name

    def _get(self):
        cv = self.__dict__.get(key)
        return default if cv is None else cv.get()

    def _set(self, value):
        cv = self.__dict__.get(key)
        if cv is None:
            cv = contextvars.ContextVar(f"{name}_{id(self)}", default=default)
            self.__dict__[key] = cv
        cv.set(value)

    return property(_get, _set)


class Analyzer:
    last_score = _ctx_attr("last_score", 0.0)    # Скор последнего решения (для радара)
    _last_top10 = _ctx_attr("_last_top10", 0.0)  # топ-10 холдеров % (последний разбор в этой задаче)
    _last_dev = _ctx_attr("_last_dev", 0.0)      # доля дева % (последний разбор в этой задаче)

    def __init__(self):
        self._clone_cache = {}
        self._clone_cache_time = {}
        self.session = None
        self.rocket_model = None  # ROCKET-модель (EVM ракеты). None = нет файла, гейты как раньше
        self.rocket_scores = {}  # address -> score 0..1 (для сайзинга в main)
        self.last_signal = ""  # Метка последнего решения: "VIP RAY-XGB 98%", "PULLBACK", "ROBINHOOD rule 72%"...
        self.last_score = 0.0  # Скор последнего решения (для радара)
        self.signals = {}  # Метки по mint: НЕ делит состояние между параллельными петлями (иначе FOMO:? в дашборде)
        self._dip_watch = {}  # OVERHEAT-пики для dip-buy: {address: (peak_price, ts)}
        self._prefetch_cache = {}  # mint -> (ts, best_pair) кэш пакетного префетча DexScreener

        # Предзагрузка моделей в память один раз при старте
        try:
            import xgboost as xgb
            self.pump_model = xgb.XGBClassifier()
            self.pump_model.load_model("pump_model.json")
        except: 
            self.pump_model = None
        
        try:
            import xgboost as xgb
            self.raydium_model = xgb.XGBClassifier()
            self.raydium_model.load_model("raydium_model_dex.json")
        except: 
            self.raydium_model = None

    def _rocket(self):
        """Ленивая загрузка ROCKET-модели + хот-релоад по mtime.
        Ретрейн идёт раз в 6ч фоном — без релоада бот до рестарта сидел бы
        на старой матрице. Fail-open: нет файла → None → поведение как раньше."""
        try:
            import os as _os
            import config as _c
            import rocket_model as _rm
            path = getattr(_c, "ROCKET_MODEL_PATH", "rocket_model.json")
            try:
                mt = _os.path.getmtime(path) if path else -1
            except Exception:
                mt = -1
            if self.rocket_model is None or mt != getattr(self, "_rocket_mtime", None):
                self.rocket_model = _rm.load(path)
                self._rocket_mtime = mt
                if self.rocket_model is not None:
                    print("🚀 ROCKET-модель загружена — ранжирую EVM-входы.")
        except Exception:
            self.rocket_model = None
        return self.rocket_model

    def _rkt_tag(self, address: str, pair_data: dict) -> str:
        """Скор ракеты для метки сигнала. Fail-open: нет модели → ''."""
        try:
            import rocket_model as _rm
            clf = self._rocket()
            s = _rm.score(clf, pair_data)
            if s is None:
                return ""
            self.rocket_scores[address] = float(s)
            if len(self.rocket_scores) > 2000:
                self.rocket_scores.clear()
                self.rocket_scores[address] = float(s)
            return f" RKT{s:.2f}"
        except Exception:
            return ""

    def rocket_size_mult(self, address: str) -> float:
        """Множитель сайза по скору ракеты: strong x1.5 / weak x0.5 / иначе x1.0.
        Нет модели или скора → 1.0 (старое поведение)."""
        try:
            import config as _c
            s = self.rocket_scores.get(address)
            if s is None:
                return 1.0
            if s >= float(getattr(_c, "ROCKET_STRONG", 0.65)):
                return float(getattr(_c, "ROCKET_SIZE_UP", 1.5))
            if s <= float(getattr(_c, "ROCKET_WEAK", 0.35)):
                return float(getattr(_c, "ROCKET_SIZE_DOWN", 0.5))
        except Exception:
            pass
        return 1.0

    async def meme_tag(self, address: str, symbol: str, pair_data: dict,
                       chain: str = "solana") -> str:
        """Тег нарратива от Gemini (ADVISORY): '' или ' MEME85'.
        Входы не режет — копим статистику тег vs исход для будущих порогов."""
        try:
            import meme_judge as _mj
            s, _w = await _mj.judge(address, symbol, pair_data or {}, chain)
            if s is None:
                return ""
            return f" MEME{s:.0f}"
        except Exception:
            return ""

    def rocket_veto(self, address: str, pair_data: dict, sig: str):
        """Вето слабых дженерик-входов: RKT < порога → причина-строка, иначе None.
        Факт из прода: MERRGER/MTA/LUXR зашли по rule 77-90% со скором
        0.04-0.25 и все умерли стопом -30%. Модель видела distribution заранее.
        Exempt (bounded билеты / чужие стратегии): DIPBUY, SCOUT, LOTTERY,
        VIP ($2 кэп), COPY, GROWTH. Нет модели/скора → None (fail-open)."""
        try:
            import config as _c
            if not getattr(_c, "ROCKET_VETO_ENABLED", False):
                return None
            s = (sig or "").upper()
            if any(k in s for k in ("LOTTERY", "DIPBUY", "COPY", "VIP", "RAY-XGB", "PUMP-XGB", "GROWTH", "JUP")):
                return None
            _ch_veto = str((pair_data or {}).get("chainId", "")).lower()
            if _ch_veto == "solana":
                return None
            if _ch_veto in ("robinhood", "base", "bsc", "ethereum"):
                self._rkt_tag(address, pair_data or {})
                sc = self.rocket_scores.get(address)
                if "SCOUT" in s:
                    _tx5 = ((pair_data or {}).get("txns") or {}).get("m5") or {}
                    _b5 = int(_tx5.get("buys", 0) or 0)
                    _s5 = int(_tx5.get("sells", 0) or 0)
                    _vm5 = float(((pair_data or {}).get("volume") or {}).get("m5", 0) or 0)
                    if _b5 >= 8 and _s5 >= 1 and _b5 >= _s5 and _vm5 >= 2000.0:
                        return None
                    if sc is not None and sc < 0.15:
                        return f"RKT-вето {sc:.2f}: слабый скор для EVM SCOUT (< 0.15, бото-стакан)"
                else:
                    if sc is not None and sc < 0.08:
                        return f"RKT-вето {sc:.2f}: пустой/мертвый стакан EVM (< 0.08)"
                return None

            if "SCOUT" in s:
                self._rkt_tag(address, pair_data or {})
                sc = self.rocket_scores.get(address)
                _tx5 = ((pair_data or {}).get("txns") or {}).get("m5") or {}
                _b5 = int(_tx5.get("buys", 0) or 0)
                _s5 = int(_tx5.get("sells", 0) or 0)
                _vm5 = float(((pair_data or {}).get("volume") or {}).get("m5", 0) or 0)
                if _b5 >= 12 and _s5 >= 2 and _b5 >= _s5 and _vm5 >= 3000.0:
                    return None
                if sc is not None and sc < 0.35:
                    return f"RKT-вето {sc:.2f}: слабый скор для SCOUT (падающий нож)"
                return None
            if not pair_data:
                return None  # нечем счислить — fail-open (пустой вектор всегда скор 0!)
            self._rkt_tag(address, pair_data or {})
            sc = self.rocket_scores.get(address)
            if sc is None:
                return None
            if sc < float(getattr(_c, "ROCKET_VETO_MIN", 0.30)):
                reason_str = (f"RKT-вето {sc:.2f}: слабая структура под импульсом "
                              f"(rule-high, distribution) — не платим за чужой выход")
                try:
                    from shadow_tracker import shadow_tracker
                    _base = (pair_data or {}).get("baseToken", {}) or {}
                    _sym = _base.get("symbol", address[:6])
                    _ch = (pair_data or {}).get("chainId", "evm")
                    _px = float((pair_data or {}).get("priceUsd", 0) or 0)
                    _lq = float(((pair_data or {}).get("liquidity", {}) or {}).get("usd", 0) or 0)
                    _m5 = float(((pair_data or {}).get("priceChange", {}) or {}).get("m5", 0) or 0)
                    shadow_tracker.log_rejection(
                        mint=address,
                        reason=f"RKT-вето {sc:.2f}",
                        symbol=_sym,
                        chain=_ch,
                        score=0.0,
                        rkt_score=sc,
                        price=_px,
                        liquidity=_lq,
                        m5_pct=_m5,
                        source=sig or "ROCKET_VETO"
                    )
                except Exception:
                    pass
                return reason_str
        except Exception:
            pass
        return None

    def _set_sig(self, key: str, text: str):
        """Метка решения для монеты. Per-mint словарь вместо общего поля:
        scanner/fomo/robinhood гоняют analyze параллельно на одном Analyzer."""
        self.signals[key] = text
        if len(self.signals) > 2000:
            self.signals.clear()
            self.signals[key] = text
        self.last_signal = text

    def _deny(self, key: str, short: str, msg: str) -> bool:
        """Отказ с записью причины в signals, минутным счетчиком воронки и отправкой в Supabase."""
        print(msg)
        self._set_sig(key, f"SKIP {short}")
        try:
            import collections as _coll
            _f = getattr(self, "_sol_funnel", None)
            if _f is None:
                self._sol_funnel = _f = _coll.Counter()
                self._sol_funnel_start_ts = time.time()
            _f[short] += 1
            now = time.time()
            if now - getattr(self, "_sol_funnel_start_ts", 0) >= 60.0:
                top = self._sol_funnel.most_common(10)
                total = sum(self._sol_funnel.values())
                reasons_str = " | ".join(f"{r}={c}" for r, c in top)
                print(f"📊 [SCANNER/RAYDIUM FUNNEL 60s] Отклонено за 1м: {total} | {reasons_str}")
                self._sol_funnel.clear()
                self._sol_funnel_start_ts = now
        except Exception:
            pass
        try:
            from shadow_tracker import shadow_tracker
            ctx = getattr(self, "_eval_ctx", {}).get(key, {})
            shadow_tracker.log_rejection(
                mint=key,
                reason=short,
                symbol=ctx.get("symbol", key[:6]),
                chain=ctx.get("chain", "solana"),
                score=float(ctx.get("score", 0.0) or 0.0),
                rkt_score=self.rocket_scores.get(key),
                price=float(ctx.get("price", 0.0) or 0.0),
                liquidity=float(ctx.get("liq", 0.0) or 0.0),
                m5_pct=float(ctx.get("m5", 0.0) or 0.0),
                source=ctx.get("source", "SCANNER")
            )
        except Exception:
            pass
        return False

    @staticmethod
    def is_churn(b5, s5, m5, n_min: int = 18, liq: float = 0.0, vol5: float = 0.0, is_clanker: bool = False) -> bool:
        """CHURN/WASH (анализ паттернов накрутки): высокий оборот без перевеса покупателей,
        микро-сделки ботов или аномальный оборот к ликвидности."""
        if is_clanker and vol5 >= 2000.0 and (b5 + s5) < 40:
            return False
        if liq >= 60000.0 and vol5 < liq * 2:
            return False
        try:
            b5 = float(b5 or 0)
            s5 = float(s5 or 0)
            m5 = float(m5 or 0)
            vol5 = float(vol5 or 0)
        except Exception:
            return False
        if (b5 + s5) < n_min or s5 <= 0:
            return False
        r = b5 / s5
        # 1. Топтание без перевеса
        if (r < 1.2 and abs(m5) < 15.0) or (r < 1.15 and abs(m5) < 60.0):
            if is_clanker and abs(m5) >= 20.0:
                return False
            return True
        # 2. Аномальный оборот без импульса (wash-trading ботов)
        if liq > 0 and vol5 > 0 and (vol5 / liq) > 3.0 and abs(m5) < 10.0:
            return True
        # 3. Микро-чеки (боты накручивают транзакции мелкими чеками)
        if (b5 + s5) >= 20 and vol5 > 0 and (vol5 / (b5 + s5)) < 15.0:
            return True
        return False

    def get_signal(self, key: str) -> str:
        # Только своя метка монеты. Чужую (last_signal) не подставляем - давала FOMO:? и чужие метки
        return self.signals.get(key) or "?"

    def _save_scanned_token(self, entry: dict):
        """Единый журнал сканов для радара (раньше метода не было - WSS-путь падал).
        Дедуп по mint, свежие сверху, максимум 150 записей."""
        import json as _json
        import os as _os
        try:
            entry = dict(entry or {})
            entry.setdefault("time", time.time())
            data = []
            if _os.path.exists("scanned_tokens.json"):
                with open("scanned_tokens.json", "r") as f:
                    data = _json.load(f) or []
            data = [t for t in data if t.get("mint") != entry.get("mint")]
            data.insert(0, entry)
            # Гарантируем, что частые батчи BASE/BSC не вытесняют Solana из радара дашборда
            sol_items = [t for t in data if not str(t.get("mint", "")).startswith("0x")][:90]
            evm_items = [t for t in data if str(t.get("mint", "")).startswith("0x")][:60]
            merged = sorted(sol_items + evm_items, key=lambda x: float(x.get("time", 0) or 0), reverse=True)
            with open("scanned_tokens.json", "w") as f:
                _json.dump(merged[:150], f)
        except Exception:
            pass

    def log_scan(self, symbol: str, mint: str, source: str, decision,
                 score: float = 0.0, liquidity: float = 0.0,
                 m5_change: float = 0.0, buys: int = 0, sells: int = 0):
        """Запись в радар из любой петли: что посмотрели и чем кончилось."""
        try:
            sig = self.signals.get(mint, "") or ""
            vol_24h = 0.0
            ctx = (getattr(self, "_eval_ctx", None) or {}).get(mint) or {}
            pd = ctx.get("pair_data") if isinstance(ctx, dict) else None
            if not pd and hasattr(self, "_prefetch_cache"):
                hit = self._prefetch_cache.get(mint)
                if isinstance(hit, tuple) and len(hit) == 2:
                    pd = hit[1]
            if not pd:
                try:
                    import evm_data as _ed
                    for (_ch, _ad), (_ts, _pair) in list(_ed._EVM_PREFETCH_CACHE.items()):
                        if _ad == (mint or "").lower():
                            pd = _pair
                            break
                except Exception:
                    pass
            if isinstance(pd, dict) and pd:
                real_sym = ((pd.get("baseToken") or {}).get("symbol")) or ""
                if real_sym and (not symbol or symbol == mint[:8] or symbol == mint[:6] or symbol == mint[:10]):
                    symbol = real_sym
                if not liquidity:
                    liquidity = float((pd.get("liquidity") or {}).get("usd", 0) or 0)
                if not m5_change:
                    m5_change = float((pd.get("priceChange") or {}).get("m5", 0) or 0)
                tx_m5 = ((pd.get("txns") or {}).get("m5") or {})
                if not buys:
                    buys = int(tx_m5.get("buys", 0) or 0)
                if not sells:
                    sells = int(tx_m5.get("sells", 0) or 0)
                vol_24h = float((pd.get("volume") or {}).get("h24", 0) or 0)
            elif isinstance(ctx, dict) and ctx:
                if ctx.get("symbol") and (not symbol or symbol == mint[:8] or symbol == mint[:6]):
                    symbol = ctx.get("symbol")
                if not liquidity:
                    liquidity = float(ctx.get("liq", 0) or 0)
                if not m5_change:
                    m5_change = float(ctx.get("m5", 0) or 0)
            self._save_scanned_token({
                "symbol": symbol or (mint[:6] + "..."),
                "mint": mint,
                "score": round(float(score or 0.0), 1),
                "safety": 0, "momentum": 0, "social": 0,
                "liquidity": liquidity, "vol_24h": vol_24h,
                "buys": buys, "sells": sells,
                "m5_change": m5_change,
                "source": source,
                "decision": "BUY" if decision is True else ("SKIP" if decision is False else "?"),
                "signal": sig,
                "time": time.time(),
            })
        except Exception:
            pass

        
    async def get_session(self):
        import aiohttp
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30, ttl_dns_cache=300, use_dns_cache=True)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def fetch_latest_tokens(self) -> list:
        tokens = []
        from http_client import fetch_json
        if True:
            # 1. Сканируем САМЫЕ СВЕЖИЕ бусты (token-boosts/latest/v1) — ловит новые ⚡-бусты сразу
            try:
                status, data = await fetch_json("https://api.dexscreener.com/token-boosts/latest/v1", timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener latest boosts fetch error: {e}")

            # 2. Сканируем новые профили, чтобы не пропускать свежие ракеты
            try:
                status, data = await fetch_json(config.DEXSCREENER_PROFILES, timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener profiles fetch error: {e}")

            # 3. Сканируем недавно обновлённые профили (recent-updates)
            try:
                status, data = await fetch_json("https://api.dexscreener.com/token-profiles/recent-updates/v1", timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener recent updates fetch error: {e}")

            # 4. Топовые (Boosted) монеты — в самый конец, чтобы не забивать очередь старыми мамонтами
            try:
                status, data = await fetch_json(config.DEXSCREENER_LATEST, timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener boosts fetch error: {e}")

            # 4. Сканируем свежие Community Takeovers (CTO)
            try:
                status, data = await fetch_json("https://api.dexscreener.com/community-takeovers/latest/v1", timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener CTO fetch error: {e}")
                
        # Возвращаем уникальные токены (по tokenAddress)
        seen = set()
        unique_tokens = []
        for t in tokens:
            addr = t.get("tokenAddress")
            chain = t.get("chainId")
            if addr and addr not in seen and chain == "solana":
                seen.add(addr)
                unique_tokens.append(t)
        return unique_tokens
                
    @staticmethod
    def _best_solana_pair(mint: str, pairs: list):
        """Лучший Solana-пул токена:
        1. Если у токена уже есть пулы с РЕАЛЬНОЙ ликвидностью (Raydium, Pumpswap, Meteora, Orca),
           выбираем пул с максимальной реальной ликвидностью usd.
           Мёртвый bonding curve (liq=0) никогда не перебивает живой AMM-пул.
        2. И только если токен всё ещё на bonding curve pump.fun (нет ни одного AMM-пула с liq >= 500),
           выбираем pump.fun пул с эффективной ликвидностью (fdv * 0.40).
        """
        ALLOWED_SOL_QUOTES = {
            "so11111111111111111111111111111111111111112",  # WSOL/SOL
            "epjfwdd5aufqssqem2qnxzybapc8g4weggkzwytdt1v",  # USDC
            "es9vmfrzacerjmffrff4h2fyd4kconky11mcce8benwnyb",  # USDT
        }
        ALLOWED_SOL_SYMBOLS = {"SOL", "WSOL", "USDC", "USDT"}
        sol_pairs = []
        for p in (pairs or []):
            if (p.get("chainId") or "").lower() != "solana":
                continue
            if ((p.get("baseToken") or {}).get("address") or mint) != mint:
                continue
            q_addr = str((p.get("quoteToken") or {}).get("address") or "").lower()
            q_sym = str((p.get("quoteToken") or {}).get("symbol") or "").upper()
            if q_addr and q_addr not in ALLOWED_SOL_QUOTES and q_sym not in ALLOWED_SOL_SYMBOLS:
                continue
            sol_pairs.append(p)
        if not sol_pairs:
            return None

        # 1. Если у токена уже есть AMM-пулы (pumpswap/raydium/meteora/orca), он УЖЕ мигрировал с кривой pump.fun.
        # Ни при каких условиях не возвращаемся к замороженному мёртвому пулу pumpfun ($43k FDV, кейс DUMB -99%).
        amm_pairs = [p for p in sol_pairs if p.get("dexId") not in ("pump", "pumpfun")]
        real_pairs = [p for p in amm_pairs if float(((p.get("liquidity") or {}).get("usd", 0) or 0)) >= 500]
        if real_pairs:
            best_pair = sorted(real_pairs, key=lambda p: float(((p.get("liquidity") or {}).get("usd", 0) or 0)), reverse=True)[0]
        elif amm_pairs:
            best_pair = sorted(amm_pairs, key=lambda p: float(((p.get("liquidity") or {}).get("usd", 0) or 0)), reverse=True)[0]
        else:
            # 2. Если AMM-пулов вообще нет — токен ещё на bonding curve pump.fun
            def _get_eff_liq(p):
                raw_l = float(((p.get("liquidity") or {}).get("usd", 0) or 0))
                if raw_l > 0:
                    return raw_l
                if p.get("dexId") in ("pump", "pumpfun"):
                    return float(p.get("fdv", 0) or p.get("marketCap", 0) or 0) * 0.40
                return 0.0

            best_pair = sorted(sol_pairs, key=_get_eff_liq, reverse=True)[0]
            if best_pair.get("dexId") in ("pump", "pumpfun"):
                cur_l = float(((best_pair.get("liquidity") or {}).get("usd", 0) or 0))
                if cur_l == 0:
                    eff = float(best_pair.get("fdv", 0) or best_pair.get("marketCap", 0) or 0) * 0.40
                    if not isinstance(best_pair.get("liquidity"), dict):
                        best_pair["liquidity"] = {}
                    best_pair["liquidity"]["usd"] = eff

        # Проверка рассинхрона цен между КРУПНЫМИ пулами (только пулы со сравнимой ликвидностью):
        # Исключаем заброшенные/старые микро-пулы, сравниваем только живые пулы >= 25% от топа
        if amm_pairs and len(amm_pairs) >= 2:
            _top_liq = float(((best_pair.get("liquidity") or {}).get("usd", 0) or 0))
            if _top_liq >= 5000.0:
                _comp_pairs = [p for p in amm_pairs if float(((p.get("liquidity") or {}).get("usd", 0) or 0)) >= max(4000.0, _top_liq * 0.25)]
                if len(_comp_pairs) >= 2:
                    _c_prices = [float(p.get("priceUsd", 0) or 0) for p in _comp_pairs if float(p.get("priceUsd", 0) or 0) > 0]
                    if len(_c_prices) >= 2:
                        max_px, min_px = max(_c_prices), min(_c_prices)
                        if min_px > 0 and (max_px / min_px) > 1.40:
                            best_pair["_price_divergence"] = True
                            best_pair["_price_divergence_ratio"] = max_px / min_px

        # Обогащаем реальным изменением за 1 минуту и глобальной памятью пиков (цена + ликвидность)
        try:
            px = float(best_pair.get("priceUsd", 0) or 0)
            cur_liq_obs = float(((best_pair.get("liquidity") or {}).get("usd", 0) or 0))
            if px > 0 and mint:
                now = time.time()
                if not hasattr(Analyzer, "_sol_price_obs"):
                    Analyzer._sol_price_obs = {}
                hist = Analyzer._sol_price_obs.setdefault(mint, [])
                hist.append((now, px, cur_liq_obs))
                if len(hist) > 80:
                    hist[:] = [x for x in hist if now - x[0] <= 2700.0]
                if len(Analyzer._sol_price_obs) > 4000:
                    Analyzer._sol_price_obs.clear()

                # Синхронизируемся с ShadowTracker (хранит пики отклонённых токенов с 60с точностью до 90 минут)
                sh_peak_px, sh_peak_liq = 0.0, 0.0
                try:
                    from shadow_tracker import shadow_tracker
                    sh_peak_px, sh_peak_liq = shadow_tracker.get_peak(mint)
                    shadow_tracker.record_observation(mint, px, cur_liq_obs)
                except Exception:
                    pass

                recent_px = [x[1] for x in hist if now - x[0] <= 2700.0 and x[1] > 0]
                if sh_peak_px > 0:
                    recent_px.append(sh_peak_px)
                if recent_px:
                    peak_px = max(recent_px)
                    if peak_px > 0:
                        best_pair["_drop_from_peak_5m"] = (px - peak_px) / peak_px * 100.0

                recent_liq = [x[2] for x in hist if len(x) >= 3 and now - x[0] <= 2700.0 and x[2] > 0]
                if sh_peak_liq > 0:
                    recent_liq.append(sh_peak_liq)
                if recent_liq and cur_liq_obs > 0:
                    peak_liq = max(recent_liq)
                    if peak_liq > 0:
                        best_pair["_liq_drop_from_peak"] = (cur_liq_obs - peak_liq) / peak_liq * 100.0

                cands = [(abs((now - x[0]) - 60.0), x[0], x[1]) for x in hist if 12.0 <= (now - x[0]) <= 150.0 and x[1] > 0]
                if cands:
                    cands.sort(key=lambda x: x[0])
                    old_px = cands[0][2]
                    real_m1 = (px - old_px) / old_px * 100.0
                    if not isinstance(best_pair.get("priceChange"), dict):
                        best_pair["priceChange"] = {}
                    if not best_pair["priceChange"].get("m1"):
                        best_pair["priceChange"]["m1"] = round(real_m1, 2)
        except Exception:
            pass
        return best_pair

    async def prefetch_token_data(self, mints: list, ttl: float = None) -> int:
        """Пакетная загрузка: до 30 mint'ов за ОДИН запрос DexScreener.
        Результат кладём в кэш, fetch_token_data берёт оттуда без сети.
        Было: 1 запрос на токен (100 токенов = 100 запросов → 429).
        Стало: 100 токенов = 4 запроса."""
        from http_client import fetch_json
        if not hasattr(self, "_prefetch_cache"):
            self._prefetch_cache = {}
        ttl = float(ttl if ttl is not None else getattr(config, "PREFETCH_TTL_SEC", 10))
        now = time.time()
        todo = [m for m in dict.fromkeys(mints or []) if m and not m.startswith("0x")
                and now - self._prefetch_cache.get(m, (0, None))[0] >= ttl]
        got = 0
        # Запрашиваем пачками по 15 минтов через /tokens/v1/solana/ (чтобы не упираться в лимит 30 пар у /latest/dex/tokens/)
        for i in range(0, len(todo), 15):
            chunk = todo[i:i + 15]
            pairs_list = []
            try:
                status, data = await fetch_json(
                    "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk),
                    timeout=10, retries=1)
                if status == 200 and isinstance(data, list):
                    pairs_list = data
                else:
                    status2, data2 = await fetch_json(
                        "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(chunk),
                        timeout=10, retries=1)
                    if status2 == 200 and isinstance(data2, dict):
                        pairs_list = data2.get("pairs") or []
            except Exception:
                continue
            if not pairs_list:
                continue
            by_mint = {}
            for p in pairs_list:
                a = (p.get("baseToken") or {}).get("address")
                if a:
                    by_mint.setdefault(a, []).append(p)
            ts = time.time()
            for m in chunk:
                best = self._best_solana_pair(m, by_mint.get(m, []))
                if best:
                    self._prefetch_cache[m] = (ts, best)
                    got += 1
        if len(self._prefetch_cache) > 3000:
            cutoff = time.time() - 120
            self._prefetch_cache = {k: v for k, v in self._prefetch_cache.items() if v[0] >= cutoff}
        return got

    async def fetch_token_data(self, mint: str) -> dict:
        # 1. Свежий пакетный кэш (prefetch_token_data) — мгновенно из памяти без сети
        _hit = getattr(self, "_prefetch_cache", {}).get(mint)
        if _hit and time.time() - _hit[0] < float(getattr(config, "PREFETCH_TTL_SEC", 60)):
            if not _hit[1]:
                return {}
            return dict(_hit[1])
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        from http_client import fetch_json
        try:
            status, data = await fetch_json(url, timeout=8, retries=1)
            if status == 200 and data:
                best_pair = self._best_solana_pair(mint, data.get("pairs") or [])
                if best_pair:
                    return best_pair
            return await self.fetch_token_data_gecko(mint)
        except Exception as e:
            return await self.fetch_token_data_gecko(mint)

    async def fetch_token_data_gecko(self, mint: str) -> dict:
        from http_client import fetch_json
        try:
            url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1"
            status, data = await fetch_json(url, timeout=10, retries=1,
                                            headers={"Accept": "application/json"})
            if status != 200 or not data:
                return {}
            
            pools = data.get("data", [])
            if not pools:
                return {}
            best = max(pools, key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd", 0) or 0))
            a = best.get("attributes", {})
            pc = a.get("price_change_percentage") or {}
            tx = a.get("transactions") or {}
            vu = a.get("volume_usd") or {}
            created = a.get("pool_created_at")
            created_ms = 0
            if created:
                from datetime import datetime
                created_ms = int(datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() * 1000)
            symbol = (a.get("name") or "UNKNOWN").split("/")[0].strip()

            def _tx(key):
                t = tx.get(key) or {}
                return {"buys": int(t.get("buys", 0) or 0), "sells": int(t.get("sells", 0) or 0)}

            print(f"🦎 GeckoTerminal fallback для {mint[:8]}: пул найден.")
            return {
                "baseToken": {"symbol": symbol, "name": symbol},
                "priceUsd": str(a.get("base_token_price_usd") or 0),
                "priceChange": {"m5": float(pc.get("m5") or 0), "m1": 0.0,
                                "h1": float(pc.get("h1") or 0), "h24": float(pc.get("h24") or 0)},
                "txns": {"m5": _tx("m5"), "h1": _tx("h1"), "h24": _tx("h24")},
                "volume": {"m5": float(vu.get("m5") or 0), "h1": float(vu.get("h1") or 0),
                           "h24": float(vu.get("h24") or 0)},
                "liquidity": {"usd": float(a.get("reserve_in_usd") or 0)},
                "fdv": float(a.get("fdv_usd") or a.get("market_cap_usd") or 0),
                "pairCreatedAt": created_ms,
                "dexId": "pump" if mint.endswith("pump") else "raydium",
                "info": {"socials": [], "websites": []},
            }
        except Exception as e:
            print(f"GeckoTerminal token data error: {type(e).__name__} {e}")
            return {}

    async def _fetch_dex_search(self, symbol: str):
        import time
        now = time.time()
        cache_key = symbol.upper()
        if cache_key in getattr(self, '_clone_cache', {}) and now - self._clone_cache_time.get(cache_key, 0) < 600:
            return self._clone_cache[cache_key]
            
        url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
        from http_client import fetch_json
        try:
            status, data = await fetch_json(url, timeout=8, retries=1)
            if status == 200 and data:
                if not hasattr(self, '_clone_cache'):
                    self._clone_cache = {}
                    self._clone_cache_time = {}
                self._clone_cache[cache_key] = data
                self._clone_cache_time[cache_key] = now
                return data
        except Exception:
            pass
        return None



    async def is_clone(self, symbol: str, current_mint: str, current_created_at: int, current_fdv: float, current_liq: float = 0.0) -> bool:
        """Проверяет, является ли этот токен фейковой копией (клоном) более старого/крупного оригинала или серийным спамом (кейсы WHITEWHALE, MOE)."""
        if not symbol or len(symbol) < 2:
            return False

        data = await self._fetch_dex_search(symbol)
        if not data:
            return False

        pairs = data.get("pairs") or []
        sym_up = symbol.upper().strip()
        active_other_mints = set()
        swarm_mints = set()
        oldest_other_ts = float("inf")

        for p in pairs:
            if p.get("chainId") != "solana":
                continue
            p_symbol = str((p.get("baseToken") or {}).get("symbol") or "").upper().strip()
            p_mint = str((p.get("baseToken") or {}).get("address") or "").strip()
            if not p_mint or p_symbol != sym_up or p_mint == current_mint:
                continue

            p_created_at = float(p.get("pairCreatedAt") or float("inf"))
            p_fdv = float(p.get("fdv") or p.get("marketCap") or 0.0)
            p_liq = float((p.get("liquidity") or {}).get("usd", 0) or 0.0)
            p_vol24 = float((p.get("volume") or {}).get("h24", 0) or 0.0)
            if p_liq < 5000 and p_vol24 < 8000:
                continue  # Мертвый/заброшенный пул, не активный оригинал

            # Если наш токен — явный доминант по ликвидности (>= $25k и крупнее соперника),
            # мелкие копии скамеров не могут сделать нашего лидера "клоном" (кейс SWORDCAT $261k liq)
            if current_liq >= 25000.0 and current_liq >= p_liq * 1.3:
                continue

            # Учитываем живые пулы с таким же тикером для детекта серийного клон-роя (кейс ARACH: 4 микро-пула одновременно на PumpSwap)
            # Срабатывает только если наш токен еще мелкий (current_liq < $25k)
            if current_liq < 25000.0:
                swarm_mints.add(p_mint)
                if len(swarm_mints) >= 2:
                    print(f"🚫 [CLONE-SWARM] {sym_up} ({current_mint[:8]}): обнаружена серийная атака клонов ({len(swarm_mints)+1}+ активных токенов {sym_up} одновременно) — блок!")
                    return True

            # Если другой живой пул с тем же тикером уже заметно крупнее по ликвидности (>= $15k и в 1.3x+ больше нашего)
            if p_liq >= 15000 and current_liq > 0 and p_liq > (current_liq * 1.3):
                return True

            p_dex = str(p.get("dexId") or "").lower()
            # Игнорируем фейковые кастомные не-pump пулы на PumpSwap как "оригинал" для одиночного сравнения, если они мельче
            if p_dex == "pumpswap" and not p_mint.endswith("pump"):
                continue

            if p_liq < 8000 and p_vol24 < 10000:
                continue

            active_other_mints.add(p_mint)
            if p_created_at < oldest_other_ts:
                oldest_other_ts = p_created_at

            # 1. Кастомный (не-pump) токен мимикрирует под существующий живой pump.fun токен (кейсы MOE 7oocXA2D, Chubby FaCjQic5, WHITEWHALE)
            if not current_mint.endswith("pump") and p_mint.endswith("pump") and (p_liq >= 8000 or p_fdv >= 25000):
                return True

            # 2. Существует БОЛЕЕ СТАРЫЙ (>1 мин раньше) живой оригинал с ликвидностью >= $10k и FDV >= $35k
            if current_created_at and p_created_at < (current_created_at - 60000) and p_liq >= 10000 and p_fdv >= 35000:
                return True

            # 3. Существует более старый и значительно более крупный оригинал (в 5x+ крупнее по FDV >= $250k)
            if p_created_at < current_created_at and current_fdv > 0 and p_fdv > (current_fdv * 5) and p_fdv >= 250000:
                return True

        return False

    async def check_rugcheck(self, mint: str) -> bool:
        url = config.RUGCHECK_API.format(mint=mint)
        from http_client import fetch_json
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
        if True:
            try:
                status, data = await fetch_json(url, headers=headers, timeout=8, retries=1)
                if status == 200 and data:
                    score = data.get("score", 1000)
                    if score >= 400:
                        return False

                    token_info = data.get("token", {})
                    if token_info.get("mintAuthority") is not None:
                        return False
                    if token_info.get("freezeAuthority") is not None:
                        return False

                    name = token_info.get("name", "").lower()
                    symbol = token_info.get("symbol", "").lower()
                    bad_words = ["test", "scam", "fuck", "nigger", "pump and dump", "rug"]
                    if any(w in name for w in bad_words) or any(w in symbol for w in bad_words):
                        return False

                    top_holders = data.get("topHolders", [])
                    hhi_index = sum([(h.get("pct", 0) * 100) ** 2 for h in top_holders[:15] if not h.get("isContract", False)])

                    top_10_pct = sum([h.get("pct", 0) for h in top_holders[:10] if not h.get("isContract", False)])

                    if top_10_pct >= 75 or hhi_index > 4000:
                        return False

                    return True
                print(f"⚠️ RugCheck HTTP {status} для {mint}. Fail-Closed: пропускаем подозрительный токен.")
                return False
            except Exception as e:
                print(f"⚠️ RugCheck fetch error ({type(e).__name__}): {e}. Fail-Closed: пропускаем подозрительный токен.")
                return False

    async def get_helius_transaction_metrics(self, mint: str) -> tuple:
        """ Возвращает (unique_buyers_m5, smart_money_inflow) """
        unique_buyers = 0
        smart_money_inflow = 0
        
        smart_wallets = set()
        import os
        if os.path.exists("smart_wallets.txt"):
            with open("smart_wallets.txt", "r") as f:
                smart_wallets = {line.strip() for line in f if line.strip()}
                
        payload_sigs = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": 30}]
        }
        
        import aiohttp
        session = await self.get_session()
        if True:
            try:
                async with session.post(config.HELIUS_RPC_URL, json=payload_sigs, timeout=3) as resp:
                    data = await resp.json()
                    signatures = [item["signature"] for item in data.get("result", [])]
                
                if not signatures:
                    return 0, 0
                    
                payload_txs = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransactions",
                    "params": [signatures, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                }
                async with session.post(config.HELIUS_RPC_URL, json=payload_txs, timeout=5) as resp:
                    tx_data = await resp.json()
                    transactions = tx_data.get("result", [])
                    
                    buyers = set()
                    for tx in transactions:
                        if not tx or not tx.get("transaction"):
                            continue
                            
                        account_keys = tx["transaction"]["message"]["accountKeys"]
                        for acc in account_keys:
                            if acc.get("signer"):
                                pubkey = acc.get("pubkey")
                                buyers.add(pubkey)
                                if pubkey in smart_wallets:
                                    smart_money_inflow += 1
                                break 
                                
                    unique_buyers = len(buyers)
                    
            except Exception as e:
                print(f"⚠️ Ошибка Helius RPC при парсинге транзакций: {e}")
                
        return unique_buyers, smart_money_inflow

    def check_hyper_rocket_momentum(self, pair_data: dict) -> bool:
        """
        VIP-полоса для Гипер-Ракет:
        Ищет аномальные всплески покупок (>50 покупок) и объема (>$30,000) в первые 5 минут.
        Позволяет пропустить стандартные жесткие фильтры.
        """
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
        volume_m5 = pair_data.get("volume", {}).get("m5", 0)
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        
        # Защита от накрутки (wash-trading): требуем реальную ликвидность, перевес покупателей и зелёный импульс
        _min_tx = getattr(config, "VIP_MIN_TX_M5", 40)
        _min_vol = getattr(config, "VIP_MIN_VOL_M5", 15000)
        _pc = pair_data.get("priceChange") or {}
        _m5 = float(_pc.get("m5", 0) or 0)
        _m1 = float(_pc.get("m1", 0) or 0)
        _h1 = float(_pc.get("h1", 0) or 0)
        _h6 = float(_pc.get("h6", 0) or 0)
        if _h1 < -8.0 or _h6 < -15.0:
            return False
        if (buys_m5 >= _min_tx and volume_m5 >= _min_vol and liquidity >= 12000 
                and sells_m5 > 0 and buys_m5 >= sells_m5 * 1.2 and _m5 >= 3.0 and _m1 >= -2.0):
            return True
        return False
        
    async def analyze_token(self, mint: str) -> bool:
        self.last_signal = ""
        self.last_score = 0.0
        self.signals.pop(mint, None)
        self._last_top10 = 0.0
        self._last_dev = 0.0
        
        if mint in ["So11111111111111111111111111111111111111112", 
                    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 
                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]:
            return False

        pair_data = await self.fetch_token_data(mint)
        if not pair_data:
            return None
            
        base_token = pair_data.get("baseToken", {})
        name = base_token.get("name", "").upper()
        symbol = base_token.get("symbol", "").upper()

        if not hasattr(self, "_eval_ctx"):
            self._eval_ctx = {}
        self._eval_ctx[mint] = {
            "symbol": symbol,
            "chain": "solana",
            "price": float(pair_data.get("priceUsd", 0) or 0),
            "liq": float((pair_data.get("liquidity") or {}).get("usd", 0) or 0),
            "m5": float((pair_data.get("priceChange") or {}).get("m5", 0) or 0),
            "source": "SOLANA_SCAN"
        }

        if pair_data.get("_price_divergence"):
            _div_r = float(pair_data.get("_price_divergence_ratio", 0.0) or 0.0)
            return self._deny(mint, "price-split", f"🚫 [PRICE-SPLIT] {symbol} ({mint[:8]}): рассинхрон цен между пулами {_div_r:.1f}x (миграция/гэп) — блок входа.")

        # === PEAK-MEMORY GUARD: запрет покупки после слива с уже виденного пика (HOBBES -51%, BUGMAGEDDON -20%, CHUNKEE -16%) ===
        _drop_from_peak = float(pair_data.get("_drop_from_peak_5m", 0.0) or 0.0)
        _liq_drop_peak = float(pair_data.get("_liq_drop_from_peak", 0.0) or 0.0)
        if _drop_from_peak <= -18.0:
            return self._deny(mint, f"post-peak-crash {_drop_from_peak:.0f}%", f"🚫 [POST-PEAK-CRASH] {symbol} ({mint[:8]}): цена уже упала на {_drop_from_peak:.1f}% от виденного ботом пика — отскок дохлой кошки после дампа.")
        if _liq_drop_peak <= -18.0:
            return self._deny(mint, f"post-peak-liq-drain {_liq_drop_peak:.0f}%", f"🚫 [LIQ-DRAIN] {symbol} ({mint[:8]}): ликвидность пула уже сдулась на {_liq_drop_peak:.1f}% от пика — снайперы/дев выводят SOL.")
        
        corporate_scam_keywords = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "XAI", "NEURALINK", "PAYPAL",
                                  "NVDA", "GOOG", "AMZN", "META", "NFLX", "ANTHROPIC", "OPENAI", "PERPLEXITY"]
        if any(keyword in symbol for keyword in corporate_scam_keywords) or any(keyword in name for keyword in corporate_scam_keywords):
            if not self._brand_whitelisted(mint):
                return self._deny(mint, "mimic-corp-scam", f"🚫 Мусор: Токен {symbol} мимикрирует под корпоративный бренд. Это 100% scam.")

        meme_keywords = ["PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN", "ELON", "MUSK",
                         "CLAUDE", "CHATGPT", "DEEPSEEK", "GROK", "GEMINI"]
        if any(keyword in symbol for keyword in meme_keywords) or any(keyword in name for keyword in meme_keywords):
            if not self._brand_whitelisted(mint):
                _tx5_mc = (pair_data.get("txns") or {}).get("m5", {}) or {}
                _b5_mc = int(_tx5_mc.get("buys", 0) or 0)
                _s5_mc = int(_tx5_mc.get("sells", 0) or 0)
                _v5_mc = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                _lq_mc = float((pair_data.get("liquidity", {}) or {}).get("usd", 0) or 0)
                # Если у мем-токена есть мощная органика — это вирусный тренд, разрешаем вход!
                if (_b5_mc >= 20 and _b5_mc >= _s5_mc) or _v5_mc >= 8000.0 or _lq_mc >= 15000.0:
                    print(f"🔥 [MIMIC-FOMO] {symbol} ({mint[:8]}): мощный органический FOMO мем-бренда (b/s {_b5_mc}/{_s5_mc}, vol5 ${_v5_mc:.0f}, liq ${_lq_mc:.0f}) — пропуск.")
                else:
                    return self._deny(mint, "mimic-scam", f"🚫 Мусор: Токен {symbol} мимикрирует под известный мем без органики (b/s {_b5_mc}/{_s5_mc}, vol5 ${_v5_mc:.0f}).")
            
        _created_pre = pair_data.get("pairCreatedAt") or 0
        _age_min_pre = (time.time() * 1000 - _created_pre) / 60000.0 if _created_pre else 999
        _sol_min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 1.5))
        if _age_min_pre < _sol_min_age:
            print(f"⏳ [ENTRY] {mint[:8]}: возраст {_age_min_pre:.1f}м < {_sol_min_age}м — flash-rug зона дева, ждём созревания.")
            return None

        is_vip = self.check_hyper_rocket_momentum(pair_data) and getattr(config, "VIP_ENTRIES_ENABLED", False)

        # === VIP OVERHEAT GUARD: не покупаем вершину вертикали, но пускаем первую волну (h1 < 250%) ===
        if is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            _m1 = _pc.get("m1", 0) or 0
            _h1_vip = float(_pc.get("h1", 0) or 0)
            _liq_v = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
            _vol_v = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
            _tx_m5_v = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _b_v = int(_tx_m5_v.get("buys", 0) or 0)
            _s_v = int(_tx_m5_v.get("sells", 0) or 0)
            _is_fresh_vip = mint.endswith("pump") or _age_min_pre < 90.0
            _max_vip_h1 = 3500.0 if (_is_fresh_vip and _m5 >= 2.5) else 400.0
            if _h1_vip > _max_vip_h1:
                return self._deny(mint, "vip-h1-exhausted", f"🚫 [VIP EXHAUSTION] {mint}: h1 {_h1_vip:+.0f}% > +{_max_vip_h1:.0f}% — памп уже отработал, вход = вершина.")
            _has_strong_buyers = (_b_v >= int(_s_v * 1.05) and _b_v >= 20 and _vol_v >= 10000)
            if _m1 <= 22.0 and _has_strong_buyers:
                _max_vip_m5 = 120.0  # Пробой ракеты
            else:
                _max_vip_m5 = 70.0
            if _m5 > _max_vip_m5:
                return self._deny(mint, "vip-overheat", f"🚫 [VIP OVERHEAT] {mint}: m5 {_m5:+.0f}% > +{_max_vip_m5:.0f}% — вертикаль уже прошла, вход = вершина.")
            if _m1 < -2.5:
                return self._deny(mint, "vip-reversal", f"🚫 [VIP REVERSAL] {mint}: m1 {_m1:+.1f}% — резкий откат за 1м, ждём pullback.")
            _max_vip_m1 = 22.0
            if _m1 > _max_vip_m1:
                return self._deny(mint, "vip-spike", f"🚫 [VIP SPIKE] {mint}: m1 {_m1:+.1f}% > +{_max_vip_m1:.0f}% — свеча-spike в моменте, вход = вершина. Ждём.")
            _min_vip_m5 = 2.5 if _liq_v >= 25000.0 else float(getattr(config, "VIP_MIN_M5_PCT", 3.0))
            if _m5 < _min_vip_m5:
                return self._deny(mint, "vip-flat", f"🚫 [VIP FLAT] {mint}: объём есть, а импульса нет (m5 {_m5:+.1f}% < +{_min_vip_m5:.1f}%) — флет.")

        _lottery = False
        if pair_data:
            _dex_pre = str(pair_data.get("dexId", "")).lower()
            if _dex_pre not in ("pump", "pumpfun"):
                _fdv_pre = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                _pc_pre = pair_data.get("priceChange") or {}
                _h1_pre = float(_pc_pre.get("h1", 0) or 0)
                _h6_pre = float(_pc_pre.get("h6", 0) or 0)
                _vm5_check = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                _m5_check = float(_pc_pre.get("m5", 0) or 0)
                if mint.endswith("pump") and 0 < _fdv_pre < 30000:
                    return self._deny(mint, "post-migration-dump-fdv", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: мигрировавший токен FDV ${_fdv_pre:,.0f} < $30k — слит ниже уровня миграции.")
                elif 0 < _fdv_pre < 15000 and not (_m5_check >= 5.0 and _vm5_check >= 2500.0):
                    return self._deny(mint, "post-migration-dump-fdv", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: AMM FDV ${_fdv_pre:,.0f} < $15k без импульса — слитый токен.")
                if _age_min_pre >= 60.0 and ((_fdv_pre < 35000 and _h1_pre < -5.0) or (_h1_pre < -12.0 or _h6_pre < -25.0)):
                    return self._deny(mint, "post-migration-dump-trend", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: часовой/6ч тренд в глубоком сливе (h1 {_h1_pre:+.1f}%, h6 {_h6_pre:+.1f}%) — отскок дохлой кошки.")
                elif _age_min_pre < 60.0 and (_h1_pre < -65.0 or (_h1_pre < -30.0 and float((_pc_pre.get("m5", 0) or 0)) < 5.0)):
                    return self._deny(mint, "post-migration-dump-young", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: молодой токен в глубоком сливе (h1 {_h1_pre:+.1f}%) без импульса отскока.")
        if not is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            _m1 = _pc.get("m1", 0) or 0
            _h1 = _pc.get("h1", 0) or 0
            # h1-exhausted должен резать ТОЛЬКО старые токены (>90м) или монеты без импульса (m5 <= 0).
            # Для свежих выпускников pump.fun (<90м) h1 +500%..+3500% — это нормальный первичный памп кривой!
            _is_fresh_pump = mint.endswith("pump") or _age_min_pre < 90.0
            _max_h1_limit = 3500.0 if (_is_fresh_pump and _m5 >= 2.5) else 400.0
            if _h1 > _max_h1_limit:
                return self._deny(mint, f"h1-exhausted {_h1:+.0f}%", f"🚫 [H1-EXHAUSTED] {mint[:8]}: h1 {_h1:+.0f}% > +{_max_h1_limit:.0f}% — памп уже отработал, вход на хаях запрещён.")

            # Запрет покупки вершины, но пропуск реальных ракет и пробоев
            _chain_ae = str((pair_data or {}).get("chainId", "solana")).lower()
            _txm5_tmp = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _b5_tmp, _s5_tmp = int(_txm5_tmp.get("buys", 0) or 0), int(_txm5_tmp.get("sells", 0) or 0)
            if _age_min_pre <= 15.0 and _b5_tmp >= _s5_tmp:
                # Молодая ракета на первичном старте (кейсы SOLCHAN +102%, 4AGENT +168%)
                _max_m5_entry = 250.0
            elif _age_min_pre <= 45.0 and _b5_tmp >= _s5_tmp * 1.05 and _m1 <= 25.0:
                _max_m5_entry = 180.0
            elif _b5_tmp >= 12 and _b5_tmp >= _s5_tmp * 1.05 and _m1 <= 25.0:
                _max_m5_entry = 120.0
            elif _age_min_pre < 45.0 and _m1 <= 22.0:
                _max_m5_entry = 95.0
            else:
                _max_m5_entry = 70.0
            if _m5 > _max_m5_entry:
                return self._deny(mint, f"m5-late {_m5:+.0f}%", f"🚫 [LATE ENTRY] {mint[:8]}: m5 {_m5:+.1f}% > {_max_m5_entry:.0f}%. Опоздание на памп — риск дампа ~90%.")
            _tx = (pair_data.get("txns") or {}).get("h1", {}) or {}
            _b, _s = _tx.get("buys", 0) or 0, _tx.get("sells", 0) or 0
            _v24 = (pair_data.get("volume") or {}).get("h24", 0) or 0
            _txm5_pre = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _vm5_pre = (pair_data.get("volume") or {}).get("m5", 0) or 0
            if (_txm5_pre.get("buys", 0) + _txm5_pre.get("sells", 0)) == 0 and _vm5_pre == 0:
                if _age_min_pre < 2.0:
                    print(f"⏳ [ENTRY] {mint}: возраст {_age_min_pre:.1f} мин, m5-окно API ещё пустое — повторю позже.")
                    return None
                else:
                    print(f"💀 [ENTRY] {mint}: возраст {_age_min_pre:.1f} мин, 0 сделок в m5 — мёртвый токен.")
                    try:
                        from birth_tracker import birth_tracker
                        birth_tracker.remove_token(mint)
                    except Exception:
                        pass
                    self._deny(mint, "dead-token", f"💀 {mint}: мёртвый токен")
                    return False
            _dex_lower = str(pair_data.get("dexId", "")).lower()
            _is_pump_curve = _dex_lower in ("pump", "pumpfun")
            # Защита от зомби: отсекаем только реально стоячие старые монеты без импульса
            if not _is_pump_curve and _age_min_pre > 60.0 and _m5 < 2.5 and _h1 < 15.0:
                return self._deny(mint, "too-old-slow", f"🚫 [ZOMBIE] {mint[:8]}: возраст {_age_min_pre:.0f}м > 60м, но импульс слабый ({_m5:+.1f}% < +2.5%)")
            _max_age = getattr(config, "SOLANA_MAX_AGE_MIN", 240.0)
            if _age_min_pre > _max_age and _m5 < 3.5:
                return self._deny(mint, "zombie-age", f"🚫 [ZOMBIE-AGE] {mint[:8]}: возраст {_age_min_pre:.0f}м > {_max_age:.0f}м, m5 {_m5:+.1f}% < +3.5% — слабый импульс для старого токена.")
            if _age_min_pre > 1440.0 and _m5 < 5.0:
                return self._deny(mint, "zombie-ancient", f"🚫 [ZOMBIE-ANCIENT] {mint[:8]}: возраст {_age_min_pre:.0f}м (>24ч), m5 {_m5:+.1f}% < +5% — старый токен без пробоя.")
            _cur_liq_pre = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
            if _cur_liq_pre == 0 and _is_pump_curve:
                _cur_liq_pre = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0) * 0.40
            _b5_pre = int(_txm5_pre.get("buys", 0) or 0)
            _s5_pre = int(_txm5_pre.get("sells", 0) or 0)
            _min_run_liq = 4000 if _is_pump_curve else (8000 if _age_min_pre < 45.0 else 15000)
            _min_run_vol = 3500 if _is_pump_curve else (4000 if _age_min_pre < 45.0 else 10000)
            _is_runner = (
                _cur_liq_pre >= _min_run_liq and 
                _vm5_pre >= _min_run_vol and 
                (_b5_pre + _s5_pre) >= 15 and 
                (_b5_pre >= _s5_pre * 1.10 or _vm5_pre >= 25000)
            )

            _lottery_enabled = getattr(config, "LOTTERY_ENABLED", False)
            if _m5 >= getattr(config, "LOTTERY_MIN_M5_PCT", 0.60) * 100:
                if _is_runner:
                    # Настоящая ракета с высокой ликвидностью и объемом (не микро-лотерейный раг)
                    _m1_max = min(25.0, getattr(config, "PULLBACK_M1_MAX_PCT", 0.25) * 100)
                    if _m1 > _m1_max:
                        return self._deny(mint, "runner-m1-spike", f"🚫 [RUNNER-SPIKE] {mint}: m1 {_m1:+.1f}% > +{_m1_max:.0f}% — свеча-прострел в моменте, ждём откат.")
                    if _m1 < getattr(config, "PULLBACK_M1_MIN_PCT", -0.15) * 100:
                        return self._deny(mint, "runner-m1-dump", f"🚫 [RUNNER-DUMP] {mint}: m1 {_m1:+.1f}% — импульс схлопнулся, это дамп.")
                    if _m5 > 250.0:
                        return self._deny(mint, "runner-m5-overheat", f"🚫 [RUNNER-EXHAUSTION] {mint}: m5 {_m5:+.0f}% > +250% — экстремальный перегрев 5м свечи.")
                    _max_h1_pct = getattr(config, "PULLBACK_MAX_H1_PCT", 25.0) * 100
                    if _h1 > _max_h1_pct:
                        return self._deny(mint, "runner-h1-gone", f"🚫 [ENTRY] {mint}: h1 {_h1:+.0f}% — уже улетел, поздно.")
                    print(f"🚀 [RUNNER-MOMENTUM] {mint}: мощный импульс m5 {_m5:+.0f}%, m1 {_m1:+.1f}%, liq ${_cur_liq_pre:,.0f}, vol5 ${_vm5_pre:,.0f} — ракета допущена к отбору.")
                elif _lottery_enabled:
                    if _m1 < 0:
                        return self._deny(mint, "lottery-m1-reversal", f"🚫 [LOTTERY] {mint}: m5 {_m5:+.0f}%, но m1 {_m1:+.1f}% — вертикаль откатывает, это вершина.")
                    print(f"🎰 [LOTTERY] {mint}: вертикаль m5 {_m5:+.0f}%, m1 {_m1:+.1f}% — кандидат на вход уменьшенным сайзом.")
                    _lottery = True
                else:
                    return self._deny(mint, "lottery-blocked", f"🚫 [LOTTERY-BLOCKED] {mint}: тонкая монета (liq ${_cur_liq_pre:,.0f}, vol5 ${_vm5_pre:,.0f}) при m5 {_m5:+.0f}% — микро-лотерея отключена.")
            else:
                # === РЕАЛИСТИЧНЫЕ ФИЛЬТРЫ ДЛЯ DEXSCREENER (золотая середина импульса) ===
                # 1. Минимальный импульс m5 (не входим в мертвые токены)
                _min_m5 = getattr(config, "PULLBACK_MIN_M5_PCT", 0.025) * 100
                if _m5 < _min_m5:
                    return self._deny(mint, f"m5-flat {_m5:+.1f}%", f"🚫 [ENTRY] {mint}: m5 {_m5:+.1f}% < {_min_m5:.1f}% — импульса нет.")
                # 2. Максимальный импульс m5 (НЕ ВХОДИМ НА ВЕРШИНЕ!)
                _max_m5 = getattr(config, "PULLBACK_MAX_M5_PCT", 1.20) * 100
                if _m5 > _max_m5:
                    return self._deny(mint, f"m5-late {_m5:+.0f}%", f"🚫 [LATE] {mint}: m5 {_m5:+.1f}% > +{_max_m5:.0f}% — ракета уже улетела, ждем откат.")
                # 3. Минутный импульс (если DexScreener отдал m1)
                if _m1 != 0:
                    _m1_max = getattr(config, "PULLBACK_M1_MAX_PCT", 0.40) * 100
                    if _m1 > _m1_max:
                        return self._deny(mint, f"m1-vertical {_m1:+.1f}%", f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% > +{_m1_max:.0f}% — вертикаль в моменте, вход = вершина.")
                    if _m1 < getattr(config, "PULLBACK_M1_MIN_PCT", -0.05) * 100:
                        return self._deny(mint, f"m1-dump {_m1:+.1f}%", f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% — падающий нож.")
                # 4. Часовой тренд (не входить в уже улетевшие или падающие)
                _max_h1 = getattr(config, "PULLBACK_MAX_H1_PCT", 3500.0)
                if _h1 > _max_h1:
                    return self._deny(mint, f"h1-gone {_h1:+.0f}%", f"🚫 [ENTRY] {mint}: h1 {_h1:+.0f}% > +{_max_h1:.0f}% — уже улетел.")
                if _h1 < -15.0:
                    return self._deny(mint, f"h1-dump {_h1:+.1f}%", f"🚫 [ENTRY] {mint}: h1 {_h1:+.1f}% — глубокий слив.")
                # 5. Объем и сделки в m5 (не входим в тихие пулы/накрутку)
                _tx_m5_p = (pair_data.get("txns") or {}).get("m5", {}) or {}
                _b5_p = int(_tx_m5_p.get("buys", 0) or 0)
                _s5_p = int(_tx_m5_p.get("sells", 0) or 0)
                _vol5_p = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                _min_vol = getattr(config, "MIN_VOLUME_M5", 2500)
                _min_tx = getattr(config, "MIN_TXNS_M5", 12)
                _min_ratio = getattr(config, "MIN_BUY_SELL_RATIO", 1.15)
                if _vol5_p < _min_vol:
                    return self._deny(mint, f"vol5-low ${_vol5_p:.0f}", f"🚫 [ENTRY] {mint}: объем m5 ${_vol5_p:.0f} < ${_min_vol} — интерес угас.")
                if (_b5_p + _s5_p) < _min_tx:
                    return self._deny(mint, f"tx5-low {_b5_p+_s5_p}<{_min_tx}", f"🚫 [ENTRY] {mint}: сделок m5 {_b5_p+_s5_p} < {_min_tx} — слишком тихо.")
                _eff_min_ratio = 1.02 if (_vol5_p >= 20000.0 or _cur_liq_pre >= 40000.0) else _min_ratio
                if _s5_p > 0 and (_b5_p / _s5_p) < _eff_min_ratio:
                    return self._deny(mint, f"bs-low {_b5_p}/{_s5_p}", f"🚫 [ENTRY] {mint}: покупки/продажи m5 {_b5_p}/{_s5_p} < {_eff_min_ratio:.2f}x — продавцы давят.")
                # 6. Защита от накрутки (средний чек < $10 — боты, либо аномальный wash-churn)
                _avg_tx = _vol5_p / max(_b5_p + _s5_p, 1)
                if (_b5_p + _s5_p) >= 20 and _avg_tx < 10.0:
                    return self._deny(mint, "wash-bot", f"🚫 [WASH] {mint}: средний чек ${_avg_tx:.1f} < $10 — накрутка ботов.")
                _cur_liq_cand = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
                if _cur_liq_cand > 0 and (_vol5_p / _cur_liq_cand) > 4.5 and _age_min_pre < 30.0:
                    return self._deny(mint, "wash-churn", f"🚫 [WASH-CHURN] {mint[:8]}: оборот {_vol5_p / _cur_liq_cand:.1f}x ликвидности за 5м в молодом токене ({_age_min_pre:.1f}м) — ловушка накрутки дева.")
                if _s5_p >= 35 and (_b5_p / _s5_p) > 3.8 and _vol5_p >= 40000 and _age_min_pre < 30.0:
                    return self._deny(mint, "fake-buy-wall", f"🚫 [FAKE-BUY-WALL] {mint[:8]}: аномальный перекос b/s {_b5_p / _s5_p:.1f}x при объёме ${_vol5_p:,.0f} — ловушка дева.")
                print(f"✅ [ENTRY-CANDIDATE] {mint}: m5 {_m5:+.1f}%, m1 {_m1:+.1f}%, h1 {_h1:+.0f}%, b/s {_b5_p}/{_s5_p}, vol5 ${_vol5_p:.0f}")
            _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _b5, _s5 = _txm5.get("buys", 0) or 0, _txm5.get("sells", 0) or 0
            if _s > 0 and _b < _s * 1.1 and not (_m5 >= 3.0 and _s5 > 0 and _b5 >= _s5 * 1.2):
                return self._deny(mint, "no-buyer-pressure-h1", f"🚫 [ENTRY] {mint}: buys {_b} / sells {_s} — нет давления покупателей.")
            if _s == 0 and _b > 10:
                return self._deny(mint, "honeypot-zero-sells", f"🚫 [HONEYPOT] {mint}: {_b} покупок и 0 продаж! Очевидный скам/honeypot.")

            # Проверка суточного объёма: не душим свежие ракеты с подтверждённым 5м-объёмом
            if _vm5_pre < 2000 and not (_m5 >= 8.0 and _b5 >= 12):
                _v24_min = 1000 if _age_min_pre < 30 else (2500 if _age_min_pre < 90 else 8000)
                if _v24 < _v24_min:
                    return self._deny(mint, f"vol24h-low ${_v24:.0f}", f"🚫 [ENTRY] {mint}: vol24h ${_v24:,.0f} < ${_v24_min:,.0f} (vol5m ${_vm5_pre:,.0f}) — совсем нет объёма.")

            _tx_min = 6 if (_m5 >= 15.0 or _lottery or is_vip or _age_min_pre < 30) else (10 if _age_min_pre < 60 else 15)
            if (_b5 + _s5) < _tx_min:
                return self._deny(mint, f"txns5m-low {_b5+_s5}<{_tx_min}", f"🚫 [VELOCITY] {mint}: txns m5 {_b5 + _s5} < {_tx_min} — слишком медленно, нет органического FOMO.")
            if _s5 > 0:
                mult = 1.0
                if _b5 < _s5 * mult:
                    return self._deny(mint, f"bs-ratio-low {_b5}/{_s5}", f"🚫 [VELOCITY] {mint}: buy/sell m5 {_b5}/{_s5} < {mult}x — {'(лотерея, ослаблено)' if _lottery else 'нет буфера покупателей'}")
            _cur_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
            if _cur_liq == 0 and str(pair_data.get("dexId", "")).lower() in ("pump", "pumpfun"):
                _cur_liq = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0) * 0.40
            if not _lottery and self.is_churn(_b5, _s5, _m5, liq=_cur_liq):
                return self._deny(mint, "churn-5m", f"🚫 [CHURN] {mint}: {_b5 + _s5} сделок, b/s {_b5 / _s5:.2f}, m5 {_m5:+.1f}% — накрутка, не FOMO.")
            _socials = (pair_data.get("info") or {}).get("socials") or []
            _websites = (pair_data.get("info") or {}).get("websites") or []
            _socials_unknown = bool(pair_data.get("_socials_unknown", False))
            _created = pair_data.get("pairCreatedAt") or 0
            if _created and not _socials_unknown:
                _age_h = (time.time() * 1000 - _created) / 3.6e6
                # Для pump.fun токенов socials хостятся на pump.fun, DexScreener не всегда индексирует их в первые минуты
                _is_organic_flow = (_b5 >= 25 and _b5 >= _s5 and _vm5_pre >= 3000.0)
                if not mint.endswith("pump") and not _is_organic_flow and _age_h < 6 and isinstance(_socials, list) and len(_socials) == 0 and len(_websites) == 0 and not _lottery and _cur_liq < 35000:
                    return self._deny(mint, "no-socials", f"🚫 [SOCIAL] {mint}: 0 соцсетей/сайта при возрасте {_age_h:.1f}ч и ликве ${_cur_liq:,.0f} (<$35k) — анонимный бандлер-раг.")
            print(f"✅ [ENTRY-CANDIDATE] {mint}: PULLBACK — импульс m5 {_m5:+.1f}%, откат m1 {_m1:+.1f}%, h1 {_h1:+.0f}%, b/s {_b}/{_s}. Кандидат на вход.")
        
        # Защита от микро-пулов:
        # Для Pump.fun бондинг-кёрва DexScreener не создаёт AMM-пул (пишет $0), но кёрв обеспечен
        # виртуальными резервами SOL (~40% от FDV/MarketCap).
        # Как только токен перешел на AMM (Raydium, Pumpswap, Meteora), требуется РЕАЛЬНАЯ ликвидность >= $12k!
        dex_id = str(pair_data.get("dexId", "")).lower()
        is_pump_curve = dex_id in ("pump", "pumpfun")
        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv_mc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)

        if is_pump_curve:
            liquidity = max(raw_liq, fdv_mc * 0.40)
            _min_liq = 2000  # Для закрытого Pump.fun кёрва $2k ликвы достаточно для ордера $15
        else:
            liquidity = raw_liq
            _min_liq = 8000 if (_age_min_pre < 30 or _m5 >= 15.0 or _lottery or is_vip) else getattr(config, "MIN_LIQUIDITY", 15000)

        # БЛОК КАСТОМНЫХ ФЕЙК-ПУЛОВ НА PUMPSWAP (кейсы WHITEWHALE -98.5% и MOE_fake):
        # На PumpSwap (pump_fun_amm) заблокированный протоколом LP имеют ТОЛЬКО официальные токены с pump.fun (оканчивающиеся на 'pump').
        # Любой кастомный минт (не 'pump') на PumpSwap имеет 100% РАЗБЛОКИРОВАННЫЙ LP в руках скамера!
        if dex_id == "pumpswap" and not mint.endswith("pump"):
            return self._deny(mint, "fake-pumpswap-unlocked-lp", f"🚫 [FAKE-PUMPSWAP] {symbol} ({mint[:8]}): кастомный не-pump контракт на PumpSwap (100% разблокированный LP скамера) — блок!")

        if liquidity < _min_liq:
            print(f"📉 Изоляция: {symbol} имеет микро-пул (${liquidity:.0f} < ${_min_liq//1000}k). Риск 100% проскальзывания.")
            return False
            
        current_created_at = pair_data.get("pairCreatedAt", 0)
        current_fdv = pair_data.get("fdv", 0)
        _tx5_cl = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5_cl = int(_tx5_cl.get("buys", 0) or 0)
        _s5_cl = int(_tx5_cl.get("sells", 0) or 0)
        _vol5_cl = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        _vol1_cl = float((pair_data.get("volume") or {}).get("m1", 0) or 0)
        _m5_cl = float((pair_data.get("priceChange") or {}).get("m5", 0) or 0)

        _strong_fomo = (
            (_b5_cl >= 40 and _b5_cl >= _s5_cl * 1.2) or
            (_vol5_cl >= 20000.0 and _b5_cl > _s5_cl) or
            (_vol1_cl >= 8000.0 and _m5_cl >= 15.0) or
            (is_vip and _b5_cl >= 30)
        )

        # Клон-фильтр работает ВСЕГДА (даже при _strong_fomo), т.к. скам-клоны WHITEWHALE и MOE специально накручивают объём ботами!
        if await self.is_clone(symbol, mint, current_created_at, current_fdv, current_liq=liquidity):
            return self._deny(mint, "clone-scam", f"🚫 [CLONE-SCAM] Токен {symbol} ({mint[:8]}) является фейковым клоном более старого/крупного оригинала!")


        info = pair_data.get("info", {}) or {}
        socials = info.get("socials", []) or []
        websites = info.get("websites", []) or []
        
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        has_socials = bool(has_twitter or has_tg or has_website)
        
        _socials_unknown = bool(pair_data.get("_socials_unknown", False))
        # Анонимные токены вообще без соцсетей (0 Twitter/TG/Web) при ликвидности < $35k — это 100% автоматические бандлер-фермы (кейс ARACH $16k liq -> -66.9%), даже если они накрутили объём (_strong_fomo)!
        if not _socials_unknown and not has_socials and liquidity < 35000 and not _lottery:
            return self._deny(mint, "no-socials-anon-rug", f"🚫 [NO-SOCIALS] У {symbol} ({mint[:8]}) 0 соцсетей/сайтов (Twitter/TG/Web) при ликвидности ${liquidity:,.0f} (<$35k) — анонимный дев-бандлер.")
            
        if is_vip:
            print(f"🚀 [VIP] {symbol}: Проверка клонов и соцсетей пройдена!")
            
        try:
            import aiohttp
            session = await self.get_session()
            
            rugcheck_url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
            rc_data = None
            async with session.get(rugcheck_url, timeout=8) as resp:
                if resp.status == 200:
                    rc_data = await resp.json(content_type=None)
                else:
                    print(f"⚠️ RugCheck API вернул {resp.status} для {mint[:8]}.")
                    
            if rc_data:
                token_info = rc_data.get("token", {})
                mint_authority = token_info.get("mintAuthority")
                freeze_authority = token_info.get("freezeAuthority")
                
                PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
                SYSTEM_PROGRAM = "11111111111111111111111111111111"
                SAFE_AUTHORITIES = {PUMPFUN_PROGRAM, SYSTEM_PROGRAM, None, ""}
                
                if mint_authority and mint_authority not in SAFE_AUTHORITIES:
                    print(f"🚫 [АНТИСКАМ] Mint Authority у ДЕВ-кошелька {mint_authority[:8]} у {mint[:8]} → СКАМ")
                    return False
                
                if freeze_authority and freeze_authority not in SAFE_AUTHORITIES:
                    print(f"🚫 [АНТИСКАМ] Freeze Authority у ДЕВ-кошелька {freeze_authority[:8]} у {mint[:8]} → СКАМ")
                    return False

                # Проверка критических рисков RugCheck с вычетом штрафа за Low Liquidity (ловит реальный скам, но не режет молодые пулы):
                _rc_raw_score = float(rc_data.get("score") or 0.0)
                _rc_low_liq_penalty = sum(
                    float(rk.get("score", 0) or 0)
                    for rk in (rc_data.get("risks") or [])
                    if "low liquidity" in str(rk.get("name", "")).lower()
                )
                _rc_score = max(0.0, _rc_raw_score - _rc_low_liq_penalty)
                if _rc_score >= 1000:
                    print(f"🚫 [АНТИСКАМ] RugCheck Очищенный Score {_rc_score:.0f} >= 1000 (высокий риск скама/рага) у {mint[:8]} → СКАМ")
                    return False

                for rk in (rc_data.get("risks") or []):
                    _rk_name = str(rk.get("name") or "").lower()
                    _rk_lvl = str(rk.get("level") or "").lower()
                    if "low liquidity" in _rk_name:
                        continue
                    if _rk_lvl == "danger":
                        print(f"🚫 [АНТИСКАМ] Критический риск RugCheck [danger]: '{rk.get('name')}' ({rk.get('description')}) у {mint[:8]} → СКАМ")
                        return False
                    if "unlocked" in _rk_name and "lp" in _rk_name:
                        print(f"🚫 [АНТИСКАМ] Незаблокированный LP пула '{rk.get('name')}' у {mint[:8]} → СКАМ")
                        return False

                _target_pair = str(pair_data.get("pairAddress") or "").strip()
                _has_verified_locked_lp = False
                for _mkt in (rc_data.get("markets") or []):
                    if isinstance(_mkt, dict):
                        _mkt_pubkey = str(_mkt.get("pubkey") or "").strip()
                        _lp_info = _mkt.get("lp") or {}
                        if isinstance(_lp_info, dict):
                            _mkt_quote_usd = float(_lp_info.get("quoteUSD") or 0.0)
                            _lp_unlocked = float(_lp_info.get("lpUnlocked") or 0.0)
                            _lp_locked_pct = float(_lp_info.get("lpLockedPct") or 0.0)
                            if _lp_locked_pct >= 80.0 and (_mkt_quote_usd >= 2000 or (_target_pair and _mkt_pubkey == _target_pair)):
                                _has_verified_locked_lp = True
                            if (_target_pair and _mkt_pubkey == _target_pair) or _mkt_quote_usd >= 2000:
                                if _lp_unlocked > 0 and _lp_locked_pct < 80.0:
                                    print(f"🚫 [АНТИСКАМ] LP пула не заблокирован ({_lp_locked_pct:.1f}% locked) у {mint[:8]} → СКАМ")
                                    return False
                if not mint.endswith("pump") and not _has_verified_locked_lp:
                    print(f"🚫 [АНТИСКАМ] У кастомного не-pump токена {symbol} ({mint[:8]}) не подтвержден заблокированный LP в RugCheck → СКАМ")
                    return False
                    
                holders = rc_data.get("topHolders", [])
                if not holders:
                    print(f"⚠️ Ошибка RugCheck (пустой список аккаунтов). Блокируем вход.")
                    return False
                    
                # Собираем все известные адреса пулов и маркеров ликвидности (DEX, AMM, Bonding curve, Lockers)
                pool_accounts = set()
                if pair_data:
                    _p_addr = pair_data.get("pairAddress")
                    if _p_addr:
                        pool_accounts.add(_p_addr)
                    for _p in (pair_data.get("pairs") or []):
                        if isinstance(_p, dict) and _p.get("pairAddress"):
                            pool_accounts.add(_p["pairAddress"])
                
                # Известные аккаунты из RugCheck (AMM, LOCKER)
                for _k_acc, _v_acc in (rc_data.get("knownAccounts") or {}).items():
                    if isinstance(_v_acc, dict) and _v_acc.get("type") in ("AMM", "LOCKER"):
                        pool_accounts.add(_k_acc)
                
                # Маркеты из RugCheck
                for _mkt in (rc_data.get("markets") or []):
                    if isinstance(_mkt, dict):
                        for _m_field in ("pubkey", "liquidityA", "liquidityB"):
                            if _mkt.get(_m_field):
                                pool_accounts.add(_mkt[_m_field])
                
                pool_keywords = ("raydium", "meteora", "pump", "orca", "pool", "vault", "locker", "amm", "pumpswap")
                
                # Реальный создатель токена по данным RugCheck
                real_creator = rc_data.get("creator") or ""
                dev_holding_pct = 0.0
                if real_creator:
                    for h in holders:
                        if h.get("owner") == real_creator or h.get("address") == real_creator:
                            dev_holding_pct = float(h.get("pct", 0) or 0.0)
                            break

                non_curve_accounts = []
                for h in holders:
                    pct = float(h.get("pct", 0) or 0.0)
                    amt = float(h.get("uiAmount", 0) or 0.0)
                    owner = str(h.get("owner") or "")
                    addr = str(h.get("address") or "")
                    
                    # Исключаем пулы ликвидности, AMM и системные адреса Raydium/Pump/Meteora
                    if owner in pool_accounts or addr in pool_accounts or owner == "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1":
                        continue
                    if any(w in owner.lower() for w in pool_keywords):
                        continue
                    if pct > 80.0:  # Первичный кёрв Pump.fun
                        continue
                    non_curve_accounts.append((amt, pct, owner))
                
                top_10 = non_curve_accounts[:10]
                
                # Защита от Jito-бандлов (Сивил-атака дева):
                bundle_suspects = [round(pct, 1) for (amt, pct, owner) in top_10 if pct >= 2.5]
                if len(bundle_suspects) >= 4:
                    from collections import Counter
                    counts = Counter(bundle_suspects)
                    most_common_pct, count = counts.most_common(1)[0]
                    if count >= 4 and most_common_pct >= 2.5 and (most_common_pct * count) >= 12.0:
                        print(f"🚫 [АНТИСКАМ] Обнаружен Jito-бандл (Сивил атака: {count} кошельков по ~{most_common_pct:.1f}%) у {mint}. Блокируем.")
                        return False
                
                top_10_sum_pct = sum([x[1] for x in top_10])
                
                # Если дев не найден в rc_data, берем крупнейший реальный некомандный кошелек
                if not real_creator and top_10:
                    dev_holding_pct = top_10[0][1]
                
                self._last_top10 = top_10_sum_pct
                self._last_dev = dev_holding_pct
                
                is_pump = pair_data and pair_data.get("dexId") in ("pump", "pumpfun")
                max_allowed_pct = 30.0 if is_pump else 35.0
                
                if top_10_sum_pct > 100:
                    print(f"⚠️ [HOLDERS] {mint[:8]}: топ-10 {top_10_sum_pct:.1f}% > 100% — битые данные сапплая.")
                elif dev_holding_pct > 10.0:
                    print(f"🚫 [АНТИСКАМ] Создатель (Dev) держит {dev_holding_pct:.1f}% (Лимит 10%). Блокируем.")
                    return False
                
                # Защита от скрытого второго кошелька дева: ни один кит не должен держать > 15%
                largest_whale_pct = top_10[0][1] if top_10 else 0.0
                max_single_whale = 15.0
                if largest_whale_pct > max_single_whale:
                    print(f"🚫 [АНТИСКАМ] Кит держит {largest_whale_pct:.1f}% > {max_single_whale:.0f}% (риск скрытого дева/дампа). Блокируем.")
                    return False
                elif top_10_sum_pct > max_allowed_pct:
                    print(f"🚫 [АНТИСКАМ] Топ-10 держат {top_10_sum_pct:.1f}% (Лимит {max_allowed_pct}%). Блокируем.")
                    return False
            else:
                # Если это кастомный токен (не с pump.fun), без отчёта RugCheck вход СТРОГО ЗАПРЕЩЁН (иначе 100% риск разблокированного LP как у WHITEWHALE)
                if not mint.endswith("pump"):
                    print(f"🚫 [NO-RUGCHECK-CUSTOM] {symbol} ({mint[:8]}): кастомный не-pump токен без отчёта RugCheck — блок входа (риск разблокированного LP).")
                    return False
                # Фоллбэк только для официальных pump.fun токенов: если RugCheck временно 404/429, проводим аудит через GoPlus
                try:
                    import goplus as _gp
                    _g_ok, _g_why = await _gp.check_solana(mint)
                    if not _g_ok:
                        print(f"🚫 [GOPLUS] {symbol} ({mint[:8]}): {_g_why} — блок входа.")
                        return False
                    _h_stats = _gp.get_solana_holder_stats(mint)
                    if _h_stats.get("has_data"):
                        self._last_top10 = _h_stats.get("top_10_pct", 0.0)
                        self._last_dev = _h_stats.get("dev_pct", 0.0)
                        if self._last_dev > 10.0:
                            print(f"🚫 [GOPLUS-DEV] {symbol} ({mint[:8]}): дев держит {self._last_dev:.1f}% > 10% — риск дампа.")
                            return False
                        _max_top10 = 30.0 if (pair_data and pair_data.get("dexId") in ("pump", "pumpfun")) else 35.0
                        if self._last_top10 > _max_top10:
                            print(f"🚫 [GOPLUS-CABAL] {symbol} ({mint[:8]}): топ-10 держат {self._last_top10:.1f}% > {_max_top10:.0f}% — риск кабала.")
                            return False
                        print(f"🛡️ [GOPLUS] RugCheck 404, но GoPlus подтвердил безопасность {mint[:8]} (Топ-10: {self._last_top10:.1f}%, Дев: {self._last_dev:.1f}%).")
                    else:
                        _is_pump = pair_data and pair_data.get("dexId") in ("pump", "pumpfun")
                        if not _is_pump:
                            print(f"🚫 [NO-AUDIT] {symbol} ({mint[:8]}): Ни RugCheck, ни GoPlus не имеют данных холдеров — блок входа по безопасности.")
                            return False
                        print(f"🛡️ [GOPLUS] RugCheck 404, GoPlus подтвердил отсутствие ловушек у {mint[:8]} (Pump.fun кривая).")
                except Exception as _ge:
                    print(f"⚠️ Не удалось проверить через RugCheck и GoPlus ({_ge}). Блокируем вход.")
                    return False
        except Exception as e:
            print(f"⚠️ Ошибка RugCheck ({str(e)[:50]}). Fail-Closed для защиты от рагпула.")
            if not mint.endswith("pump"):
                return False
            try:
                import goplus as _gp
                _g_ok, _g_why = await _gp.check_solana(mint)
                if not _g_ok:
                    return False
                _h_stats = _gp.get_solana_holder_stats(mint)
                if not _h_stats.get("has_data"):
                    return False
                self._last_top10 = _h_stats.get("top_10_pct", 0.0)
                self._last_dev = _h_stats.get("dev_pct", 0.0)
                if self._last_dev > 10.0 or self._last_top10 > 35.0:
                    return False
            except Exception:
                return False
        except Exception as e:
            print(f"⚠️ Ошибка Jito-bundle: {e}")
            return False

        # 1. Прямой ончейн-аудит Solana (Freeze Authority, Mint Authority, Token-2022) через RPC
        try:
            import goplus as _gp
            _sol_onchain_ok, _sol_onchain_why = await _gp.check_onchain_solana_security(mint)
            if not _sol_onchain_ok:
                print(f"🚫 [АНТИСКАМ-FREEZE] {symbol} ({mint[:8]}): {_sol_onchain_why} — блок входа.")
                return False
            _g_ok, _g_why = await _gp.check_solana(mint)
            if not _g_ok:
                print(f"🚫 [GOPLUS] {symbol} ({mint[:8]}): {_g_why} — блок входа.")
                return False
        except Exception as e:
            print(f"🚫 [АНТИСКАМ] Ошибка аудита безопасности ({type(e).__name__}) — блок по безопасности (fail-closed).")
            return False

        # 2. Защита от Honeypot на Solana (0 продаж при 5+ покупках — нельзя продать)
        txns_m5 = (pair_data.get("txns") or {}).get("m5") or {}
        txns_h1 = (pair_data.get("txns") or {}).get("h1") or {}
        s5 = int(txns_m5.get("sells", 0) or 0)
        s1 = int(txns_h1.get("sells", 0) or 0)
        b5 = int(txns_m5.get("buys", 0) or 0)
        if s5 == 0 and s1 == 0 and b5 >= 5:
            print(f"🚫 [HONEYPOT] {symbol} ({mint[:8]}): 0 продаж при {b5} покупках — Honeypot (нельзя продать).")
            return False
        if (b5 + s5) >= 20 and (s5 / (b5 + s5)) < 0.05:
            print(f"🚫 [HONEYPOT] {symbol} ({mint[:8]}): аномалия продаж m5 {s5}s/{b5}b (<5% продаж) — скрытый honeypot.")
            return False

        if is_vip or _lottery:
            print(f"🚀 [FAST TRACK] {symbol}: гейты пройдены, передаем на проверку холдеров (Снайперы/Бандлы).")
            self._set_sig(mint, "VIP" if is_vip else "LOTTERY")

        dex_id = pair_data.get("dexId")
        created_at = pair_data.get("pairCreatedAt", 0)
        age_minutes = (time.time() * 1000 - created_at) / (1000 * 60) if created_at else 999
        
        # Глобальная защита от дешевых скрамов/рагпуллов
        liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        if dex_id in ("pump", "pumpfun"):
            if liq == 0:
                liq = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0) * 0.40
            _floor_liq = 3500
        else:
            _floor_liq = 8000 if (age_minutes < 60 or _lottery or is_vip) else (12000 if (pair_data.get("chainId") or "").lower() == "solana" else 5000)
        if liq < _floor_liq:
            return self._deny(mint, f"low-liq ${liq:.0f}<{_floor_liq}", f"🚫 [ANTI-RUG] {symbol} ({mint[:8]}): Ликвидность всего ${liq:,.0f} < ${_floor_liq}. Слишком опасно.")

        # Защита от фантомных пулов с мизерным остатком реального SOL (кейс Hodlon -$14.91: в пуле всего 1.17 SOL):
        _quote_info = pair_data.get("liquidity", {}) or {}
        _quote_amt = float(_quote_info.get("quote", 0) or 0)
        _quote_sym = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
        if "SOL" in _quote_sym and 0 < _quote_amt < 10.0 and dex_id not in ("pump", "pumpfun"):
            return self._deny(mint, f"ghost-pool-sol {_quote_amt:.1f}<10", f"🚫 [AMM-GHOST-POOL] {symbol} ({mint[:8]}): в пуле всего {_quote_amt:.2f} SOL < 10 SOL — микро-резерв, риск 100% слива девом.")

        if dex_id in ("pump", "pumpfun"):
            ok = await self.analyze_token_xgboost(mint, pair_data, _lottery=_lottery)
        else:
            ok = await self.analyze_token_raydium(mint, pair_data)
        if ok:
            if not await self.verify_1m_candles_not_dumping(mint, pair_data, "solana"):
                return False
        return ok

    async def verify_1m_candles_not_dumping(self, address: str, pair_data: dict, chain: str = "solana") -> bool:
        """Финальная проверка 1-минутных свечей перед самой покупкой (кейс sBTC -31% за 1м):
        DexScreener не отдает m1 в priceChange, поэтому без проверки 1м свечей бот мог купить
        токен, который 2 минуты назад сделал памп, а последние 1-2 минуты уже летит камнем вниз."""
        if not pair_data:
            return True
        symbol = ((pair_data.get("baseToken") or {}).get("symbol") or address[:8])
        # 1. Проверяем просадку от уже виденного 5м-пика в памяти бота
        drop_seen = float(pair_data.get("_drop_from_peak_5m", 0.0) or 0.0)
        if drop_seen <= -12.0:
            print(f"🚫 [PEAK-DUMP] {symbol} ({address[:8]}): цена уже упала на {drop_seen:.1f}% от недавнего пика — падающий нож.")
            return False

        # 2. Запрашиваем последние 5 минутных свечей из GeckoTerminal (только для финальных кандидатов на вход)
        pair_addr = pair_data.get("pairAddress") or ""
        if not pair_addr:
            return True
        gt_net = {"solana": "solana", "base": "base", "bsc": "bsc", "robinhood": "robinhood", "ethereum": "eth"}.get(chain.lower(), chain.lower())
        now = time.time()
        if not hasattr(self, "_ohlcv_1m_cache"):
            self._ohlcv_1m_cache = {}
        hit = self._ohlcv_1m_cache.get(pair_addr)
        ohlcv = None
        if hit and (now - hit[0]) < 12.0:
            ohlcv = hit[1]
        else:
            try:
                import market_data as _md
                data = await _md._gt_get(f"/networks/{gt_net}/pools/{pair_addr}/ohlcv/minute?aggregate=1&limit=5", retries=1)
                if data:
                    ohlcv = ((data.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
                    self._ohlcv_1m_cache[pair_addr] = (now, ohlcv)
            except Exception:
                ohlcv = None

        if not ohlcv:
            return True

        try:
            cur_px = float(pair_data.get("priceUsd", 0) or 0)
            c0 = ohlcv[0]
            c0_open, c0_high, c0_low, c0_close = float(c0[1] or 0), float(c0[2] or 0), float(c0[3] or 0), float(c0[4] or 0)
            eff_close = min(c0_close, cur_px) if (cur_px > 0 and c0_close > 0) else (cur_px or c0_close)
            c0_pct = ((eff_close - c0_open) / c0_open * 100.0) if c0_open > 0 else 0.0

            if not isinstance(pair_data.get("priceChange"), dict):
                pair_data["priceChange"] = {}
            pair_data["priceChange"]["m1"] = round(c0_pct, 2)

            highs_5m = [float(r[2] or 0) for r in ohlcv[:5] if float(r[2] or 0) > 0]
            lows_5m = [float(r[3] or 0) for r in ohlcv[:5] if float(r[3] or 0) > 0]
            if cur_px > 0:
                highs_5m.append(cur_px)
                lows_5m.append(cur_px)
            high_5m = max(highs_5m) if highs_5m else 0.0
            low_5m = min(lows_5m) if lows_5m else 0.0
            drawdown_5m_pct = ((eff_close - high_5m) / high_5m * 100.0) if high_5m > 0 else 0.0

            # А) Активная красная свеча прямо сейчас (реальный слив >= -7.5% за текущую минуту)
            if c0_pct <= -7.5:
                print(f"🚫 [1M-DUMP] {symbol} ({address[:8]}): текущая 1м свеча летит вниз ({c0_pct:+.1f}%) — падающий нож, отмена входа.")
                return False

            # А2) Перегрев текущей 1м свечи (вход на макушке аномального прострела)
            tx_5m = (pair_data.get("txns") or {}).get("m5") or {}
            b5_val = int(tx_5m.get("buys", 0) or 0)
            s5_val = int(tx_5m.get("sells", 0) or 0)
            m5_pct = float((pair_data.get("priceChange") or {}).get("m5", 0) or 0)
            is_strong_trend = (m5_pct >= 20.0 and b5_val >= max(int(s5_val * 1.2), 5))

            max_m1_overheat = 38.0 if is_strong_trend else 28.0
            if c0_pct > max_m1_overheat:
                print(f"🚫 [1M-OVERHEAT] {symbol} ({address[:8]}): текущая 1м свеча +{c0_pct:.1f}% > +{max_m1_overheat:.0f}% — вертикальный прострел, вход = вершина.")
                return False

            # Б) Прошлая минутная свеча обвалилась, идут 2 красные свечи подряд или экстремальный blow-off
            if len(ohlcv) >= 2:
                c1 = ohlcv[1]
                c1_open, c1_high, c1_low, c1_close = float(c1[1] or 0), float(c1[2] or 0), float(c1[3] or 0), float(c1[4] or 0)
                c1_pct = ((c1_close - c1_open) / c1_open * 100.0) if c1_open > 0 else 0.0
                if c1_pct <= -6.0 and c0_pct < 2.0:
                    print(f"🚫 [1M-CRASH] {symbol} ({address[:8]}): прошлая 1м свеча слилась на {c1_pct:+.1f}% без откупа (сейчас {c0_pct:+.1f}%) — обвал в процессе.")
                    return False
                if ((c1_pct <= -3.0 and c0_pct <= -2.5) or (c1_pct + c0_pct <= -6.0)) and eff_close <= c1_close:
                    print(f"🚫 [2-RED-1M] {symbol} ({address[:8]}): 2 красные минутные свечи подряд ({c1_pct:+.1f}%, {c0_pct:+.1f}%) — глубокий слив.")
                    return False
                if c1_pct > 60.0 or (c0_pct + c1_pct) > 75.0:
                    print(f"🚫 [1M-BLOWOFF] {symbol} ({address[:8]}): вертикальный памп за 2 мин ({c1_pct:+.1f}%, {c0_pct:+.1f}%) — риск мгновенного дампа.")
                    return False

            # В) Blow-off Top: цена уже упала на >= 16% от максимума последних 5 минут (кейс sBTC -20.9% от пика)
            if drawdown_5m_pct <= -16.0 and c0_pct < 3.0:
                print(f"🚫 [BLOWOFF-TOP] {symbol} ({address[:8]}): цена уже упала на {drawdown_5m_pct:.1f}% от 5м-пика — вершина пройдена, идёт разгрузка.")
                return False

            # В2) 5-минутное истощение (5m Exhaustion): если за последние 5 минут токен уже вырос в >= 3.8 раза от лоя (кейс DMN 5.3x за 4м -> Infant Dump -32.8%)
            if low_5m > 0 and (high_5m / low_5m) >= 3.8:
                print(f"🚫 [5M-EXHAUSTION] {symbol} ({address[:8]}): за 5м уже сделал {high_5m/low_5m:.1f}x от лоя — кульминация пройдена.")
                return False

            # Г) Верхний фитиль (Shooting Star): внутри текущей минуты токен кольнул верх и сложился на >= 12% при слабом закрытии (<=2%), либо резкий обвал >= 25%
            c0_wick_drop = ((c0_high - eff_close) / c0_high * 100.0) if c0_high > 0 else 0.0
            if (c0_wick_drop >= 12.0 and c0_pct <= 2.0) or c0_wick_drop >= 25.0:
                print(f"🚫 [WICK-DUMP] {symbol} ({address[:8]}): слив от хая текущей свечи на -{c0_wick_drop:.1f}% — раздача в стакан.")
                return False

            # Г2) Спайк с откатом (Spike Reversal / кейс Mao -28%): свеча сделала резкий взлёт >= 15% и уже откатила сверху вниз на >= 8%
            c0_spike_pct = ((c0_high - c0_open) / c0_open * 100.0) if c0_open > 0 else 0.0
            if c0_spike_pct >= 15.0 and c0_wick_drop >= 8.0:
                print(f"🚫 [SPIKE-REVERSAL] {symbol} ({address[:8]}): спайк +{c0_spike_pct:.1f}% уже откатил на -{c0_wick_drop:.1f}% от пика — раздача на хае.")
                return False
        except Exception:
            pass
        return True

    _EVM_SCAM_KW = ["AAPL", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "XAI", "NEURALINK", "PAYPAL",
                    "NVDA", "GOOG", "AMZN", "META", "NFLX",
                    "PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN",
                    "ANTHROPIC", "CLAUDE", "OPENAI", "CHATGPT", "DEEPSEEK", "GROK", "GEMINI", "PERPLEXITY"]

    def _brand_whitelisted(self, address: str) -> bool:
        """Точный минт из вайтлиста (BOME и др. топы) — мимикри-фильтр пропускает.
        Клоны с другим адресом режутся как раньше."""
        try:
            wl = {str(m).lower() for m in (getattr(config, "BRAND_WHITELIST_MINTS", []) or [])}
            return (address or "").lower() in wl
        except Exception:
            return False

    def _evm_brand_ok(self, symbol: str, tag: str, pair_data: dict = None) -> bool:
        """Блэклист мимикрии под бренды. Бесплатно, без сети."""
        s_up = symbol.upper()
        # Базовые нативные криптовалюты и стейблы — 100% скам-имитация на L2
        majors = {"ETH", "WETH", "BTC", "WBTC", "CBBTC", "SOL", "WSOL", "BNB", "WBNB", "USDC", "USDT", "DAI", "TETHER", "BITCOIN", "ETHEREUM", "SOLANA"}
        if s_up in majors:
            print(f"🚫 [{tag}] {symbol}: имитация базовой криптовалюты/стейбла — 100% скам/ханипот.")
            return False

        # Акции / корпорации — всегда 100% скам на блокчейне
        stocks = ["AAPL", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "XAI", "NEURALINK", "PAYPAL",
                  "NVDA", "GOOG", "AMZN", "META", "NFLX", "ANTHROPIC", "OPENAI", "PERPLEXITY"]
        if any(k in s_up for k in stocks):
            print(f"🚫 [{tag}] {symbol}: мимикрия под корпоративный бренд — 100% скам.")
            return False

        # Популярные мем-имена: если есть мощный органический FOMO — разрешаем!
        meme_names = ["PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN",
                      "CLAUDE", "DEEPSEEK", "GROK", "GEMINI"]
        if any(k in s_up for k in meme_names):
            if pair_data:
                _tx5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
                _b5 = int(_tx5.get("buys", 0) or 0)
                _s5 = int(_tx5.get("sells", 0) or 0)
                _v5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                _lq = float((pair_data.get("liquidity", {}) or {}).get("usd", 0) or 0)
                if (_b5 >= 30 and _b5 >= _s5) or _v5 >= 12000.0 or _lq >= 20000.0:
                    print(f"🔥 [{tag}] {symbol}: мощный органический FOMO мем-бренда (b/s {_b5}/{_s5}, vol5 ${_v5:.0f}) — пропуск.")
                    return True
            print(f"🚫 [{tag}] {symbol}: мимикрия под бренд/мем — 100% скам.")
            return False
        return True

    async def _evm_clone_ok(self, symbol: str, address: str, pair_data: dict, chain: str, tag: str) -> bool:
        """Внутрисетевой двойник старше/жирнее или серийный спам одноимённых пулов = скам. Кросчейн = мультчейн, ок."""
        if len(symbol) <= 2:
            return True
        try:
            _tx5_m = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _b5_m = int(_tx5_m.get("buys", 0) or 0)
            _s5_m = int(_tx5_m.get("sells", 0) or 0)
            _v5_m = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
            _mine_liq = float((pair_data.get("liquidity", {}) or {}).get("usd", 0) or 0)
            _mine_fdv = float(pair_data.get("fdv", 0) or 0)

            # Ослабление клон-фильтра при мощном органическом FOMO:
            _strong_fomo = (_b5_m >= 35 and _b5_m >= _s5_m * 1.2) or _v5_m >= 15000.0 or (_mine_liq >= 25000.0 and _b5_m >= 25)
            if _strong_fomo:
                print(f"🔥 [{tag}] {symbol}: мощный органический FOMO (b/s {_b5_m}/{_s5_m}, vol5 ${_v5_m:.0f}) — клон-фильтр ослаблен.")

            from http_client import fetch_json as _fj
            _now = time.time()
            if not hasattr(self, "_dex_search_cache"):
                self._dex_search_cache = {}
            _up = symbol.upper()
            _c_entry = self._dex_search_cache.get(_up)
            if _c_entry and (_now - _c_entry[0]) < 120.0:
                _st, _sd = 200, _c_entry[1]
            else:
                _st, _sd = await _fj(f"https://api.dexscreener.com/latest/dex/search?q={symbol}",
                                     timeout=8, retries=1)
                if _st == 200 and _sd:
                    self._dex_search_cache[_up] = (_now, _sd)
            if _st != 200 or not _sd:
                return True  # API лег - не блокируем вслепую
            _mine_addr = ((pair_data.get("baseToken") or {}).get("address") or "").lower()
            _mine_pair = (pair_data.get("pairAddress") or "").lower()
            _mine_created = pair_data.get("pairCreatedAt", 0) or 0
            _mine_info = pair_data.get("info") or {}
            _mine_links = (_mine_info.get("socials") or []) + (_mine_info.get("websites") or [])
            dead_clones = 0
            for _p in (_sd.get("pairs") or []):
                if ((_p.get("chainId") or "").lower()) != chain.lower():
                    continue
                _pa = (((_p.get("baseToken") or {}).get("address")) or "").lower()
                _p_pair = str(_p.get("pairAddress", "") or "").lower()
                _p_liq = float((_p.get("liquidity", {}) or {}).get("usd", 0) or 0)
                # Защита от рерагов: только если крупный параллельный пул (liq >= $10,000) сейчас реально слит (h24 <= -80% и h1 <= -50%)
                if _pa == _mine_addr and _p_pair != _mine_pair and _p_liq >= 10000.0:
                    _p_pc_same = _p.get("priceChange", {}) or {}
                    if float(_p_pc_same.get("h24", 0) or 0) <= -80.0 and float(_p_pc_same.get("h1", 0) or 0) <= -50.0:
                        print(f"🚫 [{tag}] {symbol}: крупный параллельный пул этого токена слит ({_p_pc_same.get('h24')}%) — рераг/скам.")
                        return False
                _ps = (((_p.get("baseToken") or {}).get("symbol")) or "").upper()
                if _ps == _up and _pa and _pa != _mine_addr:
                    _pc_at = _p.get("pairCreatedAt", 0) or 0
                    _pf = float(_p.get("fdv", 0) or 0)
                    _p_pc = _p.get("priceChange", {}) or {}
                    _p_tx5 = ((_p.get("txns") or {}).get("m5") or {})
                    _p_b5 = int(_p_tx5.get("buys", 0) or 0)
                    _p_s5 = int(_p_tx5.get("sells", 0) or 0)
                    _is_older = bool(_pc_at and _mine_created and _pc_at < (_mine_created - 300000))
                    # Клон, если старший оригинал на этой же сети очевидно крупнее И у текущего нет мощного FOMO
                    if not _strong_fomo and _is_older and _p_liq >= 25000.0 and _pf >= 100000.0 and (_pf > _mine_fdv * 2.0 or _p_liq > _mine_liq * 1.5):
                        print(f"🚫 [{tag}] {symbol}: клон (оригинал {_pa[:10]} старше/жирнее).")
                        return False
                    # Серийный раггер (мёртвые пулы-двойники): проверяется ТОЛЬКО для анонимных токенов без ссылок или микро-пулов и без FOMO
                    if not _strong_fomo and (not _mine_links or _mine_liq < 15000.0):
                        if float(_p_pc.get("h24", 0) or 0) <= -65.0 or ((_p_b5 + _p_s5) == 0 and _p_liq < 3000.0):
                            dead_clones += 1
            if not _strong_fomo and dead_clones >= 2 and (not _mine_links or _mine_liq < 15000.0):
                print(f"🚫 [{tag}] {symbol}: обнаружено {dead_clones} обвалившихся пулов-двойников на {chain} — серийный раггер!")
                return False
        except Exception:
            pass
        return True

    async def analyze_robinhood_token(self, address: str, chain: str = "robinhood") -> bool:
        if chain == "robinhood" or not getattr(config, f"{chain.upper()}_ENABLED", True):
            return self._deny(address, f"{chain}-disabled", f"🚫 [{chain.upper()}] Сеть {chain} отключена в config.")
        import evm_data
        self.last_signal = ""
        self.last_score = 0.0
        self.signals.pop(address, None)
        tag = (evm_data.CHAINS.get(chain) or {}).get("tag", chain.upper())
        min_liq = (evm_data.CHAINS.get(chain) or {}).get("min_liq",
                  getattr(config, f"{chain.upper()}_MIN_LIQUIDITY", 3000.0))
        pair_data = await evm_data.get_token_data(address, chain)
        if not pair_data:
            return None
        base = pair_data.get("baseToken", {}) or {}
        symbol = base.get("symbol", address[:6])
        pc = pair_data.get("priceChange") or {}
        m5 = pc.get("m5", 0) or 0
        m1 = pc.get("m1", 0) or 0
        h1 = pc.get("h1", 0) or 0
        h24 = pc.get("h24", 0) or 0
        txh1 = (pair_data.get("txns") or {}).get("h1", {}) or {}
        b, s = txh1.get("buys", 0) or 0, txh1.get("sells", 0) or 0
        txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        b5, s5 = txm5.get("buys", 0) or 0, txm5.get("sells", 0) or 0
        vol24 = (pair_data.get("volume") or {}).get("h24", 0) or 0
        liq = (pair_data.get("liquidity") or {}).get("usd", 0) or 0
        info = pair_data.get("info") or {}
        links = (info.get("socials") or []) + (info.get("websites") or [])
        import time as _tt
        _age_min = ((_tt.time() * 1000 - (pair_data.get("pairCreatedAt") or 0)) / 60000.0) \
            if pair_data.get("pairCreatedAt") else 999.0
        _px_now = float(pair_data.get("priceUsd", 0) or 0)
        if not hasattr(self, "_eval_ctx"):
            self._eval_ctx = {}
        self._eval_ctx[address] = {
            "symbol": symbol, "chain": chain, "price": _px_now,
            "liq": liq, "m5": m5, "source": tag
        }
        # Защита от мульти-пулов с гигантским рассинхроном цен (кейс CABAL на Robinhood: $0.0023 vs $0.000096)
        if pair_data.get("_price_divergence"):
            _div_r = float(pair_data.get("_price_divergence_ratio", 0.0) or 0.0)
            return self._deny(address, "price-split",
                              f"🚫 [{tag}] {symbol}: рассинхрон цен между пулами {_div_r:.1f}x — ловушка арбитража/мультипул.")
        # Ранний Scout-режим для свежих ракет (<15 мин):
        # Ловит стартовые импульсы ракет (USEMATH, FINI, MUCRON, WISDOM, MUSEBOT, AMIRI100X, AICELL) от 0.3 мин (18 сек)
        # при подтверждённых продажах (honeypot исключён) и ликвидности >= min_liq ($2.5k)
# СТАЛО (ИСПРАВЛЕНО):
        _scout_min_liq = 2500.0 if chain in ("bsc", "base") else float(min_liq)
        _is_clanker_scout = address.lower().startswith("0xb20000000000")
        _min_s5_req = 0 if _is_clanker_scout else 2
        _min_s_req = 0 if _is_clanker_scout else 2
        _bs_cond = (b5 >= int(s5 * 1.15)) if s5 > 0 else True
        if (0.5 <= _age_min < 15.0 and 0.8 <= m5 <= 800.0 and 0.0 <= h1 <= 1500.0
                and (b5 + s5) >= 8 and liq >= _scout_min_liq
                and s5 >= _min_s5_req and s >= _min_s_req and _bs_cond and (b5 / max(s5, 1)) <= 15.0):
            if not self._evm_brand_ok(symbol, tag, pair_data):
                return False
            if not await self._evm_clone_ok(symbol, address, pair_data, chain, tag):
                return False
            # 1. Защита от покупки вершины 1м свечи и падающего ножа
            # Ракеты при старте пробивают 1м свечой +25%..+55% (кейсы WISDOM +477%, MUSEBOT +266%, AMIRI100X +151%).
            if m1 > 65.0:
                return self._deny(address, f"scout-m1-spike {m1:+.1f}%",
                                  f"🚫 [{tag}-SCOUT] {symbol}: m1 {m1:+.1f}% — верхушка 1м импульса, вход = вершина.")
            if m1 < -7.0:
                return self._deny(address, f"scout-m1-drop {m1:+.1f}%",
                                  f"🚫 [{tag}-SCOUT] {symbol}: m1 {m1:+.1f}% — падающий нож.")
            # Защита от покупки вершины уже выдохшегося пампа (кейс DATAEXPORT +474%)
            _h6_scout = float((pair_data.get("priceChange") or {}).get("h6", 0) or 0)
            if _h6_scout > 250.0 and m5 < 15.0:
                return self._deny(address, f"scout-h6-overheated {_h6_scout:+.1f}%",
                                  f"🚫 [{tag}-SCOUT] {symbol}: h6 +{_h6_scout:.1f}% — пик пампа уже пройден, риск фиксации китами.")
            if h1 > 180.0 and m5 < 10.0:
                return self._deny(address, f"scout-h1-overheated {h1:+.1f}%",
                                  f"🚫 [{tag}-SCOUT] {symbol}: h1 +{h1:.1f}% — токен уже взлетел на 2.8х+, риск фиксации китами.")
            # 2. Защита от картонных пулов (FDV / Liq > 80x)
            _fdv_scout = float(pair_data.get("fdv", 0) or 0)
            if liq > 0 and _fdv_scout > 0 and (_fdv_scout / liq) > 80.0:
                return self._deny(address, "hollow-pool",
                                  f"🚫 [{tag}-SCOUT] {symbol}: FDV ${_fdv_scout:,.0f} vs Liq ${liq:,.0f} — картонный пул.")
            # 2.1. Защита от фантомных пулов (Ghost Pools: в пуле $20k фантиков, но реального ETH < 0.35, кейс UHI)
            _q_amt = float((pair_data.get("liquidity") or {}).get("quote", 0) or 0)
            _q_sym = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
            _is_clanker = address.lower().startswith("0xb20000000000")
            if chain in ("robinhood", "base", "ethereum"):
                _min_eth_quote = 0.15 if _is_clanker else 0.35
                if ("ETH" in _q_sym or "WETH" in _q_sym) and 0 < _q_amt < _min_eth_quote:
                    return self._deny(address, "ghost-pool-quote",
                                      f"🚫 [{tag}-SCOUT] {symbol}: фантомный пул (в пуле всего {_q_amt:.6f} ETH < {_min_eth_quote:.2f} ETH — нет реальных денег, ловушка).")
                elif "USD" in _q_sym and 0 < _q_amt < 800.0:
                    return self._deny(address, "ghost-pool-quote",
                                      f"🚫 [{tag}-SCOUT] {symbol}: фантомный пул (в пуле всего ${_q_amt:.0f} USDT/C < $800).")
            elif chain == "bsc":
                if ("BNB" in _q_sym or "WBNB" in _q_sym) and 0 < _q_amt < 1.2:
                    return self._deny(address, "ghost-pool-quote",
                                      f"🚫 [{tag}-SCOUT] {symbol}: фантомный пул (в пуле всего {_q_amt:.4f} BNB < 1.2 BNB).")
                elif "USD" in _q_sym and 0 < _q_amt < 800.0:
                    return self._deny(address, "ghost-pool-quote",
                                      f"🚫 [{tag}-SCOUT] {symbol}: фантомный пул (в пуле всего ${_q_amt:.0f} USDT/C < $800).")
            elif chain == "solana":
                if "SOL" in _q_sym and 0 < _q_amt < 5.0:
                    return self._deny(address, "ghost-pool-quote",
                                      f"🚫 [{tag}-SCOUT] {symbol}: фантомный пул (в пуле всего {_q_amt:.2f} SOL < 5 SOL).")
            # 3. Защита от микро-чеков ботов (накрутка объёма)
            _vm5_scout = float((pair_data.get("volume") or {}).get("m5", 0) or 0)

            # Защита от wash-trading крупными чеками скамера ($497 x N сделок):
            if (b5 + s5) <= 15 and _vm5_scout >= 8000:
                return self._deny(address, "wash-volume",
                                  f"🚫 [{tag}-SCOUT] {symbol}: аномальный объём ${_vm5_scout:,.0f} при всего {b5+s5} сделках — накрутка одним кошельком.")

            if (b5 + s5) >= 15 and (_vm5_scout / max(b5 + s5, 1)) < 15.0:
                return self._deny(address, "wash-bot",
                                  f"🚫 [{tag}-SCOUT] {symbol}: средний чек ${_vm5_scout / max(b5 + s5, 1):.1f} < $15 — накрутка ботов.")
            # 3.5. Защита от топтания и накрутки ботов (CHURN)
            if self.is_churn(b5, s5, m5, n_min=10, liq=liq, vol5=_vm5_scout):
                return self._deny(address, f"scout-churn {b5}/{s5}",
                                  f"🚫 [{tag}-SCOUT] {symbol}: накрутка {b5}b/{s5}s без реального FOMO — churn-ловушка.")
            # 4. Антискам аудит (GoPlus + Honeypot.is + On-Chain V4 PoolManager)
            if chain in ("robinhood", "bsc", "base", "ethereum"):
                try:
                    import goplus as _gp
                    _v_ok, _v_why = await _gp.verify_evm_token(address, chain, pair_data)
                    if not _v_ok:
                        return self._deny(address, f"antiscam-{_v_why}",
                                          f"🚫 [{tag}] {symbol}: {_v_why} — блок.")
                except Exception as _e:
                    if chain in ("bsc", "robinhood", "base"):
                        return self._deny(address, "antiscam-err",
                                          f"🚫 [{tag}] {symbol}: ошибка антискама ({type(_e).__name__}) — {tag} риск.")
            if not await self.verify_1m_candles_not_dumping(address, pair_data, chain):
                return self._deny(address, "1m-candle-dump", f"🚫 [{tag}-SCOUT] {symbol}: 1м свеча в сливе / вершина пройдена.")
            # Защита от бото-накрутки и скамов: для пулов с подтверждённым ончейн-рынком
            # (например, fun b/s 61/49 vol $10k, ASH +611%, POGO +164%)
            # XGBoost скор ракеты (~0.01 из-за отсутствия 24ч истории) НЕ должен ветировать вход!
            _rkt_tag_str = self._rkt_tag(address, pair_data)
            _sc_val = self.rocket_scores.get(address)
            # Для свежих монет (<15м) XGBoost скор занижен (~0.01-0.03) из-за отсутствия 24ч истории.
            # Если есть активный живой рынок (b5 >= 10, s5 >= 2, b5 >= s5, vol5 >= $2000), скор не ветирует вход!
            _is_live_organic_market = (b5 >= 10 and s5 >= 2 and b5 >= s5 and _vm5_scout >= 2000.0)
            if not _is_live_organic_market:
                if _sc_val is not None and _sc_val < 0.15:
                    return self._deny(address, f"scout-low-rkt {_sc_val:.2f}",
                                      f"🚫 [{tag}-SCOUT] {symbol}: скор ракеты {_sc_val:.2f} < 0.15 — накрутка ботов/слабый интерес.")
            print(f"🔭 [{tag}-SCOUT] {symbol}: возраст {_age_min:.1f}м, m5 {m5:+.1f}%, liq ${liq:,.0f} — ранний билет!")
            self._set_sig(address, f"{tag} SCOUT {_age_min:.0f}m" + _rkt_tag_str + await self.meme_tag(address, symbol, pair_data, chain))
            return True

        # BSC, Robinhood & Base: адаптивный возрастной барьер (если не подошёл под Scout):
        _cfg_age = float(getattr(config, f"{chain.upper()}_MIN_AGE_MIN", 1.5))
        if chain in ("robinhood", "base", "bsc"):
            if liq >= 15000.0:
                _min_age_thr = 0.8
            elif liq >= 5000.0:
                _min_age_thr = 1.0
            elif liq >= 3000.0:
                _min_age_thr = 1.2
            else:
                _min_age_thr = _cfg_age
        else:
            _min_age_thr = _cfg_age
        if chain in ("bsc", "robinhood", "base") and _age_min < _min_age_thr:
            if _age_min < 1.0:
                print(f"⏳ [{tag}] {symbol}: возраст {_age_min:.1f}м < {_min_age_thr:.1f}м — созревает, перепроверим.")
                return None
            return self._deny(address, f"{chain}-age {_age_min:.1f}m",
                              f"🚫 [{tag}] {symbol}: возраст {_age_min:.1f}м < {_min_age_thr:.1f}м — ранняя {tag} flash-rug зона.")

        if liq < min_liq:
            return self._deny(address, "micro-liq", f"🚫 [{tag}] {symbol}: ликва ${liq:,.0f} < ${min_liq:,.0f} — микро-пул.")
        
        # Защита от фантомных пулов (Ghost Pools: $20k накрученных фантиков, но реального ETH < 0.35)
        _q_amt_evm = float((pair_data.get("liquidity") or {}).get("quote", 0) or 0)
        _q_sym_evm = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
        _is_clanker_evm = address.lower().startswith("0xb20000000000")
        if chain in ("robinhood", "base", "ethereum"):
            _min_eth_quote = 0.15 if _is_clanker_evm else 0.35
            if ("ETH" in _q_sym_evm or "WETH" in _q_sym_evm) and 0 < _q_amt_evm < _min_eth_quote:
                return self._deny(address, "ghost-pool-quote",
                                  f"🚫 [{tag}] {symbol}: фантомный пул (в пуле всего {_q_amt_evm:.6f} ETH < {_min_eth_quote:.2f} ETH — нет реальных денег).")
            elif "USD" in _q_sym_evm and 0 < _q_amt_evm < 800.0:
                return self._deny(address, "ghost-pool-quote",
                                  f"🚫 [{tag}] {symbol}: фантомный пул (в пуле всего ${_q_amt_evm:.0f} USDT/C < $800).")
        elif chain == "bsc":
            if ("BNB" in _q_sym_evm or "WBNB" in _q_sym_evm) and 0 < _q_amt_evm < 1.2:
                return self._deny(address, "ghost-pool-quote",
                                  f"🚫 [{tag}] {symbol}: фантомный пул (в пуле всего {_q_amt_evm:.4f} BNB < 1.2 BNB).")
            elif "USD" in _q_sym_evm and 0 < _q_amt_evm < 800.0:
                return self._deny(address, "ghost-pool-quote",
                                  f"🚫 [{tag}] {symbol}: фантомный пул (в пуле всего ${_q_amt_evm:.0f} USDT/C < $800).")
        
        _evm_min_m5 = 0.8 if (s5 > 0 and b5 > s5) else float(getattr(config, "EVM_MIN_M5_PCT", 1.2))
        if m5 < _evm_min_m5:
            _accum_on = bool(getattr(config, "EVM_ACCUM_ENABLED", True))
            if not _accum_on:
                return self._deny(address, f"flat m5 {m5:+.1f}%", f"· [{tag}] {symbol}: флет m5 {m5:+.1f}% < {_evm_min_m5:.1f}% (ACCUM выключен, нет импульса)")
            # === ПРОВЕРКА НАКОПЛЕНИЯ В ТРЕНДЕ (ACCUMULATION / CONTINUATION) vs МЕРТВЫЙ ФЛЭТ ===
            # Ловит ракеты в фазе 5м-паузы на растущем часовом тренде ТОЛЬКО на устоявшихся глубоких пулах (>=30м, liq >= $25k).
            # Запрещает покупку послестартовых разворотов (SEXLA, Wheel Smith, BOOMER, hi), где свежий токен сдулся в минус по m5.
            _tx1 = ((pair_data.get("txns") or {}).get("h1") or {})
            _b1 = int(_tx1.get("buys", 0) or 0)
            _s1 = int(_tx1.get("sells", 0) or 0)
            _v1 = float(((pair_data.get("volume") or {}).get("h1", 0) or 0))
            _vm5 = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))

            # 1. Только зрелые глубокие пулы (>= 30 мин, liq >= $25,000) с живым потоком сделок (m5 >= 12 сделок, >= $1,000 объём)
            if _age_min < 30.0 or liq < 25000.0 or (_b1 + _s1) < 35 or _v1 < 5000.0 or (b5 + s5) < 12 or s5 < 3 or b5 < 7 or _vm5 < 1000.0:
                if _age_min < 10.0 and liq >= 5000.0 and (b5 + s5) >= 6:
                    print(f"⏳ [{tag}] {symbol}: возраст {_age_min:.1f}м, m5 {m5:+.1f}% — токен только родился, перепроверим через 20с.")
                    return None
                return self._deny(address, f"dead-flat m5 {m5:+.1f}%", f"· [{tag}] {symbol}: флет/остывание (age: {_age_min:.0f}m, liq: ${liq:,.0f}, m5: {m5:+.1f}%, tx5m: {b5}b/{s5}s, vol5m: ${_vm5:.0f})")

            # 2. Мамонты / стейблкоины / utility (AERO, USDC, PAPER, VELVET)
            if liq > 350000.0 or any(sb in symbol.upper() for sb in ("USDC", "USDT", "DAI")):
                return self._deny(address, "dex-giant", f"· [{tag}] {symbol}: тяжелый пул (${liq:,.0f}) или стейблкоин — не мем-ракета")

            # 3. Накопление на растущем тренде (h1 от +10% до +250%, m5 >= -2.0%, m1 >= 0%, покупатели >= 1.35x продавцов)
            # Ограничиваем возраст токена до 4 часов (<= 240м) и требуем растущий 6h тренд (h6 >= 0%), чтобы исключить древние затухающие монеты (кейс Pepina 769м)
            _h6_pc = float((pair_data.get("priceChange") or {}).get("h6", 0) or 0)
            if (_age_min <= 240.0 and _h6_pc >= 0.0 and -2.0 <= m5 < _evm_min_m5 and 10.0 <= h1 <= 250.0 and m1 >= 0.0
                    and (_b1 + _s1) >= 35 and _b1 >= _s1 * 1.25 and b5 >= int(s5 * 1.35)
                    and not self.is_churn(b5, s5, m5, liq=liq)):
                if not self._evm_brand_ok(symbol, tag, pair_data):
                    return False
                if not await self._evm_clone_ok(symbol, address, pair_data, chain, tag):
                    return False
                try:
                    import goplus as _gp
                    _v_ok, _v_why = await _gp.verify_evm_token(address, chain, pair_data)
                    if not _v_ok:
                        return self._deny(address, f"antiscam-{_v_why}", f"🚫 [{tag}] {symbol}: {_v_why} — блок.")
                except Exception as _e:
                    if chain in ("bsc", "robinhood", "base"):
                        return self._deny(address, "antiscam-err", f"🚫 [{tag}] {symbol}: ошибка антискама ({type(_e).__name__}) — блок.")
                if not await self.verify_1m_candles_not_dumping(address, pair_data, chain):
                    return self._deny(address, "1m-candle-dump", f"🚫 [{tag}-ACCUM] {symbol}: 1м свеча в сливе / вершина пройдена.")
                
                # Защита от мертвых/скамных стаканов со скором ракеты 0.00-0.02 (BANKHAUS, ROBINCAT, CHUBBY, VRAX)
                _rkt = self._rkt_tag(address, pair_data)
                sc = self.rocket_scores.get(address)
                if sc is not None and sc < 0.03:
                    return self._deny(address, f"rkt-low {sc:.2f}", f"🚫 [{tag}-ACCUM] {symbol}: скор ракеты {sc:.2f} < 0.03 — пустой/скамный стакан.")

                print(f"💎 [{tag}-ACCUM] {symbol}: m5 {m5:+.1f}%, h1 {h1:+.1f}%, liq ${liq:,.0f}, tx1h {_b1}b/{_s1}s, tx5m {b5}b/{s5}s — живое накопление в тренде перед взлетом.")
                self._set_sig(address, f"{tag} ACCUM" + _rkt + await self.meme_tag(address, symbol, pair_data, chain))
                return True

            if _is_clanker_evm and -22.0 <= m5 <= 0.0 and (b5 + s5) >= 8 and float((pair_data.get("volume") or {}).get("m5", 0) or 0) >= 2000.0:
                print(f"🚀 [BASE-CLANKER-DIP] {symbol}: откат m5 {m5:+.1f}%, liq ${liq:,.0f}, tx5m {b5}b/{s5}s — вход на откате перед взлётом.")
                self._set_sig(address, f"{tag} CLANKER-DIP" + self._rkt_tag(address, pair_data) + await self.meme_tag(address, symbol, pair_data, chain))
                return True
            return self._deny(address, f"flat m5 {m5:+.1f}%", f"· [{tag}] {symbol}: флет m5 {m5:+.1f}%")
        _max_m5 = 350.0 if (chain in ("robinhood", "base", "bsc") or _is_clanker_evm) else 85.0
        # При органическом перевесе покупателей расширяем коридор m5, чтобы забирать супер-ракеты
        # (кейсы 特斯拉能飞 +994% -> +3023%, INFLUENCE +159% -> +950%, VERDIS +289%, KAIDA +270%, BRIVON +393%, NULL +375%)
        if chain in ("robinhood", "base", "bsc"):
            if (_age_min < 15.0 or _is_clanker_evm) and s5 > 0 and b5 >= int(s5 * 1.1) and not self.is_churn(b5, s5, m5, liq=liq, is_clanker=_is_clanker_evm):
                _max_m5 = 1200.0  # Свежая супер-ракета на старте: вход на импульсе (кейс 特斯拉能飞 m5 +994%)
            elif liq >= 15000.0 and s5 > 0 and b5 >= s5 and not self.is_churn(b5, s5, m5, liq=liq, is_clanker=_is_clanker_evm):
                _max_m5 = 600.0   # Мощное ралли на глубоком пуле ($15k+): вход на 2-й ступени ракеты
            elif s5 > 0 and b5 >= s5:
                _max_m5 = 450.0   # Умеренный пул
            elif _is_clanker_evm:
                _max_m5 = 350.0   # Clanker ракета
            else:
                _max_m5 = 150.0

        if m5 > _max_m5:
            return self._deny(address, f"top m5 {m5:+.0f}%", f"🚫 [{tag}-OVERHEAT] {symbol}: m5 {m5:+.1f}% — вертикаль уже прошла, вход = вершина.")
        
        if self.is_churn(b5, s5, m5, liq=liq, is_clanker=_is_clanker_evm):
            return self._deny(address, f"churn {b5}/{s5} m5 {m5:+.1f}%",
                              f"🚫 [{tag}-CHURN] {symbol}: {b5 + s5} сделок без перевеса (b/s {b5 / (s5+1):.2f}) — накрутка, не FOMO.")
        # Ослабление m1-green: мощные ракеты пробивают импульсом без отката (кейсы BEC +663%, ORVA +237%).
        _max_m1 = 60.0 if (s5 > 0 and b5 >= 2.0 * s5 and (b5 + s5) >= 12) else (45.0 if (s5 > 0 and b5 >= 1.5 * s5 and (b5 + s5) >= 8) else (25.0 if (s5 > 0 and b5 >= 1.2 * s5) else 15.0))
        if m1 > _max_m1:
            return self._deny(address, f"m1-green {m1:+.1f}%", f"🚫 [{tag}] {symbol}: m1 {m1:+.1f}% > +{_max_m1:.0f}% — вершина в моменте, ждём.")
        if m1 < -5.0:
            return self._deny(address, f"m1-drop {m1:+.1f}%", f"🚫 [{tag}] {symbol}: m1 {m1:+.1f}% — резкое падение за 1м, падающий нож.")
        # Часовой тренд: глубокий дамп режем, но здоровый откат с разворотом на m5 пропускаем (кейсы IRL +775%, FTR +123%)
        if h1 < -25.0:
            return self._deny(address, f"h1-dump {h1:+.1f}%", f"🚫 [{tag}] {symbol}: h1 {h1:+.1f}% — глубокий часовой дамп.")
        if h1 < 0.0:
            if not (m5 >= 1.5 and s5 > 0 and b5 >= s5 * 1.15):
                return self._deny(address, f"h1-red {h1:+.1f}%", f"🚫 [{tag}] {symbol}: h1 {h1:+.1f}% — часовой тренд вниз без разворота.")
        _max_h1 = 1200.0 if (liq >= 20000.0 and s5 > 0 and b5 >= s5 * 1.20) else (800.0 if (liq >= 12000.0 and s5 > 0 and b5 >= s5 * 1.15) else 350.0)
        if h1 > _max_h1:
            return self._deny(address, "gone", f"🚫 [{tag}] {symbol}: h1 {h1:+.0f}% > +{_max_h1:.0f}% — параболик уже улетел, вершина.")
        
        # --- СТРОГАЯ ЗАЩИТА ОТ HONEYPOT (Проверка реальных сделок в пуле) ---
        _is_clanker_tok = address.lower().startswith("0xb20000000000")
        if not _is_clanker_tok:
            if s5 == 0:
                return self._deny(address, "honeypot-zero-sells-5m", f"🚫 [{tag}] {symbol}: 0 продаж за 5м — Honeypot (нельзя продать).")
            if s <= 1:
                return self._deny(address, "honeypot-no-sells-h1", f"🚫 [{tag}] {symbol}: всего {s} продаж за 1ч — неподтвержденная продаваемость.")
            if s5 <= 1 and b5 >= 6:
                return self._deny(address, "honeypot-1sell-5m", f"🚫 [{tag}] {symbol}: {b5} покупок и всего {s5} продажа за 5м — ловушка Honeypot.")
        # Блокируем высокий b/s только когда продаж мало (s5 <= 2 или s <= 5).
        # Если в пуле уже прошло >= 4 продаж за 5м и >= 8 продаж за 1ч (и GoPlus/Honeypot.is подтвердил 0% tax), это живое FOMO (кейс ASI_AI +400%).
        if s5 > 0 and s5 <= 2 and (b5 / s5) > 6.0 and (b5 + s5) >= 15:
            return self._deny(address, "honeypot-ratio-5m", f"🚫 [{tag}] {symbol}: аномалия m5 {b5}b/{s5}s ({b5/s5:.1f}x) при малом числе продаж — Honeypot-риск.")
        if s > 0 and s <= 5 and (b / s) > 6.0 and (b + s) >= 25:
            return self._deny(address, "honeypot-ratio-h1", f"🚫 [{tag}] {symbol}: аномалия h1 {b}b/{s}s ({b/s:.1f}x) при малом числе продаж — Honeypot-риск.")
        vol_to_liq = vol24 / (liq + 1)
        if vol_to_liq > 100:
            return self._deny(address, "wash-trading", f"🚫 [{tag}] {symbol}: Объем в {vol_to_liq:.0f} раз больше пула ликвидности — накрутка ботами создателя.")
        
        # Если в моменте активный 5м импульс (m5 >= 2% и b5 >= s5 * 1.15), 1-часовой флет до пампа не блокирует свежий пробой (кейс ELON-SAN +131%)
        if s > 0 and b < s * 1.15 and not (m5 >= 2.0 and s5 > 0 and b5 >= s5 * 1.15):
            return self._deny(address, f"no-pressure {b}/{s}", f"🚫 [{tag}] {symbol}: buys {b} / sells {s} — нет давления.")
        _min_b5_s5 = 1.10 if (m5 >= 5.0 and liq >= 12000.0) else 1.15
        if s5 > 0 and b5 < s5 * _min_b5_s5:
            return self._deny(address, f"m5-no-pressure {b5}/{s5}", f"🚫 [{tag}] {symbol}: m5 buys {b5} / sells {s5} — продавцы давят в моменте.")
        
        if chain == "robinhood":
            _evm_tx_min = 8 if _age_min < 30 else 12
            _evm_vol_min = 1500 if _age_min < 30 else 3000
        elif liq >= 15000.0 and b5 > s5 and m5 >= 2.0:
            # На глубоком пуле ($15k+) органические ракеты стартуют с 6-8 крупными покупками
            _evm_tx_min = 6
            _evm_vol_min = 1500 if _age_min < 30 else 3000
        else:
            _evm_tx_min = 8 if _age_min < 30 else 10
            _evm_vol_min = 1500 if _age_min < 30 else 3000
        if (b5 + s5) < _evm_tx_min or vol24 < _evm_vol_min:
            return self._deny(address, "quiet", f"🚫 [{tag}] {symbol}: тихо (tx5 {(b5+s5)}, vol24 ${vol24:,.0f}).")
        # Соцсети DexScreener требуют $300 и ручной модерации (для безссылочных токенов goplus.verify_evm_token проверит лаунчпад и >=5% эмиссии в пуле).
        if not links:
            _min_unlinked_tx = 8 if chain == "robinhood" else 6
            if (b5 + s5) < _min_unlinked_tx:
                return self._deny(address, "no-links-inactive", f"🚫 [{tag}] {symbol}: нет ссылок и мало сделок (tx5 {b5+s5} < {_min_unlinked_tx}).")
            if _age_min >= 45.0 and liq < 4000:
                return self._deny(address, "no-links", f"🚫 [{tag}] {symbol}: токену {_age_min:.0f}м, нет ссылок при пуле <$4k — скам-риск.")

        if not self._evm_brand_ok(symbol, tag, pair_data):
            return False

        if not await self._evm_clone_ok(symbol, address, pair_data, chain, tag):
            return False

        score = 30.0
        score += min(m5, 60.0) * 0.5
        if s > 0:
            score += min(b / s, 3.0) * 8.0
        score += min(liq / 50000.0, 1.0) * 8.0
        if len(links) >= 2:
            score += 5.0
        score = min(score, 100.0)
        _is_live_organic = (b5 >= 10 and s5 >= 1 and b5 >= int(s5 * 1.2) and float((pair_data.get("volume") or {}).get("m5", 0) or 0) >= 1200.0)
        _min_score_thr = 55.0 if (chain in ("base", "bsc", "robinhood") and (liq >= 10000.0 or _is_live_organic)) else 65.0
        self.last_score = float(score)
        print(f"📊 [{tag}] {symbol}: финальный score {score:.1f} (порог {_min_score_thr:.0f}), m5 {m5:+.1f}%, m1 {m1:+.1f}%")
        if score >= _min_score_thr:
            if chain in ("robinhood", "bsc", "base", "ethereum"):
                try:
                    import goplus as _gp
                    _v_ok, _v_why = await _gp.verify_evm_token(address, chain, pair_data)
                    if not _v_ok:
                        return self._deny(address, f"antiscam-{_v_why}",
                                          f"🚫 [{tag}] {symbol}: {_v_why} — блок.")
                except Exception as _e:
                    if chain in ("bsc", "robinhood"):
                        return self._deny(address, "antiscam-err",
                                          f"🚫 [{tag}] {symbol}: ошибка антискама ({type(_e).__name__}) — {tag} риск.")
            if not await self.verify_1m_candles_not_dumping(address, pair_data, chain):
                return self._deny(address, "1m-candle-dump", f"🚫 [{tag}] {symbol}: 1м свеча в сливе / вершина пройдена.")
            self._set_sig(address, f"{tag} rule {score:.0f}%" + self._rkt_tag(address, pair_data) + await self.meme_tag(address, symbol, pair_data, chain))
            return True
        return self._deny(address, f"low-score {score:.1f}", f"· [{tag}] {symbol}: score {score:.1f} < {_min_score_thr:.0f} — недостаточно импульса.")
        
    async def analyze_growth_token(self, mint: str) -> bool:
        import config as _c
        self.signals.pop(mint, None)
        pair_data = await self.fetch_token_data(mint)
        if not pair_data:
            return None
        base = pair_data.get("baseToken", {}) or {}
        symbol = base.get("symbol", mint[:6])
        pc = pair_data.get("priceChange") or {}
        h1 = pc.get("h1", 0) or 0
        h6 = pc.get("h6", 0) or 0
        h24 = pc.get("h24", 0) or 0
        txh1 = (pair_data.get("txns") or {}).get("h1", {}) or {}
        b, s = txh1.get("buys", 0) or 0, txh1.get("sells", 0) or 0
        liq = (pair_data.get("liquidity") or {}).get("usd", 0) or 0
        if liq < getattr(_c, "GROWTH_MIN_LIQ", 200000):
            return False
        if h24 < getattr(_c, "GROWTH_MIN_H24_PCT", 5.0):
            print(f"🌱 [GROWTH] {symbol}: h24 {h24:+.1f}% — тренда вверх нет.")
            return False
        if not (getattr(_c, "GROWTH_MIN_H1_PCT", 1.0) <= h1 <= getattr(_c, "GROWTH_MAX_H1_PCT", 15.0)):
            print(f"🌱 [GROWTH] {symbol}: h1 {h1:+.1f}% вне окна входа.")
            return False
        if h6 > getattr(_c, "GROWTH_MAX_H6_PCT", 80.0):
            print(f"🌱 [GROWTH] {symbol}: h6 {h6:+.0f}% — перегрев.")
            return False
        if h6 > 0 and h1 < h6 / 6.0:
            print(f"🌱 [GROWTH] {symbol}: h1 {h1:+.1f}% < h6/6 ({h6/6.0:+.1f}%) — тренд затухает.")
            return False
        _mult = getattr(_c, "GROWTH_MIN_BUYSELL", 1.2)
        if s > 0 and b < s * _mult:
            print(f"🌱 [GROWTH] {symbol}: buys {b} / sells {s} — покупатели слабее {_mult}x.")
            return False
        if (b + s) < 50:
            print(f"🌱 [GROWTH] {symbol}: тихо ({b+s} сделок/час).")
            return False
        self._set_sig(mint, f"GROWTH h1{h1:+.1f}% h24{h24:+.0f}%")
        print(f"🌱 [GROWTH-CANDIDATE] {symbol}: h1 {h1:+.1f}% h24 {h24:+.0f}% b/s {b}/{s} liq ${liq:,.0f}")
        return True

    def pack_features(self, pair_data: dict) -> dict:
        try:
            pc = pair_data.get("priceChange") or {}
            txns = pair_data.get("txns") or {}
            txm5 = txns.get("m5", {}) or {}
            txh1 = txns.get("h1", {}) or {}
            txh = txns.get("h24", {}) or {}
            liq = (pair_data.get("liquidity") or {}).get("usd", 0) or 0
            b5, s5 = txm5.get("buys", 0) or 0, txm5.get("sells", 0) or 0
            b1, s1 = txh1.get("buys", 0) or 0, txh1.get("sells", 0) or 0
            bh, sh = txh.get("buys", 0) or 0, txh.get("sells", 0) or 0
            vm5 = (pair_data.get("volume") or {}).get("m5", 0) or 0
            vh = (pair_data.get("volume") or {}).get("h24", 0) or 0
            info = pair_data.get("info") or {}
            links_n = len(info.get("socials") or []) + len(info.get("websites") or [])
            age_min = 999.0
            try:
                _created = pair_data.get("pairCreatedAt") or 0
                if _created:
                    age_min = max(0.0, (time.time() * 1000 - float(_created)) / 60000.0)
            except Exception:
                pass
            try:
                from shadow_score import pair_shadow_score
                sh_score = round(pair_shadow_score(pair_data), 3)
            except Exception:
                sh_score = 0.0
            return {
                "price_change_m5": pc.get("m5", 0) or 0,
                "price_change_m1": pc.get("m1", 0) or 0,
                "price_change_h1": pc.get("h1", 0) or 0,
                "price_change_h24": pc.get("h24", 0) or 0,
                "volume_m5": vm5, "volume_h24": vh,
                "buys_m5": b5, "sells_m5": s5,
                "buys_h1": b1, "sells_h1": s1,
                "buys_h24": bh, "sells_h24": sh,
                "liquidity": liq, "fdv": pair_data.get("fdv", 0) or 0,
                "buy_sell_ratio": (b5 / (s5 + 1)) if (b5 + s5) > 0 else (bh / (sh + 1)),
                "vol_to_liq": (vm5 / (liq + 1)) if vm5 else (vh / (liq + 1)),
                "age_min": age_min, "links_count": links_n,
                "shadow_score": sh_score,
                "dex": pair_data.get("dexId", ""),
            }
        except Exception:
            return {}

    def conviction_size_mult(self, pair_data: dict) -> float:
        try:
            import config as _c
            pc = pair_data.get("priceChange") or {}
            m5 = (pc.get("m5", 0) or 0) / 100.0
            txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
            b5, s5 = txm5.get("buys", 0) or 0, txm5.get("sells", 0) or 0
            liq = (pair_data.get("liquidity") or {}).get("usd", 0) or 0
            if (getattr(_c, "CONVICTION_MIN_M5_PCT", 0.20) <= m5 <= getattr(_c, "CONVICTION_MAX_M5_PCT", 0.60)
                    and s5 > 0 and b5 >= s5 * getattr(_c, "CONVICTION_MIN_BUYSELL", 2.0)
                    and liq >= getattr(_c, "CONVICTION_MIN_LIQ", 30000)):
                return float(getattr(_c, "CONVICTION_MULT", 2.0))
        except Exception:
            pass
        return 1.0

    async def analyze_token_xgboost(self, mint: str, pair_data: dict, _lottery: bool = False) -> bool:
        if not pair_data:
            return False
            
        if pair_data.get("dexId") not in ("pump", "pumpfun"):
            return False

        _fdv_curve = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        if _fdv_curve > 24000:
            return self._deny(mint, "pump-graduation-cliff", f"🚫 [GRADUATION-CLIFF] {mint[:8]}: кривая Pump.fun (FDV ${_fdv_curve:,.0f} > $24k) в зоне разгрузки снайперов перед миграцией — ждём выхода на AMM.")

        # Защита от фантомных дев-дампов на старте (кейс 404: $3.1k cap, 5% кривой, мгновенный сброс дева)
        if _fdv_curve < 7500:
            return self._deny(mint, "pump-curve-too-early", f"🚫 [CURVE-TOO-EARLY] {mint[:8]}: капитализация ${_fdv_curve:,.0f} < $7.5k (~8% прогресса) — нет подушки ликвидности, зона мгновенного дев-дампа.")

        created_at = pair_data.get("pairCreatedAt")
        age_minutes = ((time.time() * 1000 - created_at) / (1000 * 60)) if created_at else 0.0
        # Защита от застрявших мёртвых кривых (кейсы B1DPPH -40% при 20м/$11.5k и TIMECHAIN -16.5% при 79м/$17.3k):
        if age_minutes > 15.0 and _fdv_curve < 18000:
            return self._deny(mint, "pump-stale-curve", f"🚫 [CURVE-STALE-FAIL] {mint[:8]}: возраст {age_minutes:.1f}м > 15м при FDV ${_fdv_curve:,.0f} < $18k — токен не смог выйти из кривой, 100% слив.")

        _vm5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        if _vm5 < 800:
            print(f"🚫 [PUMP-LOW-VOL] {mint[:8]}: объём 5m ${_vm5:,.0f} < $800 — фантомная активность, риск дев-дампа.")
            return False
            
        is_vip = self.check_hyper_rocket_momentum(pair_data)
        if is_vip:
            print(f"🚀🚀🚀 [HYPER-ROCKET BYPASS] Токен {mint} летит в космос! Игнорируем карантин возраста и соцсетей.")
            
        if created_at and not is_vip:
            _max_pump_age = float(getattr(config, "PUMP_MAX_AGE_MIN", 45.0))
            if age_minutes > _max_pump_age:  
                print(f"🚫 [PUMP-AGE] {mint[:8]}: возраст {age_minutes:.1f}м > {_max_pump_age:.0f}м — затухающий кёрв.")
                return False
                
        import pandas as pd
        import joblib
        from pump_fun_sniper import PumpFunSniper
        
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
            
        tx_velocity_1m = (buys_m5 + sells_m5) / 5.0
        # Защита от накрутки для Pump-модели: не даем ботам задирать velocity в космос
        tx_velocity_1m = min(tx_velocity_1m, 15.0)
        
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        has_socials = 1 if (has_twitter and (has_website or has_tg)) else 0
        
        top_10_holding_pct = getattr(self, '_last_top10', 0.0)
        dev_holding_pct = getattr(self, '_last_dev', 0.0)
        
        funded_from_cex = 0
        
        # Сбор полного набора фичей для модели
        pc = pair_data.get("priceChange") or {}
        txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        txh = (pair_data.get("txns") or {}).get("h24", {}) or {}
        b5 = float(txm5.get("buys", 0) or 0)
        s5 = float(txm5.get("sells", 0) or 0)
        bh = float(txh.get("buys", 0) or 0)
        sh = float(txh.get("sells", 0) or 0)
        vm5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        vh = float((pair_data.get("volume") or {}).get("h24", 0) or 0)
        liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        bs_ratio = (b5 / (s5 + 1.0)) if (b5 + s5) > 0 else ((bh / (sh + 1.0)) if (bh + sh) > 0 else 1.0)
        vol_to_liq = (vm5 / (liq + 1.0)) if vm5 else ((vh / (liq + 1.0)) if vh else 0.0)
        
        features_dict = {
            "dev_holding_pct": float(dev_holding_pct or 0.0),
            "top_10_holding_pct": float(top_10_holding_pct or 0.0),
            "tx_velocity_1m": float(tx_velocity_1m or 0.0),
            "has_socials": int(has_socials or 0),
            "funded_from_cex": int(funded_from_cex or 0),
            "price_change_m5": float(pc.get("m5", 0) or 0.0),
            "volume_m5": float(vm5),
            "buys_m5": float(b5),
            "sells_m5": float(s5),
            "liquidity": float(liq),
            "fdv": float(fdv),
            "buy_sell_ratio": float(bs_ratio),
            "vol_to_liq": float(vol_to_liq),
            "price_change_h24": float(pc.get("h24", 0) or 0.0),
            "volume_h24": float(vh),
            "buys_h24": float(bh),
            "sells_h24": float(sh),
        }
        
        if self.pump_model is None:
            return False 
        try:
            from shadow_score import pair_shadow_score, VETO_THRESHOLD
            _sh = pair_shadow_score(pair_data)
            if _sh < VETO_THRESHOLD:
                print(f"🚫 [SHADOW] {mint[:8]}: score {_sh:.2f} < {VETO_THRESHOLD}.")
                return False
        except Exception:
            pass

        # Динамическое выравнивание признаков под требования модели (устраняет ValueError: feature_names mismatch)
        expected_cols = None
        try:
            expected_cols = self.pump_model.get_booster().feature_names
        except Exception:
            pass
        if not expected_cols:
            expected_cols = getattr(self.pump_model, "feature_names_in_", None)

        if expected_cols:
            row = {col: float(features_dict.get(col, 0.0) or 0.0) for col in expected_cols}
            features = pd.DataFrame([row], columns=list(expected_cols))
        else:
            features = pd.DataFrame([features_dict])

        try:
            prob = float(self.pump_model.predict_proba(features)[0][1])
        except Exception as _pe:
            print(f"⚠️ Ошибка предсказания XGBoost {mint[:8]}: {type(_pe).__name__}: {_pe}. Фоллбэк на моментум.")
            prob = 0.85 if (is_vip or _lottery) else 0.50
        conf = float(prob) * 100
        self.last_score = conf
        print(f"🤖 XGBoost [DEX Poller]: {mint} | Score: {conf:.1f}%")
        threshold = 35.0  
        if is_vip:
            threshold = 45.0  
            print(f"🔥 [VIP] Порог XGBoost как у всех: {threshold}%")
        elif _lottery:
            threshold = 25.0
            print(f"🎰 [LOTTERY] Порог XGBoost для вертикали ослаблен: {threshold}% (сайз 0.25x)")

        ok = bool(conf >= threshold)
        if ok:
            _sig_name = f"{'VIP-' if is_vip else ''}{'LOTTERY-' if _lottery else ''}PUMP-XGB {conf:.0f}%"
            self._set_sig(mint, _sig_name)
        return ok

    async def analyze_token_raydium(self, mint: str, pair_data: dict) -> bool:
        if not pair_data:
            return False

        if (pair_data.get("dexId") or "").lower() in ("pump", "pumpfun"):
            return False

        _dex_low = (pair_data.get("dexId") or "").lower()
        _is_migrated_pump = mint.endswith("pump") and _dex_low in ("pumpswap", "raydium", "meteora", "orca")

        _c_ms = pair_data.get("pairCreatedAt") or 0
        import time as _t
        _age_min = ((_t.time() * 1000 - _c_ms) / 60000.0) if _c_ms else (0.5 if _is_migrated_pump else 999.0)

        # === ЗАЩИТА ОТ ДАМПА МИГРАЦИИ PUMP.FUN ===
        if _is_migrated_pump:
            # 1. Запрет входа в первые 1.5 минуты жизни AMM-пула (сброс instant-ботов и снайперов кривой)
            if _age_min < 1.5:
                return self._deny(mint, "post-migration-dump-zone",
                                  f"⏳ [POST-MIGRATION] {mint[:8]}: пул миграции создан всего {_age_min:.1f}м назад (< 1.5м) — зона сброса снайперов pump.fun.")

            _ray_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
            if _ray_liq < 10000.0:
                return self._deny(mint, "migrated-drained-liq",
                                  f"🚫 [POST-MIGRATION] {mint[:8]}: ликвидность ${_ray_liq:,.0f} < $10,000 — снайперы высосали SOL на миграции.")

            _fdv_check = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
            if 0 < _fdv_check < 30000.0:
                return self._deny(mint, "migrated-below-graduation",
                                  f"🚫 [POST-MIGRATION] {mint[:8]}: FDV ${_fdv_check:,.0f} < $30,000 — слит ниже уровня миграции.")

        _sol_min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 1.5))
        if _age_min < _sol_min_age:
            return self._deny(mint, "ray-young", f"⏳ [RAYDIUM] {mint[:8]}: возраст {_age_min:.1f}м < {_sol_min_age}м — ранняя flash-rug зона.")

        _ray_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        _min_ray_liq = 8000.0 if (_age_min < 60) else 12000.0
        if _ray_liq < _min_ray_liq:
            return self._deny(mint, "ray-low-liq", f"🚫 [RAYDIUM] {mint[:8]}: реальная ликвидность AMM ${_ray_liq:,.0f} < ${_min_ray_liq:,.0f}.")

        # Защита от анонимных бандлер-ферм без единой соцсети (не-pump токены с низкой ликвидностью)
        _ray_info = pair_data.get("info") or {}
        _ray_soc = _ray_info.get("socials") or []
        _ray_web = _ray_info.get("websites") or []
        if not mint.endswith("pump") and not pair_data.get("_socials_unknown", False) and len(_ray_soc) == 0 and len(_ray_web) == 0 and _ray_liq < 35000.0:
            return self._deny(mint, "ray-no-socials-anon", f"🚫 [RAY-NO-SOCIALS] {mint[:8]}: 0 соцсетей/сайтов при ликвидности ${_ray_liq:,.0f} (<$35k) — анонимный бандлер-раг.")

        _pc = pair_data.get("priceChange") or {}
        _m5 = float(_pc.get("m5", 0) or 0)
        _m1 = float(_pc.get("m1", 0) or 0)
        _h1 = float(_pc.get("h1", 0) or 0)
        _h6 = float(_pc.get("h6", 0) or 0)
        _fdv_check = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        
        if 0 < _fdv_check < 15000:
            return self._deny(mint, "ray-fdv-cliff", f"🚫 [RAYDIUM] {mint[:8]}: FDV ${_fdv_check:,.0f} < $15,000 — слишком мелкий пул.")
        if _age_min >= 60.0:
            if (_fdv_check < 35000 and _h1 < -5.0):
                return self._deny(mint, "ray-deadcat-trend", f"🚫 [RAYDIUM] {mint[:8]}: FDV ${_fdv_check:,.0f} < $35,000 при h1 {_h1:+.1f}% — слитый после миграции.")
            if (_h1 < -15.0 or _h6 < -35.0):
                return self._deny(mint, "ray-deadcat-trend", f"🚫 [RAYDIUM] {mint[:8]}: часовой/6ч тренд в глубоком сливе (h1 {_h1:+.1f}%, h6 {_h6:+.1f}%) — отскок дохлой кошки.")
        else:
            # Для молодых токенов (<60м): h1/h6 считаются от запуска, а не от часа.
            # Если токен просто обвалился без отскока — режем.
            if _h1 < -65.0 or (_h1 < -30.0 and _m5 < 5.0):
                return self._deny(mint, "ray-young-dump", f"🚫 [RAYDIUM] {mint[:8]}: молодой токен ({_age_min:.1f}м) в глубоком сливе (h1 {_h1:+.1f}%) без импульса отскока.")
            if _h1 < -15.0 or _h6 < -35.0:
                try:
                    from shadow_tracker import shadow_tracker
                    shadow_tracker.log_rejection(
                        mint=mint,
                        reason=f"ray-deadcat-young-shadow (h1 {_h1:+.1f}%, age {_age_min:.1f}m)",
                        symbol=str(((pair_data.get("baseToken") or {}).get("symbol") or mint[:6])).upper(),
                        chain="solana",
                        score=float(getattr(self, "last_score", 0.0) or 0.0),
                        rkt_score=self.rocket_scores.get(mint),
                        price=float(pair_data.get("priceUsd", 0) or 0.0),
                        liquidity=_ray_liq,
                        m5_pct=_m5,
                        source="RAY_YOUNG_PULLBACK"
                    )
                    print(f"⚡ [RAY-PULLBACK] {mint[:8]}: молодой токен ({_age_min:.1f}м) h1 {_h1:+.1f}%, отскок m5 {_m5:+.1f}% — дохлая кошка пропущена, пишется в shadow.")
                except Exception:
                    pass
        if _m1 < -2.5:
            return self._deny(mint, "ray-m1-drop", f"🚫 [RAYDIUM] {mint[:8]}: m1 {_m1:+.1f}% — активный сброс в моменте (падающий нож).")
        if _m1 > 22.0:
            return self._deny(mint, "ray-m1-blowoff", f"🚫 [RAYDIUM] {mint[:8]}: m1 {_m1:+.1f}% > +22% — вертикальный прострел за 1м, вход = вершина.")
        if _m5 < 2.5:
            return self._deny(mint, "ray-m5-flat", f"🚫 [RAYDIUM] {mint[:8]}: m5 {_m5:+.1f}% < +2.5% — нет импульса.")

        _vm5 = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))
        _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5, _s5 = int(_txm5.get("buys", 0) or 0), int(_txm5.get("sells", 0) or 0)

        # Защита от накрутки дев-ботами на PumpSwap (11 из 13 исторических сливов имели b/s >= 2.3x при buys >= 250)
        if _dex_low == "pumpswap" and (_b5 / max(_s5, 1)) >= 2.3 and _b5 >= 250:
            return self._deny(mint, "pumpswap-buybot-wall", f"🚫 [PUMPSWAP-BOT-WALL] {mint[:8]}: аномальный перекос покупок {_b5}b/{_s5}s ({_b5/max(_s5,1):.1f}x) — боты дева рисуют стенку перед сливом.")

        # Запрет перегретого часового тренда: для старых токенов (>90м) h1 > 400% — признак истощения.
        # Для молодых ракет (<90м) или pump.fun токенов h1 отражает запуск из бондинг-кривой (+500%..+3500%).
        _is_fresh_ray = mint.endswith("pump") or _age_min < 90.0
        _max_ray_h1 = 3500.0 if (_is_fresh_ray and _m5 >= 2.5) else 400.0
        if _h1 > _max_ray_h1:
            return self._deny(mint, f"ray-h1-exhausted {_h1:+.0f}%", f"🚫 [RAYDIUM] {mint[:8]}: h1 {_h1:+.0f}% > +{_max_ray_h1:.0f}% — гипер-памп завершается, покупатели истощены.")

        # Пропуск первой волны пробоя для молодых ракет, но блок перегрева на поздних волнах
        if _age_min <= 15.0 and _b5 >= _s5:
            _max_ray_m5 = 250.0
        elif _age_min <= 45.0 and _b5 >= _s5 * 1.05 and _m1 <= 25.0:
            _max_ray_m5 = 180.0
        elif _b5 >= 12 and _b5 >= _s5 * 1.05 and _m1 <= 25.0:
            _max_ray_m5 = 120.0
        elif _age_min < 45.0 and _m1 <= 22.0:
            _max_ray_m5 = 95.0
        else:
            _max_ray_m5 = 70.0
        if _m5 > _max_ray_m5:
            return self._deny(mint, f"ray-m5-late {_m5:+.0f}%", f"🚫 [RAYDIUM] {mint[:8]}: m5 {_m5:+.1f}% > {_max_ray_m5:.0f}% — опоздание на памп, вход на макушке.")

        _min_bs = 1.05 if (_vm5 >= 5000 and _m5 >= 5.0) else 1.12
        if (_b5 + _s5) < 15 or _vm5 < 1500 or (_s5 > 0 and _b5 < _s5 * _min_bs):
            return self._deny(mint, "ray-weak-flow", f"🚫 [FRESH] {mint[:8]}: слабый поток (b/s {_b5}/{_s5}, vol ${_vm5:,.0f}) — нет покупателей.")

        # Защита от реальной лавины продаж (продавцов больше, чем покупателей)
        if _s5 >= 40 and _b5 < _s5 * 1.05:
            return self._deny(mint, "ray-sell-avalanche", f"🚫 [SELL-AVALANCHE] {mint[:8]}: лавина продаж ({_s5} продаж vs {_b5} покупок за 5м) — продавцы сильнее.")

        # 1. Защита от микро-чеков ботов (накрутка объёма и транзакций)
        _avg_tx = _vm5 / max(_b5 + _s5, 1)
        if (_b5 + _s5) >= 20 and _avg_tx < 15.0:
            return self._deny(mint, "ray-wash-bot", f"🚫 [WASH-BOT] {mint[:8]}: средний чек ${_avg_tx:.1f} < $15 при {_b5+_s5} сделках — микро-боты накручивают объём.")

        liquidity = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv = float(pair_data.get("fdv", 0) or 0)
        if liquidity < _min_ray_liq:
            return self._deny(mint, "ray-micro-liq", f"📉 [RAYDIUM] {mint[:8]}: реальная ликвидность пула ${liquidity:,.0f} < ${_min_ray_liq:,.0f} — микро-пул с риском мгновенного слива.")

        # 2. Защита от картонных пулов: для молодых ракет / pump.fun ($12k liq при росте до $600k+ FDV) лимит до 85x
        _max_hollow_ratio = 85.0 if (_is_fresh_ray and _b5 >= _s5 * 1.15) else 45.0
        if liquidity > 0 and fdv > 0 and (fdv / liquidity) > _max_hollow_ratio:
            return self._deny(mint, "ray-hollow-pool", f"🚫 [HOLLOW-POOL] {mint[:8]}: FDV ${fdv:,.0f} vs Liq ${liquidity:,.0f} ({fdv/liquidity:.0f}x > {_max_hollow_ratio:.0f}x) — картонная ликвидность.")

        # 3. Защита от кругового wash-churn и дев-бандлов (накрутка объёма ботами перед сливом)
        if liquidity > 0 and (_vm5 / liquidity) > 3.5 and abs(_m5) < 12.0:
            return self._deny(mint, "ray-churn", f"🚫 [CHURN] {mint[:8]}: оборот {_vm5 / liquidity:.1f}x от ликвидности за 5м без импульса (m5 {_m5:+.1f}%) — накрутка.")
        if liquidity > 0 and (_vm5 / liquidity) > 4.5 and _age_min < 30.0:
            return self._deny(mint, "ray-fake-churn", f"🚫 [WASH-CHURN] {mint[:8]}: экстремальный оборот {_vm5 / liquidity:.1f}x от ликвидности в молодом токене ({_age_min:.1f}м) — ловушка накрутки дева.")
        if _s5 >= 30 and (_b5 / max(_s5, 1)) > 3.8 and _vm5 >= 25000 and _age_min < 30.0:
            if _avg_tx < 15.0:
                return self._deny(mint, "ray-fake-wall", f"🚫 [FAKE-BUY-WALL] {mint[:8]}: аномальный перекос b/s {_b5 / _s5:.1f}x при чеке ${_avg_tx:.1f} — боты дева.")
        if (_b5 + _s5) >= 800 and (_b5 / max(_s5, 1)) > 3.5:
            if _avg_tx < 15.0:
                return self._deny(mint, "ray-fake-wall", f"🚫 [FAKE-BUY-WALL] {mint[:8]}: накрутка {_b5}b/{_s5}s (b/s {_b5 / _s5:.1f}x) — ловушка дева.")

        # 4. Проверка безопасности холдеров и создателя
        _t10 = getattr(self, '_last_top10', 0.0)
        _dev = getattr(self, '_last_dev', 0.0)
        if _dev > 10.0:
            return self._deny(mint, "ray-dev-hold", f"🚫 [DEV-HOLD] {mint[:8]}: создатель держит {_dev:.1f}% > 10% — дамп-риск.")
        if _t10 > 35.0:
            return self._deny(mint, "ray-top10-cabal", f"🚫 [TOP10-CABAL] {mint[:8]}: топ-10 держат {_t10:.1f}% > 35% — кабал/дамп-риск.")

        import pandas as pd
        import joblib
        
        txns_h24 = (pair_data.get("txns") or {}).get("h24") or {}
        buys_h24 = float(txns_h24.get("buys", 0) or 0)
        sells_h24 = float(txns_h24.get("sells", 0) or 0)
        
        volume_h24 = float(((pair_data.get("volume") or {}).get("h24", 0) or 0))
        price_change_h24 = float(((pair_data.get("priceChange") or {}).get("h24", 0) or 0))
        
        # Защита XGBoost от накрученных скам-данных (Robust Inference/Winsorization):
        # Ограничиваем искусственно раздутый объем и покупки, чтобы модель не выдавала слепые 100%
        volume_h24 = min(volume_h24, liquidity * 3) 
        buys_h24 = min(buys_h24, sells_h24 * 3 + 20)
        
        buy_sell_ratio = buys_h24 / (sells_h24 + 1.0)
        vol_to_liq = volume_h24 / (liquidity + 1.0)
        
        # Дополнительно режем ratio для модели
        buy_sell_ratio = min(buy_sell_ratio, 4.0)
        vol_to_liq = min(vol_to_liq, 3.0)
        
        features = ['price_change_h24', 'volume_h24', 'buys_h24', 'sells_h24', 'liquidity', 'fdv', 'buy_sell_ratio', 'vol_to_liq']
        df = pd.DataFrame([{
            'price_change_h24': float(price_change_h24),
            'volume_h24': float(volume_h24),
            'buys_h24': float(buys_h24),
            'sells_h24': float(sells_h24),
            'liquidity': float(liquidity),
            'fdv': float(fdv),
            'buy_sell_ratio': float(buy_sell_ratio),
            'vol_to_liq': float(vol_to_liq)
        }])
        
        try:
            if self.raydium_model is None: return False
            prob = self.raydium_model.predict_proba(df)[0][1]
            conf = prob * 100
            symbol = pair_data.get("baseToken", {}).get("symbol", "")
            if symbol:
                lc_data = await self.fetch_lunarcrush_sentiment(symbol)
                if lc_data:
                    interactions = lc_data.get("interactions", 0)
                    sentiment = lc_data.get("sentiment", 50)
                    print(f"🌕 [LunarCrush] {symbol}: Interactions: {interactions}, Sentiment: {sentiment}%")
                    
                    if sentiment >= 70 and interactions > 500:
                        conf += 15.0 
                        print(f"📈 [LunarCrush] Хайп подтвержден! Буст +15% к Score.")
                    elif sentiment < 30:
                        conf -= 20.0
                        print(f"📉 [LunarCrush] Негативный сентимент! Штраф -20% к Score.")

            # Многофакторная калибровка скора (защита от слепых 100%):
            if _s5 > 0 and (_b5 / _s5) > 6.0 and (_b5 + _s5) >= 20:
                conf -= 15.0  # аномально однобокий стакан (риск honeypot/фальш-стенки)
            if liquidity > 0 and (fdv / liquidity) > 55.0 and not _is_fresh_ray:
                conf -= 10.0  # штраф за повышенную надутость капитализации для старых монет
            if _avg_tx < 25.0 and (_b5 + _s5) >= 30:
                conf -= 10.0  # штраф за подозрительно мелкие чеки

            # Защита от сонных старых токенов с огромным 24h объёмом (кейс CHUBBY: 24h vol $700k даёт 100% XGBoost, но токен спит/падает)
            if _age_min > 45.0 and _m5 < 5.0:
                conf -= 40.0
                print(f"💤 [RAYDIUM-MOMENTUM] {mint[:8]}: токен старше 45м с вялым импульсом (m5 {_m5:+.1f}% < +5.0%) — штраф -40% к XGBoost.")

            # Проверка 5-минутного оборота к ликвидности (активность в моменте)
            if liquidity > 0:
                _m5_turnover = _vm5 / liquidity
                if _m5_turnover < 0.08 and _age_min > 20.0:
                    conf -= 30.0
                    print(f"💤 [RAYDIUM-TURNOVER] {mint[:8]}: оборот 5м всего {_m5_turnover*100:.1f}% от ликвидности (< 8%) — нет активных торгов, штраф -30%.")

            # Буст для реальных ракет на взлете (мощный 5м импульс и приток покупателей)
            if _m5 >= 15.0 and _b5 >= _s5 * 1.15 and _vm5 >= 5000:
                conf += 20.0
                print(f"🚀 [ROCKET-BOOST] {mint[:8]}: мощный 5м импульс (+{_m5:.1f}%, vol5 ${_vm5:,.0f}) — буст +20% к Score!")
            
            print(f"🧠 Raydium XGBoost: {mint} | Score: {conf:.1f}%")
            self.last_score = float(conf)
            try:
                from shadow_score import pair_shadow_score, VETO_THRESHOLD
                _sh = pair_shadow_score(pair_data)
                if _sh < VETO_THRESHOLD:
                    return self._deny(mint, f"shadow {_sh:.2f}", f"🚫 [SHADOW] {mint[:8]}: score {_sh:.2f} < {VETO_THRESHOLD} — обучено на истории лузеров.")
            except Exception:
                pass
            threshold = float(getattr(config, "RAYDIUM_BUY_THRESHOLD", 55.0))  
            _hyper = self.check_hyper_rocket_momentum(pair_data)

            is_buy = bool(conf >= threshold)
            if is_buy:
                self._set_sig(mint, f"{'VIP-' if _hyper else ''}RAY-XGB {conf:.0f}%")
            
            if not is_buy:
                self._deny(mint, f"ray-xgb {conf:.0f}%", f"· [RAYDIUM-SCORE] {mint[:8]}: XGBoost score {conf:.1f}% < {threshold:.0f}%")
                try:
                    from shadow_tracker import ShadowTracker
                    shadow = ShadowTracker()
                    price = float(pair_data.get("priceUsd", 0))
                    shadow.log_rejection(
                        mint=mint,
                        reason=f"FOMO XGBoost low score: {conf:.1f}%",
                        score=conf,
                        price=price,
                        features=df.iloc[0].to_dict()
                    )
                except Exception as e:
                    print(f"Ошибка записи в ShadowTracker (Raydium): {e}")
                    
            return is_buy
        except Exception as e:
            print(f"⚠️ Ошибка XGBoost (analyze_token_raydium) для {mint}: {e}")
            return False

    async def analyze_token_ws(self, ws_data: dict) -> bool:
        mint = ws_data.get("mint")
        symbol = ws_data.get("symbol", "UNKNOWN")
        name = ws_data.get("name", "Unknown")
        
        has_twitter = bool(ws_data.get("twitter"))
        has_telegram = bool(ws_data.get("telegram"))
        has_website = bool(ws_data.get("website"))
        socials_count = sum([has_twitter, has_telegram, has_website])
        
        v_sol = ws_data.get("vSolInBondingCurve", 30.0)
        from sol_price import get_sol_price_sync
        sol_price = get_sol_price_sync()
        liq_usd = v_sol * sol_price
        
        initial_buy = ws_data.get("initialBuy", 0)
        
        safety_score = 40
        momentum_score = 40 if initial_buy > 0 else 20
        
        social_score = 10
        if has_twitter: social_score += 30
        if has_telegram: social_score += 30
        if has_website: social_score += 30
        social_score = min(100, social_score)
        
        alpha_score = int((safety_score * 0.35) + (momentum_score * 0.40) + (social_score * 0.25))
        
        self._save_scanned_token({
            "symbol": symbol,
            "mint": mint,
            "score": alpha_score,
            "safety": safety_score,
            "momentum": momentum_score,
            "social": social_score,
            "liquidity": liq_usd,
            "vol_24h": 0,
            "buys": 1 if initial_buy > 0 else 0,
            "sells": 0,
            "m5_change": 0,
            "age_mins": "0m (WSS)",
            "time": time.time()
        })
        
        print(f"📡 [WSS SNIPER] Пойман токен: {name} (${symbol}) | Liq: ${liq_usd:.0f} | Socials: {socials_count}")
        
        if socials_count == 0:
            print(f"🚫 [WSS] Отказ: Создатель {symbol} даже не прикрепил соцсети. 100% мусор.")
            return False
            
        if initial_buy < 0.1:
            print(f"🚫 [WSS] Отказ: Создатель вкинул слишком мало ({initial_buy} SOL). У него нет 'шкуры на кону'.")
            return False
        if initial_buy > 5.0:
            print(f"🚫 [WSS] Отказ: Создатель выкупил слишком много токенов ({initial_buy} SOL). Высокий риск монопольного дампа.")
            return False
            
        trader_pubkey = ws_data.get("traderPublicKey")
        uri = ws_data.get("uri")
        
        dev_balance_sol = 0
        description = ""
        
        import aiohttp
        session = await self.get_session()
        if True:
            if trader_pubkey:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [trader_pubkey, {"encoding": "jsonParsed"}]
                }
                try:
                    async with session.post(config.HELIUS_RPC_URL, json=payload, timeout=2) as resp:
                        data = await resp.json()
                        lamports = data.get("result", {}).get("value", {}).get("lamports", 0) if data.get("result", {}).get("value") else 0
                        dev_balance_sol = lamports / 1e9
                except Exception as e:
                    print(f"⚠️ Ошибка RPC баланса: {e}")
                    
            if uri:
                try:
                    async with session.get(uri, timeout=2) as resp:
                        meta = await resp.json()
                        description = meta.get("description", "").lower()
                except Exception:
                    pass
                    
        if dev_balance_sol < 0.1:
            print(f"🚫 [WSS] Отказ: Кошелек разработчика пуст ({dev_balance_sol:.2f} SOL). Скаммер-однодневка.")
            return False
            
        bad_words = ["test", "scam", "fuck", "shit", "nigger", "pump and dump", "rug"]
        if any(word in description for word in bad_words) or len(description) < 5:
            print(f"🚫 [WSS] Отказ: Мусорное описание на IPFS (спам/короткое).")
            return False
            
        print(f"🚀 [WSS СИГНАЛ] Входим в токен {symbol} на нулевой секунде! (Dev Wallet: {dev_balance_sol:.2f} SOL, Socials: {socials_count})")
        
        v_tok = ws_data.get("vTokensInBondingCurve", 1073000000.0)
        ws_data["priceUsd"] = (v_sol / v_tok) * sol_price
        return True

    async def fetch_lunarcrush_sentiment(self, symbol: str) -> dict:
        api_key = getattr(config, "LUNARCRUSH_API_KEY", "")
        if not api_key:
            return {}
            
        url = f"https://lunarcrush.com/api4/public/coins/{symbol}/v1"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, headers=headers, timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        coin_data = data.get("data", {})
                        
                        return {
                            "social_volume": coin_data.get("social_volume_24h", 0),
                            "interactions": coin_data.get("interactions_24h", 0),
                            "sentiment": coin_data.get("sentiment", 50)
                        }

            except Exception as e:
                pass
        return {}
