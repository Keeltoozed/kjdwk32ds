import glob
import re
import json
import os
import pandas as pd
import numpy as np

downloads_dir = "/Users/taya/Downloads"

print("="*70)
print("🔍 COMPREHENSIVE SCAN FOR ALL HISTORICAL TRAINING DATA")
print("="*70)

# 1. Search all trades SQL files
sql_trade_files = sorted(glob.glob(os.path.join(downloads_dir, "trades_*.sql")))
print(f"\n📂 Found {len(sql_trade_files)} SQL trade files in {downloads_dir}:")
for f in sql_trade_files:
    sz = os.path.getsize(f) / 1024
    print(f"  - {os.path.basename(f)} ({sz:.1f} KB)")

# 2. Search all trades CSV and JSON files
csv_trade_files = sorted(glob.glob(os.path.join(downloads_dir, "trades_*.csv")))
json_trade_files = sorted(glob.glob(os.path.join(downloads_dir, "trades_*.json")))
print(f"\n📂 Found {len(csv_trade_files)} CSV and {len(json_trade_files)} JSON trade files:")
for f in csv_trade_files + json_trade_files:
    sz = os.path.getsize(f) / 1024
    print(f"  - {os.path.basename(f)} ({sz:.1f} KB)")

# 3. Search rejected tokens and shadow logs
rejected_csv_files = sorted(glob.glob(os.path.join(downloads_dir, "rejected_tokens_rows*.csv")))
rejected_sql_files = sorted(glob.glob(os.path.join(downloads_dir, "rejected_tokens_rows*.sql")))
shadow_files = sorted(glob.glob(os.path.join(downloads_dir, "shadow_log_rows*")))
print(f"\n📂 Found {len(rejected_csv_files)} Rejected CSVs, {len(rejected_sql_files)} Rejected SQLs, {len(shadow_files)} Shadow files:")
for f in rejected_csv_files + rejected_sql_files + shadow_files:
    sz = os.path.getsize(f) / 1024
    print(f"  - {os.path.basename(f)} ({sz:.1f} KB)")

# 4. Check workspace files
ws_csvs = sorted(glob.glob("*.csv") + glob.glob("*.json") + glob.glob("*.db"))
print(f"\n📂 Workspace files in {os.getcwd()}:")
for f in ws_csvs:
    sz = os.path.getsize(f) / 1024
    print(f"  - {os.path.basename(f)} ({sz:.1f} KB)")

