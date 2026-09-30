"""Публичный baseline для ROCKET-модели (ловит ракеты с первого дня).

Проблема: train_rocket.py ждёт >=40 СВОИХ закрытых сделок (и >=5 ракет),
которых нет месяцами, — rocket_model.json не существует, весь ROCKET-контур
(_rkt_tag, rocket_size_mult x1.5/x0.5) мёртв и всегда возвращает нейтраль.

Решение: открытый seed-датасет из двух публичных источников —
1. Архетипы исторических 100x (WIF/BOME/MYRO, ранние часы lifecycle —
   то же знание, что лежит в moonshot_builder.py): entry-снапшоты,
   за которыми шли +50%+ движения. Метка 1.
2. Пыль/дампы/перегретые вершины — состояния, которые наши гейты режут
   (micro-liq, dump, overheat) + смерти из прод-логов. Метка 0.

Детерминировано (seed=42), без сети. Свои сделки позже домешивает
train_rocket.py (вес x2) и модель дрейфует к НАШЕМУ рынку.

Вектор — канонический порядок rocket_model.FEATURES (18 шт).
Запуск: python3 rocket_seed.py  → обучает и пишет rocket_model.json.
"""
import math
import random

import config
import rocket_model as rm

SEED = int(getattr(config, "ROCKET_SEED_SEED", 42) or 42)
N_POS = int(getattr(config, "ROCKET_SEED_POS", 120) or 120)
N_NEG = int(getattr(config, "ROCKET_SEED_NEG", 180) or 180)


def _vec(m5, m1, h1, h24, b5, s5, bh, sh, vm5, vh, liq, fdv, age, links):
    bs_m5 = (b5 / (s5 + 1)) if (b5 + s5) > 0 else (bh / (sh + 1) if (bh + sh) > 0 else 0.0)
    bs_h1 = (bh / (sh + 1)) if (bh + sh) > 0 else 0.0
    return [
        float(m5), float(m1), float(h1), float(h24),
        float(b5), float(s5), float(bh), float(sh),
        float(bs_m5), float(bs_h1),
        math.log1p(max(0.0, vm5)), math.log1p(max(0.0, vh)),
        ((vm5 / (liq + 1)) if vm5 else (vh / (liq + 1))),
        math.log1p(max(0.0, liq)), math.log1p(max(0.0, fdv)),
        math.log1p(min(max(0.0, age), 4320.0)), float(links),
    ]


def _positives(rng, n):
    """Entry-снапшоты перед +50%+: здоровый импульс + давление + живые деньги.
    ~20% — dip-входы (m5 в минусе, h1 сильный, buys давят): NFLOAT -21% → +400%,
    CSI -32% → +114%."""
    out = []
    for _ in range(n):
        # Дипы — наш рабочий край (NFLOAT/CSI заходили из отката),
        # но без фанатизма: гонка за одной синтетической точкой distortит
        # модель. Стратегию защищает интеграция (DIPBUY скипает mult).
        if rng.random() < 0.30:
            m5 = rng.uniform(-35, -3)      # откат после вертикали (CSI -32% → +114%)
            h1 = rng.uniform(15, 70)       # тренд жив
            h24 = rng.uniform(30, 200)
            m1 = rng.uniform(-4, 2)
            # Дип: продавцы ещё на месте, давление умеренное (CSI: 1.92).
            # Граница ракета/мусор здесь ~1.2-1.5, а не 2.5+ как у импульсов.
            s5 = rng.uniform(5, 80)
            b5 = s5 * rng.uniform(1.2, 3.5)
            sh = rng.uniform(50, 800)
            bh = sh * rng.uniform(1.2, 3.5)
            liq = rng.uniform(15000, 400000)
            vm5 = liq * rng.uniform(0.05, 0.8)
            vh = liq * rng.uniform(0.5, 4.0)
            out.append(_vec(m5, m1, h1, h24, b5, s5, bh, sh, vm5, vh,
                            liq, liq * rng.uniform(3, 15),
                            rng.uniform(5, 600), rng.choice([1, 1, 2, 2, 3])))
            continue
        else:
            m5 = rng.uniform(5, 45)        # импульс, не вертикаль
            m1 = rng.uniform(-3, 3)
            h1 = rng.uniform(8, 80)
            h24 = rng.uniform(20, 200)
        s5 = rng.uniform(5, 80)
        b5 = s5 * rng.uniform(1.5, 5.0)   # давление покупателей
        sh = rng.uniform(50, 800)
        bh = sh * rng.uniform(1.5, 4.0)
        liq = rng.uniform(15000, 400000)
        vm5 = liq * rng.uniform(0.05, 0.8)
        vh = liq * rng.uniform(0.5, 4.0)
        out.append(_vec(m5, m1, h1, h24, b5, s5, bh, sh, vm5, vh,
                        liq, liq * rng.uniform(3, 15),
                        rng.uniform(5, 600), rng.choice([1, 1, 2, 2, 3])))
    return out


def _negatives(rng, n):
    """Пыль, дампы, вершины, тонкие без ссылок, флет — всё, что режут гейты."""
    out = []
    kinds = (["dust"] * 3 + ["dump"] * 2 + ["top"] * 2 + ["thin"] * 2
             + ["flat"] + ["weakdip"] * 2)
    for _ in range(n):
        k = rng.choice(kinds)
        if k == "dust":  # свежая пыль BSC: денег нет, сделок нет
            liq = rng.uniform(0, 3000)
            b5, s5, bh, sh = rng.uniform(0, 3), rng.uniform(0, 2), rng.uniform(0, 10), rng.uniform(0, 8)
            out.append(_vec(rng.uniform(-2, 2), rng.uniform(-1, 1), rng.uniform(-5, 5),
                            rng.uniform(-10, 10), b5, s5, bh, sh,
                            rng.uniform(0, 500), rng.uniform(0, 3000), liq,
                            liq * rng.uniform(3, 10), rng.uniform(1, 120),
                            rng.choice([0, 0, 1])))
        elif k == "dump":  # нож: продажи доминируют
            s5 = rng.uniform(20, 150)
            b5 = s5 * rng.uniform(0.1, 0.6)
            sh = rng.uniform(100, 1000)
            bh = sh * rng.uniform(0.2, 0.7)
            liq = rng.uniform(5000, 60000)
            # Дампы и вершины ЧАСТО со ссылками (реальные пампы со сайтом!):
            # ссылки без импульса/давления — не ракета. Учим это явно.
            out.append(_vec(rng.uniform(-60, -12), rng.uniform(-15, -2),
                            rng.uniform(-70, -10), rng.uniform(-80, 0),
                            b5, s5, bh, sh, rng.uniform(1000, 20000),
                            rng.uniform(5000, 100000), liq, liq * rng.uniform(3, 12),
                            rng.uniform(10, 2000), rng.choice([1, 2, 2, 3])))
        elif k == "top":  # перегрев: вертикаль уже прошла (SKYAI-кейсы)
            liq = rng.uniform(10000, 200000)
            b5 = rng.uniform(30, 300)
            s5 = b5 * rng.uniform(0.4, 1.0)
            out.append(_vec(rng.uniform(70, 250), rng.uniform(5, 30),
                            rng.uniform(100, 800), rng.uniform(600, 20000),
                            b5, s5, b5 * rng.uniform(3, 8), s5 * rng.uniform(3, 8),
                            rng.uniform(10000, 200000), rng.uniform(100000, 2000000),
                            liq, liq * rng.uniform(3, 12), rng.uniform(10, 1500),
                            rng.choice([1, 2, 2, 3])))
        elif k == "thin":  # тонкий без ссылок: скам-риск
            liq = rng.uniform(3000, 25000)
            s5 = rng.uniform(5, 40)
            b5 = s5 * rng.uniform(0.5, 1.2)
            out.append(_vec(rng.uniform(-5, 25), rng.uniform(-4, 4),
                            rng.uniform(-10, 60), rng.uniform(-20, 150),
                            b5, s5, b5 * 4, s5 * 4,
                            rng.uniform(500, 8000), rng.uniform(3000, 40000),
                            liq, liq * rng.uniform(3, 10), rng.uniform(5, 1000), 0))
        elif k == "weakdip":  # пограничник: форма дипа, но давления нет
            # (покупателей ~ как продавцов) — отскока не будет, это стекание.
            # Ставит границу ракета/мусор около ratio 1.2-1.5, а не 2.7.
            liq = rng.uniform(15000, 100000)
            s5 = rng.uniform(10, 80)
            b5 = s5 * rng.uniform(0.7, 1.3)
            sh = rng.uniform(80, 600)
            bh = sh * rng.uniform(0.7, 1.3)
            out.append(_vec(rng.uniform(-35, -5), rng.uniform(-4, 2),
                            rng.uniform(-10, 40), rng.uniform(-10, 120),
                            b5, s5, bh, sh, rng.uniform(2000, 20000),
                            rng.uniform(10000, 120000), liq, liq * rng.uniform(3, 12),
                            rng.uniform(10, 800), rng.choice([1, 2, 2, 3])))
        else:  # flat: флет съест комиссиями
            liq = rng.uniform(5000, 80000)
            b5, s5 = rng.uniform(0, 8), rng.uniform(0, 8)
            out.append(_vec(rng.uniform(-2, 2), rng.uniform(-1, 1),
                            rng.uniform(-5, 5), rng.uniform(-15, 15),
                            b5, s5, b5 * 5 + 1, s5 * 5 + 1,
                            rng.uniform(0, 1000), rng.uniform(1000, 15000),
                            liq, liq * rng.uniform(3, 10), rng.uniform(10, 3000),
                            rng.choice([0, 1, 2])))
    return out


def build_dataset(n_pos=N_POS, n_neg=N_NEG, seed=SEED):
    """Детерминированный seed-датасет: ([vec], [label])."""
    rng = random.Random(seed)
    X = _positives(rng, n_pos) + _negatives(rng, n_neg)
    y = [1] * n_pos + [0] * n_neg
    idx = list(range(len(X)))
    rng.shuffle(idx)
    return [X[i] for i in idx], [y[i] for i in idx]


def main():
    X, y = build_dataset()
    path = getattr(config, "ROCKET_MODEL_PATH", "rocket_model.json")
    m = rm.train(X, y, path)
    print(f"🚀 ROCKET baseline (public seed): n={m['n']} rockets={m['rockets']} AUC={m['auc']} → {path}")
    top = sorted(m["importance"].items(), key=lambda kv: -kv[1])[:5]
    print("   Топ-фичи:", ", ".join(f"{k}={v}" for k, v in top))
    if m["auc"] < 0.80:
        print("⚠️ AUC низкий для seed — проверь диапазоны.")
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
