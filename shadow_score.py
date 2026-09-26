"""P0.1 Shadow-scoring: P(pump>30% | features), обучено на 513 shadow-отказах (OOF AUC 0.613).

Использование: VETO-gate. score < 0.30 -> блок (в трейне отсекло 61 токен:
54 true-лузера ценой 7 пропущенных пампов).
Знаки: +объём/+импульс/+нетто-покупки; -vol_to_liq (накрутка), -buy/sell ratio (вершина),
-ликвидность. Чистый python, без pickle/sklearn в проде.
"""
import math

_MEAN = {
    'chg': 145.641, 'log_vol': 9.214, 'buys_sells': 207.712,
    'log_liq': 4.999, 'log_fdv': 10.137, 'bsr': 1.560, 'vtl': 17588.505,
}
_SCALE = {
    'chg': 368.789, 'log_vol': 3.372, 'buys_sells': 1993.101,
    'log_liq': 5.324, 'log_fdv': 2.852, 'bsr': 1.907, 'vtl': 29250.287,
}
_COEF = {
    'chg': +0.1868, 'log_vol': +0.4072, 'buys_sells': +0.1751,
    'log_liq': -0.1443, 'log_fdv': +0.0323, 'bsr': -0.2107, 'vtl': -0.5761,
}
_INTERCEPT = -0.0871
VETO_THRESHOLD = 0.30


def _clip(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return max(-8.0, min(8.0, x))


def pump_score(chg: float = 0.0, vol: float = 0.0, buys: float = 0.0,
               sells: float = 0.0, liq: float = 0.0, fdv: float = 0.0,
               bsr: float = 0.0, vtl: float = 0.0) -> float:
    """Вероятность пампа >30%. Вход - сырые метрики окна (m5 или h24)."""
    try:
        feats = {
            'chg': float(chg or 0.0),
            'log_vol': math.log1p(max(float(vol or 0.0), 0.0)),
            'buys_sells': float(buys or 0.0) - float(sells or 0.0),
            'log_liq': math.log1p(max(float(liq or 0.0), 0.0)),
            'log_fdv': math.log1p(max(float(fdv or 0.0), 0.0)),
            'bsr': float(bsr or 0.0),
            'vtl': float(vtl or 0.0),
        }
        z = _INTERCEPT
        for k, c in _COEF.items():
            s = _SCALE[k] or 1.0
            z += c * _clip((feats[k] - _MEAN[k]) / s)
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))
    except Exception:
        return 0.5


def pair_shadow_score(pair_data: dict) -> float:
    """Скор из пары DexScreener: m5-окно если живое, иначе h24."""
    try:
        txm5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
        b5, s5 = txm5.get("buys", 0) or 0, txm5.get("sells", 0) or 0
        vm5 = (pair_data.get("volume") or {}).get("m5", 0) or 0
        if (b5 + s5) > 0:
            pc = pair_data.get("priceChange") or {}
            return pump_score(chg=pc.get("m5", 0) or 0, vol=vm5, buys=b5, sells=s5,
                              liq=(pair_data.get("liquidity") or {}).get("usd", 0) or 0,
                              fdv=pair_data.get("fdv", 0) or 0,
                              bsr=(b5 / (s5 + 1)),
                              vtl=(vm5 / (((pair_data.get("liquidity") or {}).get("usd", 0) or 0) + 1)))
        txh = (pair_data.get("txns") or {}).get("h24", {}) or {}
        bh, sh = txh.get("buys", 0) or 0, txh.get("sells", 0) or 0
        vh = (pair_data.get("volume") or {}).get("h24", 0) or 0
        liq = (pair_data.get("liquidity") or {}).get("usd", 0) or 0
        pc = pair_data.get("priceChange") or {}
        return pump_score(chg=pc.get("h24", 0) or 0, vol=vh, buys=bh, sells=sh,
                          liq=liq, fdv=pair_data.get("fdv", 0) or 0,
                          bsr=(bh / (sh + 1)), vtl=(vh / (liq + 1)))
    except Exception:
        return 0.5
