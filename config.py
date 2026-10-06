import os
from dotenv import load_dotenv

load_dotenv()

# Dexscreener API (Оставляем для фоновых проверок портфеля, если нужно)
DEXSCREENER_LATEST = "https://api.dexscreener.com/token-boosts/top/v1" 
DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1" 
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/tokens/"

# Helius / PumpPortal WSS API
HELIUS_API_KEY = "9efda6f4-fddb-42d3-a2b1-098bbbecd299"
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
PUMPPORTAL_WSS = "wss://pumpportal.fun/api/data"

# RugCheck API
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

# Supabase DB Config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
INITIAL_BALANCE_USD = 120.0  # торговый пул ($150 депо - $30 газ)    # Стартовый капитал
REINVEST_PERCENT = 5.0  # Было 3%: цель $10/день требует сайз. 5% пула на сделку
VIRTUAL_POSITION_SIZE_USD = 4.0
TRADE_AMOUNT_USD = 10.0  # Было $6: базовый ордер $10 (комиссия $0.20 = всего 2% вместо 3.3%)
MAX_CONCURRENT_POSITIONS = 7  # Было 12: 12 открытых = деньги в мусоре. 7 — только лучшие входы
# держат слоты — иначе ракете некуда войти. Капитальный тормоз MAX_DEPLOYED_PCT=60% остаётся.

# Risk Management
MAX_DAILY_LOSS_USD = 12.0  # Было 18.0: уже -$22. При 100$ депо лимит 12% строже, стоп раньше
KILL_SWITCH_ENABLED = True  # PRO-дисциплина: -$18/день = стоп до завтра. Профи не отбиваются в красный день
KILL_SWITCH_PAUSE = 3600  # 1 час паузы после дневного убытка — не лезем обратно сразу в мусор
MAX_DAILY_LOSS_PCT = 0.25
STOP_LOSS_PCT = -0.25  # Расширили до -25%, чтобы выдерживать "высадку пассажиров" (раньше было -0.12 и ракеты отлетали на паузах)
SNIPER_ENTRIES_ENABLED = True  # Было False: Human/KOTH/HIGGS рождались на pump.fun. Снайпер ловит их на 80% кривой
TIME_EXIT_MINUTES = 60  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Trailing Stop Config (Защита прибыли)
TRAILING_ACTIVATION_PCT = 0.18  # Было 0.25: BUTTONS +37% отдали в -13%. Трейлинг раньше — прибыль не отдаём
TRAILING_DISTANCE_PCT = 0.08  # базовый для скальпа; раннеры после мунбэга едут шире (см. EVM_RUNNER_TRAIL)
# === RUNNER MODE (SHCAT +1126%/+627%, 中国人能飞 +152% держались ~5ч, пик==сейчас) ===
# При каких параметрах они выжили и фильтры их НЕ выбили — фиксируем как норму:
# pnl>+5%, maxp>+5%, без -20% краша за 60с, без -8% от пика. Новое: в прибыли время не режем,
# трейлинг расширяем, стопы от входа отключаем (только от пика).
EVM_RUNNER_MAXP = 0.50  # +50% пик = раннер: Dead/Stagnant/Stop от входа OFF, только трейлинг от пика
EVM_RUNNER_TRAIL = 0.60  # Было 0.25: РАКЕТАМ НУЖЕН ВОЗДУХ! Чтобы поймать +1000%, нужно терпеть откаты в -60% от пика.
EVM_MOONBAG_TRAIL = 0.40  # после мунбэга (деньги в кармане) даём дышать 40% вместо 20%
EVM_RUNNER_CRASH = 0.55  # Crash Guard для раннера: -55% за 60с (сильные вытряхивания)
EVM_NORMAL_CRASH = 0.35  # Crash Guard для обычных токенов: -35% за 60с вместо 20%
EVM_DEAD_MIN = 60  # было 30: флет-раннеры консолидируются часами перед выстрелом
EVM_STAGNANT_MIN = 20  # Было 45: HI/PLAGUE держались мёртвыми 25м-1.6ч. Режем флет быстрее

# Moonbag: частичная фиксация 50% позиции на этом профите.
# A/B 30д (идентичные данные из кэша): 50 vs 60 — точь-в-точь одинаково,
# потому что вертикали (+200%) проскакивают оба уровня за одну 5м-свечу.
# А живые SPIRITUAL (+55%) и BINF (+82% пологий) уперлись в потолок 60%:
# 50% их фиксирует (~+20% бленд вместо -10%/+6%). Ставим 0.50.
MOONBAG_TRIGGER_PCT = 0.35
# Stagnant (PRO: время по заслугам): показал импульс (пик 5%+) — минус режем
# на N-й минуте; даже не дёрнулся — кат на FAST-минуте, слот под следующий выстрел
STAGNANT_LOSS_MIN = 15
STAGNANT_LOSS_MIN_FAST = 7
STAGNANT_HOLD_MIN = 25

# Filtering
AI_MODE = "sniper" # Было "degen": PIL/AGENCYSOL/PLAGUE пропускались. Sniper требует 80%+ уверенности
# GROWTH: $20k. Практика показала: все лузеры (Saw/TIPPED/APE/VRAX#2) сидели
# в пулах ГЛУБЖЕ $30k — убил их вход на вершине, а не проскальзывание.
# Смерти от проскальзывания (sendor -74%) были из $12-15k — их $20k режет.
# При сайзах $4-10 позиция в $20k пуле = 0.03%, импакта нет.
MIN_LIQUIDITY = 20000  # Было 10000: пулы $10-20к дают PIL/PLAGUE-подобные -30%. Поднимаем защиту
MAX_LIQUIDITY = 50000000
# Было 20000: токены на стадии $10К (KOTH/EGO/HIGGS до выноса) скипались как микро-пулы.
# Сайз $4-6 в пуле $10К = 0.05%, проскальзывания нет (кэп 0.5% пула в main.py держит).
# Скам режут соцсети+mint/freeze+Jito+GoPlus, а не ликва.
# Внешние сигналы (TG-каналы): ВКЛ, но в наморднике — полный комплект гейтов
# в fomo_signal_loop (фильтры анализатора + RKT-вето + GoPlus/honeypot.is для EVM
# + символьный кулдаун + кэп сайза). Старые -70% были до гейтов.
USE_FOMO_SIGNALS = True
USE_COPYTRADE = True
TG_MAX_SIZE_USD = 3.0  # Было 4.0: TG-коллы на вершинах тоже сливают. Меньше сайз на чужие вершины
TG_VIP_SIZE_USD = 5.0  # Увеличили сайз на VIP-ракеты (как Buto), чтобы профит покрывал минусы
MAX_FOMO_BUYS_PER_PASS = 3  # не больше 3 покупок за проход очереди (бёрст-контроль)
# Robinhood Chain: ни GoPlus, ни honeypot.is сеть 4663 не знают (fail-open всегда).
# Единственная защита от honeypot-рага (-72% ZUPITER) — размер: кэп как у TG.
ROB_MAX_SIZE_USD = 3.0  # Было 4.0: ROB без honeypot-покрытия, LOTTERY +131% слилась. Меньше сайз
# Символьный кулдаун: после закрытия ЛЮБОЙ сделки тикер банится целиком
# (кейс VRAX: +5% → перезаход в клона с тем же именем → -32%).
# Mint-гарды клонов не видят (другой адрес), тикер — видят.
SYMBOL_REBUY_COOLDOWN_SEC = 4 * 3600  # 4ч, как mint-кулдаун в add_position
# === ROCKET-МОДЕЛЬ (XGBoost поверх entry-снапшота, метка: пик >= +50%) ===
# Fail-open: нет файла — гейты и сайзы как раньше. Модель только ранжирует:
# strong-сайз x1.5, weak-сайз x0.5, середина без изменений.
ROCKET_MODEL_PATH = "rocket_model.json"
ROCKET_PEAK_PCT = 0.50  # пик/entry-1 от которого сделка считается ракетой
ROCKET_MIN_TRAIN = 40  # минимум закрытых сделок с фичами для обучения (и ≥5 ракет)
ROCKET_STRONG = 0.65  # score >= → strong
ROCKET_WEAK = 0.35  # score <= → weak
ROCKET_SIZE_UP = 1.5
ROCKET_SIZE_DOWN = 0.5
ROCKET_VETO_ENABLED = True  # вето слабых дженерик-входов (MERRGER/MTA/LUXR: rule 77-90% + RKT 0.04-0.25 → стопы)
ROCKET_VETO_MIN = 0.40  # Было 0.20: PIL(94%)/AGENCYSOL(100%) прошли но слились. Ужесточаем RKT-порог
# === GOPLUS (общедоступная модель риска: honeypot/налоги; бесплатно, без ключа) ===
GOPLUS_ENABLED = True
GOPLUS_MAX_BUY_TAX = 0.10  # блок, если налог на покупку выше 10%
GOPLUS_MAX_SELL_TAX = 0.10  # блок, если налог на продажу выше 10% (иначе -30% на выходе)
GOPLUS_CACHE_SEC = 3600  # вердикты кэшируем на час (квота free бережётся)
# === HONEYPOT.IS (второй honeypot-гейт EVM; бесплатно, без ключа) ===
HONEYPOTIS_ENABLED = True  # второе мнение после GoPlus: ловит нестандартные transfer-ловушки
HONEYPOTIS_MAX_TAX = 0.10  # блок, если sim-налог выше 10%
HONEYPOTIS_CACHE_SEC = 3600
# === COINGECKO TRENDING (без ключей; минты для GROWTH-вселенной) ===
COINGECKO_ENABLED = True  # search/trending + резолв platforms.solana, кэш 30 мин

# AI Аналитика
GEMINI_API_KEY = "AQ.Ab8RN6Ju77t6DI8AYru7TGxuPuG_0WOcqHZqq1OBsDAwHtoJxg" # Получить бесплатно на https://aistudio.google.com/
# === СУДЬЯ НАРРАТИВА (читает мем, а не цифры; free tier) ===
GEMINI_MODEL = "gemini-2.0-flash"
MEME_JUDGE_ENABLED = True  # ADVISORY: только тег MEME85 в сигнал, входы не трогает
MEME_JUDGE_TTL_SEC = 6 * 3600  # нарратив за часы не меняется
MEME_JUDGE_COOLDOWN_SEC = 3600  # пауза после 429/quota

# Birdeye API Key (free: bds.birdeye.so, 30K CU/мес; token_trending = 50 CU/запрос)
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_INTERVAL = 1800  # Ротация 4 сетей по 1 запросу: ~48 запросов/сутки. На free (20/сутки) хватит частично - ключ спит без CU
BIRDEYE_CHAINS = ["robinhood", "solana", "robinhood", "base", "robinhood", "bsc"]  # ROB каждый второй тик (50% квоты)

# Jupiter Tokens API v2 (free key: portal.jup.ag): recent + toptrending/5m для Solana
JUP_API_KEY = os.getenv("JUP_API_KEY", "")
JUP_INTERVAL = 60  # Было 120: свежие Solana-токены каждые 60с — до выноса


# === JITO BLOCK ENGINE (MEV Protection) ===
USE_JITO_EXECUTION = True # Поставь True, когда будешь готов торговать на реальные деньги
JITO_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
JITO_TIP_AMOUNT_SOL = 0.0005 # Чаевые валидатору (минимум 0.0001)
JITO_TIP_ACCOUNT = "96gYZGLnJYVFmbjzopPSU6QiCRK4rPdTuQ8hB1aP442b" # Официальный Jito Tip Account
LUNARCRUSH_API_KEY = "syvh43mkvrzrw54sor6kc5r6dmtyj5fhi4jd38ht"

# === ROBINHOOD CHAIN (EVM мемы, Arbitrum Orbit L2) ===
# Chain ID 4663, slug DexScreener/GeckoTerminal: "robinhood". Проверено живьём 2026-09-21:
# boosts/profiles DexScreener отдают chainId=robinhood, RPC отвечает 0x1237.
ROBINHOOD_ENABLED = True
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_DS_SLUG = "robinhood"  # slug DexScreener (НЕ 4663 - тот вернёт пусто!)
ROBINHOOD_GT_NETWORK = "robinhood"  # slug GeckoTerminal
ROBINHOOD_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
ROBINHOOD_EXPLORER = "https://robinhoodchain.blockscout.com"
ROBINHOOD_MIN_LIQUIDITY = 10000  # Было 20000: стадия $10К должна проходить. Скам режут GoPlus/honeypot+ссылки, сайз мелкий
# $20k+ с сайзом $6 (0.03% пула) едут. Conviction-x2 по-прежнему только от $30k.
ROBINHOOD_SCAN_INTERVAL = 10  # Было 25→15: опрос каждые 10с = вход до выноса, не на вершине
EVM_TRACK_INTERVAL = 8  # Было захардкожено 12с: цены с ноды идут 1 батчем без лимитов DS,
# поэтому трек стопов/трейлингов можно крутить чаще — раньше режем раги, раньше фиксим пик
EVM_MIN_M5_PCT = 3.5  # Было 7.0: шире окно + вето 0.58 держит раги. Больше кандидатов в ракеты
EVM_RESCAN_COOLDOWN = 60  # Было 300→90: повторная проверка через 60с — окно ракеты минуты
EVM_NEW_POOL_PAGES = 10  # Было 8: глубже свежие пулы GT = больше ранних ракет (GT-кэш 60с держит квоту)
EVM_MAX_MINTS = 200  # Было 100→150: хвост выдачи не отрезается, ранние ракеты не теряем
# EVM-копитрейдинг: кошельки китов по сетям (0x...). Их входящие Transfer = покупки:
# токен летит в скан первым с меткой COPY. Пусто = выключено (нужны адреса!).
EVM_COPY_WALLETS = {"base": [], "bsc": [], "robinhood": [], "ethereum": []}
# fomoapi.io — независимое API данных fomo.family (топ-трейдеры, доски, WS-алерты).
# Бесплатный ключ: fomoapi.io/dashboard (250k кредитов/мес). Без ключа — демо WS с задержкой 60с.
FOMO_API_KEY = "fapi_72204b592a5944b18c2600f18b47b2a7f1b852230f7b4ffebc5ae1929dcbb3dc"
FOMO_WS_ENABLED = True
FOMO_FOLLOW_TRADERS = []  # ники топов, напр. ["whatever_fomo", "pricedin"] — их покупки в приоритет
FOMO_MIN_USD = 50  # Было 100: покупки китов от $50 ловим раньше — первый кит важнее крупного
FOMO_BOARDS_INTERVAL = 180  # Было 7200 (2ч!): тренды живут минуты. Опрос каждые 3 мин
# GMGN живой мост (headless-Chromium ловит create-сигналы trenches).
# ТЯЖЁЛЫЙ: +200-400MB RAM, на бесплатном Render не влезет. Включать на тарифе 2GB+
# (и в build добавить: playwright install chromium) либо локально.
GMGN_ENABLED = False
GMGN_CHAINS = ["sol", "base", "bsc", "robinhood"]

# === BASE (EVM L2, там сидят мемы с fomo.family: musebook, DELTA...) ===
# Тот же EVM-движок, slug DexScreener "base". Пулы глубже - порог $15к.
BASE_ENABLED = True
BASE_SCAN_INTERVAL = 10  # было 25→15: каждые 10с — до пампа

# === EVM WSS factory-listener (новые пулы в реальном времени, Base+BSC) ===
# Без ключей (PublicNode). Robinhood публичного WSS не даёт - только опрос.
EVM_WSS_ENABLED = True

# === BSC (GSTOCK и co с fomo.family сидят там) ===
# Тот же движок, slug "bsc".
BSC_ENABLED = True
BSC_SCAN_INTERVAL = 10  # было 25→15: каждые 10с — до пампа

# === ETHEREUM (KLIK: TG-SIGNAL:ETHEREUM висел на $0.00 — ни один луп его не вёл,
# chain=ethereum не входил в CHAINS. Тот же EVM-движок, slug DexScreener "ethereum".)
ETHEREUM_ENABLED = True  # луп трекинга: ведём существующие (KLIK), иначе висят на $0.00
ETHEREUM_ENTRIES_ENABLED = False  # НОВЫЕ входы в L1 выкл: газ $2-6 съедает скальп, ловим только дешёвые L2
ETHEREUM_SCAN_INTERVAL = 15
ETHEREUM_MIN_LIQUIDITY = 20000.0  # GROWTH: было 30k, синхронно с остальными EVM
# === ПРЯМЫЕ ЦЕНЫ С НОД (мимо лимитов DS/GT для открытых позиций) ===
# Трекинг идёт батчем eth_call getReserves с RPC ноды: 1 HTTP-батч на сеть за цикл.
# DS/GT остаются только для дискавери (там кэши). V4-пулы (весь ROB-uniswap) —
# DS-фолбэк (у ROB нет канонического PoolManager для чтения slot0 напрямую).
EVM_DIRECT_ENABLED = True
EVM_DIRECT_RPC = {
    "robinhood": ROBINHOOD_RPC_URL,
    "base": "https://base-rpc.publicnode.com",
    "bsc": "https://bsc-rpc.publicnode.com",
    "ethereum": "https://ethereum-rpc.publicnode.com",
}
# === КОМИССИИ ПО СЕТЯМ (paper-честность: L1-газ на порядок дороже L2/Solana) ===
# (fee_small <$10, fee_big, fee_emergency, cap_frac от позиции)
# Было везде одинаково $0.075/$0.45/$0.75 cap 5% — для Ethereum это враньё:
# реальный своп L1 $2-5, аварийный с приоритетом $6+.
EVM_CHAIN_FEES = {
    "solana": (0.075, 0.45, 0.75, 0.05),
    "robinhood": (0.075, 0.45, 0.75, 0.05),
    "base": (0.075, 0.45, 0.75, 0.05),
    "bsc": (0.075, 0.45, 0.75, 0.05),
    "ethereum": (2.0, 3.5, 6.0, 0.20),
}
ETHEREUM_MIN_SIZE_USD = 15.0  # меньше $15 в L1 не входим: газ съест любой скальп

# --- Сайзинг: база $6, conviction x2 ---
# Тир решает ПОДТВЕРЖДЁННЫЙ импульс рынка (m5 + вести), а не скор модели
# (модель всем ставит 100%, а катастрофы были именно VIP-100%)
CONVICTION_MULT = 2.0  # conviction-вход едет удвоенным
CONVICTION_MIN_M5_PCT = 0.20  # m5 от +20%
CONVICTION_MAX_M5_PCT = 0.70  # Было 1.20: x2 сайз на +120% = догон вершины. Conviction только +20-70%
CONVICTION_MIN_BUYSELL = 2.0  # покупки >= продаж x2
CONVICTION_MIN_LIQ = 30000  # пул от $30к
MAX_DEPLOYED_PCT = 0.60  # суммарно в рынке не больше 60% капитала (сдерживает тиринг)
RUG_REBUY_MAX_LOSS = -0.15  # был лосс хуже -15% по монете (HYDX -52%) - второй раз не входим, раги не оживают

# === INFANT DUMP (дев-дамп в первые минуты; SWORDGUY/LAP -15..-18% за 30-70с,
# AP ETH -18.6% за 16с — это шум импульса с 1000+ buys, а не раг; раги идут -40..-96%) ===
INFANT_WINDOW_MIN = 2  # было 3: окно уже — меньше rescues пропустим, раги всё равно ловим ниже
INFANT_DUMP_PCT = -0.18  # Было -0.25: ACP успел -48% за 1м. Дев-дамп режем раньше

# === ROCKET MODE (ловля ракет, а не скальпинг: прибыли расти, убытки резать) ===
BREAKEVEN_PCT = 0.20  # Было 0.30: пик +20% → стоп в безубыток. Не отдаём ракеты обратно в ноль/минус
DIPBUY_DROP_MIN = 0.10  # Расширено: ловим даже небольшие паузы в 10% у сильных ракет
DIPBUY_DROP_MAX = 0.45  # Расширено: допускаем глубокие высадки пассажиров до 45% перед вторым импульсом
DIPBUY_WINDOW_MIN = 30  # Расширено: помним пик 30 минут, ракеты часто консолидируются дольше
# Вайтлист точных минтов: BOME и ко — легитимные топ-мемы, мимикри-фильтр их не трогает.
# (Клоны с ДРУГИМ адресом по-прежнему режутся.) Задаётся ниже, после GROWTH_WATCHLIST.

# === GROWTH MODE (тренд старых токенов, пока нет ракет) ===
# Логика: ракеты ловятся импульсом m5, а зрелые капы едут часами. Отдельный режим:
# вход в откат часового тренда, широкие стопы, удержание часами, мало сделок.
GROWTH_ENABLED = True
GROWTH_WATCHLIST = [  # ликвидные Solana-капы (путать не с чем, рага не будет)
    "So11111111111111111111111111111111111111112",  # SOL
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",  # ORCA
]
# Вайтлист точных минтов для мимикри-фильтра (BOME и др. топы — легитимны,
# клоны с другим адресом режутся как раньше).
BRAND_WHITELIST_MINTS = list(GROWTH_WATCHLIST) + [
    "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",  # BOME (проверен по нашим сделкам)
]
GROWTH_MIN_LIQ = 200000  # пул от $200к - проскальзывания нет
GROWTH_MIN_H24_PCT = 8.0  # Было 5.0: входы на +7% гнили во флете (SPX/BOME -3%). Тренд от +8%
GROWTH_MIN_H1_PCT = 3.0  # Было 1.0: брали вялые +1.0-1.9%. Вход от +3%
GROWTH_MAX_H1_PCT = 15.0  # выше +15% за час - вершина, не гонимся
GROWTH_MAX_H6_PCT = 80.0  # выше +80% за 6ч - перегрев
GROWTH_MIN_BUYSELL = 1.2  # покупки >= продаж x1.2 за h1
GROWTH_STOP_PCT = -0.10  # широкий стоп -10% (шум часовок)
GROWTH_TRAIL_ACT = 0.08  # трейлинг с +8%
GROWTH_TRAIL_DIST = 0.12  # дистанция 12%
GROWTH_STAGNANT_MIN = 120  # флет режем через 2 часа, не 7 минут
GROWTH_MAX_POS = 3  # не больше 3 трендовых позиций
GROWTH_SIZE_USD = 6.0
GROWTH_INTERVAL = 300  # опрос вотчлиста каждые 5 мин

# === TG CALLS (живые коллы из Telegram-каналов) ===
# Бесплатно: API ID+Hash с https://my.telegram.org -> env TG_API_ID/TG_API_HASH.
# Первый запуск локально (создаст .session), дальше работает везде.
TG_ENABLED = False
TG_CHANNELS = ["lxetrades"]  # добавь свои: ["lxetrades", "calls_channel", ...]
TG_SESSION = "sniper_session"
# Бесключевой сборщик коллов через t.me/s/ превью (без Telethon и API-ключей).
# Проверено: pumpfunmemecalls отдаёт контракты. solanamemeradar закрылся - не добавлять.
TG_PREVIEW_ENABLED = False
# Проверены 30.09 (превью открыто у всех): pumpfunearlytrending даёт CA $15-36k,
# memecoinwhalespump — минты в тексте; CherryTrendingEVM/Cherry — адреса в href-кнопках
# (парсер их читает); остальные (short_cryptoo, hiro_trade, CrWhale, Trade_Nobody,
# cryptosmart_org1, WhAleir_fx, noiambilliolaurent, solearlytrending) — превью живо,
# контрактов в тексте нет (картинки/кнопки без адресов), держим на случай коллов.
TG_PREVIEW_CHANNELS = ["pumpfunmemecalls", "pumpfunearlytrending", "cherrytrending",
                       "Trade_Nobody", "noiambilliolaurent", "hiro_trade", "CrWhale",
                       "short_cryptoo", "CherryTrendingEVM", "WhAleir_fx",
                       "memecoinwhalespump", "cryptosmart_org1", "solearlytrending"]
TG_PREVIEW_INTERVAL = 60  # Было 90: коллы из TG-каналов забираем каждую минуту — до пампа, не после

# === USER WATCHLIST: ты видишь рано на fomo.family - кидаешь контракт сюда, бот исполняет ===
# Формат: ["mint_solana", "0xEVM..."]. Проверка теми же гейтами (скам не пройдет),
# но без ожидания бустов/трендов. Именно так ловятся AMERICA/YAP до +1000%.
USER_WATCHLIST = []

SNIPER_MIN_UNIQUE_BUYERS = 10
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 0.70  # Было 1.50: VIP-входы 83-100% на +100-150% m5 = вершины, все в стоп -20-53%. VIP только +10-70%
PULLBACK_MIN_M5_PCT = 0.03  # Было 0.05: расширяем сеть входа
PULLBACK_M1_MIN_PCT = -0.15  # Но и не летим в падающий нож (максимум -15% за минуту)
PULLBACK_M1_MAX_PCT = 0.05  # Было 0.00 - ждали идеальный откат. Ракеты летят с m1 > 0
PULLBACK_MAX_H1_PCT = 20.00  # Было 2.00: вход на +800%/час = догон SPEC-подобных вертикалей после выноса. Ракета берётся до +2000%/час
LOTTERY_MIN_M5_PCT = 0.80  # Лотерея только для мощных вертикалей от 80%
LOTTERY_SIZE_MULT = 0.50  # Было 0.15: Увеличили размер билета, чтобы прибыль с ракет перекрывала убытки от скама
WHALE_CONSENSUS = 2  # вход на 2-м ките (3-й = уже поздно)
WHALE_MAX_RUNUP = 1.25  # Было 1.55: вход на 2-м ките только если не убежал >25%. Киты уже надули = их выход, не наш вход
