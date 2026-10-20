import aiohttp
from datetime import datetime, timezone
import time
import config
from sentiment import analyze_sentiment
from ta_tools import TATools
import math

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


class Analyzer:
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
            if any(k in s for k in ("LOTTERY", "DIPBUY", "COPY")):
                return None
            _ch_veto = str((pair_data or {}).get("chainId", "")).lower()
            if _ch_veto in ("robinhood", "base", "bsc", "ethereum"):
                # На EVM скор ракеты калиброван иначе из-за специфики L2 (меньше объем, чем на Solana Pump.fun).
                # Для SCOUT (<15м) отсекаем мертвецов и бото-накрутку (< 0.12, кейсы RIOT 0.06, ROBINBOT 0.02)
                # Для остальных входов отсекаем пустые стаканы (< 0.08)
                self._rkt_tag(address, pair_data or {})
                sc = self.rocket_scores.get(address)
                if "SCOUT" in s:
                    if sc is not None and sc < 0.25:
                        return f"RKT-вето {sc:.2f}: слабый скор для EVM SCOUT (< 0.25, бото-стакан)"
                else:
                    if sc is not None and sc < 0.08:
                        return f"RKT-вето {sc:.2f}: пустой/мертвый стакан EVM (< 0.08)"
                return None

            if "SCOUT" in s:
                # Для SCOUT не пускаем падающие ножи со скором ниже 0.45 (кейс Animates 0.38)
                self._rkt_tag(address, pair_data or {})
                sc = self.rocket_scores.get(address)
                if sc is not None and sc < 0.45:
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
        """Отказ с записью причины в signals и отправкой в Supabase (rejected_tokens)."""
        print(msg)
        self._set_sig(key, f"SKIP {short}")
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
    def is_churn(b5, s5, m5, n_min: int = 18, liq: float = 0.0, vol5: float = 0.0) -> bool:
        """CHURN/WASH (анализ паттернов накрутки): высокий оборот без перевеса покупателей,
        микро-сделки ботов или аномальный оборот к ликвидности."""
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
            with open("scanned_tokens.json", "w") as f:
                _json.dump(data[:150], f)
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
            if isinstance(pd, dict):
                if not symbol:
                    symbol = ((pd.get("baseToken") or {}).get("symbol")) or ""
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
            # 1. Сканируем топовые (Boosted) монеты — через ГЛОБАЛЬНЫЙ семафор DS (3)
            try:
                status, data = await fetch_json(config.DEXSCREENER_LATEST, timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener boosts fetch error: {e}")

            # 2. Сканируем новые профили, чтобы не пропускать свежие ракеты
            try:
                status, data = await fetch_json(config.DEXSCREENER_PROFILES, timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener profiles fetch error: {e}")
                
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

        # Обогащаем реальным изменением за 1 минуту (DexScreener не отдает m1 в priceChange!)
        try:
            px = float(best_pair.get("priceUsd", 0) or 0)
            if px > 0 and mint:
                now = time.time()
                if not hasattr(Analyzer, "_sol_price_obs"):
                    Analyzer._sol_price_obs = {}
                hist = Analyzer._sol_price_obs.setdefault(mint, [])
                hist.append((now, px))
                if len(hist) > 40:
                    hist[:] = [x for x in hist if now - x[0] <= 600.0]
                if len(Analyzer._sol_price_obs) > 4000:
                    Analyzer._sol_price_obs.clear()

                recent_5m = [p for ts, p in hist if now - ts <= 300.0 and p > 0]
                if recent_5m:
                    peak_5m = max(recent_5m)
                    if peak_5m > 0:
                        best_pair["_drop_from_peak_5m"] = (px - peak_5m) / peak_5m * 100.0

                cands = [(abs((now - ts) - 60.0), ts, p) for ts, p in hist if 12.0 <= (now - ts) <= 150.0 and p > 0]
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
        for i in range(0, len(todo), 30):
            chunk = todo[i:i + 30]
            try:
                status, data = await fetch_json(
                    "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(chunk),
                    timeout=10, retries=1)
            except Exception:
                continue
            if status != 200 or not data:
                continue
            by_mint = {}
            for p in (data.get("pairs") or []):
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
        # 1. Свежий пакетный кэш (prefetch_token_data) — без сетевого запроса
        _hit = getattr(self, "_prefetch_cache", {}).get(mint)
        if _hit and time.time() - _hit[0] < float(getattr(config, "PREFETCH_TTL_SEC", 10)):
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



    async def is_clone(self, symbol: str, current_mint: str, current_created_at: int, current_fdv: float) -> bool:
        """Проверяет, является ли этот токен дешевой копией (клоном) более старого или крупного оригинала."""
        if not symbol or len(symbol) <= 2:
            return False 
            
        data = await self._fetch_dex_search(symbol)
        if data:
                    pairs = data.get("pairs") or []

                    for p in pairs:
                        if p.get("chainId") == "solana":
                            p_symbol = p.get("baseToken", {}).get("symbol", "").upper()
                            p_mint = p.get("baseToken", {}).get("address", "")

                            if p_symbol == symbol.upper() and p_mint != current_mint:
                                p_created_at = p.get("pairCreatedAt", float('inf'))
                                p_fdv = p.get("fdv", 0)

                                if p_created_at < current_created_at and p_fdv > 250000:
                                    return True

                                if p_fdv > (current_fdv * 10) and p_fdv > 500000:
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

        if pair_data.get("_price_divergence"):
            _div_r = float(pair_data.get("_price_divergence_ratio", 0.0) or 0.0)
            print(f"🚫 [PRICE-SPLIT] {symbol} ({mint[:8]}): рассинхрон цен между пулами {_div_r:.1f}x (миграция/гэп) — блок входа.")
            return False
        
        scam_keywords = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "XAI", "NEURALINK", "PAYPAL",
                         "NVDA", "GOOG", "AMZN", "META", "NFLX", 
                         "PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN",
                         "ANTHROPIC", "CLAUDE", "OPENAI", "CHATGPT", "DEEPSEEK", "GROK", "GEMINI", "PERPLEXITY"]
                         
        if any(keyword in symbol for keyword in scam_keywords) or any(keyword in name for keyword in scam_keywords):
            if self._brand_whitelisted(mint):
                print(f"✅ Вайтлист: {symbol} — известный контракт, мимикри-фильтр пропущен.")
            else:
                print(f"🚫 Мусор: Токен {symbol} мимикрирует под известный бренд/мем. Это 100% scam.")
                return False
            
        is_vip = self.check_hyper_rocket_momentum(pair_data) and getattr(config, "VIP_ENTRIES_ENABLED", False)

        # === VIP OVERHEAT GUARD: не покупаем вершину вертикали ===
        if is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            _m1 = _pc.get("m1", 0) or 0
            if _m5 > getattr(config, "VIP_MAX_M5_PCT", 0.40) * 100:
                print(f"🚫 [VIP OVERHEAT] {mint}: m5 {_m5:+.0f}% — вертикаль уже прошла, вход = вершина.")
                return False
            if _m1 < -3.5:
                print(f"🚫 [VIP REVERSAL] {mint}: m1 {_m1:+.1f}% — резкий откат за 1м, ждём pullback.")
                return False
            if _m1 > 15.0:
                print(f"🚫 [VIP SPIKE] {mint}: m1 {_m1:+.1f}% — свеча-spike в моменте, вход = вершина. Ждём.")
                return False
            if _m5 < getattr(config, "VIP_MIN_M5_PCT", 5.0):
                print(f"🚫 [VIP FLAT] {mint}: объём есть, а импульса нет (m5 {_m5:+.1f}% < +10%) — флет съест комиссиями.")
                return False

        _lottery = False
        _created_pre = pair_data.get("pairCreatedAt") or 0
        _age_min_pre = (time.time() * 1000 - _created_pre) / 60000.0 if _created_pre else 999
        _sol_min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 2.5))
        if _age_min_pre < _sol_min_age:
            print(f"⏳ [ENTRY] {mint[:8]}: возраст {_age_min_pre:.1f}м < {_sol_min_age}м — flash-rug зона дева, ждём созревания.")
            return None
        if pair_data:
            _dex_pre = str(pair_data.get("dexId", "")).lower()
            if _dex_pre not in ("pump", "pumpfun"):
                _fdv_pre = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                _pc_pre = pair_data.get("priceChange") or {}
                _h1_pre = float(_pc_pre.get("h1", 0) or 0)
                _h6_pre = float(_pc_pre.get("h6", 0) or 0)
                if 0 < _fdv_pre < 46000:
                    print(f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: AMM FDV ${_fdv_pre:,.0f} < $46k (ниже цены миграции Pump.fun) — слитый токен.")
                    return False
                if _h1_pre < -15.0 or _h6_pre < -35.0:
                    print(f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: часовой/6ч тренд в глубоком сливе (h1 {_h1_pre:+.1f}%, h6 {_h6_pre:+.1f}%) — отскок дохлой кошки.")
                    return False
        if not is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            _m1 = _pc.get("m1", 0) or 0
            _h1 = _pc.get("h1", 0) or 0
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
                    return False
            if _m5 >= getattr(config, "LOTTERY_MIN_M5_PCT", 1.0) * 100:
                if _m1 < 0:
                    print(f"🚫 [LOTTERY] {mint}: m5 {_m5:+.0f}%, но m1 {_m1:+.1f}% — вертикаль откатывает, это вершина.")
                    return False
                print(f"🎰 [LOTTERY] {mint}: вертикаль m5 {_m5:+.0f}%, m1 {_m1:+.1f}% — кандидат на вход уменьшенным сайзом.")
                _lottery = True
            else:
                if _m5 < getattr(config, "PULLBACK_MIN_M5_PCT", 0.025) * 100:
                    print(f"🚫 [ENTRY] {mint}: m5 {_m5:+.1f}% < импульса не было, пропуск.")
                    return False
                _m1_max = getattr(config, "PULLBACK_M1_MAX_PCT", 0.20) * 100
                if _m1 > _m1_max:
                    print(f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% > +{_m1_max:.0f}% — вертикаль в процессе, купим вершину. Ждём откат.")
                    return False
                if _m1 < getattr(config, "PULLBACK_M1_MIN_PCT", -0.15) * 100:
                    print(f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% — импульс схлопнулся, это дамп, не откат.")
                    return False
                _max_h1_pct = getattr(config, "PULLBACK_MAX_H1_PCT", 25.0) * 100
                if _h1 > _max_h1_pct:
                    print(f"🚫 [ENTRY] {mint}: h1 {_h1:+.0f}% — уже улетел, поздно.")
                    return False
            _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _b5, _s5 = _txm5.get("buys", 0) or 0, _txm5.get("sells", 0) or 0
            if _s > 0 and _b < _s * 1.1 and not (_m5 >= 3.0 and _s5 > 0 and _b5 >= _s5 * 1.2):
                print(f"🚫 [ENTRY] {mint}: buys {_b} / sells {_s} — нет давления покупателей.")
                return False
            if _s == 0 and _b > 10:
                print(f"🚫 [HONEYPOT] {mint}: {_b} покупок и 0 продаж! Очевидный скам/honeypot.")
                return False
            _v24_min = 3000 if _age_min_pre < 30 else 10000
            if _v24 < _v24_min:
                print(f"🚫 [ENTRY] {mint}: vol24h ${_v24:,.0f} < ${_v24_min:,.0f} — совсем нет объёма.")
                return False

            _tx_min = 12 if _age_min_pre < 60 else 18
            if (_b5 + _s5) < _tx_min:
                print(f"🚫 [VELOCITY] {mint}: txns m5 {_b5 + _s5} < {_tx_min} — слишком медленно, нет органического FOMO.")
                return False
            if _s5 > 0:
                mult = 1.0
                if _b5 < _s5 * mult:
                    print(f"🚫 [VELOCITY] {mint}: buy/sell m5 {_b5}/{_s5} < {mult}x — {'(лотерея, ослаблено)' if _lottery else 'нет буфера покупателей'}")
                    return False
            _cur_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
            if _cur_liq == 0 and str(pair_data.get("dexId", "")).lower() in ("pump", "pumpfun"):
                _cur_liq = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0) * 0.40
            if not _lottery and self.is_churn(_b5, _s5, _m5, liq=_cur_liq):
                print(f"🚫 [CHURN] {mint}: {_b5 + _s5} сделок, b/s {_b5 / _s5:.2f}, m5 {_m5:+.1f}% — накрутка, не FOMO.")
                return False
            _socials = (pair_data.get("info") or {}).get("socials") or []
            _socials_unknown = bool(pair_data.get("_socials_unknown", False))
            _created = pair_data.get("pairCreatedAt") or 0
            if _created and not _socials_unknown:
                _age_h = (time.time() * 1000 - _created) / 3.6e6
                if _age_h < 6 and isinstance(_socials, list) and len(_socials) == 0 and not _lottery and _cur_liq < 25000:
                    print(f"🚫 [SOCIAL] {mint}: нет соцсетей при возрасте {_age_h:.1f}ч и ликве <$25k — высокий скам-риск.")
                    return False
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
            _min_liq = 12000 if _age_min_pre < 30 else getattr(config, "MIN_LIQUIDITY", 20000)

        if liquidity < _min_liq:
            print(f"📉 Изоляция: {symbol} имеет микро-пул (${liquidity:.0f} < ${_min_liq//1000}k). Риск 100% проскальзывания.")
            return False
            
        current_created_at = pair_data.get("pairCreatedAt", 0)
        current_fdv = pair_data.get("fdv", 0)
# СТАЛО: если в монете мощный независимый объем (b5 >= 60 и b5 > s5 * 1.3), не блокируем как клон:
        _tx5_cl = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5_cl = int(_tx5_cl.get("buys", 0) or 0)
        _s5_cl = int(_tx5_cl.get("sells", 0) or 0)
        _strong_fomo = (_b5_cl >= 50 and _b5_cl >= _s5_cl * 1.25)

        if not _strong_fomo and await self.is_clone(symbol, mint, current_created_at, current_fdv):
            print(f"🚫 Мусор: Токен {symbol} является клоном! На DexScreener найден более старый/крупный оригинал.")
            return False


        if is_vip:
            print(f"🚀 [VIP] {symbol}: Пропуск проверок соцсетей из-за гипер-моментума (проверка клонов пройдена)!")
        else:
            info = pair_data.get("info", {})
            socials = info.get("socials", [])
            websites = info.get("websites", [])
            
            has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
            has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
            has_website = len(websites) > 0
            
            _socials_unknown = bool(pair_data.get("_socials_unknown", False))
            if not _socials_unknown and not _lottery and liquidity < 25000:
                if not (has_twitter or has_tg or has_website):
                    print(f"🚫 Мусор: У {mint} вообще нет ни одной соцсети или сайта.")
                    return False
            
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
                bundle_suspects = [round(pct, 1) for (amt, pct, owner) in top_10 if pct >= 1.5]
                if len(bundle_suspects) >= 3:
                    from collections import Counter
                    counts = Counter(bundle_suspects)
                    most_common_pct, count = counts.most_common(1)[0]
                    if count >= 3 and (most_common_pct * count) >= 6.0:
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
                
                # Защита от скрытого второго кошелька дева: ни один кит не должен держать > 10%
                largest_whale_pct = top_10[0][1] if top_10 else 0.0
                max_single_whale = 10.0
                if largest_whale_pct > max_single_whale:
                    print(f"🚫 [АНТИСКАМ] Кит держит {largest_whale_pct:.1f}% > {max_single_whale:.0f}% (риск скрытого дева/дампа). Блокируем.")
                    return False
                elif top_10_sum_pct > max_allowed_pct:
                    print(f"🚫 [АНТИСКАМ] Топ-10 держат {top_10_sum_pct:.1f}% (Лимит {max_allowed_pct}%). Блокируем.")
                    return False
            else:
                # Фоллбэк: если RugCheck временно недоступен (404/429), проводим полный аудит через GoPlus
                try:
                    import goplus as _gp
                    _g_ok, _g_why = await _gp.check_solana(mint)
                    if not _g_ok:
                        print(f"🚫 [GOPLUS] {symbol} ({mint[:8]}): {_g_why} — блок входа.")
                        return False
                    # Извлекаем и проверяем реальные метрики китов и создателя
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
                        print(f"🛡️ [GOPLUS] RugCheck 404, GoPlus подтвердил отсутствие ловушек у {mint[:8]}.")
                except Exception as _ge:
                    print(f"⚠️ Не удалось проверить через RugCheck и GoPlus ({_ge}). Блокируем вход.")
                    return False
        except Exception as e:
            print(f"⚠️ Ошибка RugCheck: {str(e)[:50]}.")
            try:
                import goplus as _gp
                _g_ok, _g_why = await _gp.check_solana(mint)
                if not _g_ok:
                    return False
                _h_stats = _gp.get_solana_holder_stats(mint)
                if _h_stats.get("has_data"):
                    self._last_top10 = _h_stats.get("top_10_pct", 0.0)
                    self._last_dev = _h_stats.get("dev_pct", 0.0)
            except Exception:
                return False
        except Exception as e:
            print(f"⚠️ Ошибка Jito-bundle: {e}")
            return False

        try:
            import goplus as _gp
            _g_ok, _g_why = await _gp.check_solana(mint)
            if not _g_ok:
                print(f"🚫 [GOPLUS] {symbol} ({mint[:8]}): {_g_why} — блок входа.")
                return False
        except Exception as e:
            print(f"⚠️ GoPlus err: {type(e).__name__} (fail-open, вход разрешён).")

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
            _floor_liq = 15000 if (pair_data.get("chainId") or "").lower() == "solana" else 5000
        if liq < _floor_liq:
            print(f"🚫 [ANTI-RUG] {symbol} ({mint[:8]}): Ликвидность всего ${liq:,.0f} < ${_floor_liq}. Слишком опасно.")
            return False

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
            if c0_pct > 28.0:
                print(f"🚫 [1M-OVERHEAT] {symbol} ({address[:8]}): текущая 1м свеча +{c0_pct:.1f}% > +28% — вертикальный прострел, вход = вершина.")
                return False

            # Б) Прошлая минутная свеча обвалилась, идут 2 красные свечи подряд или экстремальный blow-off
            if len(ohlcv) >= 2:
                c1 = ohlcv[1]
                c1_open, c1_high, c1_low, c1_close = float(c1[1] or 0), float(c1[2] or 0), float(c1[3] or 0), float(c1[4] or 0)
                c1_pct = ((c1_close - c1_open) / c1_open * 100.0) if c1_open > 0 else 0.0
                if c1_pct <= -6.0 and c0_pct < 2.0:
                    print(f"🚫 [1M-CRASH] {symbol} ({address[:8]}): прошлая 1м свеча слилась на {c1_pct:+.1f}% без откупа (сейчас {c0_pct:+.1f}%) — обвал в процессе.")
                    return False
                if c1_pct <= -1.5 and c0_pct <= -1.0 and eff_close <= c1_close:
                    print(f"🚫 [2-RED-1M] {symbol} ({address[:8]}): 2 красные минутные свечи подряд ({c1_pct:+.1f}%, {c0_pct:+.1f}%) — импульс сломлен.")
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

    def _evm_brand_ok(self, symbol: str, tag: str) -> bool:
        """Блэклист мимикрии под бренды. Бесплатно, без сети."""
        if any(k in symbol.upper() for k in self._EVM_SCAM_KW):
            print(f"🚫 [{tag}] {symbol}: мимикрия под бренд/мем — 100% скам.")
            return False
        return True

    async def _evm_clone_ok(self, symbol: str, address: str, pair_data: dict, chain: str, tag: str) -> bool:
        """Внутрисетевой двойник старше/жирнее или серийный спам одноимённых пулов = скам. Кросчейн = мультчейн, ок."""
        if len(symbol) <= 2:
            return True
        try:
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
            _mine_fdv = float(pair_data.get("fdv", 0) or 0)
            _mine_liq = float((pair_data.get("liquidity", {}) or {}).get("usd", 0) or 0)
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
                    # Клон, если старший оригинал на этой же сети очевидно крупнее
                    if _is_older and _p_liq >= 25000.0 and _pf >= 100000.0 and (_pf > _mine_fdv * 2.0 or _p_liq > _mine_liq * 1.5):
                        print(f"🚫 [{tag}] {symbol}: клон (оригинал {_pa[:10]} старше/жирнее).")
                        return False
                    # Серийный раггер (мёртвые пулы-двойники): проверяется ТОЛЬКО для анонимных токенов без ссылок или микро-пулов
                    if not _mine_links or _mine_liq < 15000.0:
                        if float(_p_pc.get("h24", 0) or 0) <= -65.0 or ((_p_b5 + _p_s5) == 0 and _p_liq < 3000.0):
                            dead_clones += 1
            if dead_clones >= 2 and (not _mine_links or _mine_liq < 15000.0):
                print(f"🚫 [{tag}] {symbol}: обнаружено {dead_clones} обвалившихся пулов-двойников на {chain} — серийный раггер!")
                return False
        except Exception:
            pass
        return True

    async def analyze_robinhood_token(self, address: str, chain: str = "robinhood") -> bool:
        import evm_data
        self.last_signal = ""
        self.last_score = 0.0
        self.signals.pop(address, None)
        tag = (evm_data.CHAINS.get(chain) or {}).get("tag", chain.upper())
        min_liq = (evm_data.CHAINS.get(chain) or {}).get("min_liq",
                  getattr(config, "ROBINHOOD_MIN_LIQUIDITY", 8000))
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
        _scout_min_liq = 2500.0 if chain in ("bsc", "robinhood", "base") else float(min_liq)
        if (0.5 <= _age_min < 15.0 and 0.8 <= m5 <= 800.0 and 0.0 <= h1 <= 1500.0
                and (b5 + s5) >= 8 and liq >= _scout_min_liq
                and s5 >= 2 and s >= 2 and b5 >= int(s5 * 1.15) and (b5 / max(s5, 1)) <= 7.0):
            if not self._evm_brand_ok(symbol, tag):
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
            # 2. Защита от картонных пулов (FDV / Liq > 80x)
            _fdv_scout = float(pair_data.get("fdv", 0) or 0)
            if liq > 0 and _fdv_scout > 0 and (_fdv_scout / liq) > 80.0:
                return self._deny(address, "hollow-pool",
                                  f"🚫 [{tag}-SCOUT] {symbol}: FDV ${_fdv_scout:,.0f} vs Liq ${liq:,.0f} — картонный пул.")
            # 2.1. Защита от фантомных пулов (Ghost Pools: в пуле $20k фантиков, но реального ETH < 0.35, кейс UHI)
            _q_amt = float((pair_data.get("liquidity") or {}).get("quote", 0) or 0)
            _q_sym = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
            if chain in ("robinhood", "base", "ethereum"):
                if ("ETH" in _q_sym or "WETH" in _q_sym) and 0 < _q_amt < 0.35:
                    return self._deny(address, "ghost-pool-quote",
                                      f"🚫 [{tag}-SCOUT] {symbol}: фантомный пул (в пуле всего {_q_amt:.6f} ETH < 0.35 ETH — нет реальных денег, ловушка).")
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
            # Защита от бото-накрутки и скамов с околонулевым скором ракеты (RIOT 0.06, ROBINBOT 0.02, VAULTLINGS 0.23)
            _rkt_tag_str = self._rkt_tag(address, pair_data)
            _sc_val = self.rocket_scores.get(address)
            if _sc_val is not None and _sc_val < 0.25:
                return self._deny(address, f"scout-low-rkt {_sc_val:.2f}",
                                  f"🚫 [{tag}-SCOUT] {symbol}: скор ракеты {_sc_val:.2f} < 0.25 — накрутка ботов/слабый интерес.")
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
        if chain in ("robinhood", "base", "ethereum"):
            if ("ETH" in _q_sym_evm or "WETH" in _q_sym_evm) and 0 < _q_amt_evm < 0.35:
                return self._deny(address, "ghost-pool-quote",
                                  f"🚫 [{tag}] {symbol}: фантомный пул (в пуле всего {_q_amt_evm:.6f} ETH < 0.35 ETH — нет реальных денег).")
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
                if _age_min < 10.0:
                    print(f"⏳ [{tag}] {symbol}: возраст {_age_min:.1f}м, m5 {m5:+.1f}% — токен только родился, перепроверим через 20с.")
                    return None
                return self._deny(address, f"dead-flat m5 {m5:+.1f}%", f"· [{tag}] {symbol}: флет/остывание (age: {_age_min:.0f}m, liq: ${liq:,.0f}, m5: {m5:+.1f}%, tx5m: {b5}b/{s5}s, vol5m: ${_vm5:.0f})")

            # 2. Мамонты / стейблкоины / utility (AERO, USDC, PAPER, VELVET)
            if liq > 350000.0 or any(sb in symbol.upper() for sb in ("USDC", "USDT", "DAI")):
                return self._deny(address, "dex-giant", f"· [{tag}] {symbol}: тяжелый пул (${liq:,.0f}) или стейблкоин — не мем-ракета")

            # 3. Накопление на растущем тренде (h1 от +10% до +250%, m5 >= -2.0%, m1 >= 0%, покупатели >= 1.35x продавцов)
            if (-2.0 <= m5 < _evm_min_m5 and 10.0 <= h1 <= 250.0 and m1 >= 0.0
                    and (_b1 + _s1) >= 35 and _b1 >= _s1 * 1.25 and b5 >= int(s5 * 1.35)
                    and not self.is_churn(b5, s5, m5, liq=liq)):
                if not self._evm_brand_ok(symbol, tag):
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

            return self._deny(address, f"flat m5 {m5:+.1f}%", f"· [{tag}] {symbol}: флет m5 {m5:+.1f}%")
        _max_m5 = 350.0 if chain in ("robinhood", "base", "bsc") else 85.0
        # При органическом перевесе покупателей расширяем коридор m5, чтобы забирать супер-ракеты
        # (кейсы 特斯拉能飞 +994% -> +3023%, INFLUENCE +159% -> +950%, VERDIS +289%, KAIDA +270%, BRIVON +393%, NULL +375%)
        if chain in ("robinhood", "base", "bsc"):
            if _age_min < 15.0 and s5 > 0 and b5 >= int(s5 * 1.5) and not self.is_churn(b5, s5, m5, liq=liq):
                _max_m5 = 1200.0  # Свежая супер-ракета на старте: вход на импульсе (кейс 特斯拉能飞 m5 +994%)
            elif liq >= 15000.0 and s5 > 0 and b5 >= s5 and not self.is_churn(b5, s5, m5, liq=liq):
                _max_m5 = 600.0   # Мощное ралли на глубоком пуле ($15k+): вход на 2-й ступени ракеты
            elif s5 > 0 and b5 >= s5 and not self.is_churn(b5, s5, m5, liq=liq):
                _max_m5 = 450.0   # Умеренный пул
            else:
                _max_m5 = 150.0

        if m5 > _max_m5:
            return self._deny(address, f"top m5 {m5:+.0f}%", f"🚫 [{tag}-OVERHEAT] {symbol}: m5 {m5:+.1f}% — вертикаль уже прошла, вход = вершина.")
        
        if self.is_churn(b5, s5, m5, liq=liq):
            return self._deny(address, f"churn {b5}/{s5} m5 {m5:+.1f}%",
                              f"🚫 [{tag}-CHURN] {symbol}: {b5 + s5} сделок без перевеса (b/s {b5 / (s5+1):.2f}) — накрутка, не FOMO.")
        # Ослабление m1-green: мощные ракеты пробивают импульсом без отката.
        _max_m1 = 45.0 if (s5 > 0 and b5 >= 1.5 * s5 and (b5 + s5) >= 8) else (25.0 if (s5 > 0 and b5 >= 1.2 * s5) else 15.0)
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

        _up = symbol.upper()
        _scam_kw = ["AAPL", "MSFT", "TSLA", "NVDA", "GOOG", "AMZN", "META", "NFLX",
                    "PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN"]
        if any(k in _up for k in _scam_kw) and not self._brand_whitelisted(address):
            print(f"🚫 [{tag}] {symbol}: мимикрия под бренд/мем — 100% скам.")
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
        self.last_score = float(score)
        print(f"📊 [{tag}] {symbol}: финальный score {score:.1f} (порог 65), m5 {m5:+.1f}%, m1 {m1:+.1f}%")
        # СТАЛО:
        _min_score_thr = 55.0 if chain == "base" else 65.0
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
        return False
        
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
        if _fdv_curve > 32000:
            print(f"🚫 [GRADUATION-CLIFF] {mint[:8]}: кривая Pump.fun у потолка миграции (FDV ${_fdv_curve:,.0f} > $32k) — риск мгновенного дампа при выходе на AMM.")
            return False
            
        is_vip = self.check_hyper_rocket_momentum(pair_data)
        if is_vip:
            print(f"🚀🚀🚀 [HYPER-ROCKET BYPASS] Токен {mint} летит в космос! Игнорируем карантин возраста и соцсетей.")
            
        created_at = pair_data.get("pairCreatedAt")
        if created_at and not is_vip:
            age_minutes = (time.time() * 1000 - created_at) / (1000 * 60)
            if age_minutes > 15:  
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
        
        features = pd.DataFrame([{
            "dev_holding_pct": float(dev_holding_pct or 0.0),
            "top_10_holding_pct": float(top_10_holding_pct or 0.0),
            "tx_velocity_1m": float(tx_velocity_1m or 0.0),
            "has_socials": int(has_socials or 0),
            "funded_from_cex": int(funded_from_cex or 0)
        }])
        
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
        prob = self.pump_model.predict_proba(features)[0][1]
        conf = float(prob) * 100
        self.last_score = conf
        print(f"🤖 XGBoost [DEX Poller]: {mint} | Score: {conf:.1f}%")
        import config
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

        _ray_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        if _ray_liq < 15000:
            print(f"🚫 [RAYDIUM] {mint[:8]}: реальная ликвидность AMM ${_ray_liq:,.0f} < $15,000.")
            return False

        _c_ms = pair_data.get("pairCreatedAt") or 0
        import time as _t
        _age_min = ((_t.time() * 1000 - _c_ms) / 60000.0) if _c_ms else 999.0
        _sol_min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 2.5))
        if _age_min < _sol_min_age:
            print(f"⏳ [RAYDIUM] {mint[:8]}: возраст {_age_min:.1f}м < {_sol_min_age}м — ранняя flash-rug зона.")
            return False

        _pc = pair_data.get("priceChange") or {}
        _m5 = float(_pc.get("m5", 0) or 0)
        _m1 = float(_pc.get("m1", 0) or 0)
        _h1 = float(_pc.get("h1", 0) or 0)
        _h6 = float(_pc.get("h6", 0) or 0)
        _fdv_check = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        # Защита от пост-миграционных трупов (Gemma -$4.12, sBTC -$0.49):
        # Pump.fun мигрирует при ~$42k-$69k MC. Если токен на AMM уже упал ниже $46k или имеет красный часовой/6ч тренд — это слитый токен!
        if 0 < _fdv_check < 46000:
            print(f"🚫 [RAYDIUM] {mint[:8]}: FDV ${_fdv_check:,.0f} < $46,000 (ниже цены миграции Pump.fun) — слитый после миграции токен.")
            return False
        if _h1 < -15.0 or _h6 < -35.0:
            print(f"🚫 [RAYDIUM] {mint[:8]}: часовой/6ч тренд в глубоком сливе (h1 {_h1:+.1f}%, h6 {_h6:+.1f}%) — отскок дохлой кошки.")
            return False
        if _m1 < -3.5:
            print(f"🚫 [RAYDIUM] {mint[:8]}: m1 {_m1:+.1f}% — активный сброс в моменте (падающий нож).")
            return False
        if _m5 < 2.5:
            print(f"🚫 [RAYDIUM] {mint[:8]}: m5 {_m5:+.1f}% < +2.5% — нет импульса.")
            return False

        _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5, _s5 = _txm5.get("buys", 0) or 0, _txm5.get("sells", 0) or 0
        _vm5 = (pair_data.get("volume") or {}).get("m5", 0) or 0
        if (_b5 + _s5) < 20 or _vm5 < 2000 or (_s5 > 0 and _b5 < _s5 * 1.15):
            print(f"🚫 [FRESH] {mint[:8]}: слабый поток (b/s {_b5}/{_s5}, vol ${_vm5:,.0f}) — нет покупателей.")
            return False

        # Защита от реальной лавины продаж (продавцов больше, чем покупателей)
        if _s5 >= 40 and _b5 < _s5 * 1.05:
            print(f"🚫 [SELL-AVALANCHE] {mint[:8]}: лавина продаж ({_s5} продаж vs {_b5} покупок за 5м) — продавцы сильнее.")
            return False

        # 1. Защита от микро-чеков ботов (накрутка объёма и транзакций)
        _avg_tx = _vm5 / max(_b5 + _s5, 1)
        if (_b5 + _s5) >= 20 and _avg_tx < 15.0:
            print(f"🚫 [WASH-BOT] {mint[:8]}: средний чек ${_avg_tx:.1f} < $15 при {_b5+_s5} сделках — микро-боты накручивают объём.")
            return False

        liquidity = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv = float(pair_data.get("fdv", 0) or 0)
        if liquidity < 12000:
            print(f"📉 [RAYDIUM] {mint[:8]}: реальная ликвидность пула ${liquidity:,.0f} < $12,000 — микро-пул с риском мгновенного слива.")
            return False

        # 2. Защита от картонных пулов (FDV / Liquidity > 40x — 1 кит продает и пул схлопывается)
        if liquidity > 0 and fdv > 0 and (fdv / liquidity) > 40.0:
            print(f"🚫 [HOLLOW-POOL] {mint[:8]}: FDV ${fdv:,.0f} vs Liq ${liquidity:,.0f} ({fdv/liquidity:.0f}x > 40x) — картонная ликвидность.")
            return False

        # 3. Защита от кругового wash-churn (350% оборота за 5м без движения цены)
        if liquidity > 0 and (_vm5 / liquidity) > 3.5 and abs(_m5) < 12.0:
            print(f"🚫 [CHURN] {mint[:8]}: оборот {_vm5 / liquidity:.1f}x от ликвидности за 5м без импульса (m5 {_m5:+.1f}%) — накрутка.")
            return False

        # 4. Проверка безопасности холдеров и создателя
        _t10 = getattr(self, '_last_top10', 0.0)
        _dev = getattr(self, '_last_dev', 0.0)
        if _dev > 10.0:
            print(f"🚫 [DEV-HOLD] {mint[:8]}: создатель держит {_dev:.1f}% > 10% — дамп-риск.")
            return False
        if _t10 > 35.0:
            print(f"🚫 [TOP10-CABAL] {mint[:8]}: топ-10 держат {_t10:.1f}% > 35% — кабал/дамп-риск.")
            return False

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
            if liquidity > 0 and (fdv / liquidity) > 50.0:
                conf -= 10.0  # штраф за повышенную надутость капитализации
            if _avg_tx < 25.0 and (_b5 + _s5) >= 30:
                conf -= 10.0  # штраф за подозрительно мелкие чеки
            
            print(f"🧠 Raydium XGBoost: {mint} | Score: {conf:.1f}%")
            self.last_score = float(conf)
            try:
                from shadow_score import pair_shadow_score, VETO_THRESHOLD
                _sh = pair_shadow_score(pair_data)
                if _sh < VETO_THRESHOLD:
                    print(f"🚫 [SHADOW] {mint[:8]}: score {_sh:.2f} < {VETO_THRESHOLD} — обучено на истории лузеров.")
                    return False
            except Exception:
                pass
            threshold = float(getattr(config, "RAYDIUM_BUY_THRESHOLD", 55.0))  
            _hyper = self.check_hyper_rocket_momentum(pair_data)

            is_buy = bool(conf >= threshold)
            if is_buy:
                self._set_sig(mint, f"{'VIP-' if _hyper else ''}RAY-XGB {conf:.0f}%")
            
            if not is_buy:
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
