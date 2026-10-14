import csv
import json

def analyze_losses(path, label):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    
    losses = []
    wins = []
    for r in rows:
        if not r.get('pnl'):
            continue
        try:
            p = float(r['pnl'])
            if -500 <= p <= 10000:
                feat = json.loads(r['features']) if r.get('features') and r['features'].startswith('{') else {}
                entry = {**r, 'p': p, 'f': feat}
                if p <= 0:
                    losses.append(entry)
                else:
                    wins.append(entry)
        except Exception:
            pass
        
    print(f"=== {label}: {len(wins)} WINS vs {len(losses)} LOSSES ===")
    for group_name, grp in [('WINS', wins), ('LOSSES', losses)]:
        with_f = [x for x in grp if x['f']]
        if not with_f:
            continue
        avg_age = sum(x['f'].get('age_min', 0) for x in with_f) / len(with_f)
        avg_liq = sum(x['f'].get('liquidity', 0) for x in with_f) / len(with_f)
        avg_m5 = sum(x['f'].get('price_change_m5', 0) for x in with_f) / len(with_f)
        avg_ratio = sum(x['f'].get('buy_sell_ratio', 0) for x in with_f) / len(with_f)
        zero_links = sum(1 for x in with_f if x['f'].get('links_count', 0) == 0)
        avg_vol2liq = sum(x['f'].get('vol_to_liq', 0) for x in with_f) / len(with_f)
        
        print(f"{group_name} (N={len(with_f)}):")
        print(f"   Avg Age: {avg_age:.1f} min")
        print(f"   Avg Liquidity: ${avg_liq:,.0f}")
        print(f"   Avg m5 Change: {avg_m5:+.1f}%")
        print(f"   Avg Buy/Sell: {avg_ratio:.2f}")
        print(f"   Avg Vol/Liq: {avg_vol2liq:.2f}")
        print(f"   Zero Links: {zero_links}/{len(with_f)} ({zero_links/len(with_f)*100:.1f}%)")

analyze_losses('/Users/taya/.gemini/antigravity/brain/d9016886-f249-4dd9-a668-e0b6c651ed39/.user_uploaded/media_1792004203140_c46e1825.csv', 'File 1')
analyze_losses('/Users/taya/.gemini/antigravity/brain/d9016886-f249-4dd9-a668-e0b6c651ed39/.user_uploaded/media_1792004222572_5f5abf25.csv', 'File 2')
