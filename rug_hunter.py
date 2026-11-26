"""
rug_hunter.py — стратегия «Охотник на раги» (Rug-Hunter) с жёсткой изоляцией риска.

Два чётко размеченных режима (чтобы в дашборде и Supabase можно было отдельно
считать PnL и Winrate стратегии):
  1. RUG-HUNTER:UNLOCKED-LP — ранний вход в незалоченные пулы (PumpSwap CreatePool,
     Meteora, кастомный Raydium) БЕЗ honeypot/freeze/mint-атак, с проверкой истории
     времени жизни прошлых пулов создателя (отсекает мгновенных 1-минутных рагеров,
     допускает отложенных рагеров вроде 30–60м и свежие высоколиквидные пулы).
  2. RUG-HUNTER:CABAL-SURF — ранний сёрф залоченных/сожжённых пулов с сибил-/кабал-сеткой
     кошельков (<= 8м от старта, сильный живой спрос) с ранним выходом до кабал-дампа.

Выход по Rug-Hunter:
  - TP1 +25% (фиксация 60%), TP2 +55% (полный выход 100%, без жадности на 10x).
  - Breakeven Shield от пика +12% (пол +2.5%), Trailing -8% от ATH (от пика +18%).
  - Жёсткий Stop-Loss -10%, Emergency -14%.
  - Тайм-кэп 5.0м (динамически сокращается до 35% от среднего времени жизни прошлых рагов дева).
  - Лайв-монитор рагера: мгновенный выход при падении LP >= 15%, волне продаж кабала
    (delta sells >= 8 и >= 2x buys), обвале 1м свечи <= -5% или сливе >= 15% баланса
    отслеживаемых кошельков дева / топ-3 холдеров ончейн.
"""

import asyncio
import time
from typing import Dict, Optional, Tuple

import config

# Кэш профилей создателей (creator -> (ts, profile_dict))
_CREATOR_PROFILE_CACHE: Dict[str, Tuple[float, dict]] = {}
_CREATOR_PROFILE_TTL = 900.0  # 15 минут

# Программы, по которым определяем старые создания/снятия LP у дева
_DEX_REMOVE_HINTS = (
    "withdraw", "removeliquidity", "remove_liquidity", "closeposition",
    "close_position", "decrease_liquidity", "burn",
)
_DEX_CREATE_HINTS = (
    "createpool", "create_pool", "initialize2", "initialize_pool",
    "addliquidity", "add_liquidity", "migrate",
)


def is_rug_hunter_source(source: str) -> bool:
    """True если сделка открыта стратегией Rug-Hunter."""
    return "RUG-HUNTER" in str(source or "").upper()


def is_unlocked_or_seeded_mode(mode: str) -> bool:
    """True если режим относится к незалоченным или посеянным slow-cook пулам."""
    return str(mode or "").upper().strip() in ("UNLOCKED-LP", "SEEDED-LP")


def mode_from_qualify_reason(why: str, existing_mode: str = "") -> str:
    """Возвращает 'SEEDED-LP' для посеянных пулов (не понижая его обратно до 'UNLOCKED-LP')."""
    if "seeded" in str(why or "").lower() or str(existing_mode or "").upper().strip() == "SEEDED-LP":
        return "SEEDED-LP"
    return "UNLOCKED-LP"


def is_rug_hunter_position(position) -> bool:
    """True если VirtualPosition относится к стратегии Rug-Hunter."""
    if position is None:
        return False
    if bool(getattr(position, "is_rug_hunter", False)):
        return True
    if is_rug_hunter_source(getattr(position, "source", "")):
        return True
    pd = getattr(position, "pair_data", None)
    if isinstance(pd, dict) and pd.get("_rug_hunter_mode"):
        return True
    return False


def format_rug_hunter_source(mode: str, ml_conf: float = 0.0, creator_profile: Optional[dict] = None) -> str:
    """Формирует явную, читаемую метку стратегии для дашборда и Supabase."""
    m_clean = str(mode or "UNLOCKED-LP").upper().strip()
    suffix = ""
    if isinstance(creator_profile, dict):
        avg_m = float(creator_profile.get("avg_rug_min", 0.0) or 0.0)
        if avg_m >= 10.0:
            suffix = f" [~{avg_m:.0f}m-dev]"
    if ml_conf and ml_conf > 0:
        return f"🎯 RUG-HUNTER:{m_clean} ({ml_conf:.0f}%){suffix}"
    return f"🎯 RUG-HUNTER:{m_clean}{suffix}"


def count_open_rug_hunter_positions(tracker) -> int:
    """Сколько позиций Rug-Hunter открыто прямо сейчас."""
    try:
        return sum(1 for p in tracker.get_open_positions().values() if is_rug_hunter_position(p))
    except Exception:
        return 0


def has_rug_hunter_slot(tracker) -> bool:
    """Проверяет свободный слот в бюджете Rug-Hunter (по умолчанию макс 2 из 4)."""
    max_rh = int(getattr(config, "RUG_HUNTER_MAX_SLOTS", 2) or 2)
    return count_open_rug_hunter_positions(tracker) < max_rh


async def _rpc_post(method: str, params: list, timeout: float = 2.5):
    """Быстрый JSON-RPC вызов без утечки AsyncClient (через общую aiohttp-сессию)."""
    import aiohttp
    from http_client import get_session

    urls = []
    h_url = str(getattr(config, "HELIUS_RPC_URL", "") or "").strip()
    if h_url and "api.mainnet-beta" not in h_url:
        urls.append(h_url)
    urls.extend(["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"])
    sess = await get_session()
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for u in urls:
        try:
            async with sess.post(u, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    js = await resp.json(content_type=None)
                    if isinstance(js, dict) and js.get("result") is not None:
                        return js["result"]
        except Exception:
            continue
    return None


async def profile_creator_rug_timing(creator: str, mint: str = "") -> dict:
    """
    Быстрый ончейн-профайлер привычек создателя (<= 2.5с, кэш 15м):
    Проверяет историю последних транзакций кошелька создателя, чтобы отличить:
      - instant_rugger: прошлые пулы жили < 3 мин -> ЗАПРЕЩАЕМ вход даже в Rug-Hunter!
      - delayed_rugger: прошлые пулы держались >= 10–60+ мин -> допускаем с тайм-кэпом 35% от его среднего времени!
      - fresh_deployer: нет истории прошлых пулов -> допускаем по базовому строгому тайм-кэпу (5 мин).
    """
    c = str(creator or "").strip()
    default_prof = {
        "ok": True,
        "kind": "fresh_deployer",
        "avg_rug_min": 0.0,
        "recommended_max_hold_min": float(getattr(config, "RUG_HUNTER_MAX_HOLD_MIN", 5.0)),
        "tx_count": 0,
        "reason": "fresh-or-unknown-creator",
    }
    if not c or len(c) < 32:
        return default_prof

    now = time.time()
    cached = _CREATOR_PROFILE_CACHE.get(c)
    if cached and (now - cached[0]) < _CREATOR_PROFILE_TTL:
        return dict(cached[1])

    try:
        sigs = await asyncio.wait_for(
            _rpc_post("getSignaturesForAddress", [c, {"limit": 30}], timeout=2.2),
            timeout=2.5,
        )
        if not isinstance(sigs, list) or not sigs:
            _CREATOR_PROFILE_CACHE[c] = (now, default_prof)
            return dict(default_prof)

        # Ищем интервалы активности кошелька создателя по blockTime:
        # 1) Если у кошелька куча транзакций кластерами по 1-2 минуты с разрывами — классический instant-rugger.
        # 2) Если есть чёткие длинные сессии 15-90 мин — отложенный рагер (delayed_rugger), можно безопасно серфить первые 4-7 мин.
        bts = sorted(
            int(s.get("blockTime") or 0)
            for s in sigs
            if isinstance(s, dict) and s.get("err") is None and int(s.get("blockTime") or 0) > 0
        )
        prof = dict(default_prof)
        prof["tx_count"] = len(bts)

        # Разбиваем историю транзакций создателя на отдельные «сессии запуска» (пауза > 10 мин = новый запуск)
        # Исключаем самую последнюю (текущую) сессию, которая идёт прямо сейчас (< 12 мин назад)
        sessions = []
        if len(bts) >= 4:
            cur_s = [bts[0]]
            for t_i in bts[1:]:
                if (t_i - cur_s[-1]) > 600:
                    sessions.append((cur_s[0], cur_s[-1], len(cur_s)))
                    cur_s = [t_i]
                else:
                    cur_s.append(t_i)
            sessions.append((cur_s[0], cur_s[-1], len(cur_s)))

        past_sessions = [s for s in sessions if (now - s[1]) > 300 and s[2] >= 2]
        if len(past_sessions) >= 2:
            durations_min = [max(0.2, (s[1] - s[0]) / 60.0) for s in past_sessions]
            avg_dur = sum(durations_min) / len(durations_min)
            min_dur = min(durations_min)
            # Серийный спаммер: запускает кучу токенов подряд (>= 3 запусков за последние 24ч) — БЛОК!
            recent_sessions_24h = [s for s in past_sessions if (now - s[1]) < 86400]
            if len(recent_sessions_24h) >= 3:
                prof["ok"] = False
                prof["kind"] = "serial_spammer"
                prof["avg_rug_min"] = round(avg_dur, 1)
                prof["reason"] = f"serial-deployer-spam ({len(recent_sessions_24h)} launches in 24h)"
            elif len(past_sessions) >= 3 and avg_dur < 0.8:
                # Автоматизированный мгновенный бот-скрипт (< 50 секунд на запуск) — БЛОК!
                prof["ok"] = False
                prof["kind"] = "instant_bot"
                prof["avg_rug_min"] = round(avg_dur, 1)
                prof["reason"] = f"instant-bot-history (avg {avg_dur:.1f}m across {len(past_sessions)} sessions)"
            else:
                base_cap = float(getattr(config, "RUG_HUNTER_MAX_HOLD_MIN", 5.0))
                prof["ok"] = True
                prof["kind"] = "delayed_rugger" if avg_dur >= 5.0 else "repeat_deployer"
                prof["avg_rug_min"] = round(avg_dur, 1)
                prof["recommended_max_hold_min"] = base_cap
                prof["reason"] = f"normal-deployer-history ({len(past_sessions)} past sessions, avg {avg_dur:.1f}m)"

        _CREATOR_PROFILE_CACHE[c] = (now, prof)
        if len(_CREATOR_PROFILE_CACHE) > 1500:
            _CREATOR_PROFILE_CACHE.clear()
        return dict(prof)
    except Exception:
        return default_prof


def qualify_unlocked_lp_hunter(
    mint: str,
    pair_data: Optional[dict],
    age_min: float,
    liq_usd: float,
    fdv_usd: float,
    vol5_usd: float,
    buys5: int,
    sells5: int,
    m5: float,
    m1: float = 0.0,
    h1: float = 0.0,
    dev_pct: float = 0.0,
    top10_pct: float = 0.0,
    creator_profile: Optional[dict] = None,
) -> Tuple[bool, str]:
    """
    Квалификация входа RUG-HUNTER:UNLOCKED-LP:
    Входим ТОЛЬКО на ранней фазе живого пампа незалоченного пула, когда:
      - пул уже доказал продаваемость (sells5 >= 10, нет honeypot);
      - не 0-секундный раг (age >= 1.0м) и не поздняя вершина (age <= 10.0м, m5 <= 150%);
      - создатель НЕ является 1-2 минутным мгновенным рагером;
      - есть глубокая ликвидность (>= $8k), живой объём (>= $6k) и реальный перевес покупателей.
    """
    if not bool(getattr(config, "RUG_HUNTER_ENABLED", False)) or not bool(getattr(config, "RUG_HUNTER_UNLOCKED_LP_ENABLED", False)):
        return False, "rug-hunter-unlocked-disabled"

    # Не трогаем фейковые .pump CreatePool клоны поверх реальной бондинг-кривой
    mint_low = str(mint or "").strip().lower()
    dex_low = str((pair_data or {}).get("dexId") or "").strip().lower()
    if mint_low.endswith("pump") and dex_low == "pumpswap" and (pair_data or {}).get("_pumpswap_canonical") is False:
        return False, "fake-pump-createpool-clone"

    # История создателя: серийных (<15м) и мгновенных рагеров отсекаем навсегда
    if isinstance(creator_profile, dict) and not creator_profile.get("ok", True):
        return False, str(creator_profile.get("reason") or "instant-rugger")

    # Жёсткие пороги раннего живого входа на самом старте:
    min_age = float(getattr(config, "RUG_HUNTER_UNLOCKED_MIN_AGE_MIN", 0.15))
    _base_max_age = float(getattr(config, "RUG_HUNTER_UNLOCKED_MAX_AGE_MIN", 5.0))
    max_age = max(_base_max_age, 5.5) if (vol5_usd >= 10000.0 and buys5 >= 50) else _base_max_age
    if age_min < min_age:
        return False, f"rh-too-young {age_min:.2f}m < {min_age:.2f}m"
    if age_min > max_age:
        return False, f"rh-too-late {age_min:.1f}m > {max_age:.1f}m"

    min_liq = float(getattr(config, "RUG_HUNTER_MIN_LIQ_USD", 4800.0))
    max_liq = float(getattr(config, "RUG_HUNTER_MAX_LIQ_USD", 42000.0))
    if liq_usd < min_liq:
        return False, f"rh-low-liq ${liq_usd:,.0f} < ${min_liq:,.0f}"
    if liq_usd > max_liq:
        return False, f"rh-whale-bait-liq ${liq_usd:,.0f} > ${max_liq:,.0f} (приманка крупным незалоченным пулом, кейсы TUVALRM/Lidrag)"
    # Fallback: если DexScreener не успел заполнить volume.m5 для свежего пула (<5м), берём volume.h24/h1:
    if vol5_usd <= 0 and age_min <= 5.0 and isinstance(pair_data, dict):
        vol5_usd = float((pair_data.get("volume") or {}).get("h24", 0) or (pair_data.get("volume") or {}).get("h1", 0) or 0.0)
    # Динамический порог объёма: не блокируем свежепроиндексированные ракеты (кейс TTWO +1242% при vol5=$450):
    base_min_v5 = float(getattr(config, "RUG_HUNTER_MIN_VOL5_USD", 350.0))
    if age_min <= 1.2:
        min_vol5 = base_min_v5
    else:
        min_vol5 = max(420.0, base_min_v5)
    if vol5_usd < min_vol5:
        return False, f"rh-low-vol5 ${vol5_usd:,.0f} < ${min_vol5:,.0f}"

    # Посеянные slow-cook пулы (liq/fdv 0.95..2.25 — кейсы AGI +978%, CRIMELESS +523%, EWJ +426%, ZCAT +426%, HUANG +676%, RUDY +578%):
    # разрешаем ТОЛЬКО если включён RUG_HUNTER_SEEDED_LP_ENABLED, но отсекаем сломанный FDV (liq > 2.25 * fdv)!
    is_seeded_ratio = bool(fdv_usd > 0 and liq_usd >= (fdv_usd * 0.95))
    if fdv_usd > 0 and liq_usd > (fdv_usd * 2.25):
        return False, f"rh-extreme-liq-fdv liq=${liq_usd:,.0f} > 2.25x fdv=${fdv_usd:,.0f}"
    if is_seeded_ratio and not bool(getattr(config, "RUG_HUNTER_SEEDED_LP_ENABLED", True)):
        return False, f"rh-synthetic-liq-fdv liq=${liq_usd:,.0f} fdv=${fdv_usd:,.0f}"
    # Специфический барьер для SEEDED-LP: отсекаем фейковые/малооборотные пулы-приманки (кейсы NEX Ai, PUMPE, Democat):
    _is_high_vel_seeded = False
    if is_seeded_ratio:
        # Жёсткий потолок ликвидности для незалоченного SEEDED-LP ($17.0k; при $16k+ обязателен живой объём >=$12k):
        # сохраняет b (+73.6%, $16.8k/$222k), Twitch ($16.3k/$28.7k), FIONA, HOLDERS, DUVAL, CHIBI и режет MOLE ($17.2k) + $18.5k+ ловушки!
        _max_seeded_liq = float(getattr(config, "RUG_HUNTER_MAX_SEEDED_LIQ_USD", 17000.0))
        if liq_usd > _max_seeded_liq:
            return False, f"rh-seeded-highliq-trap liq=${liq_usd:,.0f} > ${_max_seeded_liq:,.0f} (жёсткий потолок ликвидности для незалоченного SEEDED-LP)"
        if liq_usd >= 16000.0 and vol5_usd < 12000.0:
            return False, f"rh-seeded-16k-lowvol liq=${liq_usd:,.0f}, vol5=${vol5_usd:,.0f} < $12,000 (приманка без взрывного оборота)"
        if liq_usd >= 14500.0 and vol5_usd < 6000.0:
            return False, f"rh-seeded-midliq-lowvol liq=${liq_usd:,.0f}, vol5=${vol5_usd:,.0f} < $6,000 (приманка со слабым оборотом)"
        if m1 < -0.5:
            return False, f"rh-seeded-red-m1 {m1:+.1f}% < -0.5% (защита от покупки падающей свечи, кейс HACKINU)"
        if liq_usd > 0 and (vol5_usd / liq_usd) >= 1.10 and m5 < 15.0:
            return False, f"rh-seeded-wash-churn vol5/liq={vol5_usd/liq_usd:.2f}x при m5={m5:+.1f}% < +15% (накрутка объёма без роста цены, кейс DEADFRESH)"

        _seeded_max_entry_age = float(getattr(config, "RUG_HUNTER_SEEDED_MAX_ENTRY_AGE_MIN", 4.2))
        _is_seeded_hyper_breakout = bool(
            (vol5_usd >= 10000.0 and buys5 >= 50)
            or (buys5 >= 70 and (sells5 == 0 or buys5 >= int(sells5 * 1.2)))
            or (vol5_usd >= 30000.0 and (vol5_usd / max(liq_usd, 1.0)) >= 1.2)
        )
        _eff_max_entry_age = 5.5 if _is_seeded_hyper_breakout else _seeded_max_entry_age
        if age_min > _eff_max_entry_age:
            return False, f"rh-seeded-too-late {age_min:.1f}m > {_eff_max_entry_age:.1f}m"
        _min_seeded_vol_base = float(getattr(config, "RUG_HUNTER_SEEDED_MIN_VOL5", 4000.0))
        _min_seeded_turn = float(getattr(config, "RUG_HUNTER_SEEDED_MIN_TURNOVER", 0.15))
        _min_seeded_buys_cfg = int(getattr(config, "RUG_HUNTER_SEEDED_MIN_BUYS", 35))
        _min_seeded_m5 = float(getattr(config, "RUG_HUNTER_SEEDED_MIN_M5_PCT", 12.0))

        # Ультра-ранний старт (<0.85м, кейс PI $14.1k, vol5=$4.65k, age=0.56м, m5=+6.26% -> +119% ATH):
        _is_ultra_early_seed = bool(
            age_min < 0.85
            and vol5_usd >= _min_seeded_vol_base
            and buys5 >= 25
            and (sells5 == 0 or buys5 >= int(sells5 * 1.5))
            and m5 >= 6.0
        )
        # Для пулов >=0.85м НЕ занижаем порог объёма $4,000 и импульса m5 +12% (отсекает DEADFRESH, TIKTOK, Partying, RANSEM, trillions, CATE!):
        _is_retail_surge = bool(buys5 >= 35 and (sells5 == 0 or buys5 >= int(sells5 * 1.8)) and m5 >= _min_seeded_m5)
        if _is_ultra_early_seed:
            _min_seeded_vol = _min_seeded_vol_base
            _min_seeded_buys = 25
            _min_seeded_sells = 0
        elif _is_retail_surge:
            _min_seeded_vol = max(_min_seeded_vol_base, liq_usd * 0.12)
            _min_seeded_buys = 25
            _min_seeded_sells = 0
        else:
            _min_seeded_vol = max(_min_seeded_vol_base, liq_usd * _min_seeded_turn)
            _min_seeded_buys = _min_seeded_buys_cfg
            _min_seeded_sells = int(getattr(config, "RUG_HUNTER_SEEDED_MIN_SELLS", 1))

        if vol5_usd < _min_seeded_vol:
            return False, f"rh-seeded-low-vol5 ${vol5_usd:,.0f} < ${_min_seeded_vol:,.0f} (liq=${liq_usd:,.0f})"
        if buys5 < _min_seeded_buys:
            return False, f"rh-seeded-few-buys {buys5} < {_min_seeded_buys}"
        if sells5 < _min_seeded_sells:
            return False, f"rh-seeded-few-sells {sells5} < {_min_seeded_sells}"

        _is_high_turnover_flow = bool(
            (liq_usd > 0 and (vol5_usd / liq_usd) >= 0.70)
            or vol5_usd >= 10000.0
            or buys5 >= 60
        )
        if _is_high_turnover_flow:
            if m5 >= 20.0 and buys5 >= 50:
                # Скальперы фиксируют прибыль на ракете, но свеча растёт (кейс FIONA +2544%)
                if sells5 > 0 and buys5 < int(sells5 * 0.95):
                    return False, f"rh-seeded-weak-ratio {buys5}b/{sells5}s (<0.95x)"
            else:
                if sells5 > 0 and buys5 < sells5:
                    return False, f"rh-seeded-weak-ratio {buys5}b/{sells5}s (<1.0x)"
        elif not (_is_retail_surge or _is_ultra_early_seed):
            if sells5 > 0 and buys5 < int(sells5 * 1.15):
                return False, f"rh-seeded-weak-ratio {buys5}b/{sells5}s (<1.15x)"

        if m5 < _min_seeded_m5 and not _is_ultra_early_seed:
            return False, f"rh-seeded-weak-m5 {m5:+.1f}% < +{_min_seeded_m5:.0f}%"
            
        # Китовая ловушка дева ($20k+ liq): реалистичный чек в Solana до $350-$450
        avg_tx_seeded = vol5_usd / max(1, buys5 + sells5)
        _base_max_avg = float(getattr(config, "RUG_HUNTER_MAX_AVG_TX_USD", 350.0))
        _is_high_vel_seeded = bool(
            liq_usd <= 35000.0
            and vol5_usd >= (liq_usd * 0.50)
            and buys5 >= 80
            and (sells5 == 0 or buys5 >= int(sells5 * 1.2))
        )
        _max_seeded_avg = 450.0 if (_is_high_vel_seeded or buys5 >= 60) else _base_max_avg
        if liq_usd >= 20000.0:
            if (buys5 < 15 and sells5 < 2) or avg_tx_seeded > _max_seeded_avg or vol5_usd < 1000.0:
                return False, f"rh-seeded-whale-trap liq=${liq_usd:,.0f} buys={buys5} sells={sells5} avg=${avg_tx_seeded:.0f}"
            if age_min >= 1.1 and not (vol5_usd >= (liq_usd * 0.20) or (buys5 >= 35 and m5 >= 5.0)):
                return False, f"rh-seeded-highliq-slow-turn vol5=${vol5_usd:,.0f} (<0.20x liq=${liq_usd:,.0f}, m5={m5:+.1f}%)"
        if liq_usd >= 15000.0 and isinstance(pair_data, dict):
            pair_data["_rh_high_liq_seeded"] = True
    if fdv_usd > float(getattr(config, "RUG_HUNTER_MAX_FDV_USD", 500000.0)):
        return False, f"rh-fdv-too-high ${fdv_usd:,.0f}"

    # Органический UNLOCKED-LP раннер (НЕ посеянный: fdv >= 1.15 * liq) на высокооборотной консолидации (кейс YARL +107.3%):
    _is_organic_unl_runner = bool(
        (not is_seeded_ratio)
        and (fdv_usd >= liq_usd * 1.15 or fdv_usd == 0)
        and vol5_usd >= 15000.0
        and buys5 >= 150
        and m5 >= 15.0
    )

    # Адаптивные требования к покупкам/продажам:
    # На раннем старте (age <= 3.8м) ловим импульс с учётом задержки индексации DexScreener (2.5-3.5м):
    early_age_th = float(getattr(config, "RUG_HUNTER_EARLY_AGE_THRESHOLD", 3.8))
    if age_min <= early_age_th:
        min_sells_req = 0 if buys5 >= 8 else int(getattr(config, "RUG_HUNTER_EARLY_MIN_SELLS", 1))
        min_buys_req = max(6, int(getattr(config, "RUG_HUNTER_EARLY_MIN_BUYS", 6)))
    else:
        min_sells_req = 3
        min_buys_req = max(15, int(sells5 * 1.15))

    if sells5 < min_sells_req:
        return False, f"rh-too-few-sells {sells5} < {min_sells_req}"
    if buys5 < min_buys_req:
        return False, f"rh-weak-buyers {buys5}b/{sells5}s (<{min_buys_req}b)"
    if _is_organic_unl_runner:
        if sells5 > 0 and buys5 < int(sells5 * 0.90):
            return False, f"rh-sellers-dominate {buys5}b/{sells5}s"
    elif sells5 > 0 and buys5 < int(sells5 * 1.15) and (buys5 - sells5) < 3:
        return False, f"rh-sellers-dominate {buys5}b/{sells5}s"

    avg_tx = vol5_usd / max(1, buys5 + sells5)
    if avg_tx < 4.0:
        return False, f"rh-micro-wash avg ${avg_tx:.1f} < $4"
    max_avg_tx_rh = float(getattr(config, "RUG_HUNTER_MAX_AVG_TX_USD", 350.0))
    if _is_high_vel_seeded or buys5 >= 50:
        max_avg_tx_rh = max(max_avg_tx_rh, 450.0)
    elif _is_organic_unl_runner:
        max_avg_tx_rh = max(max_avg_tx_rh, 450.0)
    if avg_tx > max_avg_tx_rh:
        return False, f"rh-whale-wash-avg-tx avg ${avg_tx:.1f} > ${max_avg_tx_rh:.0f} (самовыкуп дева крупными чеками)"
    _max_vl_rh = 22.0 if avg_tx >= 35.0 else 14.0
    if liq_usd > 0 and (vol5_usd / liq_usd) > _max_vl_rh:
        return False, f"rh-extreme-wash v/l {vol5_usd/liq_usd:.1f}x > {_max_vl_rh:.0f}x"

    # Импульс: на самом старте m1 может быть до +75% (мощная зеленая свеча), m5 на первых 70с не успевает сформироваться
    max_m1_rh = 75.0 if age_min <= early_age_th else 35.0
    if m1 < -3.5 or m1 > max_m1_rh:
        return False, f"rh-m1-out-of-range {m1:+.1f}%"
    if age_min <= 3.8 and fdv_usd <= 35000.0 and vol5_usd >= 4500.0 and buys5 >= 35:
        max_m5_rh = 360.0  # Ранний пробой микрокапа с низкой базой (кейсы HOLDERS +1713% при m5 +264.7% buys=41, DUVAL +1067% при m5 +328%)
    elif age_min <= 4.0 and (fdv_usd <= 65000.0 or vol5_usd >= 5000.0):
        max_m5_rh = 260.0
    else:
        max_m5_rh = 175.0
    # На ультра-раннем старте (<=1.2м) 5-минутная свеча ещё не существует — достаточно m5 >= -2.0% и m1 >= 0%
    if age_min <= 1.2:
        if m5 < -2.0 or m5 > max_m5_rh:
            return False, f"rh-m5-out-of-range {m5:+.1f}%"
        if m1 < 0.0 and buys5 < 10:
            return False, f"rh-early-m1-negative {m1:+.1f}%"
    elif age_min <= early_age_th:
        if m5 > max_m5_rh:
            return False, f"rh-m5-overextended {m5:+.1f}% > +{max_m5_rh:.0f}%"
        if m5 < 2.0:
            if not (buys5 >= 20 and buys5 >= sells5 * 1.4 and vol5_usd >= 1200.0):
                return False, f"rh-m5-weak-ignition {m5:+.1f}% < +2% (buys={buys5})"
    else:
        # > 2.2м (только если MAX_AGE > 2.2): требуем реальный импульс m5 >= 4%
        # Снижено с 8% → 4%: ракеты в зоне 2.2-3.2м имеют m5 4-8% на старте
        if m5 < 4.0 or m5 > max_m5_rh:
            if not (buys5 >= 15 and buys5 >= sells5 * 1.2 and vol5_usd >= 1000.0):
                return False, f"rh-m5-out-of-range {m5:+.1f}% (buys={buys5})"
    if h1 > 280.0 and not (age_min <= 5.0 and fdv_usd <= 65000.0):
        return False, f"rh-h1-late {h1:+.0f}%"

    # DEAD-PUMP FILTER: срабатывает только после 4.2м при реальном падении m1 < -1% (не блокирует свежие DCTY +510% при REST m1==0!)
    _h1_val = pair_data.get("priceChange", {}).get("h1", 0) if isinstance(pair_data, dict) else 0
    _h1_val = float(_h1_val or 0)
    if age_min >= 4.2 and _h1_val > 0 and abs(_h1_val - m5) < 0.5 and m5 >= 18.0 and (m1 is not None and m1 < -1.0):
        if not (buys5 >= max(18, int(sells5 * 1.3)) and vol5_usd >= 2500.0):
            return False, f"rh-dead-pump-peak m5={m5:.1f}%==h1={_h1_val:.1f}% m1={m1:.1f}%"

    # Концентрация холдеров (если известна): отсекаем явных 50%+ монополистов
    if dev_pct > 25.0:
        return False, f"rh-dev-too-high {dev_pct:.1f}% > 25%"
    if top10_pct > 88.0:
        return False, f"rh-top10-too-high {top10_pct:.1f}% > 88%"

    return True, ("rh-seeded-lp-qualified" if is_seeded_ratio else "rh-unlocked-lp-qualified")


def qualify_cabal_surf_hunter(
    mint: str,
    pair_data: Optional[dict],
    age_min: float,
    liq_usd: float,
    fdv_usd: float,
    vol5_usd: float,
    buys5: int,
    sells5: int,
    m5: float,
    m1: float = 0.0,
    h1: float = 0.0,
    reason_tag: str = "",
) -> Tuple[bool, str]:
    """
    Квалификация входа RUG-HUNTER:CABAL-SURF:
    Залоченный/сожжённый LP, но обнаружена многокошельковая сеть / кабал-ферма / инсайдеры.
    Входим СТРОГО в первые 8 минут старта (до того, как кабал начинает сливать на 10–18м!)
    и выходим рано по TP1 +25% / TP2 +55% или тайм-кэпу / всплеску продаж.
    """
    if not bool(getattr(config, "RUG_HUNTER_ENABLED", False)) or not bool(getattr(config, "RUG_HUNTER_CABAL_SURF_ENABLED", False)):
        return False, "rug-hunter-cabal-disabled"

    max_age = float(getattr(config, "RUG_HUNTER_CABAL_MAX_AGE_MIN", 8.0))
    if age_min > max_age:
        return False, f"rh-cabal-too-late {age_min:.1f}m > {max_age:.1f}m"

    min_liq = float(getattr(config, "RUG_HUNTER_MIN_LIQ_USD", 4800.0))
    min_vol5 = float(getattr(config, "RUG_HUNTER_MIN_VOL5_USD", 2000.0))
    if liq_usd < min_liq:
        return False, f"rh-cabal-low-liq ${liq_usd:,.0f}"
    if vol5_usd < min_vol5:
        return False, f"rh-cabal-low-vol5 ${vol5_usd:,.0f}"
    if fdv_usd > float(getattr(config, "RUG_HUNTER_CABAL_MAX_FDV_USD", 180000.0)):
        return False, f"rh-cabal-fdv-high ${fdv_usd:,.0f}"

    if sells5 < 8 or buys5 < max(35, int(sells5 * 1.15)):
        return False, f"rh-cabal-weak-flow {buys5}b/{sells5}s"
    if m5 < 8.0 or m5 > 220.0:
        return False, f"rh-cabal-m5 {m5:+.1f}%"
    if m1 < -1.0 or m1 > 22.0:
        return False, f"rh-cabal-m1 {m1:+.1f}%"
    if h1 > 320.0 and not (age_min <= 5.0 and 0 < fdv_usd <= 65000.0):
        return False, f"rh-cabal-h1-late {h1:+.0f}%"

    return True, f"rh-cabal-surf-qualified ({reason_tag or 'multi-wallet'})"


def snapshot_rugger_wallets(rc_data: Optional[dict], pool_accounts: Optional[set] = None) -> dict:
    """
    Сохраняет адреса токен-аккаунтов создателя и топ-3 не-пуловых холдеров на момент входа,
    чтобы фоновый аудитор мог каждые 4с проверять ончейн, не начал ли рагер / кабал слив.
    """
    watch = {}
    if not isinstance(rc_data, dict):
        return watch
    pools = set(pool_accounts or ())
    creator = str(rc_data.get("creator") or "").strip()
    top_holders = rc_data.get("topHolders") or []
    picked = []
    for h in top_holders[:18]:
        if not isinstance(h, dict):
            continue
        addr = str(h.get("address") or "").strip()
        owner = str(h.get("owner") or "").strip()
        pct = float(h.get("pct") or 0.0)
        ui_amt = float(h.get("uiAmount") or 0.0)
        if not addr or addr in pools or owner in pools or pct <= 0.4 or pct > 65.0 or ui_amt <= 0:
            continue
        label = "dev" if (creator and (owner == creator or addr == creator)) else "top"
        picked.append((0 if label == "dev" else 1, -pct, addr, ui_amt, label))
    picked.sort()
    for _, _, addr, ui_amt, label in picked[:4]:
        watch[addr] = {"init_amt": ui_amt, "label": label}
    return watch


def get_rug_hunter_tp_ladder(is_unlocked: bool = True) -> tuple:
    """Мунбэг-лесенка для стратегии Rug Hunter:
    - Для UNLOCKED-LP: TP1 +35% (40%), TP2 +90% (30%), TP3 +250% (15%), TP4 +500% (10%), TP5 +1000% (100% остатка).
      На незалоченном пуле фиксируем тело депозита на TP1, гарантируем профит на TP2,
      а лунная сумка (30%) летит до реальных иксов (3.5x-11x) под защитой трейлинг-стопа!
    - Для SEEDED / CABAL / LOCKED: полная 5-уровневая лесенка до 11x.
    """
    if is_unlocked:
        return (
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP1_PCT", 0.35)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP1_SELL_FRAC", 0.40)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP2_PCT", 0.90)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP2_SELL_FRAC", 0.30)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP3_PCT", 2.50)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP3_SELL_FRAC", 0.15)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP4_PCT", 5.00)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP4_SELL_FRAC", 0.10)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP5_PCT", 10.00)),
            float(getattr(config, "RUG_HUNTER_UNLOCKED_TP5_SELL_FRAC", 1.00)),
        )
    return (
        float(getattr(config, "RUG_HUNTER_TP1_PCT", 0.35)),
        float(getattr(config, "RUG_HUNTER_TP1_SELL_FRAC", 0.35)),
        float(getattr(config, "RUG_HUNTER_TP2_PCT", 1.10)),
        float(getattr(config, "RUG_HUNTER_TP2_SELL_FRAC", 0.30)),
        float(getattr(config, "RUG_HUNTER_TP3_PCT", 2.50)),
        float(getattr(config, "RUG_HUNTER_TP3_SELL_FRAC", 0.15)),
        float(getattr(config, "RUG_HUNTER_TP4_PCT", 5.00)),
        float(getattr(config, "RUG_HUNTER_TP4_SELL_FRAC", 0.10)),
        float(getattr(config, "RUG_HUNTER_TP5_PCT", 10.00)),
        float(getattr(config, "RUG_HUNTER_TP5_SELL_FRAC", 1.00)),
    )


def evaluate_rug_hunter_exit(position, current_price: float) -> Optional[str]:
    """
    Специализированный менеджер выходов для сбора иксов:
    Позволяет ракете бежать до 2x-10x через мунбэг-трейлинг,
    но мгновенно выдёргивает позицию при реальном сливе LP (LP-Drain -15%) или дев-дампе.
    """
    entry_px = float(getattr(position, "entry_price_usd", 0.0) or 0.0)
    if entry_px <= 0 or not current_price or current_price <= 0:
        return None

    pnl_pct = (current_price - entry_px) / entry_px
    now_ts = time.time()

    if not getattr(position, "max_price_usd", 0.0) or position.max_price_usd <= 0:
        position.max_price_usd = current_price
        position.peak_time = now_ts
    elif current_price > position.max_price_usd:
        position.max_price_usd = current_price
        position.peak_time = now_ts

    max_pnl_pct = (position.max_price_usd - entry_px) / entry_px
    drop_from_peak = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0

    et = float(getattr(position, "entry_time", now_ts) or now_ts)
    minutes_held = max(0.0, (now_ts - et) / 60.0)

    pd = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
    rh_mode = str((pd.get("_rug_hunter_mode") if isinstance(pd, dict) else "") or "").upper()
    if not rh_mode:
        src_u = str(getattr(position, "source", "") or "").upper()
        rh_mode = "CABAL-SURF" if "CABAL" in src_u else ("SEEDED-LP" if "SEEDED" in src_u else "UNLOCKED-LP")

    is_unlocked_rh = is_unlocked_or_seeded_mode(rh_mode) or bool(getattr(position, "is_unlocked_pool", False))

    # 1. Мгновенный срез при просадке ликвидности пула (вывод LP рагером)
    cur_l = 0.0
    if isinstance(pd, dict) and pd:
        liq_obj = pd.get("liquidity") or {}
        if isinstance(liq_obj, dict) and ("usd" in liq_obj or "quote" in liq_obj):
            cur_l = float(liq_obj.get("usd", 0) or 0)
            peak_l = float(getattr(position, "peak_liquidity_usd", 0.0) or getattr(position, "initial_liquidity_usd", 0.0) or 0.0)
            if cur_l > peak_l:
                position.peak_liquidity_usd = cur_l
                peak_l = cur_l
            if peak_l >= 1000.0 and (cur_l <= 250.0 or cur_l < peak_l * 0.20):
                return f"Absolute Liquidity Loss [RH] (Liq ${cur_l:.0f} from ${peak_l:.0f})"
            # Реальный дрейн LP рагером — это падение на >=40% (не ловим ложные 10% колебания от нормальных продаж):
            _drain_thr = float(getattr(config, "RUG_HUNTER_LIQ_DRAIN_PCT", 0.40))
            if peak_l >= 2000.0 and cur_l <= peak_l * (1.0 - _drain_thr):
                return f"🎯 RH Early LP-Drain Alert (-{(1.0 - cur_l/peak_l)*100:.0f}% LP, ${cur_l:.0f}/${peak_l:.0f}, PnL {pnl_pct*100:+.1f}%)"

        # Мгновенный выход ТОЛЬКО при тяжёлом 1м дампе (не режем +6%..+12% на обычной -5% откатной свече!)
        pc = pd.get("priceChange") or {}
        m1_val = float(pc.get("m1", 0) or 0)
        _m1_cut = -12.0 if is_unlocked_rh else -14.0
        if minutes_held >= 0.75 and m1_val <= _m1_cut and (pnl_pct >= 0.35 or m1_val <= -15.0):
            return f"🎯 RH 1m-Dump Escape (m1 {m1_val:+.1f}%, PnL {pnl_pct*100:+.1f}%)"

    # 2. Жёсткий Stop-Loss (-10%) и Emergency Cap (-13%) с 2-тик защитой от 4-секундного кэш-лага DexScreener vs Jupiter
    # (фиксы TROLL 0.07м -21.6% -> +97% ATH, Taffy 0.07м -30.4% -> +42% ATH, CATE 0.00м -15.3%!):
    sl = -abs(float(getattr(config, "RUG_HUNTER_SL_PCT", -0.10)))
    emerg = -abs(float(getattr(config, "RUG_HUNTER_EMERG_PCT", -0.13)))
    if pnl_pct <= emerg or pnl_pct <= sl:
        if minutes_held < 0.45 and pnl_pct > -0.38:
            position.sl_confirm_n = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            if position.sl_confirm_n < 2:
                return None
        if pnl_pct <= emerg:
            return f"🎯 RH Emergency Stop ({pnl_pct*100:.1f}%)"
        return f"🎯 RH Fast Stop Loss ({pnl_pct*100:.1f}%)"
    else:
        position.sl_confirm_n = 0

    # 3. СПЕЦИФИЧЕСКИЕ ВЫХОДЫ ДЛЯ НЕЗАЛОЧЕННЫХ И ПОСЕЯННЫХ ПУЛОВ (UNLOCKED-LP / SEEDED-LP):
    is_unlocked_or_seeded = is_unlocked_rh or ("SEEDED" in rh_mode)
    _eff_liq_exit = cur_l if cur_l > 0 else float(getattr(position, "initial_liquidity_usd", 0.0) or getattr(position, "peak_liquidity_usd", 0.0) or 0.0)
    _fdv_exit = float((pd.get("fdv") or pd.get("marketCap") or 0) if isinstance(pd, dict) else 0)
    is_seeded_mode = bool(
        ("SEEDED" in rh_mode)
        or ("SEEDED" in str(getattr(position, "source", "") or "").upper())
        or (_fdv_exit > 0 and _eff_liq_exit >= (_fdv_exit * 0.95))
    )

    if is_unlocked_or_seeded:
        # А. Полный сбор профита на супер-ракете (11x / +1000%):
        if pnl_pct >= 10.0:
            return f"🎯 RH Full Harvest (+{pnl_pct*100:.1f}%, цель 11x достигнута — 100% выход в кэш!)"

        # Б. Ранний срез мёртвой слабости: даём ранним откатам первые 2.5м до TP (кейс WORTHLESS -7.9% на 1.5м -> +200.7%),
        # но если к 2.5м токен даже не касался +3% и упал <= -7.5% — режем до SL -10%!
        if minutes_held >= 2.5 and max_pnl_pct < 0.03 and pnl_pct <= -0.075:
            return f"🎯 RH Early Bleed Escape ({pnl_pct*100:+.1f}%, 2.5м слабости в незалоченном пуле)"

        # В. Сброс нуля/флета: если за 2.5м нет даже +1% профита (кейсы ER-100, Wojak):
        if minutes_held >= 2.5 and pnl_pct <= 0.005 and max_pnl_pct < 0.08:
            return f"🎯 RH Flat Scratch ({pnl_pct*100:+.1f}%, 2.5м без импульса — сброс до рага)"

        _pd_c_ms = (pd.get("pairCreatedAt") or 0) if isinstance(pd, dict) else 0
        pool_age_min = ((now_ts * 1000.0 - _pd_c_ms) / 60000.0) if _pd_c_ms > 0 else (minutes_held + 0.3)

        # Анализ текущего потока сделок:
        _tx5_exit = ((pd.get("txns") or {}).get("m5") or {}) if isinstance(pd, dict) else {}
        _b5_exit = int(_tx5_exit.get("buys", 0) or 0)
        _s5_exit = int(_tx5_exit.get("sells", 0) or 0)
        _v5_exit = float(((pd.get("volume") or {}).get("m5", 0) or 0) if isinstance(pd, dict) else 0.0)
        _buyers_alive = bool(_b5_exit == 0 or _b5_exit >= int(_s5_exit * 0.90))

        # Признак пробившегося раннера — ТОЛЬКО когда УЖЕ взят TP1 (+20%) или пик >= +20%!
        # Не даём позициям +10%..+19% (кейс Partying +15.3%) зависать со 100% объёма на 12-16 минут до RemoveLiquidity!
        _is_breakout_runner = bool(
            getattr(position, "tp1_done", False)
            or getattr(position, "is_moonbag", False)
            or max_pnl_pct >= 0.20
        )

        peak_ts = float(getattr(position, "peak_time", now_ts) or now_ts)
        peak_age_sec = max(0.0, now_ts - peak_ts)
        _peak_stall_lim = float(getattr(config, "RUG_HUNTER_PEAK_STALL_SEC", 85.0))
        _is_mega_active_wave = bool(
            _v5_exit >= 20000.0
            and _b5_exit >= 90
            and (_s5_exit == 0 or _b5_exit >= int(_s5_exit * 1.25))
        )

        # Г-Плюс. МГНОВЕННАЯ ФИКСАЦИЯ ЗАСТОЯ НА ПИКЕ ДО RemoveLiquidity (фикс MOLE -4.34%, Lore/JaoJiu/sol -40% на 2.1м и защита Partying +15%):
        # Девы на SEEDED/UNLOCKED пулах НЕ роняют цену на -24% перед рагом — цена встаёт «полкой» в плюсе, и дев одним блоком вынимает LP!
        # 1) Если TP1 ещё НЕ взят, пик был >= +11% (не режем накопление +6.5% вроде 穗穗与羊 +110% ATH), мы в плюсе >= +6% и >= 60с нет ATH — забираем профит!
        if (not getattr(position, "tp1_done", False)) and minutes_held >= 1.15 and max_pnl_pct >= 0.11 and pnl_pct >= 0.06 and peak_age_sec >= 60.0 and not _is_mega_active_wave:
            return f"🎯 RH Peak-Stall Profit Take ({pnl_pct*100:+.1f}% после пика +{max_pnl_pct*100:.1f}%, {peak_age_sec:.0f}с без нового ATH — фиксация плюса до рага!)"
        # 2) Если взят только TP1 (без TP2 +48%) — ждём не более 55с без ATH (фикс Lore/JaoJiu/sol, которые скамились на 2.1м до 85с!); после TP2 — 85с:
        _eff_top_stall = _peak_stall_lim if bool(getattr(position, "tp2_done", False)) else min(55.0, _peak_stall_lim)
        if _is_breakout_runner and minutes_held >= 1.25 and pnl_pct >= 0.04 and peak_age_sec >= _eff_top_stall and not _is_mega_active_wave:
            return f"🎯 RH Top-Stall Harvest ({pnl_pct*100:+.1f}% после пика +{max_pnl_pct*100:.1f}%, {peak_age_sec:.0f}с полка без ATH — выход в плюс до RemoveLiquidity!)"

        # Г0. Специализированные выходы для SEEDED-LP БЕЗ взятого TP1 (< +20%):
        if is_seeded_mode and not _is_breakout_runner:
            _stag_age = float(getattr(config, "RUG_HUNTER_SEEDED_STAGNANT_AGE_MIN", 2.80))
            _stag_hold = float(getattr(config, "RUG_HUNTER_SEEDED_STAGNANT_HOLD_MIN", 1.35))
            _has_strong_dip_bid = bool(_v5_exit >= 6000.0 and _b5_exit >= 50 and pnl_pct > -0.09)
            _is_still_buying = bool(
                (pnl_pct > -0.03 or _has_strong_dip_bid)
                and _v5_exit >= 4000.0
                and _b5_exit >= 30
                and (_s5_exit == 0 or _b5_exit >= int(_s5_exit * 1.25))
            )
            if minutes_held >= _stag_hold and (pool_age_min >= _stag_age or minutes_held >= 1.55) and max_pnl_pct < 0.08 and pnl_pct < 0.03:
                if not _is_still_buying:
                    return f"🎯 RH Seeded Stagnant Evac ({pnl_pct*100:+.1f}%, hold {minutes_held:.1f}м / age {pool_age_min:.1f}м — ранний выход из мёртвого флета до RemoveLiquidity!)"

            _seeded_max_pool_age = float(getattr(config, "RUG_HUNTER_SEEDED_MAX_POOL_AGE_MIN", 4.80))
            _seeded_max_hold = float(getattr(config, "RUG_HUNTER_SEEDED_MAX_HOLD_MIN", 3.20))
            if minutes_held >= 1.5 and (pool_age_min >= _seeded_max_pool_age or minutes_held >= _seeded_max_hold):
                # Безусловный выход по таймеру, если за 3.2м удержания / 4.8м пула так и не пробили +20% TP1 (фиксируем даже +15%!)
                return f"🎯 RH Seeded Pre-Rug Evac ({pnl_pct*100:+.1f}%, hold {minutes_held:.1f}м / age {pool_age_min:.1f}м — выход до RemoveLiquidity!)"

        # Г. Потолок общего возраста пула:
        # 1) Слабые (<+3%): 4.2м.
        # 2) Обычные SEEDED/UNLOCKED с TP1/TP2, но БЕЗ активной мега-волны ($20k+ и свежий ATH <85с): жёстко 8.0м (исключает 16м раги вроде MOLE)!
        # 3) Настоящие мега-ракеты с живым объёмом >= $20k и растущим ATH (FIONA 32.5м, HOLDERS 32м, DUVAL 31.1м): едут до 38.0м!
        _base_max_age = float(getattr(config, "RUG_HUNTER_UNLOCKED_MAX_POOL_AGE_MIN", 4.2))
        _rocket_max_age = float(getattr(config, "RUG_HUNTER_ROCKET_MAX_POOL_AGE_MIN", 8.0))
        _moonbag_max_age = float(getattr(config, "RUG_HUNTER_MOONBAG_MAX_POOL_AGE_MIN", 38.0))
        _is_tp2_secured = bool(getattr(position, "tp2_done", False) or max_pnl_pct >= 0.85)
        _allow_long_moonbag = bool(_is_tp2_secured and _v5_exit >= 20000.0 and peak_age_sec < _peak_stall_lim)
        _is_rocket_trending = bool(
            _is_breakout_runner
            or (pnl_pct >= 0.03 and _buyers_alive)
        )
        _effective_max_pool_age = _moonbag_max_age if _allow_long_moonbag else (_rocket_max_age if _is_rocket_trending else _base_max_age)
        if minutes_held >= 1.5 and pool_age_min >= _effective_max_pool_age:
            _evac_msg = f"фиксация прибыли ({pnl_pct*100:+.1f}%) до позднего слива" if _is_rocket_trending else "эвакуация застоя до рага дева!"
            return f"🎯 RH Pre-Rug Evac ({pnl_pct*100:+.1f}%, возраст пула {pool_age_min:.1f}м >= {_effective_max_pool_age:.1f}м — {_evac_msg})"

        # Д. Застой после пампа с откатом (Stall Evac):
        _unl_stall_s = float(getattr(config, "RUG_HUNTER_UNLOCKED_STALL_SEC", 150.0))
        if max_pnl_pct >= 0.30 and peak_age_sec >= 75.0 and drop_from_peak >= 0.16:
            return f"🎯 RH Stall Evac ({pnl_pct*100:+.1f}%, застой {peak_age_sec:.0f}с после пика +{max_pnl_pct*100:.1f}%)"

        # Е. Ступенчатый трейлинг-стоп для незалоченных пулов (без «слепой зоны» +25%..+80%!):
        # - До +48% при tp1_done=True не душим мунбэг (даём спокойно откатиться к +3%..+10% перед 2-й волной).
        # - От +48%..+84% (зона MB +77%, Trenchors +79%, SOLGORITHM +60%): трейл 24% от пика фиксирует остаток в жирный плюс до слива!
        # - От +85%..+199%: трейл 28%; от +200%+: трейл 30% для сёрфа 3x-25x ракет!
        _tp1_already = bool(getattr(position, "tp1_done", False) or getattr(position, "is_moonbag", False))
        if max_pnl_pct >= 2.00:
            _active_trail_drop = 0.30
        elif max_pnl_pct >= 0.85:
            _active_trail_drop = 0.28
        elif max_pnl_pct >= 0.48:
            _active_trail_drop = 0.24
        elif (not _tp1_already) and max_pnl_pct >= 0.32:
            _active_trail_drop = float(getattr(config, "RUG_HUNTER_UNLOCKED_TRAIL_DROP_PCT", 0.22))
        else:
            _active_trail_drop = 99.0
        if _active_trail_drop < 1.0 and drop_from_peak >= _active_trail_drop:
            return f"🎯 RH {'Moonbag' if _tp1_already else 'Tight'} Trail (+{pnl_pct*100:+.1f}%, откат {drop_from_peak*100:.1f}% от пика +{max_pnl_pct*100:.1f}%)"

        # Ж. Абсолютный лимит удержания безопасности (сбрасываем ТОЛЬКО застрявшие позиции PnL < +3% после 3.8м):
        _unl_safety_max = float(getattr(config, "RUG_HUNTER_UNLOCKED_SAFETY_MIN", 3.8))
        if minutes_held >= _unl_safety_max and pnl_pct < 0.03 and not getattr(position, "tp1_done", False):
            return f"🎯 RH Safety Time-Cap ({pnl_pct*100:+.1f}%, {_unl_safety_max:.1f}м без роста — выход до LP-рага)"

    else:
        # ======= CABAL / LOCKED: залоченные/сожжённые пулы — свободный полёт до 15 минут =======
        trail_act = float(getattr(config, "RUG_HUNTER_TRAIL_ACT_PCT", 0.32))
        trail_drop = float(getattr(config, "RUG_HUNTER_TRAIL_DROP_PCT", 0.22))
        if max_pnl_pct >= trail_act and drop_from_peak >= trail_drop:
            return f"🎯 RH Moonbag Trail (+{pnl_pct*100:+.1f}%, откат {drop_from_peak*100:.1f}% от пика +{max_pnl_pct*100:.1f}%)"

        # Тайм-кэп для залоченных пулов (до 15 минут):
        base_max_hold = 15.0 if pnl_pct >= 0.15 else float(getattr(config, "RUG_HUNTER_MAX_HOLD_MIN", 3.8))
        if minutes_held >= base_max_hold:
            return f"🎯 RH Time-Cap Exit ({pnl_pct*100:+.1f}%, лимит {base_max_hold:.1f}м для {rh_mode})"

    # 5. Pro Breakeven & Time-Activated Profit Shield:
    # - Пик >= +28% (TP1 зона) → пол +10% (железная фиксация прибыли)
    # - Пик >= +22% → пол +4% (базовая защита без преждевременного выбивания на 1-й минуте, фикс WHITE +78.8%)
    # - После 3.8м возраста пула (зрелая зона риска рага): если пик был >= +15% → пол +4%, если пик >= +6% → пол +1.5%
    # 5. Ступенчатый Profit Ratchet (фиксирует завоёванные уровни +50%/+85%/+200% до слива, но не душит ранний мунбэг после TP1):
    if max_pnl_pct >= 2.00:
        be_floor = 0.90
    elif max_pnl_pct >= 0.85:
        be_floor = 0.38
    elif max_pnl_pct >= 0.50:
        be_floor = 0.18
    elif bool(getattr(position, "tp1_done", False)):
        # На первых 2 минутах после TP1 допускаем ретест точки входа до -2.0% (фикс Solmas +9% на 0.9м -> +134% ATH!), после 2м зажимаем +1.5%:
        be_floor = -0.02 if minutes_held < 2.0 else 0.015
    elif max_pnl_pct >= 0.35:
        be_floor = 0.08
    elif max_pnl_pct >= 0.24:
        be_floor = 0.02
    elif is_unlocked_or_seeded and minutes_held >= 3.0 and max_pnl_pct >= 0.16:
        be_floor = 0.015
    else:
        be_floor = -99.0
    if be_floor > -99.0 and pnl_pct <= be_floor:
        return f"🎯 RH Breakeven Lock ({pnl_pct*100:+.1f}% после пика +{max_pnl_pct*100:.1f}%)"

    # 6. Сброс зависшего флета без пампа (только если вообще нет движения за 2.5-3 минуты)
    _scratch_m = 2.0 if is_unlocked_rh else 3.0
    if minutes_held >= _scratch_m and max_pnl_pct < 0.04 and pnl_pct < 0.01:
        return f"🎯 RH No-Pump Scratch ({pnl_pct*100:+.1f}%, {_scratch_m:.0f}м без взлёта — сброс до рага)"

    return None


async def check_live_rugger_activity(position, live_pd: Optional[dict] = None) -> Tuple[bool, str]:
    """
    Лайв-монитор рагера и координированного слива (вызывается из position_security_auditor_loop каждые ~1.5с):
      1. Всплеск продаж (multi-wallet dump wave): delta_sells >= 12 и >= 2.5x delta_buys между тиками.
      2. Слив баланса создателя (дева) ончейн: проверяем ТОЛЬКО дева (не обычных трейдеров!),
         требуем подтверждения на 2 тиках и выдерживаем 30-секундный грейс-период после входа.
    """
    now = time.time()
    et = float(getattr(position, "entry_time", now) or now)
    minutes_held = max(0.0, (now - et) / 60.0)

    # 1. Детектор волны продаж кабала по приращению сделок m5 + подтверждению падения цены
    if isinstance(live_pd, dict) and live_pd:
        tx5 = (live_pd.get("txns") or {}).get("m5") or {}
        b5 = int(tx5.get("buys", 0) or 0)
        s5 = int(tx5.get("sells", 0) or 0)
        prev_b5 = getattr(position, "_rh_prev_b5", None)
        prev_s5 = getattr(position, "_rh_prev_s5", None)
        position._rh_prev_b5 = b5
        position._rh_prev_s5 = s5
        entry_px = float(getattr(position, "entry_price_usd", 0.0) or 0.0)
        cur_px = float(getattr(position, "current_price_usd", 0.0) or 0.0)
        eff_px = float(live_pd.get("priceUsd") or 0.0) or cur_px
        prev_px = getattr(position, "_rh_prev_px", None)
        if eff_px > 0:
            position._rh_prev_px = eff_px
        if prev_b5 is not None and prev_s5 is not None:
            db = max(0, b5 - int(prev_b5))
            ds = max(0, s5 - int(prev_s5))
            pnl_now = ((eff_px - entry_px) / entry_px) if (entry_px > 0 and eff_px > 0) else 0.0
            max_px = float(getattr(position, "max_price_usd", 0.0) or 0.0)
            max_pnl = ((max_px - entry_px) / entry_px) if (entry_px > 0 and max_px > 0) else pnl_now
            minutes_held = max(0.0, (now - et) / 60.0)
            _drop_prev = bool(prev_px and eff_px > 0 and eff_px <= float(prev_px) * 0.96)
            _drop_peak = bool(eff_px > 0 and max_pnl > pnl_now and pnl_now <= (max_pnl - 0.06))
            _price_dropping = bool(_drop_prev or _drop_peak)
            _flash_crash_sell = bool(ds >= 25 and prev_px and eff_px > 0 and eff_px <= float(prev_px) * 0.90)

            # Координированная волна продаж: защита от ложных скачков 5м-кэша DS (кейс MOOB 34с +17s/+0b).
            # Требуем >=45с в сделке, подтверждённую просадку цены и 2 тика подряд (или >=25s + обвал >= -10% за тик).
            if minutes_held >= 0.75 and ds >= 12 and ds >= max(6, int(db * 2.5)) and _price_dropping:
                _streak = int(getattr(position, "_rh_cabal_streak", 0) or 0) + 1
            else:
                _streak = 0
            position._rh_cabal_streak = _streak

            if (_streak >= 2) or (_flash_crash_sell and ds >= max(6, int(db * 2.5))):
                return True, f"🎯 RH Cabal Sell-Wave (+{ds}s/+{db}b за тик, PnL {pnl_now*100:+.1f}%)"
            # Разворот потока в минус при откате цены — только на ощутимом профите >= +20% и явном перевесе продавцов >= 1.45x (не режем +8%..+15%!)
            if minutes_held >= 1.2 and pnl_now >= 0.20 and _price_dropping and s5 >= 25 and s5 > int(b5 * 1.45) and ds >= 8 and ds > db:
                return True, f"🎯 RH Flow Flip Exit ({b5}b/{s5}s, +{ds}s, PnL {pnl_now*100:+.1f}%)"

    # 2. Прямой ончейн-мониторинг баланса ДЕВА (создателя):
    # ВАЖНО:
    # 1) Даём сделке минимум 30 секунд (minutes_held >= 0.5) на разбег — никогда не выходим на 0-й секунде!
    # 2) Проверяем ТОЛЬКО дева (label == 'dev'), а не случайных трейдеров/топ-холдеров!
    # 3) Требуем реальный парсинг SPL token account и сброс >= 50% баланса дева с подтверждением на 2 тиках!
    if minutes_held >= 0.5:
        pd_pos = getattr(position, "pair_data", None) or {}
        watch = pd_pos.get("_rug_hunter_watch") if isinstance(pd_pos, dict) else None
        last_rpc = float(getattr(position, "_rh_last_wallet_rpc", 0.0) or 0.0)
        if isinstance(watch, dict) and watch and (now - last_rpc) >= 4.0:
            position._rh_last_wallet_rpc = now
            dev_addrs = [a for a, m in watch.items() if m.get("label") == "dev"][:2]
            if dev_addrs:
                try:
                    res = await asyncio.wait_for(
                        _rpc_post("getMultipleAccounts", [dev_addrs, {"encoding": "jsonParsed"}], timeout=2.0),
                        timeout=2.2,
                    )
                    vals = (res.get("value") or []) if isinstance(res, dict) else []
                    for addr, acc in zip(dev_addrs, vals):
                        meta = watch.get(addr) or {}
                        init_amt = float(meta.get("init_amt", 0.0) or 0.0)
                        if init_amt <= 0:
                            continue
                        if not isinstance(acc, dict) or not acc:
                            continue
                        data_obj = acc.get("data")
                        if not isinstance(data_obj, dict):
                            continue
                        parsed = (data_obj.get("parsed") or {}).get("info")
                        if not isinstance(parsed, dict) or "tokenAmount" not in parsed:
                            continue
                        cur_amt = float(((parsed.get("tokenAmount") or {}).get("uiAmount")) or 0.0)
                        sold_frac = (init_amt - cur_amt) / init_amt
                        if sold_frac >= 0.50:
                            st_n = int(getattr(position, "_rh_dev_dump_n", 0) or 0) + 1
                            position._rh_dev_dump_n = st_n
                            if st_n >= 2:
                                return True, f"🎯 RH Dev On-Chain Dump ({addr[:6]} sold {sold_frac*100:.0f}% of dev stack)"
                        else:
                            position._rh_dev_dump_n = 0
                except Exception:
                    pass

    return False, ""

