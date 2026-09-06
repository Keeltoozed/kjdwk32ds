import pandas as pd
import numpy as np
import os
import time

def build_dataset():
    print("⏳ Загружаем метки успеха (Graduation) из archive/train.csv...")
    try:
        train_df = pd.read_csv("archive/train.csv", usecols=["mint", "has_graduated"])
        train_df["is_success"] = train_df["has_graduated"].fillna(False).astype(int)
        labels = dict(zip(train_df["mint"], train_df["is_success"]))
    except Exception as e:
        print(f"Ошибка загрузки train.csv: {e}")
        return
    
    print("⏳ Загружаем данные о создателях из archive/token_info_onchain_divers.csv...")
    try:
        creator_df = pd.read_csv("archive/token_info_onchain_divers.csv", usecols=["mint", "creator", "url"])
        creators = dict(zip(creator_df["mint"], creator_df["creator"]))
        # Если есть URL (ipfs), считаем что есть соцсети
        socials_map = dict(zip(creator_df["mint"], creator_df["url"].notna().astype(int)))
    except Exception as e:
        print(f"Ошибка загрузки onchain_divers: {e}")
        return
    
    dataset = []
    processed = 0
    
    cols = ["block_time", "base_coin", "signing_wallet", "direction", "base_coin_amount"]
    
    for i in range(1, 42):
        file_path = f"archive/chunk_{i}.csv"
        if not os.path.exists(file_path):
            continue
            
        print(f"⏳ Парсим {file_path}...")
        try:
            chunk_df = pd.read_csv(file_path, usecols=cols)
        except Exception as e:
            print(f"Ошибка загрузки {file_path}: {e}")
            continue
            
        grouped = chunk_df.groupby("base_coin")
        
        for mint, group in grouped:
            if mint not in labels:
                continue
                
            group = group.sort_values("block_time")
            
            first_tx_time = pd.to_datetime(group["block_time"].iloc[0])
            group["time_diff"] = (pd.to_datetime(group["block_time"]) - first_tx_time).dt.total_seconds()
            
            early_trades = group[group["time_diff"] <= 60]
            if early_trades.empty:
                continue
                
            buys = len(early_trades[early_trades["direction"] == "buy"])
            sells = len(early_trades[early_trades["direction"] == "sell"])
            tx_velocity_1m = buys + sells
            
            balances = {}
            for _, row in early_trades.iterrows():
                wallet = row["signing_wallet"]
                amt = float(row["base_coin_amount"])
                if row["direction"] == "buy":
                    balances[wallet] = balances.get(wallet, 0) + amt
                else:
                    balances[wallet] = max(0, balances.get(wallet, 0) - amt)
                    
            total_supply = 1_000_000_000 * 1_000_000
            
            dev_wallet = creators.get(mint, "")
            dev_holding = balances.get(dev_wallet, 0)
            dev_holding_pct = (dev_holding / total_supply) * 100
            
            sorted_balances = sorted(balances.values(), reverse=True)
            top_10 = sum(sorted_balances[:10])
            top_10_holding_pct = (top_10 / total_supply) * 100
            
            dataset.append({
                "mint": mint,
                "dev_holding_pct": min(100.0, dev_holding_pct),
                "top_10_holding_pct": min(100.0, top_10_holding_pct),
                "tx_velocity_1m": tx_velocity_1m,
                "has_socials": socials_map.get(mint, 1), 
                "funded_from_cex": 0,
                "is_success": labels[mint]
            })
            
            processed += 1
            if processed % 5000 == 0:
                print(f"Обраработано {processed} токенов...")
                
    final_df = pd.DataFrame(dataset)
    success_df = final_df[final_df["is_success"] == 1]
    fail_df = final_df[final_df["is_success"] == 0]
    
    fail_sample = fail_df.sample(n=min(len(fail_df), len(success_df) * 3), random_state=42)
    balanced_df = pd.concat([success_df, fail_sample]).sample(frac=1).reset_index(drop=True)
    
    filename = "pump_dataset.csv"
    balanced_df.to_csv(filename, index=False)
    
    print(f"✅ Ура! Извлекли реальные ончейн-фичи для {len(final_df)} токенов из ВСЕХ чанков.")
    print(f"💾 Сбалансированный датасет ({len(balanced_df)} строк) сохранен в {filename}")
    print(f"📈 Успешных (Ракет): {len(success_df)} | Провальных: {len(fail_sample)}")

if __name__ == "__main__":
    build_dataset()
