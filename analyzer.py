import asyncio
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


_GLOBAL_ROCKET_XGB = None

def get_rocket_xgb_model():
    """Глобальный синглтон загруженной модели Rocket XGBoost v4."""
    global _GLOBAL_ROCKET_XGB
    if _GLOBAL_ROCKET_XGB is None:
        try:
            import xgboost as xgb
            import os as _os
            _rkt_path = "rocket_xgb_v3.json" if _os.path.exists("rocket_xgb_v3.json") else "rocket_xgb_v2.json"
            if _os.path.exists(_rkt_path):
                m = xgb.XGBClassifier()
                m.load_model(_rkt_path)
                _GLOBAL_ROCKET_XGB = m
        except Exception:
            pass
    return _GLOBAL_ROCKET_XGB

def score_token_with_rocket_xgb(mint: str, pair_data: dict, holders_info: dict = None, return_features: bool = False):
    """Глобальная быстрая оценка токена моделью Rocket XGBoost v4 в памяти (0ms).
    Принимает пару DexScreener и опциональные метрики холдеров (top1, top10, dev).
    Возвращает вероятность ракеты от 0.0 до 100.0%."""
    model = get_rocket_xgb_model()
    if model is None or not pair_data:
        return (0.0, {}) if return_features else 0.0
    try:
        import time as _t
        import pandas as _pd
        _pc = pair_data.get("priceChange") or {}
        _m5 = float(_pc.get("m5", 0) or 0)
        _m1 = float(_pc.get("m1", 0) or 0)
        _h1 = float(_pc.get("h1", 0) or 0)
        # Защита фичей XGBoost от битых котировок / смены пула (+208,153,141% m1 ронял скор живых ракет):
        if abs(_m1) > max(180.0, abs(_m5) * 3.0 + 40.0):
            _m1 = max(-40.0, min(40.0, _m5 * 0.20))
        _m1 = max(-95.0, min(200.0, _m1))
        _vm5 = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5 = int(_txm5.get("buys", 0) or 0)
        _s5 = int(_txm5.get("sells", 0) or 0)
        _created = pair_data.get("pairCreatedAt") or 0
        _now_ms = _t.time() * 1000.0
        if _created:
            _age_min = (_now_ms - _created) / 60000.0
        else:
            _bt_age = None
            try:
                from birth_tracker import birth_tracker as _bt_check
                _bt_age = _bt_check.get_age(mint)
            except Exception:
                pass
            if _bt_age is not None:
                _age_min = float(_bt_age)
            elif str(mint).endswith("pump") or str(pair_data.get("dexId", "")).lower() in ("pump", "pumpfun", "pumpswap"):
                _age_min = 0.1
            else:
                _age_min = 999.0
        _dex_low = str(pair_data.get("dexId", "")).lower()
        _is_pump = 1 if (str(mint).endswith("pump") or _dex_low in ("pump", "pumpfun", "pumpswap")) else 0

        raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        fdv_val = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        curve_liq = fdv_val * 0.35 if fdv_val > 0 else 0.0
        liq_val = max(raw_liq, curve_liq) if _is_pump else raw_liq

        _info = pair_data.get("info", {}) or {}
        _socs = _info.get("socials", []) or []
        _webs = _info.get("websites", []) or []
        _has_soc = 1 if (len(_socs) > 0 or len(_webs) > 0) else 0

        _m1_exh = max(-4.0, min(4.0, (_m1 / max(abs(_m5), 1.0)) if abs(_m5) > 0 else 0.0))
        _b_ratio = (_b5 / max(_b5 + _s5, 1.0)) if (_b5 + _s5) > 0 else 0.5
        _net_b = _b5 - _s5
        _v_to_l = (_vm5 / max(liq_val, 1.0)) if liq_val > 0 else 0.0
        _avg_tx = (_vm5 / max(_b5 + _s5, 1.0)) if (_b5 + _s5) > 0 else 0.0
        _f_to_l = (fdv_val / max(liq_val, 1.0)) if liq_val > 0 else 3.5

        _top1 = 3.5
        _top10 = 20.0
        _dev = 0.0
        if holders_info and isinstance(holders_info, dict) and any(k in holders_info for k in ("top1_pct", "top10_pct", "dev_pct", "top1", "top10", "dev")):
            _top1 = float(holders_info.get("top1_pct", holders_info.get("top1", 3.5)) or 0.0)
            _top10 = float(holders_info.get("top10_pct", holders_info.get("top10", 20.0)) or 0.0)
            _raw_dev = float(holders_info.get("dev_pct", holders_info.get("dev", 0.0)) or 0.0)
            # В обучающей выборке Rocket XGBoost остаточная пыль дева (<=1.5%) соответствовала чистым ракетам (0.0):
            _dev = 0.0 if _raw_dev <= 1.5 else _raw_dev

        FEATURE_COLS = [
            "m5_pct", "m1_pct", "h1_pct", "m1_to_m5",
            "buys_m5", "sells_m5", "buy_ratio_m5", "net_buyers_m5",
            "volume_m5", "vol_to_liq", "avg_tx_usd",
            "liquidity", "fdv", "fdv_to_liq",
            "age_min", "is_pump",
            "top1_pct", "top10_pct", "dev_pct", "socials"
        ]

        rkt_features = {
            "m5_pct": float(_m5),
            "m1_pct": float(_m1),
            "h1_pct": float(_h1),
            "m1_to_m5": float(_m1_exh),
            "buys_m5": float(_b5),
            "sells_m5": float(_s5),
            "buy_ratio_m5": float(_b_ratio),
            "net_buyers_m5": float(_net_b),
            "volume_m5": float(_vm5),
            "vol_to_liq": float(_v_to_l),
            "avg_tx_usd": float(_avg_tx),
            "liquidity": float(liq_val),
            "fdv": float(fdv_val),
            "fdv_to_liq": float(_f_to_l),
            "age_min": float(_age_min),
            "is_pump": int(_is_pump),
            "top1_pct": float(_top1),
            "top10_pct": float(_top10),
            "dev_pct": float(_dev),
            "socials": int(_has_soc)
        }
        df = _pd.DataFrame([rkt_features])[FEATURE_COLS]
        prob = float(model.predict_proba(df)[0][1] * 100.0)
        if return_features:
            return prob, rkt_features
        return prob
    except Exception:
        return (0.0, {}) if return_features else 0.0


class Analyzer:
    last_score = _ctx_attr("last_score", 0.0)    # Скор последнего решения (для радара)
    _last_top10 = _ctx_attr("_last_top10", 0.0)  # топ-10 холдеров % (последний разбор в этой задаче)
    _last_dev = _ctx_attr("_last_dev", 0.0)      # доля дева % (последний разбор в этой задаче)
    last_rkt_features = _ctx_attr("last_rkt_features", None)  # Фичи Rocket XGBoost локально в корутине

    def __init__(self):
        self._clone_cache = {}
        self._clone_cache_time = {}
        self.session = None
        self.rocket_model = None  # ROCKET-модель (EVM ракеты). None = нет файла, гейты как раньше
        self.rocket_scores = {}  # address -> score 0..1 (для сайзинга в main)
        self.rkt_features_map = {}  # mint -> dict фичей Rocket XGBoost (без гонки между 8 потоками)
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
            import os as _os
            _rkt_path = "rocket_xgb_v3.json" if _os.path.exists("rocket_xgb_v3.json") else "rocket_xgb_v2.json"
            self.rocket_xgb = xgb.XGBClassifier()
            self.rocket_xgb.load_model(_rkt_path)
            print(f"🚀 [ANALYZER] Rocket XGBoost ({_rkt_path}) успешно загружена!")
        except Exception as _e:
            print(f"⚠️ [ANALYZER] Ошибка загрузки Rocket XGBoost: {_e}")
            self.rocket_xgb = None

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

    def score_rocket_xgb(self, mint: str, pair_data: dict, holders_info: dict = None) -> float:
        """Быстрая оценка токена моделью Rocket XGBoost v4 в памяти (0ms).
        Извлекает 20 ключевых фичей без ликеджа и возвращает скор уверенности от 0.0 до 100.0%."""
        if getattr(self, "rocket_xgb", None) is None:
            self.rocket_xgb = get_rocket_xgb_model()
        if getattr(self, "rocket_xgb", None) is None or not pair_data:
            return 0.0
        # ВАЖНО: Никогда не подставляем _last_top10/_last_dev от предыдущего токена при holders_info=None!
        # Иначе концентрация китов проверенного ранее скама утекает в Stage-1 скоринг чистых ракет.
        res = score_token_with_rocket_xgb(mint, pair_data, holders_info, return_features=True)
        if isinstance(res, tuple):
            prob, rkt_feats = res
            self.last_rkt_features = rkt_feats
            if not hasattr(self, "rkt_features_map"):
                self.rkt_features_map = {}
            if len(self.rkt_features_map) > 2000:
                self.rkt_features_map.clear()
            if mint and rkt_feats:
                self.rkt_features_map[mint] = rkt_feats
        else:
            prob = res
        if not hasattr(self, "rocket_scores"):
            self.rocket_scores = {}
        self.rocket_scores[mint] = prob
        self.last_score = prob
        return prob

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
            if not hasattr(self, "_decision_cache"):
                self._decision_cache = {}
            _d_ctx = getattr(self, "_eval_ctx", {}).get(key, {})
            self._decision_cache[key] = {
                "ts": time.time(),
                "price": _d_ctx.get("price", 0.0),
                "vol5": _d_ctx.get("vol5", 0.0),
                "pass": False,
                "signal": f"SKIP {short}",
                "score": getattr(self, "last_score", 0.0)
            }
            if len(self._decision_cache) > 2500:
                _cut = time.time() - 90.0
                self._decision_cache = {k: v for k, v in self._decision_cache.items() if v["ts"] >= _cut}
        except Exception:
            pass
        try:
            from shadow_tracker import shadow_tracker
            ctx = getattr(self, "_eval_ctx", {}).get(key, {})
            feats = (
                ctx.get("features")
                or getattr(self, "rkt_features_map", {}).get(key)
                or getattr(self, "last_rkt_features", None)
            )
            shadow_tracker.log_rejection(
                mint=key,
                reason=short,
                symbol=ctx.get("symbol", key[:6]),
                chain=ctx.get("chain", "solana"),
                score=float(ctx.get("score", 0.0) or 0.0),
                rkt_score=self.rocket_scores.get(key) if hasattr(self, "rocket_scores") else None,
                price=float(ctx.get("price", 0.0) or 0.0),
                liquidity=float(ctx.get("liq", 0.0) or 0.0),
                m5_pct=float(ctx.get("m5", 0.0) or 0.0),
                source=ctx.get("source", "SCANNER"),
                features=feats
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
            # 0. Прямой радар активных DEX-пулов Solana (PumpSwap / Raydium / Meteora / Bonk выпускники)
            try:
                import asyncio as _aio_lt
                _ds_search_urls = (
                    "https://api.dexscreener.com/latest/dex/search?q=pumpswap",
                    "https://api.dexscreener.com/latest/dex/search?q=SOL%20pump",
                    "https://api.dexscreener.com/latest/dex/search?q=meteora%20SOL",
                    "https://api.dexscreener.com/latest/dex/search?q=bonk%20SOL",
                    "https://api.dexscreener.com/latest/dex/search?q=raydium%20SOL",
                    "https://api.dexscreener.com/latest/dex/search?q=pump",
                    "https://api.dexscreener.com/latest/dex/search?q=SOL",
                    "https://api.dexscreener.com/latest/dex/search?q=moonshot",
                )
                _ds_sr = await _aio_lt.gather(
                    *[fetch_json(u, timeout=6, retries=0) for u in _ds_search_urls],
                    return_exceptions=True,
                )
                _allow_raw_lt = bool(getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False))
                _by_mint_lt = {}
                for _res_lt in _ds_sr:
                    if not isinstance(_res_lt, tuple) or len(_res_lt) != 2:
                        continue
                    _st_lt, _d_lt = _res_lt
                    if _st_lt != 200 or not isinstance(_d_lt, dict):
                        continue
                    for _p_lt in (_d_lt.get("pairs") or []):
                        if not isinstance(_p_lt, dict) or str(_p_lt.get("chainId") or "").lower() != "solana":
                            continue
                        _dex_lt = str(_p_lt.get("dexId") or "").lower()
                        if not _allow_raw_lt and _dex_lt in ("pump", "pumpfun", "moonshot", "meteoradbc"):
                            continue
                        _liq_lt = float((_p_lt.get("liquidity") or {}).get("usd", 0) or 0)
                        _vol5_lt = float((_p_lt.get("volume") or {}).get("m5", 0) or 0)
                        _m5_lt = float((_p_lt.get("priceChange") or {}).get("m5", 0) or 0)
                        if _liq_lt < 5000.0 or _vol5_lt < 800.0 or _m5_lt < 1.0:
                            continue
                        _addr_lt = str((_p_lt.get("baseToken") or {}).get("address") or "").strip()
                        if _addr_lt and not _addr_lt.startswith("0x") and 32 <= len(_addr_lt) <= 44:
                            tokens.append({"tokenAddress": _addr_lt, "chainId": "solana"})
                            _by_mint_lt.setdefault(_addr_lt, []).append(_p_lt)
                if _by_mint_lt:
                    if not hasattr(self, "_prefetch_cache"):
                        self._prefetch_cache = {}
                    _ts_lt = time.time()
                    for _m_lt, _plist_lt in _by_mint_lt.items():
                        _best_lt = self._best_solana_pair(_m_lt, _plist_lt)
                        if _best_lt:
                            self._prefetch_cache[_m_lt] = (_ts_lt, _best_lt)
            except Exception as e:
                print(f"Dexscreener active search radar error: {e}")

            # 1. Сканируем ТОП и САМЫЕ СВЕЖИЕ бусты (ловит самые горячие ракеты)
            try:
                status, data = await fetch_json("https://api.dexscreener.com/token-boosts/top/v1", timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    tokens.extend(data)
            except Exception as e:
                print(f"Dexscreener top boosts fetch error: {e}")

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
        amm_pairs = [p for p in sol_pairs if str(p.get("dexId") or "").lower() not in ("pump", "pumpfun", "meteoradbc")]
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

        # Проверка рассинхрона цен между КРУПНЫМИ AMM пулами (Raydium / Meteora / Orca):
        # ВАЖНО: исключаем заброшенный пул bonding curve pump.fun, у которого цена застыла на миграции!
        _amm_pools = [p for p in sol_pairs if str(p.get("dexId") or "").lower() not in ("pump", "pumpfun", "meteoradbc")]
        if _amm_pools and len(_amm_pools) >= 2:
            _top_liq = float(((best_pair.get("liquidity") or {}).get("usd", 0) or 0))
            if _top_liq >= 5000.0:
                _comp_pools = [p for p in _amm_pools if float(((p.get("liquidity") or {}).get("usd", 0) or 0)) >= max(4000.0, _top_liq * 0.25)]
                if len(_comp_pools) >= 2:
                    _c_prices = [float(p.get("priceUsd", 0) or 0) for p in _comp_pools if float(p.get("priceUsd", 0) or 0) > 0]
                    if len(_c_prices) >= 2:
                        max_px, min_px = max(_c_prices), min(_c_prices)
                        if min_px > 0 and (max_px / min_px) > 1.40:
                            best_pair["_price_divergence"] = True
                            best_pair["_price_divergence_ratio"] = max_px / min_px

        # Обогащаем реальным изменением за 1 минуту и глобальной памятью пиков (цена + ликвидность)
        try:
            px = float(best_pair.get("priceUsd", 0) or 0)
            cur_liq_obs = float(((best_pair.get("liquidity") or {}).get("usd", 0) or 0))
            cur_pair_addr = str(best_pair.get("pairAddress") or "")
            is_curve_obs = str(best_pair.get("dexId") or "").lower() in ("pump", "pumpfun")
            # Не записываем пылевые/призрачные AMM-пулы (< $1,500), иначе смена пула рождает фантом m1 +208,153,141% или -99% crash!
            if px > 0 and mint and (is_curve_obs or cur_liq_obs >= 1500.0):
                now = time.time()
                if not hasattr(Analyzer, "_sol_price_obs"):
                    Analyzer._sol_price_obs = {}
                hist = Analyzer._sol_price_obs.setdefault(mint, [])
                hist.append((now, px, cur_liq_obs, cur_pair_addr))
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

                # Сравниваем ТОЛЬКО наблюдения того же пула (или сопоставимой ликвидности) без скачков единиц x20+:
                def _same_pool_obs(obs_item):
                    if len(obs_item) >= 4 and cur_pair_addr and obs_item[3]:
                        return obs_item[3] == cur_pair_addr
                    return len(obs_item) >= 3 and obs_item[2] >= max(1500.0, cur_liq_obs * 0.25)

                recent_px = [
                    x[1] for x in hist
                    if now - x[0] <= 180.0 and x[1] > 0 and _same_pool_obs(x)
                    and 0.03 <= (px / x[1]) <= 15.0
                ]
                if recent_px:
                    peak_px = max(recent_px)
                    if peak_px > 0:
                        best_pair["_drop_from_peak_5m"] = (px - peak_px) / peak_px * 100.0

                # Расчет дистанции до исторического максимума (ATH) за всю историю наблюдений (Kaiser.charon Rule)
                overall_ath_cands = [px] + recent_px
                if sh_peak_px > 0 and 0.02 <= (px / sh_peak_px) <= 20.0:
                    overall_ath_cands.append(sh_peak_px)
                all_hist_px = [x[1] for x in hist if x[1] > 0 and _same_pool_obs(x) and 0.02 <= (px / x[1]) <= 20.0]
                if all_hist_px:
                    overall_ath_cands.extend(all_hist_px)
                if overall_ath_cands:
                    overall_ath = max(overall_ath_cands)
                    if overall_ath > 0:
                        best_pair["_ath_price"] = overall_ath
                        best_pair["_dist_to_ath_pct"] = (px - overall_ath) / overall_ath * 100.0

                recent_liq = [
                    x[2] for x in hist
                    if len(x) >= 3 and now - x[0] <= 300.0 and x[2] > 0 and _same_pool_obs(x)
                ]
                if recent_liq and cur_liq_obs > 0:
                    peak_liq = max(recent_liq)
                    if peak_liq > 0:
                        best_pair["_liq_drop_from_peak"] = (cur_liq_obs - peak_liq) / peak_liq * 100.0

                cands = [
                    (abs((now - x[0]) - 60.0), x[0], x[1])
                    for x in hist
                    if 12.0 <= (now - x[0]) <= 150.0 and x[1] > 0 and _same_pool_obs(x)
                    and 0.04 <= (px / x[1]) <= 12.0
                ]
                if cands:
                    cands.sort(key=lambda x: x[0])
                    old_px = cands[0][2]
                    real_m1 = (px - old_px) / old_px * 100.0
                    _ds_m5 = float(((best_pair.get("priceChange") or {}).get("m5", 0)) or 0)
                    _m1_cap = max(120.0, abs(_ds_m5) * 2.5 + 35.0)
                    if abs(real_m1) <= _m1_cap:
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
        async def _fetch_chunk(chunk):
            pairs_list = []
            try:
                status, data = await fetch_json(
                    "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk),
                    timeout=8, retries=1)
                if status == 200 and isinstance(data, list):
                    pairs_list = data
                else:
                    status2, data2 = await fetch_json(
                        "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(chunk),
                        timeout=8, retries=1)
                    if status2 == 200 and isinstance(data2, dict):
                        pairs_list = data2.get("pairs") or []
            except Exception:
                return chunk, []
            return chunk, pairs_list

        chunks = [todo[i:i + 30] for i in range(0, len(todo), 30)]
        results = await asyncio.gather(*[_fetch_chunk(c) for c in chunks], return_exceptions=True)
        ts = time.time()
        for res in results:
            if not isinstance(res, tuple) or len(res) != 2:
                continue
            chunk, pairs_list = res
            if not pairs_list:
                continue
            by_mint = {}
            for p in pairs_list:
                a = (p.get("baseToken") or {}).get("address")
                if a:
                    by_mint.setdefault(a, []).append(p)
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
        _is_grad = False
        try:
            from birth_tracker import birth_tracker as _bt_g
            _is_grad = _bt_g.is_graduated(mint)
        except Exception:
            pass

        # 1. Свежий пакетный кэш (prefetch_token_data) — мгновенно из памяти без сети
        _hit = getattr(self, "_prefetch_cache", {}).get(mint)
        if _hit and time.time() - _hit[0] < float(getattr(config, "PREFETCH_TTL_SEC", 60)):
            if not _hit[1]:
                if not _is_grad:
                    return {}
            else:
                _hit_dex = str(_hit[1].get("dexId") or "").lower()
                if not (_is_grad and _hit_dex in ("pump", "pumpfun", "meteoradbc")):
                    return dict(_hit[1])

        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        from http_client import fetch_json
        best_pair = None
        try:
            status, data = await fetch_json(url, timeout=8, retries=1)
            if status == 200 and data:
                best_pair = self._best_solana_pair(mint, data.get("pairs") or [])
                if best_pair:
                    _bp_dex = str(best_pair.get("dexId") or "").lower()
                    if not (_is_grad and _bp_dex in ("pump", "pumpfun", "meteoradbc")):
                        return best_pair
            # Если торговля сырыми кривыми Pump.fun выключена и минт ещё НЕ мигрировал,
            # НЕ сжигаем лимит GeckoTerminal (24 RPM) на неиндексированные кёрвы (иначе падает check_1m_candles с 429)!
            if mint.endswith("pump") and not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False):
                if not _is_grad:
                    return best_pair or {}
            # Для свежих миграций (subscribeMigration), где DS ещё запаздывает, сначала берём быстрый Jupiter v2 (0 лимитов GT)!
            try:
                import market_data as _md_fb
                _jup_pair = await _md_fb._jup_token_data(mint)
                if _jup_pair and float(_jup_pair.get("priceUsd", 0) or 0) > 0:
                    _jp_dex = str(_jup_pair.get("dexId") or "").lower()
                    if _jp_dex not in ("pump", "pumpfun", "meteoradbc") or getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False):
                        return _jup_pair
            except Exception:
                pass
            gt_pair = await self.fetch_token_data_gecko(mint)
            return gt_pair or best_pair or {}
        except Exception:
            gt_pair = await self.fetch_token_data_gecko(mint)
            return gt_pair or best_pair or {}

    async def fetch_token_data_gecko(self, mint: str) -> dict:
        import market_data
        now_gt = time.time()
        if not hasattr(self, "_gt_token_cache"):
            self._gt_token_cache = {}
        if len(self._gt_token_cache) > 1500:
            self._gt_token_cache = {k: v for k, v in self._gt_token_cache.items() if (now_gt - v[0]) < 60.0}
        cached_gt = self._gt_token_cache.get(mint)
        if cached_gt and (now_gt - cached_gt[0]) < 45.0:
            return dict(cached_gt[1]) if cached_gt[1] else {}
        try:
            data = await market_data._gt_get(f"/networks/solana/tokens/{mint}/pools?page=1", retries=0, timeout=8)
            if not data:
                self._gt_token_cache[mint] = (now_gt, {})
                return {}
            
            pools = data.get("data", [])
            if not pools:
                self._gt_token_cache[mint] = (now_gt, {})
                return {}
            best = max(pools, key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd", 0) or 0))
            a = best.get("attributes", {})
            pc = a.get("price_change_percentage") or {}
            tx = a.get("transactions") or {}
            vu = a.get("volume_usd") or {}
            created = a.get("pool_created_at")
            created_ms = market_data._parse_ms(created) if created else 0
            symbol = (a.get("name") or "UNKNOWN").split("/")[0].strip()

            def _tx(key):
                t = tx.get(key) or {}
                return {"buys": int(t.get("buys", 0) or 0), "sells": int(t.get("sells", 0) or 0)}

            pair_addr_gt = str(best.get("id") or "").replace("solana_", "").strip()
            dex_rel = str((((best.get("relationships") or {}).get("dex") or {}).get("data") or {}).get("id") or "").lower()
            res_usd = float(a.get("reserve_in_usd") or 0)
            if "pumpswap" in dex_rel:
                real_dex = "pumpswap"
            elif "raydium" in dex_rel:
                real_dex = "raydium"
            elif "meteora" in dex_rel:
                real_dex = "meteora"
            elif "orca" in dex_rel:
                real_dex = "orca"
            elif "pump" in dex_rel and res_usd < 6000.0:
                real_dex = "pump"
            else:
                real_dex = "pumpswap" if (mint.endswith("pump") and res_usd >= 6000.0) else ("pump" if mint.endswith("pump") else (dex_rel or "raydium"))

            print(f"🦎 GeckoTerminal fallback для {mint[:8]}: пул найден ({real_dex}, liq ${res_usd:,.0f}).")
            gt_out = {
                "pairAddress": pair_addr_gt,
                "baseToken": {"symbol": symbol, "name": symbol, "address": mint},
                "priceUsd": str(a.get("base_token_price_usd") or 0),
                "priceChange": {"m5": float(pc.get("m5") or 0), "m1": 0.0,
                                "h1": float(pc.get("h1") or 0), "h24": float(pc.get("h24") or 0)},
                "txns": {"m5": _tx("m5"), "h1": _tx("h1"), "h24": _tx("h24")},
                "volume": {"m5": float(vu.get("m5") or 0), "h1": float(vu.get("h1") or 0),
                           "h24": float(vu.get("h24") or 0)},
                "liquidity": {"usd": res_usd},
                "fdv": float(a.get("fdv_usd") or a.get("market_cap_usd") or 0),
                "pairCreatedAt": created_ms,
                "dexId": real_dex,
                "info": {"socials": [], "websites": []},
                "_socials_unknown": True,
            }
            self._gt_token_cache[mint] = (now_gt, gt_out)
            return dict(gt_out)
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



    async def is_clone(self, symbol: str, current_mint: str, current_created_at: int, current_fdv: float, current_liq: float = 0.0, current_vol5: float = 0.0, current_b5: int = 0) -> bool:
        """Проверяет, является ли токен фейковым клоном или спам-атакой.
        
        КЛЮЧЕВАЯ ЛОГИКА (защита от ложных блокировок):
        1. В крипте популярные тикеры (CAT, DOGE, PEPE, CHAD, AI, BULL) создаются сотнями за годы истории.
           Старые заброшенные/мертвые токены (созданные более 48 часов назад с капой < $500k) ПОЛНОСТЬЮ ИГНОРИРУЮТСЯ!
           Они не могут дисквалифицировать сегодняшнюю свежую ракету.
        2. Сравнение идет ТОЛЬКО:
           а) Против общепризнанных титанов/гигантов (FDV > $500k, liq > $60k) — защита от подделок под $WIF, $BONK, $POPCAT.
           б) Внутри ТЕКУЩЕЙ ВОЛНЫ ХАЙПА (токены последних 48 часов) — поиск лидера против копий и спам-роев.
        3. Если наш токен имеет сильный текущий импульс (vol5 >= $3,000, b5 >= 15) или является самым ранним в текущей волне —
           он признается ОРИГИНАЛЬНЫМ ЛИДЕРОМ (Narrative King) и пропускается в покупку!
        """
        if not symbol or len(symbol) < 2:
            return False

        data = await self._fetch_dex_search(symbol)
        if not data:
            return False

        pairs = data.get("pairs") or []
        sym_up = symbol.upper().strip()
        now_ms = time.time() * 1000

        # Сбор информации обо всех конкурирующих парах с таким же тикером на Solana
        competitors = []
        for p in pairs:
            if p.get("chainId") != "solana":
                continue
            p_symbol = str((p.get("baseToken") or {}).get("symbol") or "").upper().strip()
            p_mint = str((p.get("baseToken") or {}).get("address") or "").strip()
            if not p_mint or p_symbol != sym_up or p_mint == current_mint:
                continue

            p_created_at = float(p.get("pairCreatedAt") or 0.0)
            p_fdv = float(p.get("fdv") or p.get("marketCap") or 0.0)
            p_liq = float((p.get("liquidity") or {}).get("usd", 0) or 0.0)
            p_vol24 = float((p.get("volume") or {}).get("h24", 0) or 0.0)
            p_vol5 = float(((p.get("volume") or {}).get("m5", 0) or 0.0))
            p_dex = str(p.get("dexId") or "").lower()

            competitors.append({
                "mint": p_mint,
                "created_at": p_created_at,
                "fdv": p_fdv,
                "liq": p_liq,
                "vol24": p_vol24,
                "vol5": p_vol5,
                "dex": p_dex,
            })

        if not competitors:
            return False

        # =========================================================================
        # 1. ЗАЩИТА ОТ ФЕЙКОВ ПОД КРУПНЫЕ СТОЯВШИЕСЯ БЛЮЧИПЫ (WIF, POPCAT, BONK)
        # Если существует старый (>48ч) настоящий гигант с FDV > $500k и liq > $60k,
        # а наш токен — свежая микро-капа с тем же именем, это фишинг-подделка.
        # =========================================================================
        for c in competitors:
            c_age_hours = (now_ms - c["created_at"]) / (3600.0 * 1000.0) if c["created_at"] > 0 else 999.0
            if c_age_hours > 48.0:
                if c["fdv"] >= 500000.0 and c["liq"] >= 60000.0 and (c["vol24"] >= 25000.0 or c["liq"] >= 100000.0):
                    if current_fdv < c["fdv"] * 0.15:
                        print(f"🚫 [GIANT-IMPERSONATION] {sym_up} ({current_mint[:8]}): попытка мимикрировать под устоявшийся гигант {c['mint'][:8]} (FDV ${c['fdv']:,.0f}, liq ${c['liq']:,.0f}) — блок!")
                        return True

        # =========================================================================
        # 2. РАБОТА С ТЕКУЩЕЙ ВОЛНОЙ (токены, созданные за последние 48 часов)
        # Старые мертвые щиткоины из прошлого (без объемов) полностью отсекаются!
        # =========================================================================
        wave_comps = []
        for c in competitors:
            c_age_hours = (now_ms - c["created_at"]) / (3600.0 * 1000.0) if c["created_at"] > 0 else 999.0
            # Учитываем конкурента только если он создан в последние 48 часов ИЛИ имеет активные торги прямо сейчас
            if c_age_hours <= 48.0 or c["vol5"] >= 1000.0 or c["vol24"] >= 50000.0:
                wave_comps.append(c)

        if not wave_comps:
            # Все конкуренты — это мертвые токены годичной давности без объемов. Наш токен свободен!
            return False

        max_wave_vol5 = max((c["vol5"] for c in wave_comps), default=0.0)
        max_wave_liq = max((c["liq"] for c in wave_comps), default=0.0)
        valid_wave_created = [c["created_at"] for c in wave_comps if c["created_at"] > 0 and (now_ms - c["created_at"]) <= (48.0 * 3600.0 * 1000.0)]
        earliest_wave_created = min(valid_wave_created) if valid_wave_created else float("inf")
        recent_2h_comps = [c for c in wave_comps if c["created_at"] and (now_ms - c["created_at"]) <= (120.0 * 60.0 * 1000.0)]

        # =========================================================================
        # 👑 ЗАЩИТА НАСТОЯЩИХ РАКЕТ ТЕКУЩЕЙ ВОЛНЫ (NARRATIVE KING)
        # =========================================================================
        # 1. Доминация по текущему импульсу: у нас есть активный объем (vol5 >= $3,000) и покупатели (b5 >= 15),
        # и наш объем существенно выше любых клонов текущей волны
        is_volume_king = (current_vol5 >= 3000.0 and current_b5 >= 15 and (max_wave_vol5 == 0 or current_vol5 >= max_wave_vol5 * 1.4))

        # 2. Доминация по ликвидности в текущей волне: наш токен залочил ликвидность ($18k+), а клоны — мелочь
        is_liq_king = (current_liq >= 18000.0 and (max_wave_liq == 0 or current_liq >= max_wave_liq * 1.3))

        # 3. Genesis текущей волны: наш токен запущен ПЕРВЫМ среди монет этой темы (или в пределах 60 сек от первой)
        is_genesis_first = (current_created_at > 0 and (current_created_at <= earliest_wave_created + 60000.0) and current_vol5 >= max_wave_vol5)

        if is_volume_king or is_liq_king or is_genesis_first:
            print(f"👑 [NARRATIVE-KING] {sym_up} ({current_mint[:8]}): оригинальный лидер текущей волны (vol5 ${current_vol5:,.0f} vs клоны max ${max_wave_vol5:,.0f}, liq ${current_liq:,.0f}) — разрешен к покупке!")
            return False

        # =========================================================================
        # 🚫 ФИЛЬТРАЦИЯ ОТСТАЮЩИХ КЛОНОВ И СПАМ-ФЕРМ
        # =========================================================================
        # 1. Серийный спам-рой: создано 2+ клона за 2 часа, и наш токен не имеет лидерства
        if len(recent_2h_comps) >= 2 and not (current_vol5 >= 4000.0 and current_b5 >= 20):
            print(f"🚫 [CLONE-SWARM] {sym_up} ({current_mint[:8]}): серийный спам-рой ({len(recent_2h_comps)+1} токенов за 2ч без лидерства) — блок!")
            return True

        # 2. Кастомный пул мимикрирует под оригинальный pump.fun токен
        if not current_mint.endswith("pump"):
            for c in wave_comps:
                if c["mint"].endswith("pump") and (c["liq"] >= 8000 or c["fdv"] >= 25000):
                    # Если наш токен уже набрал органический объем и покупателей больше, чем у pump-токена, это не мимикрия, а самостоятельный токен / миграция
                    if current_vol5 >= 3000.0 and current_b5 >= 15 and current_vol5 >= c.get("vol5", 0.0):
                        continue
                    print(f"🚫 [CLONE-SCAM] {sym_up} ({current_mint[:8]}): не-pump пул мимикрирует под живой pump {c['mint'][:8]}!")
                    return True

        # 3. Существует более крупный живой лидер текущей волны
        for c in wave_comps:
            p_liq = c["liq"]
            p_vol5 = c["vol5"]
            p_created_at = c["created_at"]
            p_fdv = c["fdv"]

            if p_liq < 5000 and c["vol24"] < 8000:
                continue

            # Другой токен текущей волны заметно крупнее по ликвидности и объему
            if p_liq >= 15000 and current_liq > 0 and p_liq > (current_liq * 1.4) and current_vol5 < p_vol5:
                print(f"🚫 [CLONE-SCAM] {sym_up} ({current_mint[:8]}): есть более крупный лидер текущей волны {c['mint'][:8]} (liq ${p_liq:,.0f} vs ${current_liq:,.0f})")
                return True

            # Был создан раньше (>1 мин) в этой волне и опережает по объемам
            if current_created_at and p_created_at and p_created_at < (current_created_at - 60000) and p_liq >= 10000 and p_fdv >= 35000:
                if current_vol5 < p_vol5 * 1.3:
                    print(f"🚫 [CLONE-SCAM] {sym_up} ({current_mint[:8]}): создан позже оригинала текущей волны {c['mint'][:8]}")
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
        # Защита фичей XGBoost от битых котировок / смены пула (+208,153,141% m1 ронял скор живых ракет):
        if abs(_m1) > max(180.0, abs(_m5) * 3.0 + 40.0):
            _m1 = max(-40.0, min(40.0, _m5 * 0.20))
        _m1 = max(-95.0, min(200.0, _m1))
        _h6 = float(_pc.get("h6", 0) or 0)
        if _h1 < -8.0 or _h6 < -15.0:
            return False
        if (buys_m5 >= _min_tx and volume_m5 >= _min_vol and liquidity >= 12000 
                and sells_m5 > 0 and buys_m5 >= sells_m5 * 1.2 and _m5 >= 3.0 and _m1 >= -8.0):
            return True
        return False
        
    async def analyze_whale_token(self, mint: str, trader_name: str = "", prefetched_pair=None) -> tuple:
        """Специализированный глубокий аудит безопасности токена, выбранного смарт-китом.
        НЕ режет токен сканерными фильтрами (0 соцсетей на старте, низкий 5м объём или покупка на кривой Pump.fun),
        потому что киты покупают ДО розничного пампа!
        Строго блокирует ТОЛЬКО реальные смарт-контракт ловушки:
        1. Freeze Authority / Mint Authority / Token-2022 Permanent Delegate (прямой RPC ончейн).
        2. GoPlus Honeypot / Blacklist / Sell Tax.
        3. RugCheck Score >= 2500, концентрацию у 1 кошелька и кастомный незаблокированный LP (<80%).
        4. Непродаваемые AMM-пулы (Jupiter sell simulation) и мёртвые после дампа монеты (-65% от пика).
        """
        if mint in ["So11111111111111111111111111111111111111112",
                    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]:
            return False, "Native / Stablecoin"

        pair_data = prefetched_pair if isinstance(prefetched_pair, dict) and prefetched_pair else None
        if not pair_data:
            try:
                from grok_jev import grok_jev as _gj
                pair_data = await _gj.fetch_pair(mint, "solana")
            except Exception:
                pair_data = None
        if not pair_data:
            pair_data = await self.fetch_token_data(mint)

        clean_symbol = mint[:8]
        base_name = ""
        if pair_data:
            base_token = pair_data.get("baseToken", {}) or {}
            base_name = str(base_token.get("name") or "").lower()
            clean_symbol = str(base_token.get("symbol") or clean_symbol).upper().strip()

        _is_tokenized_stock = (
            "backpack securities" in base_name
            or "securities" in base_name
            or clean_symbol in ("TTWO", "BACKPACK", "SPY", "QQQ")
        )
        if _is_tokenized_stock:
            return False, f"tokenized-equity-skip (токенизированная акция {clean_symbol} — не мем-токен, пропуск без штрафа киту)"

        # 1. Защита от краха, стоячих majors и неликвидных/старых пулов
        dex_id_low = str((pair_data or {}).get("dexId") or "").lower()
        is_pump_curve = dex_id_low in ("pump", "pumpfun")
        if is_pump_curve and not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False):
            return False, "Сырая кривая Pump.fun до миграции на DEX (ALLOW_UNGRADUATED_PUMPFUN=False)"
        if pair_data:
            drop_peak = float(pair_data.get("_drop_from_peak_5m", 0.0) or 0.0)
            if drop_peak <= -40.0:
                return False, f"Цена уже упала на {drop_peak:.0f}% с пика — отскок дохлой кошки после дампа"

            liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
            fdv = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
            if fdv > 2_000_000.0 or liq > 250_000.0:
                return False, f"Крупная капа / не мем-ракета (FDV ${fdv:,.0f}, liq ${liq:,.0f}) — пропуск без штрафа киту"
            if is_pump_curve:
                if 0 < fdv < 18000.0:
                    return False, f"Слишком ранняя кривая Pump.fun (FDV ${fdv:,.0f} < $18,000)"
                if fdv > 48000.0:
                    return False, f"Кривая Pump.fun перед обрывом миграции (FDV ${fdv:,.0f} > $48,000)"
            else:
                if liq < 8000.0:
                    return False, f"Слишком низкая ликвидность AMM пула (${liq:,.0f} < $8,000)"
                if mint.endswith("pump") and 0 < fdv < 44000.0:
                    return False, f"Слитый после миграции токен (FDV ${fdv:,.0f} < $44,000)"
                if not mint.endswith("pump") and 0 < fdv < 10000.0:
                    return False, f"Микро-капа токена (FDV ${fdv:,.0f} < $10,000)"

            # 1.5. Фильтр живого импульса мем-ракеты (отсекает 18-минутные флеты CLAUDIA, HOOKED, DACITY, FUSE, NOS)
            _pc_w = pair_data.get("priceChange") or {}
            _m5_w = float(_pc_w.get("m5", 0) or 0)
            _h1_w = float(_pc_w.get("h1", 0) or 0)
            _vm5_w = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
            _tx5_w = (pair_data.get("txns") or {}).get("m5") or {}
            _b5_w = int(_tx5_w.get("buys", 0) or 0)
            _s5_w = int(_tx5_w.get("sells", 0) or 0)
            _tot_w = _b5_w + _s5_w
            _avg_w = (_vm5_w / _tot_w) if _tot_w > 0 else 0.0
            _cat_w = pair_data.get("pairCreatedAt") or 0
            _age_w = ((time.time() * 1000 - _cat_w) / 60000.0) if _cat_w else 30.0
            if _age_w > 360.0 and not (_age_w <= 1440.0 and _vm5_w >= 15000.0 and _m5_w >= 10.0 and _b5_w >= _s5_w * 1.25):
                return False, f"Старый стоячий токен ({_age_w/60:.1f}ч > 6ч без прорывного объёма)"
            if _vm5_w < 3500.0 or _tot_w < 20:
                return False, f"Вялый стакан без объёма (vol5 ${_vm5_w:,.0f} < $3,500, txns {_tot_w})"
            if _m5_w < 2.5 or _m5_w > 135.0:
                return False, f"Нет живого импульса / перегрев (m5 {_m5_w:+.1f}% вне [+2.5%, +135%])"
            if _h1_w < 0.0:
                return False, f"Часовой тренд в минусе (h1 {_h1_w:+.1f}%)"
            if _s5_w > 0 and (_b5_w / _s5_w) < 1.12:
                return False, f"Слабый поток покупателей ({_b5_w}b/{_s5_w}s < 1.12x)"
            if _tot_w >= 100 and _avg_w < 8.0:
                return False, f"Накрутка микро-ботом (avg ${_avg_w:.2f} < $8)"
            if _age_w >= 20.0 and _vm5_w < 6000.0 and (_vm5_w / max(liq, 1.0)) < 0.20:
                return False, f"Низкий оборот зрелого пула (vol5 ${_vm5_w:,.0f}, v/l {_vm5_w/max(liq,1.0):.2f}x)"
            _links_w = len((pair_data.get("info") or {}).get("socials", []) or []) + len((pair_data.get("info") or {}).get("websites", []) or [])
            if _age_w >= 20.0 and _links_w == 0 and not bool(pair_data.get("_socials_unknown", False)):
                return False, f"Зрелый токен ({_age_w:.0f}м) без единой соцсети/сайта"
        else:
            return False, "Нет рыночных данных пула (DexScreener)"

        # 2. Корпоративный фейк-скам
        corp_scams = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "NVDA", "OPENAI"]
        if any(c in clean_symbol for c in corp_scams):
            if not self._brand_whitelisted(mint):
                return False, f"Токен мимикрирует под корпоративный бренд ({clean_symbol}) — scam"

        # 3. Прямой ончейн RPC + GoPlus проверка контракта (Freeze, Mint, Token-2022 Delegate, Honeypot)
        try:
            import goplus
            onchain_ok, onchain_why = await goplus.check_onchain_solana_security(mint)
            if not onchain_ok:
                return False, f"On-Chain Contract Trap: {onchain_why}"
            gp_ok, gp_why = await goplus.check_solana(mint)
            gp_why_low = str(gp_why or "").lower()
            # В analyze_whale_token концентрацию холдеров проверяет RugCheck (с точным исключением AMM/CEX сейфов),
            # поэтому от GoPlus блокируем только реальные контрактные ловушки (honeypot/tax/freeze/mint/closable),
            # а не сырые строки 'кит держит' / 'топ-10 китов' на неразмеченных AMM-пулах
            rc_st, rc_data = await goplus.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=6)
            _gp_is_holder_str = ("кит держит" in gp_why_low or "топ-10 китов" in gp_why_low)
            _liq_w = float(((pair_data or {}).get("liquidity") or {}).get("usd", 0) or 0)
            if not gp_ok and "rugcheck-score" not in gp_why_low and not (_gp_is_holder_str and (rc_st == 200 or _liq_w >= 20000.0)):
                return False, f"GoPlus Honeypot/Tax: {gp_why}"

            # 4. Кэшированный RugCheck аудит (концентрация холдеров и блокировка LP на кастомных токенах)
            _dex_low_rc = str((pair_data or {}).get("dexId", "")).lower()
            from news_narrative import news_radar
            _is_narr_match = bool(news_radar.match_token(symbol, name))
            _is_pump_origin_chk = mint.endswith("pump") or is_pump_curve or _is_narr_match
            if rc_st == 200 and isinstance(rc_data, dict):
                raw_sc = float(rc_data.get("score") or 0.0)
                _allowed_lp_terms_chk = (
                    "large amount of lp unlocked", "lp provider", "single lp provider",
                    "lp not locked", "unlocked lp"
                ) if _is_pump_origin_chk else ("lp provider",)
                ign = sum(
                    float(rk.get("score", 0) or 0)
                    for rk in (rc_data.get("risks") or [])
                    if any(k in str(rk.get("name", "")).lower() for k in (
                        "low liquidity", "creator history", "copycat", "symbol mimic", *_allowed_lp_terms_chk
                    ))
                )
                clean_sc = max(0.0, raw_sc - ign)
                if clean_sc >= 2500:
                    return False, f"RugCheck риск {clean_sc:.0f} >= 2500"
                for rk in (rc_data.get("risks") or []):
                    rkn = str(rk.get("name", "")).lower()
                    rkl = str(rk.get("level", "")).lower()
                    if _is_pump_origin_chk and "large amount of lp unlocked" in rkn:
                        continue
                    if rkl == "danger" and any(k in rkn for k in ("single holder", "high ownership", "top 10 holders", "large amount of lp unlocked", "freeze authority")):
                        return False, f"RugCheck критический риск: {rk.get('name')}"
                if not _is_pump_origin_chk:
                    lp_locked_ok = any(
                        isinstance(m, dict) and (float((m.get("lp") or {}).get("lpLockedPct", 0) or 0) >= 80.0 or float((m.get("lp") or {}).get("lpBurnedPct", 0) or 0) >= 80.0)
                        for m in (rc_data.get("markets") or [])
                    )
                    if not lp_locked_ok:
                        return False, "Кастомный неизвестный токен без заблокированного LP (<80%)"
            elif not _is_pump_origin_chk:
                return False, f"Кастомный неизвестный токен ещё не проиндексирован RugCheck (статус {rc_st})"
        except Exception as e:
            print(f"⚠️ [WHALE-ANALYSIS] Ошибка проверки контракта ({e})")
            if not _is_pump_origin_chk:
                return False, f"Сбой проверки кастомного токена ({e})"

        # 5. Для токенов на AMM DEX (не сырой bonding curve) проверяем маршрут продажи через Jupiter
        if not is_pump_curve:
            try:
                from jupiter import JupiterAPI
                j_ok, j_why = await JupiterAPI.is_token_sellable(mint)
                w_low = str(j_why or "").lower()
                if not j_ok and any(k in w_low for k in ("no routes", "cannot sell", "honeypot", "freeze", "0 sol returned")):
                    return False, f"Jupiter: токен непродаваем ({j_why})"
            except Exception:
                pass

        # 6. Быстрый ML-скоринг через актуальную модель rocket_xgb
        ml_score = 0.0
        _whale_ml_thr = float(getattr(config, "ROCKET_XGB_STAGE1_THRESHOLD", 18.0))
        if pair_data and getattr(self, "rocket_xgb", None) is not None:
            try:
                ml_score = float(self.score_rocket_xgb(mint, pair_data) or 0.0)
                self.last_score = ml_score
                if not hasattr(self, "rocket_scores"):
                    self.rocket_scores = {}
                self.rocket_scores[mint] = ml_score
                if ml_score < _whale_ml_thr:
                    return False, f"Rocket XGB низкий скор {ml_score:.1f}% < {_whale_ml_thr:.0f}%"
            except Exception:
                pass

        return True, f"Passed Whale Contract & Momentum Audit (ML: {ml_score:.1f}%)"

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
            
        _pc = pair_data.get("priceChange") or {}
        _m5 = float(_pc.get("m5", 0) or 0)
        _m1 = float(_pc.get("m1", 0) or 0)
        _h1 = float(_pc.get("h1", 0) or 0)
        # Защита фичей XGBoost от битых котировок / смены пула (+208,153,141% m1 ронял скор живых ракет):
        if abs(_m1) > max(180.0, abs(_m5) * 3.0 + 40.0):
            _m1 = max(-40.0, min(40.0, _m5 * 0.20))
        _m1 = max(-95.0, min(200.0, _m1))
        _vm5_pre = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
        _v24 = float((pair_data.get("volume") or {}).get("h24", 0) or 0)
        _txm5_pre = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5_pre = int(_txm5_pre.get("buys", 0) or 0)
        _s5_pre = int(_txm5_pre.get("sells", 0) or 0)
        _tx_h1 = (pair_data.get("txns") or {}).get("h1", {}) or {}
        _b = int(_tx_h1.get("buys", 0) or 0)
        _s = int(_tx_h1.get("sells", 0) or 0)

        base_token = pair_data.get("baseToken", {})
        name = base_token.get("name", "").upper()
        symbol = base_token.get("symbol", "").upper()
        cur_px_eval = float(pair_data.get("priceUsd", 0) or 0.0)

        # === DECISION CACHE (Kaiser.charon Trench Rule) ===
        # Если монета недавно проверена и отклонена, и цена/объем не изменились (>7% цена, >15% объем),
        # используем сохраненный вердикт мгновенно (0ms), не перегружая CPU и внешние API!
        if not hasattr(self, "_decision_cache"):
            self._decision_cache = {}
        _cached = self._decision_cache.get(mint)
        if _cached and (time.time() - _cached["ts"]) < 40.0:
            _old_px = _cached["price"]
            _px_d = abs(cur_px_eval - _old_px) / max(_old_px, 1e-9)
            _vol_d = abs(_vm5_pre - _cached["vol5"]) / max(_cached["vol5"], 1.0)
            if _px_d < 0.07 and _vol_d < 0.15:
                if not _cached.get("pass", False):
                    self.signals[mint] = _cached.get("signal", "")
                    self.last_score = _cached.get("score", 0.0)
                    return False

        if not hasattr(self, "_eval_ctx"):
            self._eval_ctx = {}
        self._eval_ctx[mint] = {
            "symbol": symbol,
            "chain": "solana",
            "price": cur_px_eval,
            "liq": float((pair_data.get("liquidity") or {}).get("usd", 0) or 0),
            "vol5": _vm5_pre,
            "m5": float((pair_data.get("priceChange") or {}).get("m5", 0) or 0),
            "source": "SOLANA_SCAN"
        }

        if pair_data.get("_price_divergence"):
            _div_r = float(pair_data.get("_price_divergence_ratio", 0.0) or 0.0)
            return self._deny(mint, "price-split", f"🚫 [PRICE-SPLIT] {symbol} ({mint[:8]}): рассинхрон цен между пулами {_div_r:.1f}x (миграция/гэп) — блок входа.")

        # === ATH DISTANCE & PEAK-MEMORY GUARD (Kaiser.charon Trench Rule) ===
        _dist_ath = float(pair_data.get("_dist_to_ath_pct", 0.0) or 0.0)
        _drop_from_peak = float(pair_data.get("_drop_from_peak_5m", 0.0) or 0.0)
        _liq_drop_peak = float(pair_data.get("_liq_drop_from_peak", 0.0) or 0.0)
        if _dist_ath <= -48.0 and _m5 < 10.0:
            return self._deny(mint, f"ath-dumped {_dist_ath:.0f}%", f"🚫 [ATH-DUMP] {symbol} ({mint[:8]}): цена провалилась на {_dist_ath:.1f}% от ATH — отскок дохлой кошки с бэгхолдерами.")
        if _drop_from_peak <= -38.0:
            return self._deny(mint, f"post-peak-crash {_drop_from_peak:.0f}%", f"🚫 [POST-PEAK-CRASH] {symbol} ({mint[:8]}): цена уже упала на {_drop_from_peak:.1f}% от виденного ботом пика — отскок дохлой кошки после дампа.")
        if _liq_drop_peak <= -45.0:
            return self._deny(mint, f"post-peak-liq-drain {_liq_drop_peak:.0f}%", f"🚫 [LIQ-DRAIN] {symbol} ({mint[:8]}): ликвидность пула уже сдулась на {_liq_drop_peak:.1f}% от пика — снайперы/дев выводят SOL.")
        
        corporate_scam_keywords = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "TESLA", "TESLAA", "SPACEX", "STARLINK", "XAI", "NEURALINK", "PAYPAL",
                                  "NVDA", "NVIDIA", "GOOG", "AMZN", "META", "NFLX", "ANTHROPIC", "OPENAI", "PERPLEXITY"]
        if any(keyword in symbol for keyword in corporate_scam_keywords) or any(keyword in name for keyword in corporate_scam_keywords):
            if not self._brand_whitelisted(mint):
                _tx5_corp = (pair_data.get("txns") or {}).get("m5", {}) or {}
                _b5_corp = int(_tx5_corp.get("buys", 0) or 0)
                _s5_corp = int(_tx5_corp.get("sells", 0) or 0)
                _v5_corp = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                _lq_corp = float((pair_data.get("liquidity", {}) or {}).get("usd", 0) or 0)
                _is_valid_corp_runner = (
                    (_v5_corp >= 4000.0 and _b5_corp >= _s5_corp and _lq_corp >= 10000.0) or
                    (_v5_corp >= 2500.0 and _b5_corp >= int(_s5_corp * 1.10) and _lq_corp >= 12000.0) or
                    (_v5_corp >= 10000.0 and _b5_corp >= _s5_corp)
                )
                if _is_valid_corp_runner:
                    print(f"🔥 [CORP-MEME-RUNNER] {symbol} ({mint[:8]}): подтвержденный пародийный мем (b/s {_b5_corp}/{_s5_corp}, vol5 ${_v5_corp:,.0f}) — пропуск.")
                else:
                    return self._deny(mint, "mimic-corp-scam", f"🚫 Мусор: Токен {symbol} мимикрирует под корпоративный бренд без объема. Это 100% scam.")

        meme_keywords = ["PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN", "ELON", "ELUN", "ELOM", "MUSK",
                         "CLAUDE", "CHATGPT", "DEEPSEEK", "GROK", "GEMINI"]
        if any(keyword in symbol for keyword in meme_keywords) or any(keyword in name for keyword in meme_keywords):
            from news_narrative import news_radar
            _is_narr_meme = bool(news_radar.match_token(symbol, name))
            if not self._brand_whitelisted(mint) and not mint.endswith("pump") and not _is_narr_meme:
                _tx5_mc = (pair_data.get("txns") or {}).get("m5", {}) or {}
                _b5_mc = int(_tx5_mc.get("buys", 0) or 0)
                _s5_mc = int(_tx5_mc.get("sells", 0) or 0)
                _v5_mc = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                _lq_mc = float((pair_data.get("liquidity", {}) or {}).get("usd", 0) or 0)
                _links_m = len((pair_data or {}).get("info", {}).get("socials", []) or []) + len((pair_data or {}).get("info", {}).get("websites", []) or [])
                _v_to_l_m = (_v5_mc / max(_lq_mc, 1.0)) if _lq_mc > 0 else 0.0
                _avg_tx_m = (_v5_mc / max(1, _b5_mc + _s5_mc)) if (_b5_mc + _s5_mc) > 0 else 0.0
                # Живые вирусные мемы (PEPE69 $172k vol, DUGE, TRUMP, PNUT): пускаем при подтверждённом торговом потоке
                _is_valid_meme_runner = (
                    (_links_m > 0 and _v5_mc >= 3000.0 and _b5_mc >= _s5_mc) or
                    (_v5_mc >= 4000.0 and _b5_mc >= _s5_mc and _lq_mc >= 12000.0) or
                    (_v5_mc >= 2500.0 and _b5_mc >= int(_s5_mc * 1.10) and _lq_mc >= 15000.0)
                )
                if _is_valid_meme_runner:
                    print(f"🔥 [MIMIC-FOMO] {symbol} ({mint[:8]}): подтвержденный вирусный мем (b/s {_b5_mc}/{_s5_mc}, vol5 ${_v5_mc:,.0f}, links {_links_m}) — пропуск.")
                else:
                    return self._deny(mint, "mimic-scam", f"🚫 Мусор: Токен {symbol} мимикрирует под известный мем без активности (links {_links_m}, v/l {_v_to_l_m:.1f}x, vol5 ${_v5_mc:,.0f}).")
            
        _created_pre = pair_data.get("pairCreatedAt") or 0
        if _created_pre:
            _age_min_pre = (time.time() * 1000 - _created_pre) / 60000.0
            if _age_min_pre > 50000.0 and mint.endswith("pump"):
                try:
                    from birth_tracker import birth_tracker as _bt_check
                    _bt_a = _bt_check.get_age(mint)
                    if _bt_a is not None and _bt_a < 120.0:
                        _age_min_pre = float(_bt_a)
                except Exception:
                    pass
        else:
            _bt_age = None
            try:
                from birth_tracker import birth_tracker as _bt_check
                _bt_age = _bt_check.get_age(mint)
            except Exception:
                pass
            if _bt_age is not None:
                _age_min_pre = float(_bt_age)
            elif mint.endswith("pump") or str(pair_data.get("dexId", "")).lower() in ("pump", "pumpfun", "pumpswap"):
                # Если метки времени нет у токена pump.fun — это новорожденный токен (<30 сек), а НЕ 999 минут!
                _age_min_pre = 0.2
            else:
                _age_min_pre = 999.0
        _dex_pre_early = str(pair_data.get("dexId", "")).lower()
        if _dex_pre_early in ("pump", "pumpfun", "moonshot", "meteoradbc") or mint.endswith("moon"):
            if not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False):
                return self._deny(
                    mint,
                    "ungraduated-pump-curve",
                    f"⏳ [CURVE-SKIP] {symbol} ({mint[:8]}): сырая кривая bonding curve без миграции на DEX — ждём выхода на PumpSwap/Raydium с сожжённым LP.",
                )
            _fdv_curve_pre = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
            if _fdv_curve_pre <= 0:
                _px_calc = float(pair_data.get("priceUsd", 0) or 0)
                if _px_calc > 0:
                    _fdv_curve_pre = _px_calc * 1_000_000_000.0
            _min_curve_mcap = float(getattr(config, "MIN_PUMP_CURVE_MCAP_USD", 7500.0))
            _max_curve_mcap = float(getattr(config, "MAX_PUMP_CURVE_MCAP_USD", 42000.0))
            if _fdv_curve_pre > 0 and _fdv_curve_pre < _min_curve_mcap:
                return self._deny(
                    mint,
                    f"pump-curve-too-early {_fdv_curve_pre:.0f}<{_min_curve_mcap:.0f}",
                    f"🚫 [CURVE-FLASH-RUG] {symbol} ({mint[:8]}): FDV ${_fdv_curve_pre:,.0f} < ${_min_curve_mcap:,.0f} (пустой кёрв < 12%) — зона flash-rug с пустым пулом.",
                )
            if _fdv_curve_pre > _max_curve_mcap:
                return self._deny(
                    mint,
                    f"pump-curve-top {_fdv_curve_pre:.0f}>{_max_curve_mcap:.0f}",
                    f"🚫 [CURVE-CLIFF] {symbol} ({mint[:8]}): FDV ${_fdv_curve_pre:,.0f} > ${_max_curve_mcap:,.0f} (опасная зона обрыва перед миграцией) — ждём выхода на Raydium/PumpSwap.",
                )
        # FAST-TRACK ДЛЯ МОЛОДЫХ РАКЕТ (от 90 сек):
        # Требуем хотя бы 90 сек и органические продажи (_s5_pre >= 2), чтобы отсечь 0-секундные дев-бандлы (RIOT/Elun/Белка)
        _is_fast_rocket = (_age_min_pre >= 1.5 and _vm5_pre >= 4000.0 and _b5_pre >= 12 and _s5_pre >= 2 and _b5_pre >= _s5_pre * 1.2 and _m5 >= 2.5)
        _raw_liq_age = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        _avg_tx_age = _vm5_pre / max(1, _b5_pre + _s5_pre)
        _is_early_rocket = (_raw_liq_age >= 12000.0 and _vm5_pre >= 6000.0 and _b5_pre >= 30
                            and _b5_pre >= _s5_pre * 1.30 and _avg_tx_age >= 25.0)
        # СТАЛО (замени строки 543-605 в analyzer.py):
        _sol_min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 0.5))
        if _age_min_pre < _sol_min_age:
            print(f"⏳ [ENTRY] {mint[:8]}: возраст {_age_min_pre:.1f}м < {_sol_min_age}м — выдерживаем карантин.")
            return None

        # 1. Защита от старых увядающих зомби (> 6ч без активного пробоя ликвидного пула и все > 24ч) и переросших mid-caps (> $1.8M)
        _fdv_chk_pre = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
        if _fdv_chk_pre > float(getattr(config, "JEV_MAX_MCAP_USD", 1800000.0)):
            return self._deny(
                mint,
                f"mcap-too-large ${_fdv_chk_pre/1e6:.1f}M",
                f"🚫 [LARGE-CAP-SKIP] {symbol} ({mint[:8]}): FDV ${_fdv_chk_pre:,.0f} > $1.8M — ракета уже тяжёлая.",
            )
        if mint.endswith("pump") and _dex_pre_early not in ("pump", "pumpfun") and 0 < _fdv_chk_pre < 46000.0:
            _is_rebounding = (_m5 >= 4.0 and _b5_pre >= _s5_pre and _vm5_pre >= 1200.0)
            _is_real_dump = not _is_rebounding and ((_fdv_chk_pre < 8500.0) or (_s5_pre > int(_b5_pre * 1.35) and _m5 < -10.0))
            _is_active_grad = (_vm5_pre >= 1200.0 and _b5_pre >= _s5_pre and _m5 >= -6.0)
            if _is_real_dump or not (_is_active_grad or _is_rebounding):
                return self._deny(
                    mint,
                    f"post-mig-dumped ${_fdv_chk_pre:.0f}<$46k",
                    f"🚫 [POST-MIGRATION-DUMP] {symbol} ({mint[:8]}): выпускник Pump.fun слит ниже миграции (FDV ${_fdv_chk_pre:,.0f} < $46k) — кейс SHANE.",
                )

        _max_age_min = float(getattr(config, "SOLANA_MAX_AGE_MIN", 240.0))
        # Разрешаем монеты до 360м ТОЛЬКО при огромной ликвидности ($40k+) и мощном притоке покупателей
        _is_active_breakout_pool = (
            _age_min_pre <= 360.0
            and _fdv_chk_pre <= 1500000.0
            and _raw_liq_age >= 25000.0
            and _vm5_pre >= 12000.0
            and (_vm5_pre / max(_raw_liq_age, 1.0)) >= 0.15
            and _m5 >= 3.0
            and _b5_pre >= _s5_pre * 1.10
        )
        if _age_min_pre > _max_age_min and not _is_active_breakout_pool:
            return self._deny(
                mint,
                f"zombie-old-token {_age_min_pre:.0f}m",
                f"🚫 [ZOMBIE-AGE] {symbol} ({mint[:8]}): возраст {_age_min_pre:.0f}м > {_max_age_min:.0f}м — уставший старый пул (кейс GIGAHUG/INVEST).",
            )

        # 2. Защита от фейковых микро bump-ботов (кейсы LAURA $1.60 avg_tx, HODL $3.10, IPHONEDUO $5.00)
        _info_pre = pair_data.get("info") or {}
        _has_soc_pre = bool((_info_pre.get("socials") or []) or (_info_pre.get("websites") or []))
        _raw_liq_chk = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        _is_burned_launchpad = mint.endswith(("pump", "bonk", "BAGS"))
        _tot_tx5_pre = _b5_pre + _s5_pre
        _avg_tx_pre = (_vm5_pre / _tot_tx5_pre) if _tot_tx5_pre > 0 else 0.0
        _is_real_bump_runner = (
            (_b5_pre >= _s5_pre * 1.15 and _vm5_pre >= 3000.0 and _raw_liq_chk >= 14000.0)
            or (_b5_pre >= _s5_pre * 1.30 and (_b5_pre - _s5_pre) >= 60 and _vm5_pre >= 2500.0)
            or (_vm5_pre >= 10000.0 and _b5_pre >= _s5_pre)
        )
        if _tot_tx5_pre >= 120 and _avg_tx_pre < 3.0 and not _is_real_bump_runner:
            return self._deny(
                mint,
                f"fake-bump-wash-bot avg ${_avg_tx_pre:.1f}",
                f"🚫 [WASH-BUMP-TRAP] {symbol} ({mint[:8]}): накрутка микро-ботом ({_tot_tx5_pre} txns, avg ${_avg_tx_pre:.2f} < $3) — блок!",
            )
        _is_deep_launchpad_runner = (
            _is_burned_launchpad
            and _raw_liq_chk >= 8000.0
            and _vm5_pre >= 2000.0
            and _m5 >= 2.0
            and _b5_pre >= _s5_pre
        )
        if _tot_tx5_pre >= 200 and _avg_tx_pre < 2.5 and not _has_soc_pre and not _is_deep_launchpad_runner and not _is_real_bump_runner:
            return self._deny(
                mint,
                f"fake-bump-wash-bot avg ${_avg_tx_pre:.1f}",
                f"🚫 [WASH-BUMP-TRAP] {symbol} ({mint[:8]}): накрутка микро-ботом ({_tot_tx5_pre} txns, avg ${_avg_tx_pre:.2f} < $2.5).",
            )

        # 3. ЖЕСТКИЙ ФИЛЬТР ИМПУЛЬСА, СКОРОСТИ ОБОРОТА И ТРЕНДА ДО ROCKET-XGB:
        _base_v5_cfg = float(getattr(config, "MIN_VOLUME_M5", 2000.0))
        # Для свежих миграций и молодых токенов (<10м) пускаем ранний старт от $600 при живом перевесе покупателей
        if _age_min_pre <= 10.0 or _is_burned_launchpad:
            _min_v5_cfg = 600.0 if (_b5_pre >= _s5_pre and _tot_tx5_pre >= 12) else 1400.0
        else:
            _min_v5_cfg = _base_v5_cfg
        if _vm5_pre < _min_v5_cfg:
            return self._deny(
                mint,
                f"low-vol5 ${_vm5_pre:.0f}<${_min_v5_cfg:.0f}",
                f"🚫 [LOW-MOMENTUM] {symbol} ({mint[:8]}): 5м объем ${_vm5_pre:,.0f} < ${_min_v5_cfg:,.0f} — слабый пул.",
            )
        if _age_min_pre >= 20.0:
            _is_massive_mature_flow = (_vm5_pre >= 15000.0 and _b5_pre >= _s5_pre)
            _is_strong_mature_breakout = (_m5 >= 3.0 and _vm5_pre >= 3000.0 and _b5_pre >= _s5_pre * 1.05 and _tot_tx5_pre >= 15)
            _v2l_mat = (_vm5_pre / max(_raw_liq_chk, 1.0)) if _raw_liq_chk > 0 else 0.0
            if not _is_massive_mature_flow and not _is_strong_mature_breakout and (_vm5_pre < 4000.0 or _m5 < 1.0 or _tot_tx5_pre < 20):
                return self._deny(
                    mint,
                    f"slow-mature-pool v5=${_vm5_pre:.0f} v/l={_v2l_mat:.2f}x",
                    f"🚫 [MATURE-STALL-GUARD] {symbol} ({mint[:8]}): токену {_age_min_pre:.0f}м, но оборот вялый (vol5 ${_vm5_pre:,.0f}, v/l {_v2l_mat:.2f}x, m5 {_m5:+.1f}%) — кейс PROPFUN.",
                )

        # Запрет входа, если продавцов больше или почти столько же, сколько покупателей (на глубоких пулах >=$25k допускаем 1.08x при зелёном m5/m1)
        _base_bs_cfg = float(getattr(config, "MIN_BUY_SELL_RATIO", 1.08))
        if _b5_pre >= 50 and _vm5_pre >= 8000.0 and _m5 >= 2.0 and (_b5_pre / max(_s5_pre, 1)) >= 0.85:
            _min_bs_cfg = 0.85  # Здоровая двухсторонняя торговля/консолидация на ракетах с объемом (кейс GINGATO 650b/675s, PERCOLATOR)
        elif _b5_pre >= 150 and _vm5_pre >= 15000.0 and _b5_pre >= _s5_pre:
            _min_bs_cfg = 1.00  # Массивный встречный объём на ракетах (кейс PERCOLATOR 879b/845s)
        elif _b5_pre >= 40 and _vm5_pre >= 8000.0 and _b5_pre > _s5_pre:
            _min_bs_cfg = 1.05 if (_m5 >= 3.0 and _m1 >= -2.0) else 1.08
        elif _raw_liq_chk >= 20000.0 and _m5 >= 4.0 and _m1 >= 0.0:
            _min_bs_cfg = 1.06
        else:
            _min_bs_cfg = _base_bs_cfg
        if _s5_pre > 0 and (_b5_pre / _s5_pre) < _min_bs_cfg:
            return self._deny(
                mint,
                f"weak-buyers {_b5_pre}b/{_s5_pre}s",
                f"🚫 [WEAK-BUYERS] {symbol} ({mint[:8]}): покупки/продажи 5м {_b5_pre}/{_s5_pre} ({_b5_pre/_s5_pre:.2f}x < {_min_bs_cfg:.2f}x) — продавцы давят!",
            )

        # Запрет покупки «дохлых кошек» в часовом сливе, КРОМЕ подтверждённого V-образного разворота с объёмом (SCAMSEM +122%)
        # Проверяем часовой тренд ТОЛЬКО если монете реально >= 45 минут!
        # Молодые токены (<45м) не имеют полноценной 1ч истории, а h1 отражает кривую Pump.fun (кейсы MEME +342%, GORP +100%)
        if _age_min_pre >= 45.0:
            _min_h1_cfg = float(getattr(config, "JEV_MIN_H1_PCT", -8.0))
            _h6_pre_val = float(_pc.get("h6", 0) or 0)
            _is_v_reversal = (
                _h1 >= -25.0 and _h6_pre_val >= -30.0
                and _m5 >= 12.0 and _vm5_pre >= 15000.0
                and _raw_liq_chk >= 18000.0 and _b5_pre >= _s5_pre * 1.30
            )
            if not _is_v_reversal and (_h1 < _min_h1_cfg or (_age_min_pre >= 90.0 and _h6_pre_val < -25.0)):
                return self._deny(
                    mint,
                    f"downtrend-h1 {_h1:+.0f}%",
                    f"🚫 [DEAD-CAT-DUMP] {symbol} ({mint[:8]}): часовой тренд в сливе (h1 {_h1:+.1f}%, h6 {_h6_pre_val:+.1f}%) — падающий нож!",
                )

        # Ранний вход в накопление на ликвидных пулах (>= +1.2% при liq >= $14k, EMBER +38%, FUSE +34%)
        # и вход на микро-откате покупателей (до -2.5% при b5 >= s5 * 1.25)
        _is_buyer_dip_pre = (_b5_pre >= int(_s5_pre * 1.15) and _vm5_pre >= 1500.0 and _m5 >= -4.0)
        _min_m5_req = 1.0 if (_raw_liq_chk >= 10000.0 and _b5_pre >= _s5_pre and _vm5_pre >= 1500.0) else 1.8
        if _m5 < _min_m5_req and not _is_buyer_dip_pre:
            return self._deny(mint, f"m5-too-weak {_m5:+.1f}%", f"🚫 [WEAK-M5] {symbol} ({mint[:8]}): m5 {_m5:+.1f}% < +{_min_m5_req:.1f}% — нет разгона.")
        _base_max_m1 = float(getattr(config, "MAX_ENTRY_M1_PCT", 25.0))
        # Разрешаем импульсный пробой (свеча +15%..+35%) при активном спросе покупателей (b5 >= s5 и vol >= $1500)
        if _b5_pre >= int(_s5_pre * 1.05) and _vm5_pre >= 1500.0 and _m5 >= 2.0:
            _max_m1_cfg = 35.0  # Импульсный пробой ракеты
        else:
            _max_m1_cfg = min(_base_max_m1, 25.0)
        _min_m1_floor = -10.0 if _m5 >= 8.0 else -6.0
        if _m1 > _max_m1_cfg or _m1 < _min_m1_floor:
            return self._deny(
                mint,
                f"m1-bad {_m1:+.1f}%",
                f"🚫 [M1-FILTER] {symbol} ({mint[:8]}): 1м свеча {_m1:+.1f}% вне коридора [{_min_m1_floor:+.1f}%, +{_max_m1_cfg:.0f}%].",
            )

        # 4. Anti-FOMO: Защита от перегретых вершин (для свежих выпускников <=120м допускаем h1 до +2500%, т.к. h1 включает рост на кёрве $5k->$69k!)
        _base_max_entry_h1 = float(getattr(config, "MAX_ENTRY_H1_PCT", 350.0))
        _is_active_fresh_rocket = (
            _age_min_pre <= 120.0
            and _vm5_pre >= 2000.0
            and _b5_pre >= int(_s5_pre * 0.90)
        )
        _max_entry_h1 = 2500.0 if _is_active_fresh_rocket else _base_max_entry_h1
        _max_entry_m5 = float(getattr(config, "MAX_ENTRY_M5_PCT", 45.0))
        if _h1 > _max_entry_h1 and _age_min_pre > 4.0:
            return self._deny(
                mint,
                f"h1-exhausted {_h1:.0f}%>{_max_entry_h1:.0f}%",
                f"🚫 [EXHAUSTED-TOP] {symbol} ({mint[:8]}): рост за час {_h1:+.1f}% > +{_max_entry_h1:.0f}% — вершина пампа.",
            )
        # Защита от покупки на верхушке вертикальной свечи:
        # Не покупаем перегретые свечи >+65% (вершина FOMO-палки, кейсы AIB/MAX/yarl/p402).
        # Пропускаем только здоровый старт ракеты (m5 до +65%, либо до +85% при мощном доминировании покупателей b5/s5 >= 1.35)
        # либо остывающий откат (m1 <= -1.0%). Разрешаем сильные ракетные пампы (+100%..+400%) при перевесе покупателей!
        if _m5 > _max_entry_m5:
            _is_strong_buyer_breakout = (
                (_m5 <= 120.0 and _b5_pre >= int(_s5_pre * 1.05) and _vm5_pre >= 2000.0) or
                (_m5 <= 450.0 and _b5_pre >= int(_s5_pre * 1.10) and _vm5_pre >= 2500.0) or
                (_is_burned_launchpad and _b5_pre >= _s5_pre and _m5 <= 500.0) or
                (_is_narr_match and _b5_pre >= _s5_pre)
            )
            _is_cooling_pullback = (_m1 <= -1.0 and _m5 <= 180.0 and _b5_pre >= _s5_pre * 1.02)
            if not (_is_strong_buyer_breakout or _is_cooling_pullback):
                return self._deny(
                    mint,
                    f"m5-vertical-spike {_m5:.0f}%>{_max_entry_m5:.0f}%",
                    f"🚫 [FOMO-VERTICAL] {symbol} ({mint[:8]}): 5м свеча {_m5:+.1f}% > +{_max_entry_m5:.0f}% (покупка вершины зеленой палки без отката) — кейс yarl/p402/AIB.",
                )

        # Защита от давления продавцов и скрытого сброса китов (кейсы SOLC 735 продаж, DOG™ 291 продажа):
        # Блокируем ТОЛЬКО если продавцы реально давят (sells >= 52% и покупателей меньше чем продавцов)
        _txns_tot_pre = _b5_pre + _s5_pre
        if _txns_tot_pre >= 30 and _s5_pre > 0:
            _sell_ratio = _s5_pre / _txns_tot_pre
            _v2l_pre = (_vm5_pre / max(_raw_liq_chk, 1.0)) if _raw_liq_chk > 0 else 0.0
            if _b5_pre < _s5_pre and (_sell_ratio >= 0.52 or (_sell_ratio >= 0.48 and _v2l_pre >= 3.5)):
                return self._deny(
                    mint,
                    f"heavy-sell-pressure {_sell_ratio*100:.0f}% sells",
                    f"🚫 [SELL-PRESSURE] {symbol} ({mint[:8]}): продавцов {_s5_pre}/{_txns_tot_pre} ({_sell_ratio*100:.0f}%) при v/l {_v2l_pre:.1f}x — сброс китов (кейс SOLC/DOG™).",
                )

        # 4.9. Wash-Trading Spike Guard: НЕ баним живые ракеты (ELYSIONPAD 25.2x avg $183, CHIMPS 1.5x, CALLCAT 2.2x), режем реальный симметричный wash-дамп!
        _avg_tx_pre = (_vm5_pre / max(1, _txns_tot_pre)) if _txns_tot_pre > 0 else 0.0
        if _avg_tx_pre >= 35.0 and _b5_pre >= 150 and _b5_pre >= _s5_pre * 1.20 and _raw_liq_chk >= 15000.0:
            _max_vl_allowed = 30.0
        elif _is_burned_launchpad and _raw_liq_chk >= 8000.0:
            _max_vl_allowed = 8.50 if (_b5_pre >= _s5_pre * 1.05 and _avg_tx_pre >= 12.0) else 4.50
        elif _raw_liq_chk >= 25000.0 and _b5_pre >= _s5_pre * 1.15 and _avg_tx_pre >= 20.0:
            _max_vl_allowed = 8.50
        elif _b5_pre >= _s5_pre * 1.05 and _avg_tx_pre >= 12.0 and _raw_liq_chk >= 8000.0:
            _max_vl_allowed = 7.50
        else:
            _max_vl_allowed = 2.50
        if _raw_liq_chk > 0 and (_vm5_pre / _raw_liq_chk) > _max_vl_allowed:
            return self._deny(
                mint,
                f"wash-spike {(_vm5_pre/_raw_liq_chk):.1f}x",
                f"🚫 [WASH-SPIKE] {symbol} ({mint[:8]}): 5м объем (${_vm5_pre:,.0f}) в {(_vm5_pre/_raw_liq_chk):.1f}x > {_max_vl_allowed:.2f}x пула ликвидности — накрутка ботами.",
            )

        # 5. Соцсети на ВСЕХ возрастах: раньше проверка выключалась после 8м (STONKS 12.5м -42.3%, scamsem 25.8м, LAURA 88.6м)!
        _links_cnt = len((pair_data or {}).get("info", {}).get("socials", []) or []) + len((pair_data or {}).get("info", {}).get("websites", []) or [])
        _soc_unknown = bool((pair_data or {}).get("_socials_unknown", False))
        _req_soc_age = float(getattr(config, "REQUIRE_SOCIALS_MIN_AGE_MIN", 8.0))
        if _links_cnt == 0 and not _soc_unknown:
            _vol_liq_r = (_vm5_pre / max(_raw_liq_chk, 1.0)) if _raw_liq_chk > 0 else 0.0
            _txns_tot = _b5_pre + _s5_pre
            _is_canonical_pump = mint.endswith("pump") or str((pair_data or {}).get("dexId", "")).lower() in ("pumpswap", "pump", "pumpfun")
            # Для токенов Pump.fun / сожженного LP: даем грейс-период на индексацию соцсетей DexScreener (до _req_soc_age = 8м)
            # при подтвержденном чистом потоке реальных покупателей!
            _is_safe_young_pump = (
                (_is_canonical_pump or _is_burned_launchpad)
                and _age_min_pre < _req_soc_age
                and _raw_liq_chk >= 7000.0
                and _b5_pre >= 15
                and _b5_pre >= int(_s5_pre * 1.05)
                and _vm5_pre >= 2000.0
            )
            _is_organic_stealth = (
                _age_min_pre < 5.0
                and _raw_liq_chk >= 12000.0
                and _vm5_pre >= 10000.0
                and _vol_liq_r <= 6.5
                and _b5_pre >= 40
                and _b5_pre >= _s5_pre * 1.30
            )
            _is_viral_stealth = (
                _vm5_pre >= 40000.0
                and _b5_pre >= 200
                and _b5_pre >= int(_s5_pre * 1.35)
            )
            if not (_is_safe_young_pump or _is_organic_stealth or _is_viral_stealth):
                _sub_reason = f"no-socials-mature {_age_min_pre:.0f}m" if _age_min_pre >= 12.0 else f"no-socials-dev-rug {_age_min_pre:.0f}m"
                return self._deny(
                    mint,
                    _sub_reason,
                    f"🚫 [NO-SOCIALS-GUARD] {symbol} ({mint[:8]}): 0 соцсетей при возрасте {_age_min_pre:.1f}м (v/l {_vol_liq_r:.1f}x, b/s {_b5_pre}/{_s5_pre}) — анонимный дев-раг.",
                )
            else:
                _stealth_label = "PUMP-INDEXING-GRACE" if _is_safe_young_pump else ("VIRAL-STEALTH" if _is_viral_stealth else "ORGANIC-STEALTH")
                print(f"💎 [{_stealth_label}] {symbol} ({mint[:8]}): 0 соцсетей (индексация DexScreener), но подтверждён защищённый пул и чистый приток (${_vm5_pre:,.0f}, {_b5_pre}b/{_s5_pre}s, v/l {_vol_liq_r:.2f}) — пропуск в анализ.")

        # =========================================================================
        # 🚀 ЭТАП 1: ROCKET XGBOOST v2 — ПЕРВИЧНЫЙ ML-СКРИНЕР (0ms IN-MEMORY)
        # Все монеты СРАЗУ прогоняются через ML. Модель оценивает комплекс параметров
        # (m5, m1, h1, buy/sell ratio, объем, ликвидность, FDV, соцсети) БЕЗ отсева
        # старыми жесткими эвристиками. Если скор < порога (50%), монета отсекается в 0мс.
        # =========================================================================
        if getattr(self, "rocket_xgb", None) is not None and getattr(config, "ROCKET_XGB_ENABLED", True):
            conf = self.score_rocket_xgb(mint, pair_data)
            threshold = float(getattr(config, "ROCKET_XGB_STAGE1_THRESHOLD", getattr(config, "ROCKET_XGB_THRESHOLD", 13.0)))
            self.last_score = conf
            if not hasattr(self, "rocket_scores"):
                self.rocket_scores = {}
            self.rocket_scores[mint] = conf

            dex_id_low = str(pair_data.get("dexId", "")).lower()
            is_pump_curve = dex_id_low in ("pump", "pumpfun")
            raw_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
            fdv_val = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
            curve_liq = fdv_val * 0.35 if fdv_val > 0 else 0.0
            liq_val = max(raw_liq, curve_liq) if is_pump_curve else raw_liq

            if (conf + 0.05) < threshold:
                self._deny(mint, f"rocket-xgb {conf:.0f}%", f"· [ROCKET-XGB] {symbol} ({mint[:8]}): уверенность модели {conf:.1f}% < {threshold:.0f}% — отсеян до тяжелых проверок (0ms).")
                try:
                    from shadow_tracker import shadow_tracker
                    shadow_tracker.log_rejection(
                        mint=mint,
                        reason=f"Rocket XGBoost low score: {conf:.1f}%",
                        symbol=symbol,
                        chain="solana",
                        score=conf,
                        rkt_score=conf,
                        price=float(pair_data.get("priceUsd", 0) or 0.0),
                        liquidity=liq_val,
                        m5_pct=_m5,
                        source="ROCKET_XGB_PRIMARY",
                        features=getattr(self, "rkt_features_map", {}).get(mint) or getattr(self, "last_rkt_features", None)
                    )
                except Exception:
                    pass
                return False

            print(f"🚀 [ROCKET-XGB PASSED] {symbol} ({mint[:8]}): ML Score {conf:.1f}% >= {threshold:.0f}%! Запуск ончейн-аудита безопасности...")

            # =========================================================================
            # 🛡️ ЭТАП 2: ОНЧЕЙН-АУДИТ БЕЗОПАСНОСТИ И ЗАЩИТА ОТ СКАМА (ДЛЯ ML-КАНДИДАТОВ)
            # =========================================================================

            # 1. Защита от микрованночек, сырых кёрвов и фейковых AMM-пулов
            _is_raw_curve = dex_id_low in ("pump", "pumpfun") or is_pump_curve or mint.endswith("pump") or _is_canonical_pump
            _min_grad_liq = float(getattr(config, "MIN_GRADUATED_LIQ_USD", 5000.0))
            _min_liq = 1000.0 if _is_raw_curve else _min_grad_liq
            _eff_pool_liq = liq_val if _is_raw_curve else raw_liq
            if _eff_pool_liq < _min_liq and not _is_raw_curve:
                # Восстановление USD-ликвидности при задержке индексации DexScreener
                _q_info_a = pair_data.get("liquidity", {}) or {}
                _q_amt_a = float(_q_info_a.get("quote", 0) or 0)
                _q_sym_a = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
                if _q_amt_a > 0 and ("SOL" in _q_sym_a or "WSOL" in _q_sym_a):
                    _eff_pool_liq = _q_amt_a * 2.0 * 185.0
                    raw_liq = _eff_pool_liq
                    if isinstance(pair_data.get("liquidity"), dict):
                        pair_data["liquidity"]["usd"] = _eff_pool_liq
                else:
                    _gt_c_a = getattr(self, "_gt_token_cache", {}).get(mint, (0, {}))[1]
                    if _gt_c_a and isinstance(_gt_c_a, dict):
                        _gt_l_a = float((_gt_c_a.get("liquidity") or {}).get("usd", 0) or 0)
                        if _gt_l_a >= _min_liq:
                            _eff_pool_liq = _gt_l_a
                            raw_liq = _gt_l_a
                            if isinstance(pair_data.get("liquidity"), dict):
                                pair_data["liquidity"]["usd"] = _eff_pool_liq
            if _eff_pool_liq < _min_liq:
                return self._deny(mint, f"low-liq ${_eff_pool_liq:.0f}<{_min_liq:.0f}", f"🚫 [ANTI-RUG] {symbol} ({mint[:8]}): Реальная ликвидность DEX ${_eff_pool_liq:,.0f} < ${_min_liq:,.0f} — риск дампа/проскальзывания.")

            # Защита от анонимных wash-bot накруток на DEX (кейсы Belief/BUZZ: 0 соцсетей, liq <$25k, vol5/liq > 2.2x при слабом перевесе покупателей)
            # НО: не блокируем сожжённые лаунчпад-пулы с перевесом покупателей и реальные ракеты с живым чеком!
            _info_a = pair_data.get("info") or {}
            _has_soc_a = bool((_info_a.get("socials") or []) or (_info_a.get("websites") or []))
            if not _is_raw_curve and not _has_soc_a and raw_liq < 25000.0:
                _v2l_a = _vm5_pre / max(raw_liq, 1.0)
                _bs_a = _b5_pre / float(max(_s5_pre, 1))
                _tot_tx5_a = _b5_pre + _s5_pre
                _avg_tx5_a = _vm5_pre / max(_tot_tx5_a, 1)
                _is_organic_trades = (
                    (_avg_tx5_a >= 32.0 and _b5_pre >= 40 and _bs_a >= 1.25)
                    or (mint.endswith(("pump", "bonk", "BAGS")) and raw_liq >= 14000.0 and _bs_a >= 1.22)
                )
                if _v2l_a > 2.6 and _bs_a < 1.5 and not _is_organic_trades:
                    return self._deny(mint, f"anon-wash-churn {_v2l_a:.1f}x", f"🚫 [WASH-TRAP] {symbol} ({mint[:8]}): 0 соцсетей, liq ${raw_liq:,.0f}, оборот 5м {_v2l_a:.1f}x пула (b/s {_bs_a:.2f}, avg ${_avg_tx5_a:.0f}) — анонимный wash-дамп!")

            # Блок кастомных не-pump токенов на PumpSwap с микропулом
            if dex_id_low == "pumpswap" and not mint.endswith("pump") and raw_liq < _min_grad_liq:
                return self._deny(mint, "fake-pumpswap-unlocked-lp", f"🚫 [FAKE-PUMPSWAP] {symbol} ({mint[:8]}): кастомный токен на PumpSwap с микропулом (${raw_liq:.0f} < ${_min_grad_liq:.0f}) — блок!")

            # Защита от фантомных пулов с мизерным остатком реального SOL
            _quote_info = pair_data.get("liquidity", {}) or {}
            _quote_amt = float(_quote_info.get("quote", 0) or 0)
            _quote_sym = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
            _min_sol_pool = 8.0 if dex_id_low == "pumpswap" else 5.0
            if "SOL" in _quote_sym and 0 < _quote_amt < _min_sol_pool and not is_pump_curve:
                return self._deny(mint, f"ghost-pool-sol {_quote_amt:.1f}<{_min_sol_pool:.0f}", f"🚫 [AMM-GHOST-POOL] {symbol} ({mint[:8]}): в пуле всего {_quote_amt:.2f} SOL < {_min_sol_pool:.0f} SOL — микро-резерв.")

            # 2. Защита от Honeypot и искусственных бандлов без реальных продаж (кейс Белка -98%: 12 покупок и 0 продаж)
            if _s5_pre == 0 and _b5_pre >= 5:
                return self._deny(mint, "zero-sells-dev-bundle", f"🚫 [DEV-BUNDLE] {symbol} ({mint[:8]}): 0 продаж при {_b5_pre} покупках — искусственный бандл дева без живого рынка (кейс Белка -98%)!")
            _is_canonical_pump = mint.endswith("pump") and dex_id_low in ("pump", "pumpfun", "pumpswap")
            if not _is_canonical_pump:
                if (_b5_pre + _s5_pre) >= 20 and (_s5_pre / (_b5_pre + _s5_pre)) < 0.05:
                    return self._deny(mint, "honeypot-anomaly", f"🚫 [HONEYPOT] {symbol} ({mint[:8]}): аномалия продаж m5 {_s5_pre}s/{_b5_pre}b (<5% продаж) — скрытый honeypot.")

            # 3. Ончейн-аудит: RugCheck & GoPlus
            # Каноничный pump-флаг ставится ТОЛЬКО для монет с bonding curve Pump.fun (оканчивающихся на 'pump' или на сырой кривой).
            # Наличие пула на PumpSwap/Raydium НЕ делает кастомный скам-токен безопасным pump-токеном (кейсы DEMOCAT/SAFU/SOUVENIR)!
            _dex_low_main = str((pair_data or {}).get("dexId", "")).lower()
            _is_canonical_pump = mint.endswith("pump") or _dex_low_main in ("pump", "pumpfun", "pumpswap", "meteora", "meteoradbc") or is_pump_curve
            _is_pump_flag = 1 if _is_canonical_pump else 0
            _cur_tok_top10 = 0.0
            _cur_tok_dev = 0.0
            try:
                import goplus as _gp_rc
                _rc_st, rc_data = await _gp_rc.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=7)
                if _rc_st == 400:
                    print(f"ℹ️ [RUGCHECK] {mint[:8]}: монета ещё индексируется RugCheck (код 400) -> ожидаем отчёта...")
                elif _rc_st not in (200, 0, 429):
                    print(f"⚠️ RugCheck API вернул {_rc_st} для {mint[:8]}.")

                # Для кастомных токенов требуем обязательный отчёт RugCheck.
                # Токены Pump.fun и Narrative Radar НЕ ждут RugCheck (код 400/404 на старте) — как в прибыльном коммите 0d2b1ae:
                if not _is_canonical_pump and not _is_narr_match and (_rc_st != 200 or not rc_data):
                    return self._deny(
                        mint,
                        "waiting-rugcheck-index",
                        f"⏳ [SECURITY-WAIT] {symbol} ({mint[:8]}): кастомный токен ожидает индексации RugCheck (статус {_rc_st}) для проверки LP..."
                    )

                if rc_data:
                    token_info = rc_data.get("token", {})
                    mint_authority = token_info.get("mintAuthority")
                    freeze_authority = token_info.get("freezeAuthority")
                    PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
                    SYSTEM_PROGRAM = "11111111111111111111111111111111"
                    SAFE_AUTHORITIES = {PUMPFUN_PROGRAM, SYSTEM_PROGRAM, None, ""}
                    if mint_authority and mint_authority not in SAFE_AUTHORITIES:
                        return self._deny(mint, "mint-authority-scam", f"🚫 [АНТИСКАМ] Mint Authority у ДЕВ-кошелька {mint_authority[:8]} у {mint[:8]} → СКАМ")
                    if freeze_authority and freeze_authority not in SAFE_AUTHORITIES:
                        return self._deny(mint, "freeze-authority-scam", f"🚫 [АНТИСКАМ] Freeze Authority у ДЕВ-кошелька {freeze_authority[:8]} у {mint[:8]} → СКАМ")

                    # 1. Проверяем РЕАЛЬНУЮ блокировку/сожжение LP во всех рынках (Raydium, PumpSwap, Meteora)
                    # ВАЖНО: В PumpSwap AMM и Meteora DLMM ликвидность контролируется смарт-контрактом пула,
                    # дев физически не может ее снести. В Raydium AMM требуется сожжение/лок >= 75% либо отсутствие кошелька-кита.
                    _KNOWN_AMM_OWNERS = {
                        PUMPFUN_PROGRAM, SYSTEM_PROGRAM, None, "",
                        "2ekvogH7UGLZuCqq2hzbersH8r2iHRsA7pLUgEiSMnkZ",  # PumpSwap AMM Program Authority
                        "BSfD6SHZigAfDWSQu5ngnxBgGWdxPGshaCDFiMmU3zst",  # Pump.fun Raydium migration authority
                        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium Authority V4
                    }
                    _lp_ok = False
                    _unlocked_lp_holder_danger = False
                    for _m in (rc_data.get("markets") or []):
                        if isinstance(_m, dict):
                            _m_type = str(_m.get("marketType") or "").lower()
                            if _m_type in ("pump_fun_amm", "pump_amm", "meteora_damm", "meteora_damm_v2", "meteora_dlmm", "meteora"):
                                _lp_ok = True
                            _m_lp_info = _m.get("lp") or {}
                            if isinstance(_m_lp_info, dict):
                                _m_lp_locked_pct = float(_m_lp_info.get("lpLockedPct", 0) or 0)
                                _m_lp_burned_pct = float(_m_lp_info.get("lpBurnedPct", 0) or 0)
                                _m_holders = _m_lp_info.get("holders") or []
                                if _m_lp_locked_pct >= 75.0 or _m_lp_burned_pct >= 75.0:
                                    _lp_ok = True
                                elif not _lp_ok:
                                    # Проверяем, держит ли неизвестный индивидуальный кошелек >= 25% незалоченного пула:
                                    for h in _m_holders:
                                        if isinstance(h, dict):
                                            h_pct = float(h.get("pct", 0) or 0)
                                            h_owner = str(h.get("owner", "") or h.get("address", ""))
                                            if h_pct >= 25.0 and h_owner not in _KNOWN_AMM_OWNERS and not h_owner.startswith("11111111"):
                                                _unlocked_lp_holder_danger = True

                    # Если кастомный пул удерживается индивидуальным кошельком (LP whale >= 25%) — риск выдергивания (кейс JENSEN)
                    if _unlocked_lp_holder_danger:
                        return self._deny(
                            mint,
                            "unlocked-custom-lp",
                            f"🚫 [UNLOCKED-LP-RUG] {symbol} ({mint[:8]}): пул LP удерживается индивидуальным кошельком (>=25%) — риск сноса ликвидности девом (кейс JENSEN)!"
                        )

                    # Для токенов вне bonding curve и вне официального AMM требуем подтвержденный заблокированный LP >= 75%
                    if not _lp_ok and not _is_canonical_pump and not _is_narr_match:
                        return self._deny(
                            mint,
                            "unlocked-custom-lp",
                            f"🚫 [UNLOCKED-LP-RUG] {symbol} ({mint[:8]}): незаблокированный кастомный LP (<75%) — риск сноса пула (кейсы JENSEN/SOUVENIR)!"
                        )

                    _rc_raw_score = float(rc_data.get("score") or 0.0)

                    # Безвредный шум RugCheck: для launchpad bonding curve / pump / Raydium / Meteora пулов вычитаем "Large Amount of LP Unlocked" (как в 0d2b1ae)!
                    _allowed_lp_terms_main = (
                        "large amount of lp unlocked", "lp provider", "single lp provider",
                        "lp not locked", "unlocked lp", "low liquidity", "copycat", "symbol mimic",
                        "creator history", "low amount of lp providers"
                    )

                    # Исключаем безвредный шум: низкая ликвидность для молодых пулов, copycat для мемов, и LP контракт пула.
                    _rc_penalty_to_ignore = sum(
                        float(rk.get("score", 0) or 0)
                        for rk in (rc_data.get("risks") or [])
                        if any(k in str(rk.get("name", "")).lower() for k in _allowed_lp_terms_main)
                    )

                    _rc_score = max(0.0, _rc_raw_score - _rc_penalty_to_ignore)
                    if _rc_score >= 5000:
                        return self._deny(mint, f"rugcheck-score {_rc_score:.0f}", f"🚫 [АНТИСКАМ] RugCheck Score {_rc_score:.0f} >= 5000 у {mint[:8]} → СКАМ")

                    # Жёсткий фильтр критических опасностей RugCheck
                    for rk in (rc_data.get("risks") or []):
                        _rk_name = str(rk.get("name", "")).lower()
                        _rk_lvl = str(rk.get("level", "")).lower()
                        if (_is_canonical_pump or _is_narr_match or _lp_ok) and any(k in _rk_name for k in ("large amount of lp unlocked", "lp not locked", "unlocked lp")):
                            continue
                        if "creator history" in _rk_name:
                            # В коммите 0d2b1ae creator history не блокировал молодые pump токены
                            continue
                        if _rk_lvl == "danger" and any(k in _rk_name for k in ("single holder", "high ownership", "top 10 holders", "freeze authority")):
                            return self._deny(mint, f"rc-danger {_rk_name[:15]}", f"🚫 [АНТИСКАМ-RC] {symbol} ({mint[:8]}): критический риск RugCheck '{rk.get('name')}' — блок скама!")

                    # Проверка холдеров RugCheck
                    top_holders = rc_data.get("topHolders") or []
                    dev_holding_pct = 0.0
                    creator = rc_data.get("creator")
                    try:
                        from birth_tracker import birth_tracker as _bt_c
                        if creator and _bt_c.is_bad_creator(creator):
                            return self._deny(mint, "serial-creator-blacklist", f"🚫 [АНТИСКАМ-DEV] {symbol} ({mint[:8]}): создатель {creator[:8]} в черном списке серийных раггеров — блок скама!")
                    except Exception:
                        pass

                    # Собираем все известные адреса пулов и маркеров ликвидности (DEX, AMM, Bonding curve, Lockers)
                    pool_accounts = set()
                    if pair_data:
                        _p_addr = pair_data.get("pairAddress")
                        if _p_addr:
                            pool_accounts.add(_p_addr)
                        for _p in (pair_data.get("pairs") or []):
                            if isinstance(_p, dict) and _p.get("pairAddress"):
                                pool_accounts.add(_p["pairAddress"])
                    for _k_acc, _v_acc in (rc_data.get("knownAccounts") or {}).items():
                        if isinstance(_v_acc, dict) and (_v_acc.get("type") in ("AMM", "LOCKER", "DEX") or any(w in str(_v_acc.get("name", "")).lower() for w in ("pool", "amm", "raydium", "meteora", "pump", "vault", "locker"))):
                            pool_accounts.add(_k_acc)
                        elif isinstance(_v_acc, str) and any(w in _v_acc.lower() for w in ("pool", "amm", "raydium", "meteora", "pump", "vault", "locker")):
                            pool_accounts.add(_k_acc)
                    _target_pair_rc = str((pair_data or {}).get("pairAddress") or "").strip()
                    for _mkt in (rc_data.get("markets") or []):
                        if isinstance(_mkt, dict):
                            # Точечная защита от незаблокированных пулов Meteora (DAMM V2, DLMM, DYN2) и кастомных Raydium
                            # ВАЖНО: не блокируем канонический выпускник PumpSwap (...pump с сожжённым LP $25k+) из-за стороннего $10 DLMM арбитраж-пула (кейс DEBTDOG +46%)!
                            _m_pub = str(_mkt.get("pubkey") or "").strip()
                            _m_type = str(_mkt.get("marketType", "")).lower()
                            _m_lp = _mkt.get("lp") or {}
                            _m_lp_locked = float(_m_lp.get("lpLockedPct", 0) or 0)
                            _m_lp_burned = float(_m_lp.get("lpBurnedPct", 0) or 0)
                            _m_lp_safe = max(_m_lp_locked, _m_lp_burned)
                            _m_quote_usd = float(_m_lp.get("quoteUSD", 0) or 0)
                            _is_target_mkt = bool(_target_pair_rc and _m_pub == _target_pair_rc)
                            _is_meteora_pool = ("meteora" in _m_type and (_is_target_mkt or "meteora" in dex_id_low or (not _is_canonical_pump and _m_quote_usd >= 2000.0)))
                            if _is_meteora_pool and _m_lp_safe < 80.0:
                                _has_crit_rug = bool(rc_data.get("freezeAuthority")) or bool(rc_data.get("mintAuthority")) or any(
                                    rk.get("level") == "danger" and any(w in str(rk.get("name", "")).lower() for w in ("single holder", "high ownership", "creator holding"))
                                    for rk in (rc_data.get("risks") or [])
                                )
                                if _has_crit_rug:
                                    return self._deny(mint, "meteora-unlocked-lp", f"🚫 [METEORA-RUG] {symbol} ({mint[:8]}): незаблокированный пул Meteora {_m_type} (lock/burn {_m_lp_safe:.0f}% < 80%) с критическим риском — дев выдернет SOL (кейсы PEPEBOT/Smiley)!")
                            if not _is_pump_flag and "raydium" in _m_type and (_is_target_mkt or _m_quote_usd >= 2000.0) and _m_lp_safe < 80.0:
                                _has_crit_rug_ray = bool(rc_data.get("freezeAuthority")) or bool(rc_data.get("mintAuthority"))
                                if _has_crit_rug_ray:
                                    return self._deny(mint, "raydium-unlocked-lp", f"🚫 [RAYDIUM-RUG] {symbol} ({mint[:8]}): кастомный токен на Raydium с незаблокированным LP (lock/burn {_m_lp_safe:.0f}% < 80%) — риск сноса ликвидности!")

                            for _m_field in ("pubkey", "liquidityA", "liquidityB", "baseVault", "quoteVault"):
                                if _mkt.get(_m_field):
                                    pool_accounts.add(_mkt[_m_field])

                    if dex_id_low == "meteora":
                        _met_locked = any("meteora" in str(m.get("marketType", "")).lower() and (float((m.get("lp") or {}).get("lpLockedPct", 0) or 0) >= 80.0 or float((m.get("lp") or {}).get("lpBurnedPct", 0) or 0) >= 80.0) for m in (rc_data.get("markets") or []))
                        if not _met_locked:
                            _has_crit_met = bool(rc_data.get("freezeAuthority")) or bool(rc_data.get("mintAuthority")) or any(
                                rk.get("level") == "danger" and any(w in str(rk.get("name", "")).lower() for w in ("single holder", "high ownership", "creator holding"))
                                for rk in (rc_data.get("risks") or [])
                            )
                            if _has_crit_met:
                                return self._deny(mint, "meteora-unlocked-lp", f"🚫 [METEORA-RUG] {symbol} ({mint[:8]}): токен на Meteora без заблокированного LP с критическим риском манипуляции (кейс PEPEBOT)!")

                    DEX_POOL_AUTHORITIES = {
                        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", # Raydium AMM V4
                        "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5", # Raydium CP-Swap
                        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", # PumpSwap AMM
                        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB", # Meteora Dynamic Pools
                        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", # Meteora DLMM
                        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", # Orca Whirlpools
                        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", # Pump.fun Bonding Curve
                        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1", # Pump.fun Authority
                    }

                    pool_keywords = ["raydium", "pool", "vault", "pump", "amm", "meteora", "orca", "locker"]
                    non_curve_accounts = []
                    for h in top_holders:
                        owner = str(h.get("address") or "")
                        owner_wallet = str(h.get("owner") or "")
                        pct = float(h.get("pct", 0) or 0)
                        amt = float(h.get("amount", 0) or h.get("uiAmount", 0) or 0)
                        if creator and (owner == creator or owner_wallet == creator):
                            dev_holding_pct = pct

                        # Исключаем адреса пулов ликвидности, AMM и системные адреса Raydium/Pump/Meteora
                        if owner in pool_accounts or owner_wallet in pool_accounts or owner in DEX_POOL_AUTHORITIES or owner_wallet in DEX_POOL_AUTHORITIES:
                            continue
                        if any(w in owner.lower() for w in pool_keywords) or any(w in owner_wallet.lower() for w in pool_keywords):
                            continue
                        non_curve_accounts.append((amt, pct, owner))
                    top_10 = non_curve_accounts[:10]
                    # Jito bundles sybil check
                    bundle_suspects = [round(pct, 1) for (amt, pct, owner) in top_10 if pct >= 2.5]
                    if len(bundle_suspects) >= 4:
                        from collections import Counter
                        counts = Counter(bundle_suspects)
                        most_common_pct, count = counts.most_common(1)[0]
                        if count >= 4 and most_common_pct >= 2.5 and (most_common_pct * count) >= 12.0:
                            return self._deny(mint, "jito-bundle-sybil", f"🚫 [АНТИСКАМ] Jito-бандл ({count} кошельков по ~{most_common_pct:.1f}%) у {mint[:8]} → Блок.")
                    top_10_sum_pct = sum([x[1] for x in top_10])
                    _cur_tok_top10 = top_10_sum_pct
                    _cur_tok_dev = dev_holding_pct
                    self._last_top10 = top_10_sum_pct
                    self._last_dev = dev_holding_pct

                    # Лимиты холдеров (@savipww Auditor: Dev <= 6% sold 0, Whale <= 15%, Top10 <= 30% на DEX)
                    max_dev_pct = 8.0 if _is_pump_flag else float(getattr(config, "AUDITOR_MAX_DEV_PCT", 6.0))
                    if dev_holding_pct and dev_holding_pct > max_dev_pct:
                        return self._deny(mint, f"dev-hold {dev_holding_pct:.1f}%", f"🚫 [АНТИСКАМ] Дев держит {dev_holding_pct:.1f}% > {max_dev_pct:.0f}% у {mint[:8]} → Блок.")
                    try:
                        from birth_tracker import birth_tracker as _bt_a
                        _dev_dumped, _dev_dump_why = _bt_a.check_creator_dumped(mint, dev_holding_pct)
                        if _dev_dumped:
                            return self._deny(mint, "dev-dumping", f"🚫 [АНТИСКАМ-L3] {symbol} ({mint[:8]}): {_dev_dump_why} → Блок.")
                    except Exception:
                        pass
                    largest_whale_pct = top_10[0][1] if top_10 else 0.0
                    max_whale_pct = 18.0 if _is_pump_flag else 15.0
                    if largest_whale_pct > max_whale_pct:
                        return self._deny(mint, f"whale-hold {largest_whale_pct:.1f}%", f"🚫 [АНТИСКАМ] Кит держит {largest_whale_pct:.1f}% > {max_whale_pct:.0f}% у {mint[:8]} → Блок.")
                    _pump_top10_cap = 38.0 if (_is_pump_flag and (_vm5_pre >= 10000.0 or _b5_pre >= 80)) else (35.0 if _is_pump_flag else float(getattr(config, "AUDITOR_MAX_TOP10_PCT", 32.0)))
                    max_allowed_top10 = _pump_top10_cap
                    if top_10_sum_pct > max_allowed_top10:
                        return self._deny(mint, f"top10-hold {top_10_sum_pct:.1f}%", f"🚫 [АНТИСКАМ] Топ-10 держат {top_10_sum_pct:.1f}% > {max_allowed_top10:.0f}% у {mint[:8]} → Блок.")

                    # Layer 7 (RugCheck insiders/graph) + Layer 8 (SPL Holder Delegations) + Layer 9/10 (Staircase & Sybil Farm)
                    try:
                        import asyncio as _aio_aud
                        import goplus as _gp_aud
                        _tok_meta = rc_data.get("token") or {}
                        _raw_s = float(_tok_meta.get("supply") or 0.0)
                        _dec_s = int(_tok_meta.get("decimals") or 6)
                        _ui_s = (_raw_s / (10 ** _dec_s)) if _raw_s > 0 else 1_000_000_000.0
                        _h_accs = [str(h.get("address") or "") for h in top_holders[:20] if h.get("address") and str(h.get("address")) not in pool_accounts]
                        _h_owners = [str(h.get("owner") or "") for h in top_holders[:20] if h.get("owner") and str(h.get("owner")) not in pool_accounts and float(h.get("pct") or 0) <= 50.0]
                        (_l7_ok, _l7_why), (_l8_ok, _l8_why), (_l9_ok, _l9_why) = await _aio_aud.gather(
                            _gp_aud.check_rugcheck_insider_graph(mint, total_supply_raw=_raw_s, pool_accounts=pool_accounts),
                            _gp_aud.check_spl_holder_delegations(mint, holder_token_accounts=_h_accs, total_supply_ui=_ui_s),
                            _gp_aud.check_staircase_and_sybil_farm(mint, pair_address=str(pair_data.get("pairAddress") or ""), holder_owners=_h_owners),
                        )
                        if not _l7_ok:
                            return self._deny(mint, "insider-graph-bundle", f"🚫 [АНТИСКАМ-L7] {symbol} ({mint[:8]}): {_l7_why} → Блок.")
                        if not _l8_ok:
                            return self._deny(mint, "spl-delegation-drain", f"🚫 [АНТИСКАМ-L8] {symbol} ({mint[:8]}): {_l8_why} → Скрытый дрейнер!")
                        if not _l9_ok:
                            return self._deny(mint, "staircase-sybil-farm", f"🚫 [АНТИСКАМ-L9/10] {symbol} ({mint[:8]}): {_l9_why} → Ботоферма дева!")
                    except Exception:
                        pass
                else:
                    # Фоллбэк если RugCheck ещё не вернул отчёт (код 400/404 или 429/таймаут):
                    # Вслепую НЕ покупаем (защита от Qeshcrb), но возвращаем None (перепроверка через 25с вместо вечного бана)!
                    print(f"⏳ [RUGCHECK-PENDING] {symbol} ({mint[:8]}): отчёт RugCheck ещё не готов (HTTP {_rc_st}) — повтор через 25с.")
                    return None

                # Прямой ончейн RPC аудит
                try:
                    import goplus as _gp
                    _sol_onchain_ok, _sol_onchain_why = await _gp.check_onchain_solana_security(mint)
                    if not _sol_onchain_ok:
                        return self._deny(mint, "onchain-freeze-mint-scam", f"🚫 [АНТИСКАМ-RPC] {symbol} ({mint[:8]}): {_sol_onchain_why} — блок входа.")
                except Exception:
                    pass
            except Exception as e:
                print(f"⚠️ Ошибка проверки безопасности ({e})")
                return False

            # 4. Финальная проверка минутных свечей (защита от водопада в моменте)
            if not await self.verify_1m_candles_not_dumping(mint, pair_data, "solana"):
                return False

            # 5. Stage-2 ML пересчёт с реальными ончейн-данными холдеров этого конкретного токена (локальные переменные защищают от гонок ScanWindow!)
            if _cur_tok_top10 > 0 or _cur_tok_dev > 0:
                _real_holders = {
                    "top1_pct": max(float(_cur_tok_dev or 0.0), float(_cur_tok_top10 or 20.0) / 5.0),
                    "top10_pct": float(_cur_tok_top10 or 20.0),
                    "dev_pct": 0.0 if float(_cur_tok_dev or 0.0) <= 1.5 else float(_cur_tok_dev or 0.0),
                }
                conf = self.score_rocket_xgb(mint, pair_data, holders_info=_real_holders)
                self.last_score = conf
                self.rocket_scores[mint] = conf
            _final_thr = float(getattr(config, "ROCKET_XGB_FINAL_THRESHOLD", 14.0))
            if _links_cnt == 0 and not _soc_unknown:
                _final_thr = max(_final_thr, 18.0)
            if (conf + 0.05) < _final_thr:
                return self._deny(mint, f"rocket-xgb-s2 {conf:.0f}%", f"🚫 [ROCKET-XGB-S2] {symbol} ({mint[:8]}): скор с учётом холдеров {conf:.1f}% < {_final_thr:.0f}% — отсев.")

            self._set_sig(mint, f"ROCKET-XGB {conf:.0f}%")
            print(f"🚀 [ENTRY-APPROVED] {symbol} ({mint[:8]}): ML Score {conf:.1f}%, Безопасность подтверждена! Сигнал на покупку.")
            return True

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
            if _has_strong_buyers and _vol_v >= 5000.0:
                _max_vip_m5 = 500.0  # Пробой ракеты (не режем супер-раннеров вроде BARRON +869%)
            elif _m1 <= 22.0 and _has_strong_buyers:
                _max_vip_m5 = 250.0
            else:
                _max_vip_m5 = 120.0
            if _m5 > _max_vip_m5:
                return self._deny(mint, "vip-overheat", f"🚫 [VIP OVERHEAT] {mint}: m5 {_m5:+.0f}% > +{_max_vip_m5:.0f}% — вертикаль уже прошла, вход = вершина.")
            if _m1 < -8.0:
                return self._deny(mint, "vip-reversal", f"🚫 [VIP REVERSAL] {mint}: m1 {_m1:+.1f}% — глубокий дамп за 1м, отмена.")
            _max_vip_m1 = 28.0
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
                _tx5_rc = (pair_data.get("txns") or {}).get("m5", {}) or {}
                _b5_rc = int(_tx5_rc.get("buys", 0) or 0)
                _s5_rc = int(_tx5_rc.get("sells", 0) or 0)
                if mint.endswith("pump") and _age_min_pre < 2.0:
                    _has_launch_mom = (_vm5_check >= 3000.0 and _m5_check >= 4.0 and _b5_rc >= 8)
                    if not _has_launch_mom:
                        return self._deny(mint, "post-migration-dump-zone", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: возраст {_age_min_pre:.1f}м < 2.0м — опасная зона сброса снайперов кривой.")
                if mint.endswith("pump") and 0 < _fdv_pre < 35000:
                    # DIP RECLAIM: Если токен после миграции просел, но активно выкупается (M5 > +2.5%, объем > $1k, покупателей >= продавцов) — это точка разворота ракеты (как 大A +1156%, OMNI +986%)!
                    _is_reclaim = (_fdv_pre >= 5000 and _m5_check >= 2.5 and _vm5_check >= 1000.0 and _b5_rc >= _s5_rc * 0.85)
                    if _is_reclaim:
                        print(f"🚀 [DIP RECLAIM] {symbol} ({mint[:8]}): FDV ${_fdv_pre:,.0f}, m5 +{_m5_check:.1f}%, vol5 ${_vm5_check:,.0f}, {_b5_rc}b/{_s5_rc}s — разворот после миграции разрешён!")
                    else:
                        return self._deny(mint, "post-migration-dump-fdv", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: мигрировавший токен FDV ${_fdv_pre:,.0f} < $35k без подтверждённого отскока.")
                elif 0 < _fdv_pre < 8000 and not (_m5_check >= 3.0 and _vm5_check >= 1000.0):
                    return self._deny(mint, "post-migration-dump-fdv", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: AMM FDV ${_fdv_pre:,.0f} < $8k без импульса — слитый токен.")
                if _age_min_pre >= 60.0 and ((_fdv_pre < 35000 and _h1_pre < -5.0) or (_h1_pre < -12.0 or _h6_pre < -25.0)):
                    return self._deny(mint, "post-migration-dump-trend", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: часовой/6ч тренд в глубоком сливе (h1 {_h1_pre:+.1f}%, h6 {_h6_pre:+.1f}%) — отскок дохлой кошки.")
                elif _age_min_pre < 60.0 and (_h1_pre < -65.0 or (_h1_pre < -30.0 and float((_pc_pre.get("m5", 0) or 0)) < 5.0)):
                    return self._deny(mint, "post-migration-dump-young", f"🚫 [POST-MIGRATION-DUMP] {mint[:8]}: молодой токен в глубоком сливе (h1 {_h1_pre:+.1f}%) без импульса отскока.")
        if not is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            _m1 = _pc.get("m1", 0) or 0
            _h1 = _pc.get("h1", 0) or 0
            _txm5_pre = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _vm5_pre = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
            # h1-exhausted: режем покупки на излете пампа (вход на хаях)
            if _h1 > 250.0 and _age_min_pre > 10.0:
                return self._deny(mint, f"h1-exhausted {_h1:+.0f}%", f"🚫 [H1-EXHAUSTED] {mint[:8]}: h1 {_h1:+.0f}% > +250% при возрасте {_age_min_pre:.1f}м — памп уже отработал, вход на вершине запрещён.")
            _is_fresh_pump = mint.endswith("pump") or _age_min_pre < 90.0
            _max_h1_limit = 400.0 if not _is_fresh_pump else 800.0
            if _h1 > _max_h1_limit:
                return self._deny(mint, f"h1-exhausted {_h1:+.0f}%", f"🚫 [H1-EXHAUSTED] {mint[:8]}: h1 {_h1:+.0f}% > +{_max_h1_limit:.0f}% — памп уже отработал, вход на хаях запрещён.")

            # Запрет покупки вершины, но пропуск реальных ракет и пробоев
            _chain_ae = str((pair_data or {}).get("chainId", "solana")).lower()
            _b5_tmp, _s5_tmp = int(_txm5_pre.get("buys", 0) or 0), int(_txm5_pre.get("sells", 0) or 0)
            if _age_min_pre <= 30.0 and _b5_tmp >= _s5_tmp:
                # Молодая ракета на первичном старте (кейсы SOLCHAN +102%, 4AGENT +168%, 10/29 +146%)
                _max_m5_entry = 250.0
            elif _vm5_pre >= 4000.0 and _b5_tmp >= _s5_tmp * 0.95 and _m1 <= 25.0:
                # Мощный подтвержденный объем покупателей — ракета в полете
                _max_m5_entry = 220.0
            elif _age_min_pre <= 60.0 and _b5_tmp >= _s5_tmp * 1.05 and _m1 <= 25.0:
                _max_m5_entry = 180.0
            elif _b5_tmp >= 12 and _b5_tmp >= _s5_tmp * 1.02 and _m1 <= 25.0:
                _max_m5_entry = 150.0
            elif _age_min_pre < 60.0 and _m1 <= 22.0:
                _max_m5_entry = 120.0
            else:
                _max_m5_entry = 90.0
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
                if _is_runner or (_vm5_pre >= 2500.0 and _b5_pre >= 15 and _b5_pre >= _s5_pre * 1.05):
                    # Настоящая ракета с высокой ликвидностью и объемом (не микро-лотерейный раг)
                    _m1_max = getattr(config, "PULLBACK_M1_MAX_PCT", 0.12) * 100
                    if _m1 > _m1_max:
                        return self._deny(mint, "runner-m1-spike", f"🚫 [RUNNER-SPIKE] {mint}: m1 {_m1:+.1f}% > +{_m1_max:.0f}% — свеча-прострел в моменте, ждём откат.")
                    if _m1 < getattr(config, "PULLBACK_M1_MIN_PCT", -0.15) * 100:
                        return self._deny(mint, "runner-m1-dump", f"🚫 [RUNNER-DUMP] {mint}: m1 {_m1:+.1f}% — импульс схлопнулся, это дамп.")
                    if _m5 > 450.0:
                        return self._deny(mint, "runner-m5-overheat", f"🚫 [RUNNER-EXHAUSTION] {mint}: m5 {_m5:+.0f}% > +450% — экстремальный перегрев 5м свечи.")
                    _max_h1_cfg = float(getattr(config, "PULLBACK_MAX_H1_PCT", 160.0))
                    _max_h1_pct = (_max_h1_cfg * 100.0) if _max_h1_cfg <= 20.0 else _max_h1_cfg
                    _max_h1_allowed = min(350.0, _max_h1_pct * 1.4)
                    if _h1 > _max_h1_allowed:
                        return self._deny(mint, "runner-h1-gone", f"🚫 [ENTRY] {mint}: h1 {_h1:+.0f}% > +{_max_h1_allowed:.0f}% — ракета уже выдохлась/перегрета, поздно.")
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
                # 2. Максимальный импульс m5 (НЕ ВХОДИМ НА ВЕРШИНЕ, но пускаем подтвержденные ракеты!)
                _max_m5 = 350.0 if (_vm5_pre >= 3500.0 and _b5_pre >= 18 and _b5_pre >= _s5_pre * 1.10) else (getattr(config, "PULLBACK_MAX_M5_PCT", 0.45) * 100)
                if _m5 > _max_m5:
                    return self._deny(mint, f"m5-late {_m5:+.0f}%", f"🚫 [LATE] {mint}: m5 {_m5:+.1f}% > +{_max_m5:.0f}% — ракета уже улетела, ждем откат.")
                # 3. Минутный импульс (если DexScreener отдал m1)
                if _m1 != 0:
                    _m1_max = getattr(config, "PULLBACK_M1_MAX_PCT", 0.12) * 100
                    if _m1 > _m1_max:
                        return self._deny(mint, f"m1-vertical {_m1:+.1f}%", f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% > +{_m1_max:.0f}% — вертикаль в моменте, вход = вершина.")
                    if _m1 < getattr(config, "PULLBACK_M1_MIN_PCT", -0.05) * 100:
                        return self._deny(mint, f"m1-dump {_m1:+.1f}%", f"🚫 [ENTRY] {mint}: m1 {_m1:+.1f}% — падающий нож.")
                # 4. Часовой тренд (не входить в уже улетевшие или падающие)
                _max_h1_cfg = getattr(config, "PULLBACK_MAX_H1_PCT", 160.0)
                _max_h1 = (_max_h1_cfg * 100.0) if _max_h1_cfg <= 20.0 else float(_max_h1_cfg)
                if _h1 > _max_h1:
                    return self._deny(mint, f"h1-gone {_h1:+.0f}%", f"🚫 [ENTRY] {mint}: h1 {_h1:+.0f}% > +{_max_h1:.0f}% — уже улетел.")
                if _h1 < -15.0:
                    return self._deny(mint, f"h1-dump {_h1:+.1f}%", f"🚫 [ENTRY] {mint}: h1 {_h1:+.1f}% — глубокий слив.")
                # 5. Объем и сделки в m5 (не входим в тихие пулы/накрутку)
                _tx_m5_p = (pair_data.get("txns") or {}).get("m5", {}) or {}
                _b5_p = int(_tx_m5_p.get("buys", 0) or 0)
                _s5_p = int(_tx_m5_p.get("sells", 0) or 0)
                _vol5_p = float((pair_data.get("volume") or {}).get("m5", 0) or 0)
                _min_vol = getattr(config, "MIN_VOLUME_M5", 1200)
                _min_tx = getattr(config, "MIN_TXNS_M5", 8)
                _min_ratio = getattr(config, "MIN_BUY_SELL_RATIO", 1.05)
                if _vol5_p < _min_vol:
                    return self._deny(mint, f"vol5-low ${_vol5_p:.0f}", f"🚫 [ENTRY] {mint}: объем m5 ${_vol5_p:.0f} < ${_min_vol} — интерес угас.")
                if (_b5_p + _s5_p) < _min_tx:
                    return self._deny(mint, f"tx5-low {_b5_p+_s5_p}<{_min_tx}", f"🚫 [ENTRY] {mint}: сделок m5 {_b5_p+_s5_p} < {_min_tx} — слишком тихо.")
                _eff_min_ratio = 1.02 if (_vol5_p >= 15000.0 or _cur_liq_pre >= 30000.0) else _min_ratio
                if _s5_p > 0 and (_b5_p / _s5_p) < _eff_min_ratio:
                    return self._deny(mint, f"bs-low {_b5_p}/{_s5_p}", f"🚫 [ENTRY] {mint}: покупки/продажи m5 {_b5_p}/{_s5_p} < {_eff_min_ratio:.2f}x — продавцы давят.")
                # 6. Защита от накрутки (средний чек < $4 — боты, либо аномальный wash-churn)
                _avg_tx = _vol5_p / max(_b5_p + _s5_p, 1)
                if (_b5_p + _s5_p) >= 20 and _avg_tx < 4.0:
                    return self._deny(mint, "wash-bot", f"🚫 [WASH] {mint}: средний чек ${_avg_tx:.1f} < $4 — накрутка ботов.")
                _cur_liq_cand = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
                _churn_cap = 6.0 if _age_min_pre < 15.0 else 12.0
                if _cur_liq_cand > 0 and (_vol5_p / _cur_liq_cand) > _churn_cap and _age_min_pre < 30.0:
                    return self._deny(mint, "wash-churn", f"🚫 [WASH-CHURN] {mint[:8]}: оборот {_vol5_p / _cur_liq_cand:.1f}x ликвидности за 5м в молодом токене ({_age_min_pre:.1f}м) — ловушка накрутки дева.")
                if _s5_p >= 35 and (_b5_p / _s5_p) > 3.8 and _vol5_p >= 40000 and _age_min_pre < 30.0:
                    return self._deny(mint, "fake-buy-wall", f"🚫 [FAKE-BUY-WALL] {mint[:8]}: аномальный перекос b/s {_b5_p / _s5_p:.1f}x при объёме ${_vol5_p:,.0f} — ловушка дева.")
                print(f"✅ [ENTRY-CANDIDATE] {mint}: m5 {_m5:+.1f}%, m1 {_m1:+.1f}%, h1 {_h1:+.0f}%, b/s {_b5_p}/{_s5_p}, vol5 ${_vol5_p:.0f}")
            _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
            _b5, _s5 = _txm5.get("buys", 0) or 0, _txm5.get("sells", 0) or 0
            if _s > 0 and _b < _s * 1.1 and not (_m5 >= 3.0 and _s5 > 0 and _b5 >= _s5 * 1.2):
                return self._deny(mint, "no-buyer-pressure-h1", f"🚫 [ENTRY] {mint}: buys {_b} / sells {_s} — нет давления покупателей.")
            if _s == 0 and _b > 10 and not mint.endswith("pump"):
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

        # БЛОК КАСТОМНЫХ ФЕЙК-ПУЛОВ НА PUMPSWAP:
        # Блокируем только микро-пулы скамеров (< $6,000 ликвидности),
        # но НЕ режем органические ракеты с реальной ликвой (10/29 +114%, AISI +222%, GTA AI +205%, PODCAT +196%, GANG +176%, CPU +118%)
        if dex_id == "pumpswap" and not mint.endswith("pump") and liquidity < 6000.0:
            return self._deny(mint, "fake-pumpswap-unlocked-lp", f"🚫 [FAKE-PUMPSWAP] {symbol} ({mint[:8]}): кастомный не-pump контракт на PumpSwap с микро-пулом (${liquidity:.0f} < $6k) — блок!")

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

        # Клон-фильтр: защищает от скам-подделок, но пропускает подтвержденных органических лидеров
        _is_organic_leader = (_vol5_cl >= 3000.0 and _b5_cl >= 20 and _b5_cl >= _s5_cl * 1.10)
        if _is_organic_leader:
            print(f"🔥 [ORGANIC-RUNNER] {symbol} ({mint[:8]}): подтвержден живой покупательский поток ({_b5_cl}b/{_s5_cl}s, vol5 ${_vol5_cl:,.0f}) — пропуск клон-фильтра!")
        elif await self.is_clone(symbol, mint, current_created_at, current_fdv, current_liq=liquidity, current_vol5=_vol5_cl, current_b5=_b5_cl):
            return self._deny(mint, "clone-scam", f"🚫 [CLONE-SCAM] Токен {symbol} ({mint[:8]}) является фейковым клоном более старого/крупного оригинала!")


        info = pair_data.get("info", {}) or {}
        socials = info.get("socials", []) or []
        websites = info.get("websites", []) or []
        
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        has_socials = bool(has_twitter or has_tg or has_website)
        
        _socials_unknown = bool(pair_data.get("_socials_unknown", False))
        _is_pump_mint = mint.endswith("pump") or str(pair_data.get("dexId", "")).lower() in ("pumpswap", "pump", "pumpfun", "meteora", "meteoradbc", "raydium", "moonshot")
        # Анонимные токены вообще без соцсетей при ликвидности < $35k — это бандлер-фермы, ЕСЛИ это не pump.fun и нет органического потока!
        if not _socials_unknown and not has_socials and liquidity < 35000 and not _lottery and not _is_pump_mint and not _is_organic_leader:
            return self._deny(mint, "no-socials-anon-rug", f"🚫 [NO-SOCIALS] У {symbol} ({mint[:8]}) 0 соцсетей/сайтов (Twitter/TG/Web) при ликвидности ${liquidity:,.0f} (<$35k) — анонимный дев-бандлер.")
            
        if is_vip:
            print(f"🚀 [VIP] {symbol}: Проверка клонов и соцсетей пройдена!")
            
        try:
            import goplus as _gp_rc
            _rc_st, rc_data = await _gp_rc.fetch_rugcheck_report_cached(mint, ttl=120.0, timeout=6)
            if _rc_st == 400:
                print(f"ℹ️ [RUGCHECK] {mint[:8]}: монета ещё не проиндексирована RugCheck (код 400) -> аудит через GoPlus & On-Chain RPC...")
            elif _rc_st not in (200, 0, 429):
                print(f"⚠️ RugCheck API вернул {_rc_st} для {mint[:8]}.")
                    
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
                if not _is_pump_mint and not _has_verified_locked_lp:
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
                        for _m_field in ("pubkey", "liquidityA", "liquidityB", "baseVault", "quoteVault"):
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
                    
                    DEX_POOL_AUTHORITIES = {
                        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", # Raydium AMM V4
                        "D4FPEruKEHrG5NTsqazoqfv8zsFWApqTjuzFLUQDkTz5", # Raydium CP-Swap
                        "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL", # Raydium CPMM / LaunchLab
                        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", # Raydium CLMM
                        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", # PumpSwap AMM
                        "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg", # Pump.fun Migration Authority
                        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB", # Meteora Dynamic Pools
                        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", # Meteora DLMM
                        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", # Orca Whirlpools
                        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", # Pump.fun Bonding Curve
                        "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1", # Pump.fun Authority
                    }
                    # Исключаем пулы ликвидности, AMM и системные адреса Raydium/Pump/Meteora
                    if owner in pool_accounts or addr in pool_accounts or owner in DEX_POOL_AUTHORITIES or addr in DEX_POOL_AUTHORITIES:
                        continue
                    if any(w in owner.lower() for w in pool_keywords) or any(w in addr.lower() for w in pool_keywords):
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
                
                # Если дев не найден в rc_data, не приравниваем слепо кита к деву (кита проверит max_single_whale)
                self._last_top10 = top_10_sum_pct
                self._last_dev = dev_holding_pct
                
                is_pump = pair_data and pair_data.get("dexId") in ("pump", "pumpfun")
                max_allowed_pct = 40.0 if is_pump else 45.0

                max_dev_pct = float(getattr(config, "ROBINHOOD_MAX_CREATOR_PCT", 0.20)) * 100.0
                if is_pump:
                    max_dev_pct = min(max_dev_pct, 15.0)
                else:
                    max_dev_pct = min(max_dev_pct, 10.0)
                
                if top_10_sum_pct > 100:
                    print(f"⚠️ [HOLDERS] {mint[:8]}: топ-10 {top_10_sum_pct:.1f}% > 100% — битые данные сапплая.")
                elif dev_holding_pct and dev_holding_pct > max_dev_pct:
                    print(f"🚫 [АНТИСКАМ] Создатель (Dev) держит {dev_holding_pct:.1f}% (Лимит {max_dev_pct:.0f}%). Блокируем.")
                    return False
                
                # Защита от скрытого второго кошелька дева: ни один кит не должен держать > 25% (pump) / 20% (dex)
                largest_whale_pct = top_10[0][1] if top_10 else 0.0
                max_single_whale = 25.0 if is_pump else 20.0
                if largest_whale_pct > max_single_whale:
                    print(f"🚫 [АНТИСКАМ] Кит держит {largest_whale_pct:.1f}% > {max_single_whale:.0f}% (риск скрытого дева/дампа). Блокируем.")
                    return False
                elif top_10_sum_pct > max_allowed_pct:
                    print(f"🚫 [АНТИСКАМ] Топ-10 держат {top_10_sum_pct:.1f}% (Лимит {max_allowed_pct}%). Блокируем.")
                    return False
            else:
                # rc_data is None (RugCheck 400, 404, 429, timeout)
                if not _is_pump_mint:
                    # Для не-pump токенов допускаем вход ТОЛЬКО если GoPlus подтверждает чистоту контракта и есть данные о холдерах и пул >= $10k
                    _gp_can_fallback = False
                    try:
                        import goplus as _gp
                        _g_ok, _g_why = await _gp.check_solana(mint)
                        _h_stats = _gp.get_solana_holder_stats(mint)
                        if _g_ok and _h_stats.get("has_data") and liquidity >= 10000.0:
                            _gp_can_fallback = True
                    except Exception:
                        pass
                    if not _gp_can_fallback:
                        print(f"🚫 [NO-RUGCHECK-CUSTOM] {symbol} ({mint[:8]}): кастомный не-pump токен без отчёта RugCheck и без подтверждения GoPlus — блок входа (риск разблокированного LP).")
                        return False
                # Фоллбэк: если RugCheck временно 404/429/400, проводим аудит через GoPlus
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
                        _max_top10 = 30.0 if _is_pump_mint else 35.0
                        if self._last_top10 > _max_top10:
                            print(f"🚫 [GOPLUS-CABAL] {symbol} ({mint[:8]}): топ-10 держат {self._last_top10:.1f}% > {_max_top10:.0f}% — риск кабала.")
                            return False
                        print(f"🛡️ [GOPLUS] RugCheck недоступен, но GoPlus подтвердил безопасность {mint[:8]} (Топ-10: {self._last_top10:.1f}%, Дев: {self._last_dev:.1f}%).")
                    else:
                        if not _is_pump_mint:
                            print(f"🚫 [NO-AUDIT] {symbol} ({mint[:8]}): Ни RugCheck, ни GoPlus не имеют данных холдеров — блок входа по безопасности.")
                            return False
                        print(f"🛡️ [GOPLUS] RugCheck недоступен, GoPlus подтвердил отсутствие ловушек у {mint[:8]} (Pump.fun кривая).")
                except Exception as _ge:
                    print(f"⚠️ Не удалось проверить через RugCheck и GoPlus ({_ge}). Блокируем вход.")
                    return False
        except Exception as e:
            print(f"⚠️ Ошибка RugCheck ({str(e)[:50]}). Fail-Closed для защиты от рагпула.")
            if not _is_pump_mint:
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
            _floor_liq = 1000
        else:
            _floor_liq = 4000 if (age_minutes < 60 or _lottery or is_vip) else (8000 if (pair_data.get("chainId") or "").lower() == "solana" else 4000)
        if liq < _floor_liq:
            return self._deny(mint, f"low-liq ${liq:.0f}<{_floor_liq}", f"🚫 [ANTI-RUG] {symbol} ({mint[:8]}): Ликвидность всего ${liq:,.0f} < ${_floor_liq}. Слишком опасно.")

        # Защита от фантомных пулов с мизерным остатком реального SOL (кейс Hodlon -$14.91: в пуле всего 1.17 SOL):
        _quote_info = pair_data.get("liquidity", {}) or {}
        _quote_amt = float(_quote_info.get("quote", 0) or 0)
        _quote_sym = str((pair_data.get("quoteToken") or {}).get("symbol", "")).upper()
        if "SOL" in _quote_sym and 0 < _quote_amt < 5.0 and dex_id not in ("pump", "pumpfun"):
            return self._deny(mint, f"ghost-pool-sol {_quote_amt:.1f}<5", f"🚫 [AMM-GHOST-POOL] {symbol} ({mint[:8]}): в пуле всего {_quote_amt:.2f} SOL < 5 SOL — микро-резерв, риск 100% слива девом.")

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
            if c0_open > 0 and 0.04 <= (eff_close / c0_open) <= 12.0:
                c0_pct = max(-95.0, min(180.0, (eff_close - c0_open) / c0_open * 100.0))
                if not isinstance(pair_data.get("priceChange"), dict):
                    pair_data["priceChange"] = {}
                pair_data["priceChange"]["m1"] = round(c0_pct, 2)
            else:
                c0_pct = float(((pair_data.get("priceChange") or {}).get("m1", 0)) or 0)

            highs_5m = [float(r[2] or 0) for r in ohlcv[:5] if float(r[2] or 0) > 0]
            lows_5m = [float(r[3] or 0) for r in ohlcv[:5] if float(r[3] or 0) > 0]
            if cur_px > 0:
                highs_5m.append(cur_px)
                lows_5m.append(cur_px)
            high_5m = max(highs_5m) if highs_5m else 0.0
            low_5m = min(lows_5m) if lows_5m else 0.0
            drawdown_5m_pct = ((eff_close - high_5m) / high_5m * 100.0) if high_5m > 0 else 0.0

            # А) Активная красная свеча прямо сейчас (реальный слив >= -7.5% за текущую минуту)
        
            # Расширяем до -12.0%: для мемкоинов откат на 7-10% — это нормальный ретест, а не смерть
            if c0_pct <= -12.0:
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

            # В) Blow-off Top: цена упала на >= 30% от максимума последних 5 минут (разрешаем умеренные откаты)
            if drawdown_5m_pct <= -30.0 and c0_pct < 2.0:
                print(f"🚫 [BLOWOFF-TOP] {symbol} ({address[:8]}): цена уже упала на {drawdown_5m_pct:.1f}% от 5м-пика — вершина пройдена, идёт разгрузка.")
                return False

            # В2) 5-минутное истощение (5m Exhaustion): если за последние 5 минут токен уже вырос в >= 5.0 раз от лоя
            if low_5m > 0 and (high_5m / low_5m) >= 5.0:
                print(f"🚫 [5M-EXHAUSTION] {symbol} ({address[:8]}): за 5м уже сделал {high_5m/low_5m:.1f}x от лоя — кульминация пройдена.")
                return False

            # Г) Верхний фитиль (Shooting Star): глубокий слив от хая текущей свечи >= 22%
            c0_wick_drop = ((c0_high - eff_close) / c0_high * 100.0) if c0_high > 0 else 0.0
            if (c0_wick_drop >= 22.0 and c0_pct <= 0.0) or c0_wick_drop >= 35.0:
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
        _c_ms_sc = pair_data.get("pairCreatedAt") or 0
        if _c_ms_sc:
            _age_min = ((_tt.time() * 1000 - _c_ms_sc) / 60000.0)
        else:
            _bt_age = None
            try:
                from birth_tracker import birth_tracker as _bt_check
                _bt_age = _bt_check.get_age(address)
            except Exception:
                pass
            if _bt_age is not None:
                _age_min = float(_bt_age)
            elif str(address).endswith("pump") or str(pair_data.get("dexId", "")).lower() in ("pump", "pumpfun", "pumpswap"):
                _age_min = 0.1
            else:
                _age_min = 999.0
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
        if _fdv_curve > 58000:
            return self._deny(mint, "pump-graduation-cliff", f"🚫 [GRADUATION-CLIFF] {mint[:8]}: кривая Pump.fun (FDV ${_fdv_curve:,.0f} > $58k) в зоне разгрузки снайперов перед миграцией — ждём выхода на AMM.")

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

        if not getattr(config, "PUMP_XGB_ENABLED", False):
            return self._deny(mint, "pump-xgb-disabled", f"🚫 [PUMP-XGB] {mint[:8]}: PUMP-XGB отключен в config.py (100% сливов на вершинах).")

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
            if _ray_liq < 4500.0:
                return self._deny(mint, "migrated-drained-liq",
                                  f"🚫 [POST-MIGRATION] {mint[:8]}: ликвидность ${_ray_liq:,.0f} < $4,500 — снайперы высосали SOL на миграции.")

            _fdv_check = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
            if 0 < _fdv_check < 14000.0:
                return self._deny(mint, "migrated-below-graduation",
                                  f"🚫 [POST-MIGRATION] {mint[:8]}: FDV ${_fdv_check:,.0f} < $14,000 — слит ниже уровня миграции.")

        _sol_min_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 1.5))
        if _age_min < _sol_min_age:
            return self._deny(mint, "ray-young", f"⏳ [RAYDIUM] {mint[:8]}: возраст {_age_min:.1f}м < {_sol_min_age}м — ранняя flash-rug зона.")

        _ray_liq = float((pair_data.get("liquidity") or {}).get("usd", 0) or 0)
        _min_ray_liq = 4000.0 if (_age_min < 60) else 8000.0
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
        # Защита фичей XGBoost от битых котировок / смены пула (+208,153,141% m1 ронял скор живых ракет):
        if abs(_m1) > max(180.0, abs(_m5) * 3.0 + 40.0):
            _m1 = max(-40.0, min(40.0, _m5 * 0.20))
        _m1 = max(-95.0, min(200.0, _m1))
        _h6 = float(_pc.get("h6", 0) or 0)
        _fdv_check = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)

        _vm5 = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))
        _txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        _b5, _s5 = int(_txm5.get("buys", 0) or 0), int(_txm5.get("sells", 0) or 0)

        # Защита от покупки экстремального вертикального выброса (Blow-off peak > 35%)
        _max_blow = max(35.0, getattr(config, "PULLBACK_M1_MAX_PCT", 0.12) * 100)
        if _m1 > _max_blow and not (_b5 >= _s5 * 1.5 and _vm5 >= 20000):
            return self._deny(mint, "m1-blowoff", f"🚫 [FOMO-TOP] {mint[:8]}: m1 +{_m1:.1f}% > +{_max_blow:.0f}% — экстремальный минутный прострел (вершина).")
        
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
        if _m1 < -8.0:
            return self._deny(mint, "ray-m1-drop", f"🚫 [RAYDIUM] {mint[:8]}: m1 {_m1:+.1f}% — активный сброс в моменте (падающий нож).")
        if _m1 > 22.0:
            return self._deny(mint, "ray-m1-blowoff", f"🚫 [RAYDIUM] {mint[:8]}: m1 {_m1:+.1f}% > +22% — вертикальный прострел за 1м, вход = вершина.")
        if _m5 < 2.5:
            return self._deny(mint, "ray-m5-flat", f"🚫 [RAYDIUM] {mint[:8]}: m5 {_m5:+.1f}% < +2.5% — нет импульса.")

        # Защита от накрутки дев-ботами на PumpSwap (только при микро-чеках ботов < $5.0, а реальный поток покупателей b5>=250 пропускаем!)
        if _dex_low == "pumpswap" and (_b5 / max(_s5, 1)) >= 3.5 and _b5 >= 250 and (_vm5 / max(_b5 + _s5, 1)) < 5.0:
            return self._deny(mint, "pumpswap-buybot-wall", f"🚫 [PUMPSWAP-BOT-WALL] {mint[:8]}: аномальный перекос покупок {_b5}b/{_s5}s ({_b5/max(_s5,1):.1f}x) при микро-чеке — боты дева рисуют стенку.")

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
        if _t10 > 65.0:
            return self._deny(mint, "ray-top10-cabal", f"🚫 [TOP10-CABAL] {mint[:8]}: топ-10 держат {_t10:.1f}% > 65% — кабал/дамп-риск.")

        # === ROCKET XGBOOST INFERENCE (20 ФИЧЕЙ БЕЗ ЛИКЕДЖА) ===
        import pandas as pd
        
        # 1. Защита от покупки на макушке вертикали
        if _m1 > 35.0:
            return self._deny(mint, "fomo-exhaustion", f"🚫 [EXHAUSTION] {mint[:8]}: 1m свеча +{_m1:.1f}% > 35% перегрета — ждём отката (bid the flush).")

        _is_pump = 1 if (mint.endswith("pump") or _dex_low in ("pump", "pumpfun", "pumpswap")) else 0
        _curve_liq = fdv * 0.35 if _is_pump else 0.0
        _eff_liq = max(liquidity, _curve_liq) if _is_pump else liquidity

        _m1_exhaustion = (_m1 / max(abs(_m5), 1.0)) if abs(_m5) > 0 else 0.0
        _buy_ratio_m5 = (_b5 / max(_b5 + _s5, 1.0)) if (_b5 + _s5) > 0 else 0.5
        _net_buyers_m5 = _b5 - _s5
        _vol_to_liq = (_vm5 / max(_eff_liq, 1.0)) if _eff_liq > 0 else 0.0
        _avg_tx_usd = (_vm5 / max(_b5 + _s5, 1.0)) if (_b5 + _s5) > 0 else 0.0
        _fdv_to_liq = (fdv / max(_eff_liq, 1.0)) if _eff_liq > 0 else 3.5
        _socials = 1 if len((pair_data.get("info") or {}).get("socials") or []) > 0 else 0
        _top1_val = getattr(self, '_last_top1', 3.5)

        FEATURE_COLS = [
            "m5_pct", "m1_pct", "h1_pct", "m1_to_m5",
            "buys_m5", "sells_m5", "buy_ratio_m5", "net_buyers_m5",
            "volume_m5", "vol_to_liq", "avg_tx_usd",
            "liquidity", "fdv", "fdv_to_liq",
            "age_min", "is_pump",
            "top1_pct", "top10_pct", "dev_pct", "socials"
        ]

        rkt_features = {
            "m5_pct": float(_m5),
            "m1_pct": float(_m1),
            "h1_pct": float(_h1),
            "m1_to_m5": float(_m1_exhaustion),
            "buys_m5": float(_b5),
            "sells_m5": float(_s5),
            "buy_ratio_m5": float(_buy_ratio_m5),
            "net_buyers_m5": float(_net_buyers_m5),
            "volume_m5": float(_vm5),
            "vol_to_liq": float(_vol_to_liq),
            "avg_tx_usd": float(_avg_tx_usd),
            "liquidity": float(_eff_liq),
            "fdv": float(fdv),
            "fdv_to_liq": float(_fdv_to_liq),
            "age_min": float(_age_min),
            "is_pump": int(_is_pump),
            "top1_pct": float(_top1_val),
            "top10_pct": float(_t10),
            "dev_pct": float(_dev),
            "socials": int(_socials)
        }

        # Если доступна новая модель Rocket XGBoost v2, используем её
        if getattr(self, "rocket_xgb", None) is not None and getattr(config, "ROCKET_XGB_ENABLED", True):
            try:
                rkt_df = pd.DataFrame([rkt_features])[FEATURE_COLS]
                prob = self.rocket_xgb.predict_proba(rkt_df)[0][1]
                conf = float(prob * 100.0)
                threshold = float(getattr(config, "ROCKET_XGB_FINAL_THRESHOLD", getattr(config, "ROCKET_XGB_THRESHOLD", 18.0)))
                self.last_score = conf
                is_buy = bool((conf + 0.05) >= threshold)
                print(f"🚀 [ROCKET-XGB v3] {mint[:8]} ({pair_data.get('baseToken', {}).get('symbol', '?')}): Score {conf:.1f}% (Порог {threshold:.0f}%) -> {'✅ BUY' if is_buy else '🚫 SKIP'}")
                if is_buy:
                    self._set_sig(mint, f"ROCKET-XGB {conf:.0f}%")
                    return True
                else:
                    self._deny(mint, f"rocket-xgb {conf:.0f}%", f"· [ROCKET-XGB] {mint[:8]}: уверенность модели {conf:.1f}% < {threshold:.0f}%")
                    try:
                        from shadow_tracker import shadow_tracker
                        price = float(pair_data.get("priceUsd", 0) or 0.0)
                        symbol_name = str(pair_data.get('baseToken', {}).get('symbol') or mint[:6]).upper()
                        shadow_tracker.log_rejection(
                            mint=mint,
                            reason=f"Rocket XGBoost low score: {conf:.1f}%",
                            symbol=symbol_name,
                            chain="solana",
                            score=conf,
                            rkt_score=conf,
                            price=price,
                            liquidity=float(_eff_liq),
                            m5_pct=float(_m5),
                            source="ROCKET_XGB_RAYDIUM",
                            features=rkt_features
                        )
                    except Exception:
                        pass
                    return False
            except Exception as e:
                print(f"⚠️ Ошибка инференса Rocket XGBoost v2: {e}")

        # Старая модель Raydium XGBoost: если явно не включена в config, блокируем вход
        if not getattr(config, "RAY_XGB_ENABLED", False):
            return self._deny(mint, "ray-xgb-disabled", f"🚫 [RAY-XGB] {mint[:8]}: старая модель Raydium XGB отключена в config.py.")

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
            _hyper = self.check_hyper_rocket_momentum(pair_data) and bool(getattr(config, "VIP_ENTRIES_ENABLED", False))

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
