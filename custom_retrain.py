import json
import pandas as pd
import xgboost as xgb

rejected_json = """[{"mint":"14WQz2ZXETJPMA2SeArA4DQpUSuitNqRyb94m4AGpump","rejected_at":"2026-09-09 05:11:40.544259","reason":"FOMO XGBoost low score: 2.4%","score":2.35014,"price_at_rejection":0.000004978,"features":"{\\"price_change_m5\\": -0.28, \\"volume_m5\\": 6.33, \\"buys_m5\\": 0.0, \\"sells_m5\\": 2.0, \\"liquidity\\": 0.0, \\"fdv\\": 4978.28, \\"buy_sell_ratio\\": 0.0, \\"vol_to_liq\\": 6.33}","ath_price":0.000004307,"hypothetical_pnl":-13.4793,"check_1h_done":1,"check_4h_done":1,"check_24h_done":1},{"mint":"215MkqNg6G17U8Ys9aSh21M8rR68F2vszHqq8iNJTStk","rejected_at":"2026-09-11 12:31:20.082949","reason":"FOMO XGBoost low score: 23.2%","score":23.1525,"price_at_rejection":0.00001949,"features":"{\\"price_change_h24\\": -66.48, \\"volume_h24\\": 54565.44, \\"buys_h24\\": 546.0, \\"sells_h24\\": 295.0, \\"liquidity\\": 11358.65, \\"fdv\\": 19499.0, \\"buy_sell_ratio\\": 1.8445945945945945, \\"vol_to_liq\\": 4.803443768073841}","ath_price":0.00005969,"hypothetical_pnl":206.26,"check_1h_done":1,"check_4h_done":1,"check_24h_done":1},{"mint":"4KsGXPQ6BZGgCdYVDrqacDUuhFhSf8TKfbjACcApgLPF","rejected_at":"2026-09-09 05:11:30.640679","reason":"FOMO XGBoost low score: 55.7%","score":55.6573,"price_at_rejection":0.00007718,"features":"{\\"price_change_m5\\": 75.53, \\"volume_m5\\": 49630.58, \\"buys_m5\\": 318.0, \\"sells_m5\\": 292.0, \\"liquidity\\": 23166.2, \\"fdv\\": 77180.0, \\"buy_sell_ratio\\": 1.0853242320819112, \\"vol_to_liq\\": 2.142277875617252}","ath_price":0.008891,"hypothetical_pnl":11419.8,"check_1h_done":1,"check_4h_done":1,"check_24h_done":1},{"mint":"5cNaeFkSqLGVE3BJufKVAzJtxkH2zHhKu3Ao7PCipump","rejected_at":"2026-09-09 05:12:22.940031","reason":"FOMO XGBoost low score: 5.3%","score":5.34131,"price_at_rejection":0.000002191,"features":"{\\"price_change_m5\\": 0.0, \\"volume_m5\\": 0.0, \\"buys_m5\\": 0.0, \\"sells_m5\\": 0.0, \\"liquidity\\": 2467.7, \\"fdv\\": 2170.0, \\"buy_sell_ratio\\": 0.0, \\"vol_to_liq\\": 0.0}","ath_price":0.000042,"hypothetical_pnl":1816.93,"check_1h_done":1,"check_4h_done":1,"check_24h_done":1}]"""

trades_json = """[{"mint":"2pRVUtGgbUpFVtY9dFhH5pLeyJZX6vhmaG89uXgcpump","pnl":8.16278},{"mint":"5GefefPX1mDs6ZJB1apYmz6fCTCiNJpHturZ9bvFpump","pnl":-101.875},{"mint":"6YPeWTjSXzrTUrV8K938Biu2LpdE6mYYxjZzsWqvXA4Q","pnl":33.3976},{"mint":"5aC7DM8QPhJnxWuuvxB8r6g99qHhCwBF45XQWMorpump","pnl":-91.2889},{"mint":"H118HpvTMPGno3rSiq2Bx6GF6WMcUyyGUzWSfw24pump","pnl":53.8815},{"mint":"J1U1BWkVUDtujjkew4fCGwvUteeaJwDirEkuPcYCgALL","pnl":57.3632},{"mint":"FLk6FKAN26m1FT4ucguwy3uHBMzLKcEu8KMTYD2Zpump","pnl":442.119},{"mint":"GKCvJmW9vgsrvQMsYBySFVcvtVuGeHN6M5B54DPCpump","pnl":103.806}]"""

rejected = json.loads(rejected_json)
trades = json.loads(trades_json)

df_base = pd.read_csv("pump_dataset.csv")

features_list = []
for item in rejected:
    if item['hypothetical_pnl'] > 100:  # Missed rocket! target = 1
        try:
            f = json.loads(item['features'])
            f['target'] = 1
            features_list.append(f)
        except: pass
    elif item['hypothetical_pnl'] < -50: # Correctly rejected! target = 0
        try:
            f = json.loads(item['features'])
            f['target'] = 0
            features_list.append(f)
        except: pass

# I am adding dummy logic for trades because we need their features, but we don't have them in the abbreviated JSON.
# Wait, I DO have features for rejected tokens in the prompt!
# I will just write a simpler parser that extracts the full JSON array from my current transcript if needed, 
# or I will just trust the user's prompt contains the whole JSON arrays.

