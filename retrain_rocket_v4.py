#!/usr/bin/env python3
"""
Обучение калиброванной модели Rocket XGBoost v4.1 (Solana Runner Detector).
Исправлена инверсия таргета (ранние ракеты +100%..+980% больше не метятся как дампы из-за отката через 24ч).
Устранено искажение по соцсетям (socials=0 на 1-2 минутах — это норма для стелс-запусков).
Добавлены реальные профили пулов Raydium AMM (fdv_to_liq 0.50..0.85).
"""

import os
import glob
import re
import json
import math
import random
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.metrics import (
    roc_auc_score,
    precision_score,
    recall_score,
    f1_score,
    accuracy_score,
    confusion_matrix,
)

FEATURE_COLS = [
    "m5_pct", "m1_pct", "h1_pct", "m1_to_m5",
    "buys_m5", "sells_m5", "buy_ratio_m5", "net_buyers_m5",
    "volume_m5", "vol_to_liq", "avg_tx_usd",
    "liquidity", "fdv", "fdv_to_liq",
    "age_min", "is_pump",
    "top1_pct", "top10_pct", "dev_pct", "socials"
]

DOWNLOADS_DIR = "/Users/taya/Downloads"

def to_f(v, d=0.0):
    if v is None:
        return d
    try:
        s = str(v).strip()
        if not s or s in ("None", "null", "nan"):
            return d
        val = float(s)
        return d if math.isnan(val) or math.isinf(val) else val
    except Exception:
        return d

def compute_effective_liq(raw_liq, fdv, is_pump=0):
    """Рассчитывает эффективную ликвидность с учётом резервов bonding curve."""
    if is_pump:
        curve_liq = fdv * 0.35 if fdv > 0 else 0.0
        return max(raw_liq, curve_liq)
    return raw_liq

def collect_training_data():
    dataset = []

    # =========================================================================
    # 1. РЕАЛЬНЫЕ СДЕЛКИ ИЗ SQL-ДАМПОВ
    # =========================================================================
    sql_files = sorted(glob.glob(os.path.join(DOWNLOADS_DIR, "trades_*.sql")))
    print(f"📦 Сбор сделок из {len(sql_files)} SQL-дампов...")
    sql_count = 0

    for sf in sql_files:
        fname = os.path.basename(sf)
        with open(sf, encoding="utf-8", errors="ignore") as f:
            text = f.read()

        tuples = re.findall(r"\'(\{.*?\})\',\s*([^\,]+),\s*([^\,]+),\s*\'([^\']*)\'", text)
        for m in tuples:
            feat_str, conf_str, pnl_str, exit_reason = m
            try:
                feats = json.loads(feat_str)
                if not isinstance(feats, dict) or "price_change_m5" not in feats:
                    continue
                pnl = to_f(pnl_str, 0.0)
                m5 = to_f(feats.get("price_change_m5", 0.0))
                m1 = to_f(feats.get("price_change_m1", 0.0))
                if m1 == 0.0 and abs(m5) > 0.5:
                    m1 = m5 * 0.15
                h1 = to_f(feats.get("price_change_h1", 0.0))
                b5 = to_f(feats.get("buys_m5", 0.0))
                s5 = to_f(feats.get("sells_m5", 0.0))
                vm5 = to_f(feats.get("volume_m5", 0.0))
                raw_liq = to_f(feats.get("liquidity", 0.0))
                fdv = to_f(feats.get("fdv", 0.0)) or (raw_liq * 3.5)
                age = to_f(feats.get("age_min", 10.0))
                t1 = to_f(feats.get("top1_pct", 3.5))
                t10 = to_f(feats.get("top10_pct", 20.0))
                dev = to_f(feats.get("dev_pct", 0.0))
                soc = 1 if (feats.get("socials") or to_f(feats.get("links_count", 0)) > 0) else 0
                dex = str(feats.get("dex", "")).lower()
                is_pmp = 1 if ("pump" in dex) else 0

                liq = compute_effective_liq(raw_liq, fdv, is_pmp)
                if liq <= 0 and vm5 <= 0 and m5 == 0.0:
                    continue

                if pnl >= 10.0 or ("Trailing" in exit_reason and pnl > 4.0) or ("Scalp Profit" in exit_reason and pnl > 4.0):
                    target = 1
                elif pnl <= -3.0 or "Stop" in exit_reason or "Crash Guard" in exit_reason or "Dead" in exit_reason:
                    target = 0
                else:
                    target = 1 if pnl > 0 else 0

                m1_to_m5 = (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0
                buy_ratio_m5 = (b5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.5
                net_buyers_m5 = b5 - s5
                vol_to_liq = (vm5 / max(liq, 1.0)) if liq > 0 else 0.0
                avg_tx_usd = (vm5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.0
                fdv_to_liq = (fdv / max(liq, 1.0)) if liq > 0 else 3.5

                dataset.append({
                    "m5_pct": m5, "m1_pct": m1, "h1_pct": h1, "m1_to_m5": m1_to_m5,
                    "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": buy_ratio_m5, "net_buyers_m5": net_buyers_m5,
                    "volume_m5": vm5, "vol_to_liq": vol_to_liq, "avg_tx_usd": avg_tx_usd,
                    "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv_to_liq,
                    "age_min": age, "is_pump": is_pmp,
                    "top1_pct": t1, "top10_pct": t10, "dev_pct": dev, "socials": soc,
                    "target": target, "pnl": pnl, "source": f"sql_{fname}", "weight": 2.0
                })
                sql_count += 1
            except Exception:
                pass

    print(f"  ✅ Загружено {sql_count} сделок из SQL.")

    # =========================================================================
    # 2. РЕАЛЬНЫЕ СДЕЛКИ ИЗ CSV ФАЙЛОВ
    # =========================================================================
    csv_trade_files = glob.glob(os.path.join(DOWNLOADS_DIR, "trades_*.csv")) + glob.glob("trades_*.csv")
    csv_trade_files += glob.glob("/Users/taya/.gemini/antigravity/brain/*/.user_uploaded/*.csv")
    csv_trade_files = list(set(csv_trade_files))
    print(f"📦 Сбор сделок из {len(csv_trade_files)} CSV файлов...")
    csv_count = 0

    for cf in csv_trade_files:
        if "rejected" in cf or "shadow" in cf or "export" in cf:
            continue
        try:
            df = pd.read_csv(cf)
            if "features" not in df.columns:
                continue
            for _, r in df.iterrows():
                f_str = r.get("features")
                if not f_str or pd.isna(f_str):
                    continue
                try:
                    feats = json.loads(f_str) if isinstance(f_str, str) else f_str
                    if not isinstance(feats, dict) or "price_change_m5" not in feats:
                        continue
                    pnl = to_f(r.get("pnl_percent") or r.get("pnl"), 0.0)
                    m5 = to_f(feats.get("price_change_m5", 0.0))
                    m1 = to_f(feats.get("price_change_m1", 0.0))
                    if m1 == 0.0 and abs(m5) > 0.5:
                        m1 = m5 * 0.15
                    h1 = to_f(feats.get("price_change_h1", 0.0))
                    b5 = to_f(feats.get("buys_m5", 0.0))
                    s5 = to_f(feats.get("sells_m5", 0.0))
                    vm5 = to_f(feats.get("volume_m5", 0.0))
                    raw_liq = to_f(feats.get("liquidity", 0.0))
                    fdv = to_f(feats.get("fdv", 0.0)) or (raw_liq * 3.5)
                    age = to_f(feats.get("age_min", 10.0))
                    t1 = to_f(feats.get("top1_pct", 3.5))
                    t10 = to_f(feats.get("top10_pct", 20.0))
                    dev = to_f(feats.get("dev_pct", 0.0))
                    soc = 1 if (feats.get("socials") or to_f(feats.get("links_count", 0)) > 0) else 0
                    is_pmp = 1 if ("pump" in str(feats.get("dex", "")).lower()) else 0

                    liq = compute_effective_liq(raw_liq, fdv, is_pmp)
                    if liq <= 0 and vm5 <= 0 and m5 == 0.0:
                        continue

                    target = 1 if pnl >= 10.0 else (0 if pnl <= -3.0 else (1 if pnl > 0 else 0))
                    m1_to_m5 = (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0
                    buy_ratio_m5 = (b5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.5
                    net_buyers_m5 = b5 - s5
                    vol_to_liq = (vm5 / max(liq, 1.0)) if liq > 0 else 0.0
                    avg_tx_usd = (vm5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.0
                    fdv_to_liq = (fdv / max(liq, 1.0)) if liq > 0 else 3.5

                    dataset.append({
                        "m5_pct": m5, "m1_pct": m1, "h1_pct": h1, "m1_to_m5": m1_to_m5,
                        "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": buy_ratio_m5, "net_buyers_m5": net_buyers_m5,
                        "volume_m5": vm5, "vol_to_liq": vol_to_liq, "avg_tx_usd": avg_tx_usd,
                        "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv_to_liq,
                        "age_min": age, "is_pump": is_pmp,
                        "top1_pct": t1, "top10_pct": t10, "dev_pct": dev, "socials": soc,
                        "target": target, "pnl": pnl, "source": f"csv_{os.path.basename(cf)}", "weight": 2.0
                    })
                    csv_count += 1
                except Exception:
                    pass
        except Exception:
            pass

    print(f"  ✅ Загружено {csv_count} сделок из CSV.")

    # =========================================================================
    # 3. РЕАЛЬНЫЕ ТОКЕНЫ ИЗ REJECTED_TOKENS (С ВЕРИФИЦИРОВАННЫМИ ИСХОДАМИ ATH)
    # =========================================================================
    rej_files = glob.glob(os.path.join(DOWNLOADS_DIR, "rejected_tokens_rows*.csv"))
    rej_files += glob.glob("/Users/taya/.gemini/antigravity/brain/*/.user_uploaded/*rejected*.csv")
    rej_files += glob.glob("rejected_tokens*.csv")
    rej_files = list(set(rej_files))
    print(f"📦 Сбор верифицированных исходов из {len(rej_files)} rejected_tokens файлов...")

    unique_real_tokens = {}
    for rf in sorted(rej_files):
        try:
            df_rej = pd.read_csv(rf)
            for _, r in df_rej.iterrows():
                m = str(r.get("mint", "")).strip()
                if not m or len(m) < 20:
                    continue
                f_str = r.get("features")
                if not f_str or pd.isna(f_str):
                    continue
                try:
                    f_json = json.loads(f_str) if isinstance(f_str, str) else f_str
                    if not isinstance(f_json, dict) or "m5_pct" not in f_json:
                        continue
                    ath = to_f(r.get("hypothetical_pnl"), 0.0)
                    curr = to_f(r.get("current_pnl"), 0.0)
                    sym = str(r.get("symbol", "")).strip()
                    outcome = str(r.get("outcome", ""))
                    reason = str(r.get("reason", ""))

                    # Запоминаем или обновляем более свежим слепком
                    unique_real_tokens[m] = {
                        "mint": m, "symbol": sym, "ath": ath, "curr": curr,
                        "outcome": outcome, "reason": reason, "features": f_json
                    }
                except Exception:
                    pass
        except Exception:
            pass

    rej_pos, rej_neg = 0, 0
    for m, info in unique_real_tokens.items():
        f = info["features"]
        ath = info["ath"]
        curr = info["curr"]
        outcome = info["outcome"]
        sym = info["symbol"]

        # ПРАВИЛЬНЫЙ ТАРГЕТ ДЛЯ МОМЕНТУМ-СКАЛЬПЕРА:
        # Если монета дала импульс ATH >= +35% (включая ракеты, которые через 1-2ч откатили) -> УСПЕШНАЯ СДЕЛКА (target=1)!
        # Скальпер фиксирует прибыль по TP (+25%, +50%) и тралит стоп, он не сидит в позиции 24 часа.
        if ath >= 35.0 or "Ракета летит" in outcome or ("Ракета +" in outcome and ath >= 35.0):
            target = 1
            weight = 5.0 if ath >= 100.0 else 3.5  # Высокий приоритет реальным ракетам (REGRETAMIN, PIRATE, etc.)
            rej_pos += 1
        elif ath < 15.0 or (curr <= -60.0 and ath < 25.0) or "Мгновенный раг" in outcome or "Флет" in outcome:
            target = 0
            weight = 2.5 if curr <= -80.0 else 1.5  # Важный вес для реальных дампов и флета
            rej_neg += 1
        else:
            continue  # Серая зона 15-34%

        row = {col: float(f.get(col, 0.0)) for col in FEATURE_COLS}
        # Согласование с analyzer.py: если m1 == 0, а m5 активно, вычисляем m1 proxy
        if row["m1_pct"] == 0.0 and abs(row["m5_pct"]) > 0.5:
            row["m1_pct"] = max(-25.0, min(25.0, row["m5_pct"] * 0.15))
            row["m1_to_m5"] = row["m1_pct"] / max(abs(row["m5_pct"]), 1.0)
        row["target"] = target
        row["pnl"] = ath if target == 1 else curr
        row["source"] = f"real_rejected_{sym}"
        row["weight"] = weight
        dataset.append(row)

    print(f"  ✅ Загружено {rej_pos} РЕАЛЬНЫХ ракет и {rej_neg} РЕАЛЬНЫХ дампов из rejected_tokens!")

    # =========================================================================
    # 4. КАЛИБРОВАННЫЕ РЫНОЧНЫЕ АРХЕТИПЫ SOLANA MEMECOINS
    # =========================================================================
    rng = random.Random(42)
    n_synth_pos = 1000
    n_synth_neg = 1400
    print(f"🔬 Генерация калиброванных архетипов ({n_synth_pos} ракет, {n_synth_neg} скамов/дампов)...")

    # 4.1 Ракеты (Stealth Raydium, Pump Curve Takeoffs, Classic Breakouts, Organic Pullbacks)
    for _ in range(n_synth_pos):
        mode = rng.choice(["stealth_raydium", "pump_takeoff", "breakout", "pullback"])
        if mode == "stealth_raydium":
            # Свежий стелс-запуск на Raydium / Meteora (профиль REGRETAMIN, PIRATE, CPAD, VAMPLON)
            m5 = rng.uniform(15.0, 80.0)
            m1 = m5 * rng.uniform(0.10, 0.25)
            h1 = rng.uniform(20.0, 150.0)
            b_ratio = rng.uniform(0.55, 0.85)
            s5 = rng.randint(15, 80)
            b5 = int(s5 * b_ratio / (1.0 - b_ratio))
            liq = rng.uniform(12000.0, 35000.0)
            fdv_ratio = rng.uniform(0.50, 0.85)  # Стандартное соотношение CPMM пула Raydium
            fdv = liq * fdv_ratio
            vm5 = liq * rng.uniform(0.15, 2.5)
            age = rng.uniform(0.8, 3.5)
            is_p = 0
            soc = 1 if rng.random() < 0.08 else 0  # 92% свежих стелсов НЕ имеют соцсетей на 1-й минуте!
            top10 = rng.uniform(10.0, 25.0) if rng.random() < 0.6 else 0.0
            top1 = rng.uniform(2.0, 5.0) if top10 > 0 else 0.0
        elif mode == "pump_takeoff":
            # Ранний взлёт на кривой pump.fun / pumpswap (SPERM INU, SUGAR, DRNK профиль)
            m5 = rng.uniform(35.0, 250.0)
            m1 = m5 * rng.uniform(0.10, 0.25)
            h1 = rng.uniform(40.0, 300.0)
            b_ratio = rng.uniform(0.65, 0.90)
            s5 = rng.randint(10, 50)
            b5 = int(s5 * b_ratio / (1.0 - b_ratio))
            fdv = rng.uniform(20000.0, 85000.0)
            liq = fdv * 0.35  # Резервы bonding curve
            vm5 = liq * rng.uniform(0.3, 2.0)
            age = rng.uniform(1.0, 10.0)
            is_p = 1
            soc = 1 if rng.random() < 0.25 else 0  # 75% без соцсетей на ранней кривой
            top10 = rng.uniform(10.0, 25.0)
            top1 = rng.uniform(2.0, 5.0)
        elif mode == "breakout":
            # Классический зрелый прорыв Raydium
            m5 = rng.uniform(12.0, 65.0)
            m1 = m5 * rng.uniform(0.10, 0.20)
            h1 = rng.uniform(25.0, 180.0)
            b_ratio = rng.uniform(0.60, 0.85)
            s5 = rng.randint(20, 90)
            b5 = int(s5 * b_ratio / (1.0 - b_ratio))
            liq = rng.uniform(25000.0, 200000.0)
            fdv_ratio = rng.uniform(1.8, 5.0)
            fdv = liq * fdv_ratio
            vm5 = liq * rng.uniform(0.2, 1.2)
            age = rng.uniform(4.0, 60.0)
            is_p = 1 if rng.random() < 0.5 else 0
            soc = 1 if rng.random() < 0.70 else 0
            top10 = rng.uniform(10.0, 25.0)
            top1 = rng.uniform(2.0, 5.0)
        else:  # pullback
            # Здоровый откат после импульса
            m5 = rng.uniform(-15.0, -1.5)
            m1 = m5 * rng.uniform(0.10, 0.20)
            h1 = rng.uniform(35.0, 150.0)
            b_ratio = rng.uniform(0.58, 0.82)
            s5 = rng.randint(20, 80)
            b5 = int(s5 * b_ratio / (1.0 - b_ratio))
            liq = rng.uniform(20000.0, 180000.0)
            fdv_ratio = rng.uniform(1.5, 4.5)
            fdv = liq * fdv_ratio
            vm5 = liq * rng.uniform(0.2, 0.9)
            age = rng.uniform(5.0, 120.0)
            is_p = 1 if rng.random() < 0.5 else 0
            soc = 1 if rng.random() < 0.70 else 0
            top10 = rng.uniform(10.0, 25.0)
            top1 = rng.uniform(2.0, 5.0)

        avg_tx = vm5 / max(b5 + s5, 1)
        dataset.append({
            "m5_pct": m5, "m1_pct": m1, "h1_pct": h1,
            "m1_to_m5": (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0,
            "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": b_ratio,
            "net_buyers_m5": b5 - s5,
            "volume_m5": vm5, "vol_to_liq": vm5 / max(liq, 1), "avg_tx_usd": avg_tx,
            "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv / max(liq, 1),
            "age_min": age, "is_pump": is_p,
            "top1_pct": top1, "top10_pct": top10, "dev_pct": rng.uniform(0.0, 3.0),
            "socials": soc,
            "target": 1, "pnl": rng.uniform(40.0, 650.0), "source": "archetype_rocket", "weight": 2.0
        })

    # 4.2 Дампы и скамы (Dead, Overheated Top Dump, Fake Churn, Flash Rug, Thin Liq)
    for _ in range(n_synth_neg):
        mode = rng.choice(["dead", "wash_churn", "overheated_dump", "flash_rug", "thin_liq_trap"])
        if mode == "dead":
            # Мертвая монета без интереса (SCRVAN, CBUCKS профиль)
            m5 = rng.uniform(-2.0, 2.0)
            m1 = 0.0
            h1 = rng.uniform(-5.0, 5.0)
            b5 = rng.randint(0, 4)
            s5 = rng.randint(0, 4)
            b_ratio = 0.5
            liq = rng.uniform(10000.0, 40000.0)
            fdv = liq * rng.uniform(0.5, 3.5)
            vm5 = rng.uniform(0.0, 400.0)
            age = rng.uniform(1.0, 120.0)
            is_p = 1 if rng.random() < 0.5 else 0
            soc = 1 if rng.random() < 0.1 else 0
            top10 = rng.uniform(15.0, 45.0)
            top1 = rng.uniform(3.0, 15.0)
        elif mode == "wash_churn":
            # Накрутка объема ботами при стоячей цене
            m5 = rng.uniform(-1.0, 2.5)
            m1 = 0.0
            h1 = rng.uniform(0.0, 8.0)
            b5 = rng.randint(60, 200)
            s5 = rng.randint(60, 200)
            b_ratio = rng.uniform(0.48, 0.52)
            liq = rng.uniform(10000.0, 35000.0)
            fdv = liq * rng.uniform(0.5, 3.5)
            vm5 = rng.uniform(15000.0, 80000.0)
            age = rng.uniform(1.0, 60.0)
            is_p = 1 if rng.random() < 0.5 else 0
            soc = 1 if rng.random() < 0.1 else 0
            top10 = rng.uniform(15.0, 45.0)
            top1 = rng.uniform(3.0, 15.0)
        elif mode == "overheated_dump":
            # Перегретый дамп (сброс дева на пике)
            m5 = rng.uniform(50.0, 300.0)
            m1 = rng.uniform(-25.0, -8.0)  # Резкий сброс на 1m!
            h1 = rng.uniform(100.0, 600.0)
            b_ratio = rng.uniform(0.20, 0.45)  # Продавцы давят
            b5 = rng.randint(15, 50)
            s5 = int(b5 * (1.0 - b_ratio) / max(b_ratio, 0.01))
            liq = rng.uniform(12000.0, 60000.0)
            fdv = liq * rng.uniform(1.5, 6.0)
            vm5 = liq * rng.uniform(0.5, 3.0)
            age = rng.uniform(2.0, 25.0)
            is_p = 1 if rng.random() < 0.5 else 0
            soc = 1 if rng.random() < 0.3 else 0
            top10 = rng.uniform(25.0, 60.0)
            top1 = rng.uniform(5.0, 25.0)
        elif mode == "flash_rug":
            # Дев выдернул ликвидность или сбросил пул
            m5 = rng.uniform(-65.0, -15.0)
            m1 = rng.uniform(-20.0, -5.0)
            h1 = rng.uniform(-80.0, -25.0)
            b5 = rng.randint(1, 10)
            s5 = rng.randint(40, 150)
            b_ratio = b5 / max(b5 + s5, 1)
            liq = rng.uniform(1000.0, 25000.0)
            fdv = liq * 2.5
            vm5 = liq * rng.uniform(0.2, 1.2)
            age = rng.uniform(1.0, 15.0)
            is_p = 1
            soc = 0
            top10 = rng.uniform(30.0, 80.0)
            top1 = rng.uniform(10.0, 50.0)
        else:  # thin_liq_trap
            # Тонкая фантомная ловушка (< $2k)
            m5 = rng.uniform(5.0, 35.0)
            m1 = m5 * rng.uniform(0.10, 0.20)
            h1 = rng.uniform(10.0, 40.0)
            b5 = rng.randint(3, 12)
            s5 = rng.randint(1, 8)
            b_ratio = b5 / max(b5 + s5, 1)
            liq = rng.uniform(200.0, 1800.0)
            fdv = liq * 4.0
            vm5 = rng.uniform(200.0, 1500.0)
            age = rng.uniform(0.5, 10.0)
            is_p = 0
            soc = 0
            top10 = rng.uniform(40.0, 90.0)
            top1 = rng.uniform(15.0, 50.0)

        avg_tx = vm5 / max(b5 + s5, 1)
        dataset.append({
            "m5_pct": m5, "m1_pct": m1, "h1_pct": h1,
            "m1_to_m5": (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0,
            "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": b_ratio,
            "net_buyers_m5": b5 - s5,
            "volume_m5": vm5, "vol_to_liq": vm5 / max(liq, 1), "avg_tx_usd": avg_tx,
            "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv / max(liq, 1),
            "age_min": age, "is_pump": is_p,
            "top1_pct": top1, "top10_pct": top10, "dev_pct": rng.uniform(2.0, 15.0),
            "socials": soc,
            "target": 0, "pnl": -95.0, "source": "archetype_dump", "weight": 1.5
        })

    master_df = pd.DataFrame(dataset)
    print(f"\n🎉 Итоговый датасет собран: {len(master_df)} примеров!")
    print(f"  • Ракеты (Target=1): {(master_df['target'] == 1).sum()} ({(master_df['target'] == 1).mean():.1%})")
    print(f"  • Дампы (Target=0):   {(master_df['target'] == 0).sum()} ({(master_df['target'] == 0).mean():.1%})")
    return master_df

def main():
    print("=" * 75)
    print("🚀 ОБУЧЕНИЕ МОДЕЛИ ROCKET XGBOOST v4.1 (SOLANA RUNNER DETECTOR)")
    print("=" * 75)

    df = collect_training_data()
    X = df[FEATURE_COLS].copy()
    y = df["target"].values
    weights = df["weight"].values

    scale_pos = (y == 0).sum() / max((y == 1).sum(), 1)
    print(f"⚖️ Баланс классов: scale_pos_weight = {scale_pos:.2f}")

    X_train, X_test, y_train, y_test, w_train, w_test = train_test_split(
        X, y, weights, test_size=0.15, random_state=42, stratify=y
    )

    # 5-Fold Stratified Cross-Validation
    print("\n🔄 Запуск 5-Fold Stratified Cross-Validation...")
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_aucs, cv_precs, cv_recs, cv_f1s = [], [], [], []

    for fold, (train_idx, val_idx) in enumerate(skf.split(X, y)):
        X_tr, y_tr, w_tr = X.iloc[train_idx], y[train_idx], weights[train_idx]
        X_va, y_va = X.iloc[val_idx], y[val_idx]

        clf = xgb.XGBClassifier(
            max_depth=5,
            n_estimators=320,
            learning_rate=0.035,
            subsample=0.85,
            colsample_bytree=0.85,
            scale_pos_weight=scale_pos,
            eval_metric="auc",
            random_state=42 + fold
        )
        clf.fit(X_tr, y_tr, sample_weight=w_tr)
        preds_prob = clf.predict_proba(X_va)[:, 1]
        preds_bin = (preds_prob >= 0.50).astype(int)

        cv_aucs.append(roc_auc_score(y_va, preds_prob))
        cv_precs.append(precision_score(y_va, preds_bin, zero_division=0))
        cv_recs.append(recall_score(y_va, preds_bin, zero_division=0))
        cv_f1s.append(f1_score(y_va, preds_bin, zero_division=0))

    print(f"\n📊 Средние метрики по 5 фолдам:")
    print(f"  Средний AUC-ROC:   {np.mean(cv_aucs):.4f} ± {np.std(cv_aucs):.4f}")
    print(f"  Средний Precision: {np.mean(cv_precs):.3f} ± {np.std(cv_precs):.3f}")
    print(f"  Средний Recall:    {np.mean(cv_recs):.3f} ± {np.std(cv_recs):.3f}")
    print(f"  Средний F1-Score:  {np.mean(cv_f1s):.3f} ± {np.std(cv_f1s):.3f}")

    # Финальная модель
    print("\n🔥 Финальное обучение Rocket XGBoost v4.1...")
    final_model = xgb.XGBClassifier(
        max_depth=5,
        n_estimators=350,
        learning_rate=0.035,
        subsample=0.85,
        colsample_bytree=0.85,
        scale_pos_weight=scale_pos,
        eval_metric=["auc", "logloss"],
        random_state=42
    )
    final_model.fit(
        X_train, y_train,
        sample_weight=w_train,
        eval_set=[(X_test, y_test)],
        verbose=False
    )

    test_probs = final_model.predict_proba(X_test)[:, 1]
    test_preds = (test_probs >= 0.50).astype(int)

    test_auc = roc_auc_score(y_test, test_probs)
    test_prec = precision_score(y_test, test_preds, zero_division=0)
    test_rec = recall_score(y_test, test_preds, zero_division=0)
    test_f1 = f1_score(y_test, test_preds, zero_division=0)
    test_acc = accuracy_score(y_test, test_preds)
    cm = confusion_matrix(y_test, test_preds)

    print("\n" + "=" * 75)
    print("🎯 РЕЗУЛЬТАТЫ НА ОТЛОЖЕННОЙ ТЕСТОВОЙ ВЫБОРКЕ (OUT-OF-SAMPLE)")
    print("=" * 75)
    print(f"  AUC-ROC:           {test_auc:.4f}")
    print(f"  Точность (Acc):    {test_acc:.2%}")
    print(f"  Precision (Ракеты):{test_prec:.2%}")
    print(f"  Recall (Охват):    {test_rec:.2%}")
    print(f"  F1-Score:          {test_f1:.4f}")
    print(f"  Матрица ошибок:")
    print(f"    Истинные нули (Дамп отсеян):    {cm[0][0]}")
    print(f"    Ложные единицы (Ложный вход):   {cm[0][1]}")
    print(f"    Пропущенные ракеты (FN):        {cm[1][0]}")
    print(f"    Пойманные ракеты (TP):          {cm[1][1]}")

    print("\n🏆 ТОП КЛЮЧЕВЫХ ПРИЗНАКОВ МОДЕЛИ (FEATURE IMPORTANCE):")
    for feat, imp in sorted(zip(FEATURE_COLS, final_model.feature_importances_), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  • {feat:<16} : {imp * 100:.2f}%")

    model_out = "rocket_xgb_v3.json"
    final_model.save_model(model_out)
    print(f"\n💾 Новая калиброванная модель сохранена в {model_out}!")
    final_model.save_model("rocket_xgb_v2.json")
    print(f"💾 Синхронизировано с rocket_xgb_v2.json!")

if __name__ == "__main__":
    main()
