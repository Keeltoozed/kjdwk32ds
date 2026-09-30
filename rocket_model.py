"""ROCKET-модель: ловит ракеты, а не мусор.

Что это: XGBoost-классификатор поверх снапшота рынка В МОМЕНТ ВХОДА.
Метка: пик сделки >= +50% (ROCKET_PEAK_PCT) → 1, иначе 0.
Учится на НАШИХ закрытых сделках (entry-фичи из ml_features + пик из
max_price_usd / post_exit_ath), поэтому знает именно наши EVM-пампы
(NFLOAT +400%, CSI +114%), а не чужую теорию.

Интеграция fail-open: нет файла модели → score None → поведение как раньше.
Модель только УСИЛИВАЕТ (x1.5 сайз при score>=0.65) и ОСЛАБЛЯЕТ (x0.5 при
score<=0.35), но никогда не ветирует гейты — поток сделок не убивает.
"""
import math
import os

MODEL_PATH = "rocket_model.json"

# Канонический вектор (порядок фиксирован!). Два ридера ниже приводят
# к нему и живой pair_data, и stored ml_features.
FEATURES = [
    "m5", "m1", "h1", "h24",            # импульс, %
    "buys_m5", "sells_m5",              # давление 5м
    "buys_h1", "sells_h1",              # давление часа
    "bs_ratio_m5", "bs_ratio_h1",       # buys/sells
    "vol_m5", "vol_h24",                # объёмы (log)
    "vol_to_liq",                       # объём/ликвидность
    "log_liq", "log_fdv",               # размер (log)
    "age_min",                          # возраст пула (log)
    "links",                            # соцсети+сайты, шт
]


def _f(x, default=0.0):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _log1p(x):
    return math.log1p(max(0.0, _f(x)))


def features_from_pair(pair_data: dict) -> list:
    """Вектор из живого pair_data (EVM/Solana форма DexScreener)."""
    try:
        pd = pair_data or {}
        pc = pd.get("priceChange") or {}
        txm5 = ((pd.get("txns") or {}).get("m5") or {})
        txh1 = ((pd.get("txns") or {}).get("h1") or {})
        vol = (pd.get("volume") or {})
        liq = _f((pd.get("liquidity") or {}).get("usd", 0))
        b5, s5 = _f(txm5.get("buys", 0)), _f(txm5.get("sells", 0))
        bh, sh = _f(txh1.get("buys", 0)), _f(txh1.get("sells", 0))
        vm5, vh = _f(vol.get("m5", 0)), _f(vol.get("h24", 0))
        info = (pd.get("info") or {})
        links = len(info.get("socials") or []) + len(info.get("websites") or [])
        age = 999.0
        try:
            import time as _t
            _created = pd.get("pairCreatedAt") or 0
            if _created:
                age = max(0.0, (_t.time() * 1000 - float(_created)) / 60000.0)
        except Exception:
            pass
        return [
            _f(pc.get("m5", 0)), _f(pc.get("m1", 0)),
            _f(pc.get("h1", 0)), _f(pc.get("h24", 0)),
            b5, s5, bh, sh,
            (b5 / (s5 + 1)) if (b5 + s5) > 0 else (bh / (sh + 1)),
            (bh / (sh + 1)) if (bh + sh) > 0 else 0.0,
            _log1p(vm5), _log1p(vh),
            ((vm5 / (liq + 1)) if vm5 else (vh / (liq + 1))),
            _log1p(liq), _log1p(_f(pd.get("fdv", 0))),
            _log1p(min(age, 4320.0)), float(links),
        ]
    except Exception:
        return [0.0] * len(FEATURES)


def features_from_stored(ml: dict):
    """Вектор из stored ml_features закрытой сделки. None — фичей нет ({})."""
    try:
        ml = ml or {}
        if not ml:
            return None
        b5 = _f(ml.get("buys_m5", 0))
        s5 = _f(ml.get("sells_m5", 0))
        bh = _f(ml.get("buys_h1", ml.get("buys_h24", 0)))
        sh = _f(ml.get("sells_h1", ml.get("sells_h24", 0)))
        vm5 = _f(ml.get("volume_m5", 0))
        vh = _f(ml.get("volume_h24", 0))
        liq = _f(ml.get("liquidity", 0))
        return [
            _f(ml.get("price_change_m5", 0)), _f(ml.get("price_change_m1", 0)),
            _f(ml.get("price_change_h1", 0)), _f(ml.get("price_change_h24", 0)),
            b5, s5, bh, sh,
            _f(ml.get("buy_sell_ratio", (b5 / (s5 + 1)) if (b5 + s5) > 0 else 0.0)),
            ((bh / (sh + 1)) if (bh + sh) > 0 else 0.0),
            _log1p(vm5), _log1p(vh),
            _f(ml.get("vol_to_liq", ((vm5 / (liq + 1)) if vm5 else (vh / (liq + 1))))),
            _log1p(liq), _log1p(_f(ml.get("fdv", 0))),
            _log1p(min(_f(ml.get("age_min", 999.0), 999.0), 4320.0)),
            _f(ml.get("links_count", 0)),
        ]
    except Exception:
        return None


def _auc(y, p) -> float:
    """AUC без sklearn (ранговая формула Манна-Уитни)."""
    try:
        order = sorted(range(len(p)), key=lambda i: p[i])
        ranks = [0.0] * len(p)
        for r, i in enumerate(order, 1):
            ranks[i] = r
        n1 = sum(1 for v in y if v == 1)
        n0 = len(y) - n1
        if not n1 or not n0:
            return 0.5
        s = sum(ranks[i] for i, v in enumerate(y) if v == 1)
        return (s - n1 * (n1 + 1) / 2) / (n1 * n0)
    except Exception:
        return 0.5


def train(X, y, save_path: str = MODEL_PATH) -> dict:
    """Обучает XGB на (X, y). Возвращает метрики. Кидает исключение при беде."""
    import xgboost as xgb
    n1 = sum(1 for v in y if v == 1)
    n0 = len(y) - n1
    # Простой сплит 80/20 по порядку (новые сделки — в тесте, как в проде)
    cut = max(1, int(len(y) * 0.8))
    Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]
    if len(Xte) < 5 or not any(yte) or all(yte):
        Xtr, ytr, Xte, yte = X, y, X, y  # мало данных — метрика на трейне
    clf = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0,
        scale_pos_weight=(n0 / max(1, n1)), eval_metric="logloss",
    )
    clf.fit(Xtr, ytr)
    try:
        proba = [float(v) for v in clf.predict_proba(Xte)[:, 1]]
    except Exception:
        proba = [0.5] * len(Xte)
    metrics = {
        "n": len(y), "rockets": n1, "duds": n0,
        "auc": round(_auc(yte, proba), 3),
        "importance": {f: round(float(v), 3) for f, v in
                       zip(FEATURES, clf.feature_importances_)},
    }
    clf.save_model(save_path)
    return metrics


def load(path: str = MODEL_PATH):
    """Загружает модель или None (fail-open: нет файла = нет модели)."""
    try:
        if not path or not os.path.exists(path):
            return None
        import xgboost as xgb
        clf = xgb.XGBClassifier()
        clf.load_model(path)
        return clf
    except Exception as e:
        print(f"🚀 ROCKET-модель не загружена ({e}) — работаем по гейтам.")
        return None


def score(clf, pair_data: dict):
    """Вероятность ракеты 0..1 или None при любой беде."""
    try:
        if clf is None:
            return None
        import numpy as _np
        v = features_from_pair(pair_data)
        return float(clf.predict_proba(_np.array([v]))[0][1])
    except Exception:
        return None
