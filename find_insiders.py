import requests
import json
import pandas as pd
import config
from collections import defaultdict
import time
import os

def resolve_token_account_owner(token_account: str) -> str:
    url = f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            token_account,
            {"encoding": "jsonParsed"}
        ]
    }
    try:
        resp = requests.post(url, json=payload).json()
        if "result" in resp and resp["result"] and "value" in resp["result"]:
            parsed = resp["result"]["value"].get("data", {}).get("parsed", {})
            return parsed.get("info", {}).get("owner", "")
    except Exception as e:
        print(f"Error fetching owner for {token_account}: {e}")
    return ""

def find_insiders():
    print("🐋 Ищем китов и инсайдеров среди упущенных ракет...")
    if not os.path.exists("false_negatives_dataset.csv"):
        print("Файл false_negatives_dataset.csv не найден. Сначала обучи ИИ.")
        return
        
    df = pd.read_csv("false_negatives_dataset.csv")
    mints = df['mint'].tolist()
    
    print(f"Анализируем {len(mints)} успешных монет...")
    wallet_hits = defaultdict(list)
    url = f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}"
    
    for i, mint in enumerate(mints):
        print(f"[{i+1}/{len(mints)}] Сканируем холдеров {mint}...")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint]
        }
        try:
            resp = requests.post(url, json=payload).json()
            accounts = resp.get("result", {}).get("value", [])[:15] # Берем топ-15
            for acc in accounts:
                token_acc = acc["address"]
                owner = resolve_token_account_owner(token_acc)
                if owner and owner not in ["5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pT4028"]: # Raydium AMM
                    wallet_hits[owner].append(mint)
        except Exception as e:
            print(f"Ошибка с токеном {mint}: {e}")
        time.sleep(0.5) # Anti-spam
        
    print("\n🔍 Анализ завершен! Ищем совпадения...")
    insiders = []
    for wallet, coins in wallet_hits.items():
        if len(set(coins)) >= 2: # Купил минимум 2 ракеты
            insiders.append((wallet, len(set(coins))))
            
    insiders.sort(key=lambda x: x[1], reverse=True)
    
    if not insiders:
        print("Инсайдеров, купивших несколько ракет, не найдено.")
        return
        
    print(f"✅ Найдено {len(insiders)} смарт-кошельков!")
    
    with open("smart_wallets.txt", "a") as f:
        for wallet, count in insiders:
            print(f"🐋 {wallet} (Поймал {count} ракет)")
            f.write(f"{wallet}\n")
            
    print("Кошельки добавлены в smart_wallets.txt для Копитрейдера!")

if __name__ == "__main__":
    find_insiders()
