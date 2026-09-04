import requests
import json
import time

HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
wallets = [
    "5FGoPPj1nL8LCnfVnpTmreqQtqLuMXXAwuS1uahMrp8V", # @DumbCrayonEater
    "2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V", # @Salem1299534
    "DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39", # @Natan_benish
    "GFRjGNXY8JrGSPC46inqrH4XPdUFMDLkE1oNm1nXiPsJ", # @brrrgrrrz
    "7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9"  # @notanicecat69
]

for w in wallets:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            w,
            {"limit": 1}
        ]
    }
    try:
        res = requests.post(HELIUS_RPC, json=payload).json()
        if "result" in res and res["result"]:
            sig = res["result"][0]
            # check how long ago it was
            age = time.time() - sig.get("blockTime", 0)
            print(f"Wallet {w}: Last tx {age/60:.1f} mins ago. Sig: {sig['signature']}")
        else:
            print(f"Wallet {w}: No recent tx.")
    except Exception as e:
        print(e)

