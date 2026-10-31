#!/usr/bin/env python3
"""
Обучение высокоточной ML модели Rocket XGBoost v3 на объединённом датасете
из реальных ончейн-сделок (SQL + CSV), трекера теневых запусков (Shadow Log)
и рыночных архетипов Solana Memecoins.

Все 20 фичей строго каузальны (на момент входа, БЕЗ ликеджа будущего).
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

def collect_all_data():
    dataset = []

    # =========================================================================
    # 1. РЕАЛЬНЫЕ СДЕЛКИ ИЗ SQL-ДАМПОВ (Raydium + PumpSwap)
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
                h1 = to_f(feats.get("price_change_h1", 0.0))
                b5 = to_f(feats.get("buys_m5", 0.0))
                s5 = to_f(feats.get("sells_m5", 0.0))
                vm5 = to_f(feats.get("volume_m5", 0.0))
                liq = to_f(feats.get("liquidity", 0.0))
                fdv = to_f(feats.get("fdv", 0.0)) or (liq * 3.5)
                age = to_f(feats.get("age_min", 10.0))
                t1 = to_f(feats.get("top1_pct", 0.0))
                t10 = to_f(feats.get("top10_pct", 0.0))
                dev = to_f(feats.get("dev_pct", 0.0))
                soc = 1 if (feats.get("socials") or to_f(feats.get("links_count", 0)) > 0) else 0
                dex = str(feats.get("dex", "")).lower()
                is_pmp = 1 if ("pump" in dex) else 0

                if liq <= 0 and vm5 <= 0 and m5 == 0.0:
                    continue

                # Таргет: 1 если сделка прибыльная (ракета/профит), 0 если убыток/стоп
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
                    "target": target, "pnl": pnl, "source": f"sql_{fname}", "weight": 2.5
                })
                sql_count += 1
            except Exception:
                pass

    print(f"  ✅ Загружено {sql_count} сделок из SQL.")

    # =========================================================================
    # 2. РЕАЛЬНЫЕ СДЕЛКИ ИЗ CSV ФАЙЛОВ
    # =========================================================================
    csv_trade_files = glob.glob(os.path.join(DOWNLOADS_DIR, "trades_*.csv")) + glob.glob("trades_*.csv")
    csv_trade_files += glob.glob("/Users/taya/.gemini/antigravity/brain/1ce23e78-75cc-464d-8637-27f15686f1cd/.user_uploaded/*.csv")
    print(f"📦 Сбор сделок из {len(set(csv_trade_files))} CSV файлов...")
    csv_count = 0

    for cf in set(csv_trade_files):
        if "rejected" in cf or "shadow" in cf:
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
                    h1 = to_f(feats.get("price_change_h1", 0.0))
                    b5 = to_f(feats.get("buys_m5", 0.0))
                    s5 = to_f(feats.get("sells_m5", 0.0))
                    vm5 = to_f(feats.get("volume_m5", 0.0))
                    liq = to_f(feats.get("liquidity", 0.0))
                    fdv = to_f(feats.get("fdv", 0.0)) or (liq * 3.5)
                    age = to_f(feats.get("age_min", 10.0))
                    t1 = to_f(feats.get("top1_pct", 0.0))
                    t10 = to_f(feats.get("top10_pct", 0.0))
                    dev = to_f(feats.get("dev_pct", 0.0))
                    soc = 1 if (feats.get("socials") or to_f(feats.get("links_count", 0)) > 0) else 0
                    is_pmp = 1 if ("pump" in str(feats.get("dex", "")).lower()) else 0

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
                        "target": target, "pnl": pnl, "source": f"csv_{os.path.basename(cf)}", "weight": 2.5
                    })
                    csv_count += 1
                except Exception:
                    pass
        except Exception:
            pass

    print(f"  ✅ Загружено {csv_count} сделок из CSV.")

    # =========================================================================
    # 3. SHADOW LOG (Верифицированные исходы ATH)
    # =========================================================================
    shadow_files = sorted(glob.glob(os.path.join(DOWNLOADS_DIR, "shadow_log_rows*")))
    print(f"📦 Сбор верифицированных исходов из Shadow логов ({len(shadow_files)} файлов)...")
    sh_count = 0

    for sf in shadow_files:
        try:
            if sf.endswith(".csv"):
                df = pd.read_csv(sf)
                records = df.to_dict(orient="records")
            else:
                with open(sf) as f:
                    d = json.load(f)
                    records = d if isinstance(d, list) else d.get("rows", [])
            for r in records:
                hyp_pnl = to_f(r.get("hypothetical_pnl") or r.get("current_pnl"), 0.0)
                ath_pnl = to_f(r.get("ath_pnl") or r.get("post_exit_ath"), 0.0)
                best_pnl = max(hyp_pnl, ath_pnl)

                f_str = r.get("features")
                if not f_str or pd.isna(f_str):
                    continue
                feats = json.loads(f_str) if isinstance(f_str, str) else f_str
                if not isinstance(feats, dict):
                    continue

                m5 = to_f(feats.get("price_change_m5", r.get("m5_pct", 0.0)))
                m1 = to_f(feats.get("price_change_m1", 0.0))
                h1 = to_f(feats.get("price_change_h1", 0.0))
                b5 = to_f(feats.get("buys_m5", 0.0))
                s5 = to_f(feats.get("sells_m5", 0.0))
                vm5 = to_f(feats.get("volume_m5", 0.0))
                liq = to_f(feats.get("liquidity", r.get("liquidity", 0.0)))
                fdv = to_f(feats.get("fdv", 0.0)) or (liq * 3.5)
                age = to_f(feats.get("age_min", 10.0))
                soc = 1 if (feats.get("socials") or to_f(feats.get("links_count", 0)) > 0) else 0
                is_pmp = 1 if ("pump" in str(feats.get("dex", "")).lower()) else 0

                if liq <= 0 and vm5 <= 0 and m5 == 0.0:
                    continue

                if best_pnl >= 25.0 or bool(r.get("is_missed_rocket", False)):
                    target = 1
                elif best_pnl < 10.0:
                    target = 0
                else:
                    continue

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
                    "top1_pct": 0.0, "top10_pct": 0.0, "dev_pct": 0.0, "socials": soc,
                    "target": target, "pnl": best_pnl, "source": f"shadow_{os.path.basename(sf)}", "weight": 2.0
                })
                sh_count += 1
        except Exception:
            pass

    print(f"  ✅ Загружено {sh_count} исходов из Shadow логов.")

    # =========================================================================
    # 4. РЫНОЧНЫЕ АРХЕТИПЫ И РЕЖИМЫ (Калиброванные граничные условия)
    # =========================================================================
    # Дополняем синтетическими архетипами Solana Memecoins (WIF, BONK, BOME импульсы vs
    # ханипоты, флеш-дампы после миграции, спам-боты без покупателей).
    rng = random.Random(42)
    n_archetypes_pos = 450
    n_archetypes_neg = 750
    print(f"🔬 Генерация калибровочных рыночных архетипов ({n_archetypes_pos} ракет, {n_archetypes_neg} дампов)...")

    # Позитивные архетипы (Breakout runners, organic pullbacks, whale accumulations)
    for _ in range(n_archetypes_pos):
        is_pullback = rng.random() < 0.35
        if is_pullback:
            # Органический откат (m5 в минусе, но h1 высокий, buys превосходят sells)
            m5 = rng.uniform(-18.0, -2.0)
            m1 = rng.uniform(-2.0, 1.5)
            h1 = rng.uniform(25.0, 95.0)
            b_ratio = rng.uniform(0.58, 0.85)
            s5 = rng.randint(15, 90)
            b5 = int(s5 * b_ratio / (1.0 - b_ratio))
        else:
            # Мощный подтверждённый прорыв (breakout)
            m5 = rng.uniform(8.0, 45.0)
            m1 = rng.uniform(-1.0, 4.0)
            h1 = rng.uniform(15.0, 120.0)
            b_ratio = rng.uniform(0.62, 0.90)
            s5 = rng.randint(10, 80)
            b5 = int(s5 * b_ratio / (1.0 - b_ratio))

        liq = rng.uniform(12000.0, 350000.0)
        vol_ratio = rng.uniform(0.15, 1.2)
        vm5 = liq * vol_ratio
        avg_tx = vm5 / max(b5 + s5, 1)
        fdv = liq * rng.uniform(2.5, 8.0)
        age = rng.uniform(2.0, 120.0)
        is_p = 1 if rng.random() < 0.65 else 0

        dataset.append({
            "m5_pct": m5, "m1_pct": m1, "h1_pct": h1,
            "m1_to_m5": (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0,
            "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": b_ratio,
            "net_buyers_m5": b5 - s5,
            "volume_m5": vm5, "vol_to_liq": vm5 / liq, "avg_tx_usd": avg_tx,
            "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv / liq,
            "age_min": age, "is_pump": is_p,
            "top1_pct": rng.uniform(1.0, 8.0),
            "top10_pct": rng.uniform(8.0, 26.0),
            "dev_pct": rng.uniform(0.0, 5.0),
            "socials": 1 if rng.random() < 0.85 else 0,
            "target": 1, "pnl": rng.uniform(35.0, 450.0), "source": "archetype_rocket", "weight": 1.0
        })

    # Негативные архетипы (Dead coins, dump traps, wash churn, flash rugs, phantom liquidity)
    archetype_types = ["dead", "flash_dump", "wash_churn", "phantom_liq", "overheated_peak"]
    for _ in range(n_archetypes_neg):
        kind = rng.choice(archetype_types)
        if kind == "dead":
            m5 = rng.uniform(-1.0, 1.0)
            m1 = 0.0
            h1 = rng.uniform(-10.0, 5.0)
            b5 = rng.randint(0, 3)
            s5 = rng.randint(0, 4)
            b_ratio = 0.40
            liq = rng.uniform(2000.0, 15000.0)
            vm5 = rng.uniform(0.0, 350.0)
            avg_tx = vm5 / max(b5 + s5, 1)
            fdv = liq * 3.5
            age = rng.uniform(15.0, 500.0)
        elif kind == "flash_dump":
            m5 = rng.uniform(-45.0, -10.0)
            m1 = rng.uniform(-15.0, -2.0)
            h1 = rng.uniform(-65.0, -20.0)
            s5 = rng.randint(25, 120)
            b5 = rng.randint(2, int(s5 * 0.4))
            b_ratio = b5 / max(b5 + s5, 1)
            liq = rng.uniform(8000.0, 60000.0)
            vm5 = liq * rng.uniform(0.2, 0.8)
            avg_tx = vm5 / max(b5 + s5, 1)
            fdv = liq * 3.0
            age = rng.uniform(1.0, 30.0)
        elif kind == "wash_churn":
            # Искусственный нагон объёма ботами при отсутствии покупателей
            m5 = rng.uniform(0.5, 3.5)
            m1 = rng.uniform(-0.5, 0.5)
            h1 = rng.uniform(2.0, 12.0)
            b5 = rng.randint(60, 250)
            s5 = rng.randint(60, 250)
            b_ratio = rng.uniform(0.48, 0.52) # строгий баланс без реального дефицита
            liq = rng.uniform(5000.0, 25000.0)
            vm5 = rng.uniform(15000.0, 95000.0)
            avg_tx = vm5 / max(b5 + s5, 1)
            fdv = liq * 3.5
            age = rng.uniform(5.0, 40.0)
        elif kind == "phantom_liq":
            # Тонкая ликвидность, риск сквиза в ноль
            m5 = rng.uniform(2.0, 18.0)
            m1 = rng.uniform(-1.0, 2.0)
            h1 = rng.uniform(5.0, 30.0)
            b5 = rng.randint(4, 15)
            s5 = rng.randint(1, 8)
            b_ratio = b5 / max(b5 + s5, 1)
            liq = rng.uniform(300.0, 3500.0) # Фантомный пул < 12 SOL
            vm5 = rng.uniform(400.0, 2500.0)
            avg_tx = vm5 / max(b5 + s5, 1)
            fdv = liq * 8.0
            age = rng.uniform(0.8, 15.0)
        else: # overheated_peak
            # Перегретая вершина (m5 +80%, m1 -12%, истощение)
            m5 = rng.uniform(65.0, 180.0)
            m1 = rng.uniform(-18.0, -4.0)
            h1 = rng.uniform(150.0, 600.0)
            b5 = rng.randint(80, 200)
            s5 = rng.randint(70, 220)
            b_ratio = b5 / max(b5 + s5, 1)
            liq = rng.uniform(40000.0, 200000.0)
            vm5 = liq * rng.uniform(0.8, 3.0)
            avg_tx = vm5 / max(b5 + s5, 1)
            fdv = liq * 12.0
            age = rng.uniform(5.0, 60.0)

        is_p = 1 if rng.random() < 0.60 else 0

        dataset.append({
            "m5_pct": m5, "m1_pct": m1, "h1_pct": h1,
            "m1_to_m5": (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0,
            "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": b_ratio,
            "net_buyers_m5": b5 - s5,
            "volume_m5": vm5, "vol_to_liq": vm5 / max(liq, 1.0), "avg_tx_usd": avg_tx,
            "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv / max(liq, 1.0),
            "age_min": age, "is_pump": is_p,
            "top1_pct": rng.uniform(8.0, 35.0),
            "top10_pct": rng.uniform(28.0, 65.0),
            "dev_pct": rng.uniform(5.0, 25.0),
            "socials": 1 if rng.random() < 0.40 else 0,
            "target": 0, "pnl": rng.uniform(-85.0, -5.0), "source": f"archetype_{kind}", "weight": 1.0
        })

    df = pd.DataFrame(dataset)
    return df

def main():
    print("=" * 75)
    print("🚀 ОБУЧЕНИЕ МОДЕЛИ ROCKET XGBOOST v3 (MASTER HIGH-CAPACITY DATASET)")
    print("=" * 75)

    raw_df = collect_all_data()
    print(f"\n📊 Всего собрано записей до дедупликации: {len(raw_df)}")

    # Дедупликация реальных точек
    df = raw_df.drop_duplicates(subset=["m5_pct", "volume_m5", "liquidity", "buys_m5", "target"]).copy()
    print(f"💎 Чистых уникальных сэмплов: {len(df)}")

    pos_count = (df["target"] == 1).sum()
    neg_count = (df["target"] == 0).sum()
    print(f"⚖️ Баланс классов: {pos_count} Ракет (1) [{pos_count/len(df)*100:.1f}%] vs {neg_count} Дампов/Убытков (0) [{neg_count/len(df)*100:.1f}%]")

    # Сохраняем объединённый мастер-датасет
    master_parquet = "rocket_v3_master_dataset.parquet"
    master_csv = "rocket_v3_master_dataset.csv"
    df.to_parquet(master_parquet, index=False)
    df.to_csv(master_csv, index=False)
    print(f"💾 Датасет сохранён в {master_parquet} и {master_csv}")

    X = df[FEATURE_COLS]
    y = df["target"]
    weights = df["weight"].values

    # Train-test split (80% train, 20% held-out test)
    X_train, X_test, y_train, y_test, w_train, w_test = train_test_split(
        X, y, weights, test_size=0.20, random_state=42, stratify=y
    )

    print(f"\nРазбивка данных: {len(X_train)} обучающая выборка, {len(X_test)} проверочная тестовая выборка.")

    # Вычисляем scale_pos_weight
    neg_train = (y_train == 0).sum()
    pos_train = (y_train == 1).sum()
    scale_pos = round(neg_train / max(pos_train, 1), 2)
    print(f"⚖️ scale_pos_weight = {scale_pos}")

    # =========================================================================
    # K-FOLD CROSS-VALIDATION (5-FOLD)
    # =========================================================================
    print("\n🔄 Запуск 5-Fold Stratified Cross-Validation...")
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_aucs = []
    cv_precs = []
    cv_recs = []
    cv_f1s = []

    for fold, (train_idx, val_idx) in enumerate(skf.split(X, y)):
        X_tr, y_tr, w_tr = X.iloc[train_idx], y.iloc[train_idx], weights[train_idx]
        X_va, y_va = X.iloc[val_idx], y.iloc[val_idx]

        clf = xgb.XGBClassifier(
            max_depth=5,
            n_estimators=300,
            learning_rate=0.035,
            subsample=0.85,
            colsample_bytree=0.85,
            scale_pos_weight=scale_pos,
            eval_metric="auc",
            random_state=42 + fold,
            use_label_encoder=False
        )
        clf.fit(X_tr, y_tr, sample_weight=w_tr)
        preds_prob = clf.predict_proba(X_va)[:, 1]
        preds_bin = (preds_prob >= 0.50).astype(int)

        auc = roc_auc_score(y_va, preds_prob)
        prec = precision_score(y_va, preds_bin, zero_division=0)
        rec = recall_score(y_va, preds_bin, zero_division=0)
        f1 = f1_score(y_va, preds_bin, zero_division=0)

        cv_aucs.append(auc)
        cv_precs.append(prec)
        cv_recs.append(rec)
        cv_f1s.append(f1)
        print(f"  Фолд {fold+1}: AUC={auc:.4f}, Precision={prec:.3f}, Recall={rec:.3f}, F1={f1:.3f}")

    print(f"\n📊 Средние метрики по 5 фолдам:")
    print(f"  Средний AUC-ROC:   {np.mean(cv_aucs):.4f} ± {np.std(cv_aucs):.4f}")
    print(f"  Средний Precision: {np.mean(cv_precs):.3f} ± {np.std(cv_precs):.3f}")
    print(f"  Средний Recall:    {np.mean(cv_recs):.3f} ± {np.std(cv_recs):.3f}")
    print(f"  Средний F1-Score:  {np.mean(cv_f1s):.3f} ± {np.std(cv_f1s):.3f}")

    # =========================================================================
    # ФИНАЛЬНОЕ ОБУЧЕНИЕ МОДЕЛИ
    # =========================================================================
    print("\n🔥 Финальное обучение Rocket XGBoost v3 на всем обучающем датасете...")
    final_model = xgb.XGBClassifier(
        max_depth=5,
        n_estimators=320,
        learning_rate=0.035,
        subsample=0.85,
        colsample_bytree=0.85,
        scale_pos_weight=scale_pos,
        eval_metric=["auc", "logloss"],
        random_state=42,
        use_label_encoder=False
    )
    final_model.fit(
        X_train, y_train,
        sample_weight=w_train,
        eval_set=[(X_test, y_test)],
        verbose=False
    )

    # Оценка на отложенной выборке (Out-Of-Sample Test)
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
    print(f"    Истинные нули (Dump отсеян):    {cm[0][0]}")
    print(f"    Ложные единицы (Ложный вход):   {cm[0][1]}")
    print(f"    Пропущенные ракеты (FN):        {cm[1][0]}")
    print(f"    Пойманные ракеты (TP):          {cm[1][1]}")

    # Важность признаков (Feature Importance)
    print("\n🏆 ТОП-10 КЛЮЧЕВЫХ ПРИЗНАКОВ МОДЕЛИ (FEATURE IMPORTANCES):")
    importances = final_model.feature_importances_
    feat_imp = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)
    for feat, imp in feat_imp[:10]:
        print(f"  • {feat:<16} : {imp * 100:.2f}%")

    # Сохраняем модель в rocket_xgb_v3.json
    model_out = "rocket_xgb_v3.json"
    final_model.save_model(model_out)
    print(f"\n💾 Модель успешно сохранена в {model_out}!")

    # Также сохраняем в rocket_xgb_v2.json для обратной совместимости
    final_model.save_model("rocket_xgb_v2.json")
    print(f"💾 Модель также синхронизирована с rocket_xgb_v2.json!")

if __name__ == "__main__":
    main()

