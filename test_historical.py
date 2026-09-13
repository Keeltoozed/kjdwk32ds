import requests

mint = "GVBYpmPd7hmNFEMTNGhyoNc4RbqAf64aEwYVs4o6pump" # from dataset

# Get pool address
resp = requests.get(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}")
if resp.status_code == 200:
    data = resp.json()
    pools = data.get("data", {}).get("relationships", {}).get("top_pools", {}).get("data", [])
    if pools:
        pool_id = pools[0]["id"].split("_")[1]
        print(f"Pool ID: {pool_id}")
        
        # Get trades
        trades_resp = requests.get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_id}/trades")
        if trades_resp.status_code == 200:
            trades = trades_resp.json().get("data", [])
            print(f"Got {len(trades)} trades!")
            if trades:
                print(trades[0]["attributes"]["price_in_usd"])
