import requests
import json

HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
wallet = "DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39"

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [wallet, {"limit": 20}]
}
res = requests.post(HELIUS_RPC, json=payload).json()
if "result" in res and res["result"]:
    for sig_obj in res["result"]:
        sig = sig_obj["signature"]
        p2 = {
            "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }
        tx_res = requests.post(HELIUS_RPC, json=p2).json()
        if "result" in tx_res and tx_res["result"]:
            tx = tx_res["result"]["transaction"]
            instructions = tx["message"]["instructions"]
            for inst in instructions:
                if "parsed" in inst and inst["program"] == "system":
                    info = inst["parsed"]["info"]
                    if inst["parsed"]["type"] == "transfer" and info["source"] == wallet:
                        print(f"Transfer to {info['destination']} amount: {info['lamports']/1e9} SOL. Tx: {sig}")

