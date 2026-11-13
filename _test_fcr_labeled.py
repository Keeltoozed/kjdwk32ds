"""Полный прогон FCR по токенам из реальной разметки trades_raydium_rows.csv."""
import asyncio
import sys
import time

sys.path.insert(0, "/Users/taya/Downloads/kjdwk32ds-main--2")

import first_candle_rug_guard as fcr
from http_client import close_session

# mint -> (symbol, exit_reason, pnl)  — из trades_raydium_rows (16).csv
CASES = [
    ("3ZHz4gzF2AEzoyRkMe1nCbsCms5H5wg3jcphCP4opump", "CASH", "First Candle Rug Cut", -16.58),
    ("5Kcuiqh3ebktBAe8cBwPasKnR9izUu1AXs4BPefJpump", "TITTY", "First Candle Rug Cut", -16.56),
    ("ELbCDkRuUTuzPCR6CarMVtR1CYh71AzGhuZTBPePpump", "GIGAPIG", "First Candle Rug Cut", -13.71),
    ("8mMKCKDpvHXbNsN9Dvp6paJtMU7Sf1SnVsVQY28kpump", "FOOD CAT", "Early Liquidity Drain", -17.32),
    ("2FbqgFEWTwTRqZCemnf4AaP9tmhHKha3oqMjCNmcpump", "CSI", "Early Liquidity Drain", -19.48),
    ("LaZV2Qz2mzRVwN6xGcZJkG76ziquRmSU2TBy4gxpump", "AUTISM", "WIN +90.5%", 90.55),
    ("ByCUjGx4FkhK7ac8Vdqzb41yScUtvG78GBYSXKsMpump", "DCA", "Stalled +6.6%", 4.99),
    ("G9E8zzhdqCGpGqrHSgL2CKW1MuAbCVTQPFUhFk72pump", "CHARITY", "Dead Momentum", -10.97),
    ("2eBGQFFjmARa2kPECwLhMiuz7WZaSvgncH3nSruLpump", "TIMSEN", "Faded Pump", -1.60),
]


async def main():
    import market_data
    for mint, sym, reason, pnl in CASES:
        t0 = time.time()
        try:
            td = await market_data.get_token_data(mint)
            v = await fcr.pre_entry_rug_check(mint, pair_data=td, symbol=sym, use_cache=False)
            wa = v.get("wallet_analysis") or {}
            print("\n" + "=" * 74)
            print(f"{sym:12} pnl={pnl:+7.2f}  {reason}")
            print(v["report"])
            print(f"   >> {round(time.time()-t0,1)}s verdict={v['verdict']} score={v['score']} "
                  f"cluster={wa.get('cluster_score')} micro={wa.get('micro_score')} "
                  f"hard={wa.get('hard_score')} graph_clusters={wa.get('graph_cluster_count')}")
            for c in (v.get("clusters") or [])[:3]:
                print(f"   >> [{c.get('source','funding')}] n={c['wallet_count']} "
                      f"pct={c['total_pct']}% sol={c.get('total_amount_sol')} "
                      f"burst={c.get('funding_burst')} launch_prep={c.get('launch_prep_count')} "
                      f"creator={c.get('has_creator')}")
        except Exception as e:
            import traceback
            print(f"ERR {sym}: {type(e).__name__}: {e}")
            traceback.print_exc()
    await close_session()


asyncio.run(main())
