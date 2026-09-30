"""Карта покрытия радаров: какие парсеры видят сделки, а какие спят.

Проблема: бот несёт десяток источников, но часть молча выключена
(нет бесплатного ключа, выкл флагом, забанена Cloudflare) — и никто не видит,
что именно сейчас в эфире. Модуль печатает таблицу при старте и отдаёт её
для тестов/дашборда.

Статусы: ON (работает), IDLE (включён, но ждёт — напр. FOMO-демо),
OFF (выключен флагом/без ключа), DEAD (недоступен из дата-центра).
"""
import config as _c


def _has(name: str) -> bool:
    try:
        return bool((getattr(_c, name, "") or "").strip())
    except Exception:
        return False


def _cfg(name, default):
    try:
        return getattr(_c, name, default)
    except Exception:
        return default


def coverage_report() -> list:
    """[(источник, статус, деталь), ...]. Без сети, только конфиг."""
    rep = []
    rep.append(("PumpPortal WSS (Solana новые токены+трейды)",
                "ON", "push, без квот"))
    rep.append(("EVM factory WSS (Base/BSC новые пулы)",
                "ON" if getattr(_c, "EVM_WSS_ENABLED", True) else "OFF",
                "push секунду-в-секунду" if getattr(_c, "EVM_WSS_ENABLED", True) else "EVM_WSS_ENABLED=False"))
    rep.append(("DexScreener boosts/profiles+search",
                "ON", "pull через семафор DS(3)"))
    rep.append(("GeckoTerminal new/trending/top pools",
                "ON", "pull через семафор GT(1), кэши 60-180с"))
    rep.append(("pump.fun frontend API",
                "DEAD", "Cloudflare банит дата-центры (проверено; работает только локально)"))
    if _has("JUP_API_KEY"):
        rep.append(("Jupiter recent+trending (Solana)", "ON", "ключ задан"))
    else:
        rep.append(("Jupiter recent+trending (Solana)", "OFF",
                    "нет JUP_API_KEY — бесплатно: portal.jup.ag"))
    if _has("BIRDEYE_API_KEY"):
        rep.append(("Birdeye trending (4 сети)", "ON", "ключ задан"))
    else:
        rep.append(("Birdeye trending (4 сети)", "OFF",
                    "нет BIRDEYE_API_KEY — бесплатно: bds.birdeye.so"))
    if getattr(_c, "TG_PREVIEW_ENABLED", True):
        rep.append(("TG-превью коллов (без ключей)", "ON", "t.me/s опрос"))
    else:
        rep.append(("TG-превью коллов (без ключей)", "OFF", "TG_PREVIEW_ENABLED=False"))
    if _has("FOMO_API_KEY"):
        rep.append(("fomoapi.io WS+доски", "ON", "ключ задан"))
    elif getattr(_c, "FOMO_WS_ENABLED", True):
        rep.append(("fomoapi.io WS+доски", "IDLE", "демо без ключа (задержка 60с)"))
    else:
        rep.append(("fomoapi.io WS+доски", "OFF", "FOMO_WS_ENABLED=False"))
    rep.append(("CopyTrader (киты)",
                "ON" if getattr(_c, "USE_COPYTRADE", False) else "OFF",
                "слушает" if getattr(_c, "USE_COPYTRADE", False) else "USE_COPYTRADE=False"))
    rep.append(("FOMO-сигналы (входы)",
                "ON" if getattr(_c, "USE_FOMO_SIGNALS", False) else "OFF",
                "покупают" if getattr(_c, "USE_FOMO_SIGNALS", False) else "USE_FOMO_SIGNALS=False (покупки выкл)"))
    rep.append(("GMGN-мост", "ON" if getattr(_c, "GMGN_ENABLED", False) else "OFF",
                "тяжёлый (2GB+)" if not getattr(_c, "GMGN_ENABLED", False) else "включён"))
    rep.append(("CoinGecko trending → Solana-минты", "ON" if _cfg("COINGECKO_ENABLED", True) else "OFF",
                "без ключей, кэш 30 мин" if _cfg("COINGECKO_ENABLED", True) else "COINGECKO_ENABLED=False"))
    rep.append(("GoPlus + honeypot.is (гейты, не парсеры)", "ON", "EVM+Solana финалисты"))
    return rep


def print_coverage() -> list:
    rep = coverage_report()
    on = sum(1 for _, s, _ in rep if s in ("ON", "IDLE"))
    print(f"📡 Радар: {on}/{len(rep)} источников в эфире:")
    for name, st, det in rep:
        mark = {"ON": "🟢", "IDLE": "🟡", "OFF": "⚪", "DEAD": "🔴"}.get(st, "❔")
        print(f"   {mark} {name}: {st} — {det}")
    return rep
