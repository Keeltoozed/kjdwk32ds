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
            # Если создатель серийно проводит прошлые сессии за < 2.8 минуты (мгновенный 1-2 мин рагер) — БЛОК!
            if avg_dur < 2.8 or (len(durations_min) >= 3 and sum(1 for d in durations_min if d < 2.5) >= 2):
                prof["ok"] = False
                prof["kind"] = "instant_rugger"
                prof["avg_rug_min"] = round(avg_dur, 1)
                prof["reason"] = f"instant-rugger-history (avg {avg_dur:.1f}m, min {min_dur:.1f}m across {len(past_sessions)} sessions)"
            elif avg_dur >= 10.0:
                base_cap = float(getattr(config, "RUG_HUNTER_MAX_HOLD_MIN", 5.0))
                # Выходим максимум через 35% от его типичного времени жизни (но не дольше 8 мин!)
                dyn_cap = max(3.5, min(8.0, avg_dur * 0.35))
                prof["ok"] = True
                prof["kind"] = "delayed_rugger"
                prof["avg_rug_min"] = round(avg_dur, 1)
                prof["recommended_max_hold_min"] = round(max(base_cap, dyn_cap), 1)
                prof["reason"] = f"delayed-rugger-history (avg ~{avg_dur:.0f}m, safe window {prof['recommended_max_hold_min']:.1f}m)"
            else:
                prof["ok"] = True
                prof["kind"] = "short_session_dev"
                prof["avg_rug_min"] = round(avg_dur, 1)
                prof["recommended_max_hold_min"] = round(max(2.5, min(4.0, avg_dur * 0.45)), 1)
                prof["reason"] = f"short-session-dev (avg {avg_dur:.1f}m, cap {prof['recommended_max_hold_min']:.1f}m)"

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

    # История создателя: мгновенных рагеров (<2.8м) отсекаем навсегда
    if isinstance(creator_profile, dict) and not creator_profile.get("ok", True):
        return False, str(creator_profile.get("reason") or "instant-rugger")

    # Жёсткие пороги раннего живого входа:
    min_age = float(getattr(config, "RUG_HUNTER_UNLOCKED_MIN_AGE_MIN", 1.0))
    max_age = float(getattr(config, "RUG_HUNTER_UNLOCKED_MAX_AGE_MIN", 10.0))
    if age_min < min_age:
        return False, f"rh-too-young {age_min:.1f}m < {min_age:.1f}m"
    if age_min > max_age:
        return False, f"rh-too-late {age_min:.1f}m > {max_age:.1f}m"

    min_liq = float(getattr(config, "RUG_HUNTER_MIN_LIQ_USD", 8000.0))
    min_vol5 = float(getattr(config, "RUG_HUNTER_MIN_VOL5_USD", 6000.0))
    if liq_usd < min_liq:
        return False, f"rh-low-liq ${liq_usd:,.0f} < ${min_liq:,.0f}"
    if vol5_usd < min_vol5:
        return False, f"rh-low-vol5 ${vol5_usd:,.0f} < ${min_vol5:,.0f}"

    # Отсекаем синтетический 100%-посеянный пул без цены/объёма (liq >= 95% FDV)
    if fdv_usd > 0 and liq_usd >= (fdv_usd * 0.95):
        return False, f"rh-synthetic-liq-fdv liq=${liq_usd:,.0f} fdv=${fdv_usd:,.0f}"
    if fdv_usd > float(getattr(config, "RUG_HUNTER_MAX_FDV_USD", 250000.0)):
        return False, f"rh-fdv-too-high ${fdv_usd:,.0f}"

    # Доказанная двухсторонняя торговля (не honeypot и не микро-боты):
    if sells5 < 10:
        return False, f"rh-too-few-sells {sells5} < 10"
    if buys5 < max(35, int(sells5 * 1.15)):
        return False, f"rh-weak-buyers {buys5}b/{sells5}s"
    avg_tx = vol5_usd / max(1, buys5 + sells5)
    if avg_tx < 12.0:
        return False, f"rh-micro-wash avg ${avg_tx:.1f} < $12"
    if liq_usd > 0 and (vol5_usd / liq_usd) > 10.0:
        return False, f"rh-extreme-wash v/l {vol5_usd/liq_usd:.1f}x > 10x"

    # Импульс: уже разгоняется, но ещё не перегретая вершина
    if m5 < 6.0 or m5 > 160.0:
        return False, f"rh-m5-out-of-range {m5:+.1f}%"
    if m1 < -2.0 or m1 > 22.0:
        return False, f"rh-m1-out-of-range {m1:+.1f}%"
    if h1 > 260.0 and not (age_min <= 5.0 and 0 < fdv_usd <= 65000.0):
        return False, f"rh-h1-late {h1:+.0f}%"

    # Концентрация холдеров (если известна): отсекаем явных 50%+ монополистов
    if dev_pct > 25.0:
        return False, f"rh-dev-too-high {dev_pct:.1f}% > 25%"
    if top10_pct > 55.0:
        return False, f"rh-top10-too-high {top10_pct:.1f}% > 55%"

    return True, "rh-unlocked-lp-qualified"


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

    min_liq = float(getattr(config, "RUG_HUNTER_MIN_LIQ_USD", 8000.0))
    min_vol5 = float(getattr(config, "RUG_HUNTER_MIN_VOL5_USD", 6000.0))
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


def get_rug_hunter_tp_ladder() -> tuple:
    """Ускоренная лесенка фиксации прибыли для Rug-Hunter: TP1 +25% (60%), TP2 +55% (100%)."""
    return (
        float(getattr(config, "RUG_HUNTER_TP1_PCT", 0.25)),
        float(getattr(config, "RUG_HUNTER_TP1_SELL_FRAC", 0.60)),
        float(getattr(config, "RUG_HUNTER_TP2_PCT", 0.55)),
        float(getattr(config, "RUG_HUNTER_TP2_SELL_FRAC", 1.00)),
        5.00, 0.0,
        5.00, 0.0,
        10.00, 0.0,
    )


def evaluate_rug_hunter_exit(position, current_price: float) -> Optional[str]:
    """
    Специализированный сверхбыстрый менеджер выходов для позиций RUG-HUNTER.
    Цель: забрать +20%..+55% быстро и выскочить ДО того, как рагер снимет LP или кабал сольёт стек.
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
    pt = float(getattr(position, "peak_time", et) or et)
    minutes_held = max(0.0, (now_ts - et) / 60.0)
    peak_age_min = max(0.0, (now_ts - pt) / 60.0)

    pd = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
    rh_mode = str((pd.get("_rug_hunter_mode") if isinstance(pd, dict) else "") or "").upper()
    if not rh_mode:
        src_u = str(getattr(position, "source", "") or "").upper()
        rh_mode = "CABAL-SURF" if "CABAL" in src_u else "UNLOCKED-LP"

    # 1. Мгновенный срез при просадке ликвидности пула >= 15% (в 3 раза чувствительнее обычной стратегии!)
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
            liq_drain_thr = float(getattr(config, "RUG_HUNTER_LIQ_DRAIN_PCT", 0.15))
            if peak_l >= 3000.0 and cur_l <= peak_l * (1.0 - liq_drain_thr):
                st_n = int(getattr(position, "_rh_liq_streak", 0) or 0) + 1
                position._rh_liq_streak = st_n
                if st_n >= 2 or cur_l <= peak_l * 0.75:
                    return f"🎯 RH Liq-Drain Escape (-{(1.0 - cur_l/peak_l)*100:.0f}% LP, ${cur_l:.0f}/${peak_l:.0f}, PnL {pnl_pct*100:+.1f}%)"
            else:
                position._rh_liq_streak = 0

        # Мгновенный выход при развороте 1м свечи <= -5% в плюсе или <= -7% вообще:
        pc = pd.get("priceChange") or {}
        m1_val = float(pc.get("m1", 0) or 0)
        if minutes_held >= 0.35 and m1_val <= -5.0 and (pnl_pct >= 0.05 or m1_val <= -7.5):
            return f"🎯 RH 1m-Dump Escape (m1 {m1_val:+.1f}%, PnL {pnl_pct*100:+.1f}%)"

    # 2. Жёсткий Stop-Loss (-10%) и Emergency Cap (-14%)
    sl = -abs(float(getattr(config, "RUG_HUNTER_SL_PCT", -0.10)))
    emerg = -abs(float(getattr(config, "RUG_HUNTER_EMERG_PCT", -0.14)))
    if pnl_pct <= emerg:
        if minutes_held >= 0.25 or pnl_pct <= -0.25:
            return f"🎯 RH Emergency Stop ({pnl_pct*100:.1f}%)"
    if pnl_pct <= sl:
        sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
        position.sl_confirm_n = sl_c
        if sl_c >= 2 or (minutes_held >= 0.5 and pnl_pct <= sl * 1.15):
            return f"🎯 RH Fast Stop Loss ({pnl_pct*100:.1f}%)"
    else:
        position.sl_confirm_n = 0

    # 3. Полный выход на TP2 (+55%) — не сидим в раге ради 10x!
    tp2_thr = float(getattr(config, "RUG_HUNTER_TP2_PCT", 0.55))
    if pnl_pct >= tp2_thr:
        return f"🎯 RH Target Hit TP2 (+{pnl_pct*100:.1f}% >= +{tp2_thr*100:.0f}% — выход до рага!)"

    # 4. Жёсткий Trailing Stop (-8% от ATH после пика +18%) и Floor Lock
    trail_act = float(getattr(config, "RUG_HUNTER_TRAIL_ACT_PCT", 0.18))
    trail_drop = float(getattr(config, "RUG_HUNTER_TRAIL_DROP_PCT", 0.08))
    if max_pnl_pct >= trail_act and drop_from_peak >= trail_drop:
        return f"🎯 RH Profit Trail (+{pnl_pct*100:+.1f}%, откат {drop_from_peak*100:.1f}% от пика +{max_pnl_pct*100:.1f}%)"

    # 5. Мгновенный Breakeven Shield (пик >= +12% -> пол +2.5%, уходить в минус запрещено!)
    be_act = float(getattr(config, "RUG_HUNTER_BREAKEVEN_PCT", 0.12))
    be_floor = float(getattr(config, "RUG_HUNTER_BREAKEVEN_FLOOR", 0.025))
    if max_pnl_pct >= be_act and pnl_pct <= be_floor:
        return f"🎯 RH Breakeven Lock (+{pnl_pct*100:+.1f}% после пика +{max_pnl_pct*100:.1f}%)"

    # 6. Застой после профита или быстрый срез флета (в раге нельзя пересиживать!)
    if minutes_held >= 1.5 and max_pnl_pct >= 0.08 and peak_age_min >= 0.75 and pnl_pct >= 0.04:
        return f"🎯 RH Peak Stall Harvest (+{pnl_pct*100:.1f}%, {peak_age_min*60:.0f}с без нового ATH)"
    if minutes_held >= 2.0 and max_pnl_pct < 0.04 and pnl_pct < 0.01:
        return f"🎯 RH No-Pump Scratch ({pnl_pct*100:+.1f}%, 2м без взлёта — сброс до рага)"

    # 7. Динамический жёсткий тайм-кэп (по истории дева или базовые 5.0м)
    base_max_hold = float(getattr(config, "RUG_HUNTER_MAX_HOLD_MIN", 5.0))
    prof_cap = 0.0
    if isinstance(pd, dict):
        cp = pd.get("_rug_hunter_creator_profile")
        if isinstance(cp, dict):
            prof_cap = float(cp.get("recommended_max_hold_min", 0.0) or 0.0)
    eff_max_hold = prof_cap if prof_cap > 0 else base_max_hold
    # Если уже есть профит >= +6% и прошло 3.5м без TP2 — фиксируем до дампа!
    if minutes_held >= min(3.5, eff_max_hold * 0.75) and pnl_pct >= 0.06 and peak_age_min >= 0.5:
        return f"🎯 RH Early Harvest (+{pnl_pct*100:.1f}%, {minutes_held:.1f}м — фиксация до рага)"
    if minutes_held >= eff_max_hold:
        return f"🎯 RH Time-Cap Exit ({pnl_pct*100:+.1f}%, лимит {eff_max_hold:.1f}м для {rh_mode})"

    return None


async def check_live_rugger_activity(position, live_pd: Optional[dict] = None) -> Tuple[bool, str]:
    """
    Лайв-монитор рагера и многокошелькового слива (вызывается из position_security_auditor_loop каждые ~1.5с):
      1. Всплеск продаж (multi-wallet dump wave): delta_sells >= 8 и >= 2x delta_buys между тиками.
      2. Слив баланса дева / топ-3 холдеров ончейн (>= 15% от снимка входа, проверка каждые 4с через RPC getMultipleAccounts).
    """
    now = time.time()
    # 1. Детектор волны продаж кабала по приращению сделок m5
    if isinstance(live_pd, dict) and live_pd:
        tx5 = (live_pd.get("txns") or {}).get("m5") or {}
        b5 = int(tx5.get("buys", 0) or 0)
        s5 = int(tx5.get("sells", 0) or 0)
        prev_b5 = getattr(position, "_rh_prev_b5", None)
        prev_s5 = getattr(position, "_rh_prev_s5", None)
        position._rh_prev_b5 = b5
        position._rh_prev_s5 = s5
        if prev_b5 is not None and prev_s5 is not None:
            db = max(0, b5 - int(prev_b5))
            ds = max(0, s5 - int(prev_s5))
            entry_px = float(getattr(position, "entry_price_usd", 0.0) or 0.0)
            cur_px = float(getattr(position, "current_price_usd", 0.0) or 0.0)
            pnl_now = ((cur_px - entry_px) / entry_px) if (entry_px > 0 and cur_px > 0) else 0.0
            # Координированная волна продаж (>=8 продаж за 1-2с с перевесом продавцов >=2x)
            if ds >= 8 and ds >= max(4, int(db * 2.0)):
                return True, f"🎯 RH Cabal Sell-Wave (+{ds}s/+{db}b за тик, PnL {pnl_now*100:+.1f}%)"
            # Разворот потока в минус, когда мы уже в плюсе >= +5%
            if pnl_now >= 0.05 and s5 >= 15 and s5 > int(b5 * 1.15) and ds >= 4 and ds > db:
                return True, f"🎯 RH Flow Flip Exit ({b5}b/{s5}s, +{ds}s, PnL {pnl_now*100:+.1f}%)"

    # 2. Прямой ончейн-мониторинг балансов дева / топ-3 кошельков каждые 4с
    pd_pos = getattr(position, "pair_data", None) or {}
    watch = pd_pos.get("_rug_hunter_watch") if isinstance(pd_pos, dict) else None
    last_rpc = float(getattr(position, "_rh_last_wallet_rpc", 0.0) or 0.0)
    if isinstance(watch, dict) and watch and (now - last_rpc) >= 4.0:
        position._rh_last_wallet_rpc = now
        addrs = list(watch.keys())[:4]
        try:
            res = await asyncio.wait_for(
                _rpc_post("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}], timeout=2.0),
                timeout=2.2,
            )
            vals = (res.get("value") or []) if isinstance(res, dict) else []
            dumped_wallets = []
            for addr, acc in zip(addrs, vals):
                meta = watch.get(addr) or {}
                init_amt = float(meta.get("init_amt", 0.0) or 0.0)
                if init_amt <= 0:
                    continue
                cur_amt = 0.0
                if isinstance(acc, dict):
                    parsed = ((acc.get("data") or {}).get("parsed") or {}).get("info") or {}
                    cur_amt = float(((parsed.get("tokenAmount") or {}).get("uiAmount")) or 0.0)
                sold_frac = (init_amt - cur_amt) / init_amt
                if sold_frac >= 0.15:
                    dumped_wallets.append((meta.get("label", "top"), addr[:6], sold_frac * 100.0))
            if dumped_wallets:
                # Если слил дев — выходим сразу при 1 кошельке; если топ-холдеры — при 1 крупном >=35% или >=2 кошельках сразу (кабал-слив!)
                has_dev_dump = any(w[0] == "dev" for w in dumped_wallets)
                has_big_dump = any(w[2] >= 35.0 for w in dumped_wallets)
                if has_dev_dump or has_big_dump or len(dumped_wallets) >= 2:
                    desc = ", ".join(f"{w[0]}:{w[1]}(-{w[2]:.0f}%)" for w in dumped_wallets[:3])
                    return True, f"🎯 RH On-Chain Rugger Dump ({desc})"
        except Exception:
            pass

    return False, ""

