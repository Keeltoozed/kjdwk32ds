import requests
import json
import time

HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
ligma = "AEXbqWmEHY4wXS1XeLfieBDh3ZEvVqkAdDzdaMAwpump"

# Get last 50 signatures for LIGMA
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [ligma, {"limit": 50}]
}
res = requests.post(HELIUS_RPC, json=payload).json()
if "result" in res and res["result"]:
    print(f"Got {len(res['result'])} signatures for LIGMA")
    
    # We will fetch transactions and print the buyers (people who received LIGMA)
    sigs = [r["signature"] for r in res["result"]]
    
    for i in range(0, min(10, len(sigs))):
        sig = sigs[i]
        p2 = {
            "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }
        tx_res = requests.post(HELIUS_RPC, json=p2).json()
        if "result" in tx_res and tx_res["result"]:
            meta = tx_res["result"].get("meta", {})
            pre = meta.get("preTokenBalances", [])
            post = meta.get("postTokenBalances", [])
            
            # Find users whose LIGMA balance increased
            owners = set([b.get("owner") for b in post if b.get("mint") == ligma])
            for owner in owners:
                pre_amt = next((float(b["uiTokenAmount"]["uiAmountString"]) for b in pre if b.get("owner") == owner and b.get("mint") == ligma), 0.0)
                post_amt = next((float(b["uiTokenAmount"]["uiAmountString"]) for b in post if b.get("owner") == owner and b.get("mint") == ligma), 0.0)
                if post_amt > pre_amt:
                    print(f"BUYER of LIGMA: {owner} (+{post_amt - pre_amt} LIGMA)")

