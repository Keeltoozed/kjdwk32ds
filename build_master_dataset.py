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
        if not s or s == "None" or s == "null": return d
        return float(s)
    except:
        return d

extracted_samples = []

# =========================================================================
# 1. PARSE ALL SQL TRADE FILES
# =========================================================================
sql_trade_files = glob.glob(os.path.join(downloads_dir, "trades_*.sql"))
print(f"Parsing {len(sql_trade_files)} SQL trade files...")

sql_trade_count = 0
for sf in sorted(sql_trade_files):
    fname = os.path.basename(sf)
    with open(sf, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    
    # Each row in INSERT INTO "public"."trades_raydium" or "trades_pump":
    # ('mint', 'entry_time', 'features', confidence, pnl, 'exit_reason', 'status', 'exit_time', post_exit_ath, missed_pnl, ...)
    # Let us extract rows using regex or string splits
    tuples = re.findall(r"\((?:\'[^\']+\'|NULL|\d+),\s*\'[^\']+\',\s*\'(\{.*?\})\',\s*([^\,]+),\s*([^\,]+),\s*\'([^\']*)\'", text)
    if not tuples:
        # Alternative pattern without exact quoting
        tuples = re.findall(r"\((.*?)\)(?:, |;|\n)", text)
    
    parsed_in_file = 0
    for item in tuples:
        if isinstance(item, tuple) and len(item) >= 4:
            feat_str, conf_str, pnl_str, exit_reason = item[0], item[1], item[2], item[3]
        elif isinstance(item, str):
            # Try to extract JSON features
            j_match = re.search(r"\'(\{.*?\})\'", item)
            if not j_match:
                continue
            feat_str = j_match.group(1)
            # parts before and after
            after_json = item[j_match.end():]
            parts = [p.strip().replace("'", "") for p in after_json.split(",")]
            conf_str = parts[1] if len(parts) > 1 else "0"
            pnl_str = parts[2] if len(parts) > 2 else "0"
            exit_reason = parts[3] if len(parts) > 3 else ""
        else:
            continue
        
        try:
            feats = json.loads(feat_str)
            if not isinstance(feats, dict) or len(feats) < 3:
                continue
            
            pnl = to_f(pnl_str, 0.0)
            
            # Extract the 20 features
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

            # Target label:
            # 1 = true winner/rocket (PnL >= +15% or scalp profit > +10%)
            # 0 = loser or dump (PnL <= -5% or stopped out)
            if pnl >= 15.0 or ("Trailing" in exit_reason and pnl > 5.0) or ("Scalp Profit" in exit_reason and pnl > 5.0):
                target = 1
            elif pnl <= -5.0 or "Stop" in exit_reason or "Crash Guard" in exit_reason or "Dead" in exit_reason:
                target = 0
            else:
                target = 1 if pnl > 0 else 0

            extracted_samples.append({
                "m5_pct": m5, "m1_pct": m1, "h1_pct": h1, "m1_to_m5": m1_to_m5,
                "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": buy_ratio_m5, "net_buyers_m5": net_buyers_m5,
                "volume_m5": vm5, "vol_to_liq": vol_to_liq, "avg_tx_usd": avg_tx_usd,
                "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv_to_liq,
                "age_min": age, "is_pump": is_pmp,
                "top1_pct": t1, "top10_pct": t10, "dev_pct": dev, "socials": soc,
                "pnl": pnl, "target": target, "source": f"sql_{fname}"
            })
            parsed_in_file += 1
            sql_trade_count += 1
        except Exception:
            pass
    print(f"  {fname}: {parsed_in_file} valid trades extracted")

print(f"\nTotal valid trades extracted from SQL: {sql_trade_count}")

# =========================================================================
# 2. PARSE ALL CSV & JSON TRADE FILES
# =========================================================================
csv_trade_files = glob.glob(os.path.join(downloads_dir, "trades_*.csv")) + glob.glob("*.csv")
json_trade_files = glob.glob(os.path.join(downloads_dir, "trades_*.json"))
print(f"\nParsing {len(csv_trade_files)} CSV and {len(json_trade_files)} JSON trade files...")

csv_trade_count = 0
for f in set(csv_trade_files):
    fname = os.path.basename(f)
    if "rejected" in fname or "shadow" in fname: continue
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
            m_str = str(r.get("mint", ""))
            dex_str = str(feats.get("dex", "")).lower()
            is_pmp = 1 if (m_str.endswith("pump") or "pump" in dex_str) else 0

            if liq <= 0 and vm5 <= 0 and m5 == 0.0:
                continue

            m1_to_m5 = (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0
            buy_ratio_m5 = (b5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.5
            net_buyers_m5 = b5 - s5
            vol_to_liq = (vm5 / max(liq, 1.0)) if liq > 0 else 0.0
            avg_tx_usd = (vm5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.0
            fdv_to_liq = (fdv / max(liq, 1.0)) if liq > 0 else 3.5

            target = 1 if pnl >= 15.0 else (0 if pnl <= -5.0 else (1 if pnl > 0 else 0))

            extracted_samples.append({
                "m5_pct": m5, "m1_pct": m1, "h1_pct": h1, "m1_to_m5": m1_to_m5,
                "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": buy_ratio_m5, "net_buyers_m5": net_buyers_m5,
                "volume_m5": vm5, "vol_to_liq": vol_to_liq, "avg_tx_usd": avg_tx_usd,
                "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv_to_liq,
                "age_min": age, "is_pump": is_pmp,
                "top1_pct": t1, "top10_pct": t10, "dev_pct": dev, "socials": soc,
                "pnl": pnl, "target": target, "source": f"csv_{fname}"
            })
            csv_trade_count += 1
    except Exception as e:
        pass

print(f"Total valid trades extracted from CSV: {csv_trade_count}")

# =========================================================================
# 3. PARSE SHADOW LOGS & REJECTED TOKENS WITH VERIFIED OUTCOMES
# =========================================================================
shadow_files = glob.glob(os.path.join(downloads_dir, "shadow_log_rows*.csv")) + glob.glob(os.path.join(downloads_dir, "shadow_log_rows*.json"))
print(f"\nParsing {len(shadow_files)} Shadow log files...")

shadow_count = 0
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
            elif isinstance(feat_raw, dict):
                feats = feat_raw
            else:
                feats = {}

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
            m_str = str(r.get("mint", ""))
            is_pmp = 1 if (m_str.endswith("pump") or "pump" in str(feats.get("dex", "")).lower()) else 0

            if liq <= 0 and vm5 <= 0 and m5 == 0.0:
                continue

            # Ground truth from hypothetical ATH:
            # If token surged >= +35%, it was a MISSED ROCKET! Target = 1
            # If token surged <= +10% or dumped, it was a DUMP/RUG! Target = 0
            best_pnl = max(hyp_pnl, ath_pnl)
            if best_pnl >= 35.0 or bool(r.get("is_missed_rocket", False)):
                target = 1
            elif best_pnl < 15.0:
                target = 0
            else:
                continue  # skip ambiguous 15-35%

            m1_to_m5 = (m1 / max(abs(m5), 1.0)) if abs(m5) > 0 else 0.0
            buy_ratio_m5 = (b5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.5
            net_buyers_m5 = b5 - s5
            vol_to_liq = (vm5 / max(liq, 1.0)) if liq > 0 else 0.0
            avg_tx_usd = (vm5 / max(b5 + s5, 1.0)) if (b5 + s5) > 0 else 0.0
            fdv_to_liq = (fdv / max(liq, 1.0)) if liq > 0 else 3.5

            extracted_samples.append({
                "m5_pct": m5, "m1_pct": m1, "h1_pct": h1, "m1_to_m5": m1_to_m5,
                "buys_m5": b5, "sells_m5": s5, "buy_ratio_m5": buy_ratio_m5, "net_buyers_m5": net_buyers_m5,
                "volume_m5": vm5, "vol_to_liq": vol_to_liq, "avg_tx_usd": avg_tx_usd,
                "liquidity": liq, "fdv": fdv, "fdv_to_liq": fdv_to_liq,
                "age_min": age, "is_pump": is_pmp,
                "top1_pct": 0.0, "top10_pct": 0.0, "dev_pct": 0.0, "socials": soc,
                "pnl": best_pnl, "target": target, "source": f"shadow_{os.path.basename(sf)}"
            })
            shadow_count += 1
    except Exception as e:
        pass

print(f"Total valid shadow tracking samples: {shadow_count}")

# =========================================================================
# SUMMARY
# =========================================================================
df_all = pd.DataFrame(extracted_samples)
print("\n" + "="*70)
print(f"🚀 TOTAL DATASET COLLECTED: {len(df_all)} SAMPLES")
print("="*70)

if len(df_all) > 0:
    # Deduplicate by key metrics
    df_clean = df_all.drop_duplicates(subset=["m5_pct", "volume_m5", "liquidity", "buys_m5", "pnl"])
    print(f"Unique clean training samples after deduplication: {len(df_clean)}")
    pos = (df_clean["target"] == 1).sum()
    neg = (df_clean["target"] == 0).sum()
    print(f"Target distribution: {pos} Rockets (1) vs {neg} Dumps/Losses (0)")
    print(f"Positive ratio: {pos / len(df_clean) * 100:.1f}%")
    df_clean.to_parquet("training_dataset_master.parquet", index=False)
    print("Saved clean master dataset to training_dataset_master.parquet")

