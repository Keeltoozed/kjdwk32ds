import requests
import json

HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
tx_sig = "3KqazcT4Udc7J5pPqyqPXSdHBow7qckeWkt4VcZcivi36pjwHCZ7Yra2EnsYd7ES4EH2AVnToq8zsTXVZCtDenCJ"

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getTransaction",
    "params": [
        tx_sig,
        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
    ]
}

res = requests.post(HELIUS_RPC, json=payload).json()
if "result" in res and res["result"]:
    print(json.dumps(res["result"]["meta"]["postTokenBalances"], indent=2))
