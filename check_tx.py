import requests
import json

HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
sig = "61ozasKnbcw2oFDZxqBn1qxq6tzGoHN7xaP4VP3LmwY6vqHqGeFo1LdVx5jpsCgQSizbnJJPNX4NK1HvCMtNcadN"
wallet = "GFRjGNXY8JrGSPC46inqrH4XPdUFMDLkE1oNm1nXiPsJ"

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getTransaction",
    "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
}
res = requests.post(HELIUS_RPC, json=payload).json()

if "result" in res and res["result"]:
    meta = res["result"].get("meta", {})
    
    # check accounts
    accounts = [k["pubkey"] for k in res["result"]["transaction"]["message"]["accountKeys"]]
    print(f"Is wallet in accounts? {wallet in accounts}")
    
    pre = meta.get("preTokenBalances", [])
    post = meta.get("postTokenBalances", [])
    
    pre_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in pre if b.get("owner") == wallet}
    post_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in post if b.get("owner") == wallet}
    
    print("Pre:", pre_dict)
    print("Post:", post_dict)
    
