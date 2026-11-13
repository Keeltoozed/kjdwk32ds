"""
Детерминированный тест FCR: проверяем, что кластеры «одного источника финансирования»
корректно находятся и что вердикт становится BLOCK.

Финансирование подменяется моком — проверяем чистую логику кластеризации и скоринга.
"""
import asyncio
import sys

sys.path.insert(0, "/Users/taya/Downloads/kjdwk32ds-main--2")

import first_candle_rug_guard as fcr

MINT = "So11111111111111111111111111111111111111112"
DEV_FUNDER = "DeVFundeR1111111111111111111111111111111"
CLEAN_FUNDER = "C1eanFundeR11111111111111111111111111111"
EXCHANGE_FUNDER = "5tzFkiKscXHK5ZXCGbXZxdw7gTizD5M3EpJ1SKhK4Eis"

W1 = "W1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
W2 = "W2bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
W3 = "W3ccccccccccccccccccccccccccccccccc"
W4 = "W4ddddddddddddddddddddddddddddddddd"


def mock_funding(funder_map, amounts=None, ts_map=None, tx_map=None, touched=None):
    amounts = amounts or {}
    ts_map = ts_map or {}
    tx_map = tx_map or {}
    touched = touched or set()

    async def _fake(wallet_list, mint, priority=None):
        out = {}
        for wallet in wallet_list:
            out[wallet] = {
                "wallet": wallet,
                "funder": funder_map.get(wallet),
                "amount_sol": float(amounts.get(wallet, 1.0)),
                "funding_ts": int(ts_map.get(wallet, 1_700_000_000)),
                "tx_count": int(tx_map.get(wallet, 3)),
                "genesis_sig": "sig",
                "oldest_txs": ["sig", "sig2"],
                "touched_mint": wallet in touched,
                "error": None,
            }
        return out

    return _fake


async def run_case(name, cohort, funder_map, expect_block, **kw):
    orig = fcr._trace_all_funders
    orig_cohort = fcr.build_cohort
    fcr._trace_all_funders = mock_funding(funder_map, **kw)

    async def _fake_cohort(mint, pair_data=None):
        return [dict(c) for c in cohort]

    fcr.build_cohort = _fake_cohort
    fcr._clear_cache()
    try:
        v = await fcr.pre_entry_rug_check(MINT, pair_data={}, symbol=name, use_cache=False)
    finally:
        fcr._trace_all_funders = orig
        fcr.build_cohort = orig_cohort
        fcr._clear_cache()

    wa = v["wallet_analysis"]
    largest = wa.get("largest_cluster") or {}
    ok = (v["verdict"] == "BLOCK") == expect_block
    print(f"\n{'✅' if ok else '❌'} {name}: verdict={v['verdict']} score={v['score']} "
          f"(ожидали {'BLOCK' if expect_block else 'не BLOCK'})")
    print(f"   кластер: n={largest.get('wallet_count')} pct={largest.get('total_pct')} "
          f"sol={largest.get('total_amount_sol')} burst={largest.get('funding_burst')} "
          f"equal={largest.get('equal_funding_ratio')} launch_prep={largest.get('launch_prep_count')} "
          f"fresh={largest.get('fresh_wallet_count')}")
    for r in v["reasons"][:5]:
        print(f"   ↳ {r}")
    return ok


async def main():
    results = []
    cohort = [
        {"wallet": W1, "pct": 6.0, "source": "top_holder"},
        {"wallet": W2, "pct": 4.0, "source": "top_holder"},
        {"wallet": W3, "pct": 2.0, "source": "top_holder"},
        {"wallet": W4, "pct": 3.0, "source": "top_holder"},
    ]
    dev_map = {W1: DEV_FUNDER, W2: DEV_FUNDER, W3: DEV_FUNDER, W4: CLEAN_FUNDER}
    T0 = 1_700_000_000

    # 1. СИБИЛ-ФЕРМА: 3 кошелька от одного источника, держат 12% эмиссии,
    #    профинансированы бурстом (в пределах 60с) одинаковыми суммами
    results.append(await run_case(
        "Сибил-ферма: 3 кошелька / 12% / бурст раздачи", cohort, dev_map, True,
        amounts={W1: 2.5, W2: 2.5, W3: 2.5, W4: 1.0},
        ts_map={W1: T0, W2: T0 + 30, W3: T0 + 60, W4: T0},
    ))

    # 2. То же + launch-prep (первым делом купили токен) — ещё опаснее
    results.append(await run_case(
        "Сибил-ферма + launch-prep", cohort, dev_map, True,
        amounts={W1: 2.5, W2: 2.5, W3: 2.5, W4: 1.0},
        ts_map={W1: T0, W2: T0 + 30, W3: T0 + 60, W4: T0},
        touched={W1, W2},
    ))

    # 3. Чистые кошельки с РАЗНЫМИ источниками => не блокируем
    fm_clean = {w: f"Funder{i}11111111111111111111111111111111" for i, w in enumerate(dev_map)}
    results.append(await run_case(
        "Разные источники финансирования", cohort, fm_clean, False,
        amounts={w: 1.0 for w in dev_map},
    ))

    # 4. Честные люди с общей биржей: профинансированы за недели, кошельки не одноразовые
    fm_ex = {W1: EXCHANGE_FUNDER, W2: EXCHANGE_FUNDER, W3: EXCHANGE_FUNDER, W4: CLEAN_FUNDER}
    results.append(await run_case(
        "3 кошелька с одной биржи (выводы за недели) — не блокируем", cohort, fm_ex, False,
        amounts={W1: 1.3, W2: 4.7, W3: 0.8, W4: 1.0},
        ts_map={W1: T0, W2: T0 + 86400 * 5, W3: T0 + 86400 * 12, W4: T0},
        tx_map={W1: 210, W2: 84, W3: 33, W4: 12},
    ))

    # 5. Источник = DEX/агрегатор => игнорируем
    fm_dex = {w: fcr._KNOWN_LIQUIDITY_SOURCES.__iter__().__next__() for w in dev_map}
    results.append(await run_case(
        "Источник = DEX (игнорируем)", cohort, fm_dex, False,
        amounts={w: 1.0 for w in dev_map},
    ))

    # 6. Кластер из 2 кошельков на 5% => предупреждение, не блок
    cohort2 = [
        {"wallet": W1, "pct": 3.0, "source": "top_holder"},
        {"wallet": W2, "pct": 2.0, "source": "top_holder"},
    ]
    fm2 = {W1: DEV_FUNDER, W2: DEV_FUNDER}
    results.append(await run_case(
        "Малый кластер 2 кошелька / 5% => WARN, не BLOCK", cohort2, fm2, False,
        amounts={W1: 2.0, W2: 2.0},
        ts_map={W1: T0, W2: T0 + 20},
    ))

    # 7. Dev держит 30% эмиссии => блок по рыночным сигналам
    cohort3 = [
        {"wallet": W1, "pct": 30.0, "source": "creator"},
        {"wallet": W2, "pct": 4.0, "source": "top_holder"},
    ]
    fm3 = {W1: DEV_FUNDER, W2: CLEAN_FUNDER}
    results.append(await run_case(
        "Дев держит 30% эмиссии => BLOCK", cohort3, fm3, True,
        amounts={W1: 5.0, W2: 1.0},
    ))

    print("\n" + "=" * 60)
    print(f"ПРОЙДЕНО: {sum(1 for r in results if r)}/{len(results)}")
    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))

