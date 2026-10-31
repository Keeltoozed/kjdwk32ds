import glob
import re
import json
import os
import pandas as pd
import numpy as np

downloads_dir = "/Users/taya/Downloads"

FEATURE_COLS = [
    "m5_pct", "m1_pct", "h1_pct", "m1_to_m5",
    "buys_m5", "sells_m5", "buy_ratio_m5", "net_buyers_m5",
    "volume_m5", "vol_to_liq", "avg_tx_usd",
    "liquidity", "fdv", "fdv_to_liq",
    "age_min", "is_pump",
    "top1_pct", "top10_pct", "dev_pct", "socials"
]

def to_f(v, d=0.0):
    try:
        if v is None: return d
        s = str(v).strip()
        if not s or s == "None" or s == "null" or s == "nan": return d
        return float(s)
    except:
        return d

dataset = []

# =========================================================================
# SOURCE 1: pump_dataset.csv (30,004 rows of ground truth onchain data)
# =========================================================================
pump_csv = "/Users/taya/Downloads/kjdwk32ds-main--5/pump_dataset.csv"
if os.path.exists(pump_csv):
    print("Loading pump_dataset.csv (30,004 rows)...")
    df_pmp = pd.read_csv(pump_csv)
    for _, r in df_pmp.iterrows():
        m5 = to_f(r.get("price_change_m5"))
        m1 = 0.0
        h1 = to_f(r.get("price_change_h24")) / 4.0 if to_f(r.get("price_change_h24")) != 0 else m5 * 1.5
        b5 = to_f(r.get("buys_m5"))
        s5 = to_f(r.get("sells_m5"))
        vm5 = to_f(r.get("volume_m5"))
        liq = to_f(r.get("liquidity"))
        fdv = to_f(r.get("fdv")) or (liq * 3.5)
        dev = to_f(r.get("dev_holding_pct"))
        t10 = to_f(r.get("top_10_holding_pct"))
        soc = int(to_f(r.get("has_socials")))
        tgt = int(to_f(r.get("target") or r.get("is_success")))
        
        m1_to_m5 = 0.0
        b_ratio = (b5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.5
        net_b = b5 - s5
        v_to_l = (vm5 / max(liq, 1.0)) if liq > 0 else 0.0
        avg_tx = (vm5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.0
        f_to_l = (fdv / max(liq, 1.0)) if liq > 0 else 3.5
        
        dataset.append({
            "m5_pct": m5, "m1_pct": m1, "h1_pct": h1, "m1_to_m5": m1_to_m5,
            "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": b_ratio, "net_buyers_m5": net_b,
            "volume_m5": vm5, "vol_to_liq": v_to_l, "avg_tx_usd": avg_tx,
            "liquidity": liq, "fdv": fdv, "fdv_to_liq": f_to_l,
            "age_min": 10.0, "is_pump": 1,
            "top1_pct": min(dev, t10 / 2.0), "top10_pct": t10, "dev_pct": dev, "socials": soc,
            "target": tgt, "source": "pump_dataset_30k"
        })
    print(f"  Loaded {len(df_pmp)} samples from pump_dataset.csv")

# =========================================================================
# SOURCE 2: Real trades from SQL Dumps (5,007 rows)
# =========================================================================
print("Loading real executed trades from SQL files...")
sql_files = glob.glob(os.path.join(downloads_dir, "trades_*.sql"))
sql_loaded = 0
for sf in sorted(sql_files):
    fname = os.path.basename(sf)
    with open(sf, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    matches = re.findall(r"\(\x27[^\x27]+\x27,\s*\x27[^\x27]+\x27,\s*\x27(\{.*?\})\x27,\s*([^\,]+),\s*([^\,]+),\s*\x27([^\x27]*)\x27", text)
    if not matches:
        matches = re.findall(r"\([^\,]+,\s*[^\,]+,\s*\x27(\{.*?\})\x27,\s*([^\,]+),\s*([^\,]+),\s*\x27([^\x27]*)\x27", text)
    
    for m in matches:
        try:
            feats = json.loads(m[0])
            if not isinstance(feats, dict) or len(feats) < 3:
                continue
            pnl = to_f(m[2], 0.0)
            reason = str(m[3])
            
            # Ground truth target:
            # 1 = winner (PnL >= +15% or scalp profit > +5%)
            # 0 = loser (PnL <= -5% or stopped out)
            if pnl >= 15.0 or ("Trailing" in reason and pnl > 5.0) or ("Scalp Profit" in reason and pnl > 5.0):
                target = 1
            elif pnl <= -5.0 or "Stop" in reason or "Crash Guard" in reason or "Dead" in reason:
                target = 0
            else:
                target = 1 if pnl > 0 else 0

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
                "target": target, "source": f"sql_{fname}"
            })
            sql_loaded += 1
        except Exception:
            pass

print(f"  Loaded {sql_loaded} samples from SQL dumps")

# =========================================================================
# SOURCE 3: CSV Trades from Live Runs
# =========================================================================
csv_trade_files = glob.glob(os.path.join(downloads_dir, "trades_*.csv")) + glob.glob("trades_*.csv")
csv_loaded = 0
for f in set(csv_trade_files):
    if "rejected" in f or "shadow" in f: continue
    try:
        df = pd.read_csv(f)
        for _, r in df.iterrows():
            feat_raw = r.get("features") or r.get("ml_features") or "{}"
            if isinstance(feat_raw, str):
                try: feats = json.loads(feat_raw)
                except: feats = {}
            elif isinstance(feat_raw, dict):
                feats = feat_raw
            else:
                feats = {}

            pnl = to_f(r.get("pnl_percent") or r.get("pnl"), 0.0)
            m5 = to_f(r.get("m5_pct") or feats.get("price_change_m5", 0.0))
            m1 = to_f(r.get("m1_pct") or feats.get("price_change_m1", 0.0))
            h1 = to_f(r.get("h1_pct") or feats.get("price_change_h1", 0.0))
            b5 = to_f(r.get("buys_m5") or feats.get("buys_m5", 0.0))
            s5 = to_f(r.get("sells_m5") or feats.get("sells_m5", 0.0))
            vm5 = to_f(r.get("volume_m5") or feats.get("volume_m5", 0.0))
            liq = to_f(r.get("liquidity") or feats.get("liquidity", 0.0))
            fdv = to_f(r.get("fdv") or feats.get("fdv", 0.0)) or (liq * 3.5)
            age = to_f(r.get("age_min") or feats.get("age_min", 10.0))
            t1 = to_f(feats.get("top1_pct", 0.0))
            t10 = to_f(feats.get("top10_pct", 0.0))
            dev = to_f(feats.get("dev_pct", 0.0))
            soc = 1 if (feats.get("socials") or to_f(feats.get("links_count", 0)) > 0) else 0
            is_pmp = 1 if (str(r.get("mint", "")).endswith("pump") or "pump" in str(feats.get("dex", "")).lower()) else 0

            if liq <= 0 and vm5 <= 0 and m5 == 0.0: continue

            m1_to_m5 = (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0
            buy_ratio_m5 = (b5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.5
            net_buyers_m5 = b5 - s5
            vol_to_liq = (vm5 / max(liq, 1.0)) if liq > 0 else 0.0
            avg_tx_usd = (vm5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.0
            fdv_to_liq = (fdv / max(liq, 1.0)) if liq > 0 else 3.5

            target = 1 if pnl >= 15.0 else (0 if pnl <= -5.0 else (1 if pnl > 0 else 0))

            dataset.append({
                "m5_pct": m5, "m1_pct": m1, "h1_pct": h1, "m1_to_m5": m1_to_m5,
                "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": buy_ratio_m5, "net_buyers_m5": net_buyers_m5,
                "volume_m5": vm5, "vol_to_liq": vol_to_liq, "avg_tx_usd": avg_tx_usd,
                "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv_to_liq,
                "age_min": age, "is_pump": is_pmp,
                "top1_pct": t1, "top10_pct": t10, "dev_pct": dev, "socials": soc,
                "target": target, "source": f"csv_{os.path.basename(f)}"
            })
            csv_loaded += 1
    except:
        pass

print(f"  Loaded {csv_loaded} samples from live trade CSVs")

# =========================================================================
# SOURCE 4: Shadow Tracker Logs with ATH Outcomes
# =========================================================================
shadow_files = glob.glob(os.path.join(downloads_dir, "shadow_log_rows*.csv")) + glob.glob(os.path.join(downloads_dir, "shadow_log_rows*.json"))
shadow_loaded = 0
for sf in set(shadow_files):
    try:
        if sf.endswith(".csv"):
            sdf = pd.read_csv(sf)
            records = sdf.to_dict(orient="records")
        else:
            with open(sf) as fp:
                records = json.load(fp)
                if isinstance(records, dict): records = records.get("rows", [])
        for r in records:
            hyp_pnl = to_f(r.get("hypothetical_pnl") or r.get("current_pnl"), 0.0)
            ath_pnl = to_f(r.get("ath_pnl") or r.get("post_exit_ath"), 0.0)
            feat_raw = r.get("features") or "{}"
            if isinstance(feat_raw, str):
                try: feats = json.loads(feat_raw)
                except: feats = {}
            elif isinstance(feat_raw, dict): feats = feat_raw
            else: feats = {}

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
            is_pmp = 1 if (str(r.get("mint", "")).endswith("pump") or "pump" in str(feats.get("dex", "")).lower()) else 0

            if liq <= 0 and vm5 <= 0 and m5 == 0.0: continue

            best_pnl = max(hyp_pnl, ath_pnl)
            if best_pnl >= 35.0 or bool(r.get("is_missed_rocket", False)):
                target = 1
            elif best_pnl < 15.0:
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
                "target": target, "source": f"shadow_{os.path.basename(sf)}"
            })
            shadow_loaded += 1
    except:
        pass

print(f"  Loaded {shadow_loaded} samples from Shadow logs")

# =========================================================================
# TOTAL UNIFIED DATASET
# =========================================================================
df_total = pd.DataFrame(dataset)
print("\n" + "="*70)
print(f"🎉 TOTAL DATASET ASSEMBLED: {len(df_total)} SAMPLES")
print("="*70)

# Deduplicate
df_clean = df_total.drop_duplicates(subset=["m5_pct", "volume_m5", "liquidity", "buys_m5", "fdv", "target"])
print(f"Clean samples after deduplication: {len(df_clean)}")
pos = (df_clean["target"] == 1).sum()
neg = (df_clean["target"] == 0).sum()
print(f"Class Balance: {pos} Rockets (1) [{pos/len(df_clean)*100:.1f}%] vs {neg} Dumps/Losses (0) [{neg/len(df_clean)*100:.1f}%]")

# Save to parquet and csv
output_parquet = "unified_training_dataset.parquet"
output_csv = "unified_training_dataset.csv"
df_clean.to_parquet(output_parquet, index=False)
df_clean.to_csv(output_csv, index=False)
print(f"💾 Saved master unified dataset to {output_parquet} and {output_csv}!")

