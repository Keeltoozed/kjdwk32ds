import requests
import config

def test():
    mint = "4KsGXPQ6BZGgCdYVDrqacDUuhFhSf8TKfbjACcApgLPF"
    url = f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint]
    }
    resp = requests.post(url, json=payload).json()
    print(resp)

test()
