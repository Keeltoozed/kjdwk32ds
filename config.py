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
def _clean_supabase_url(raw: str) -> str:
    u = (raw or "").strip().strip('"').strip("'").rstrip("/")
    # Частая ошибка: вставляют URL с суффиксом /rest/v1, /auth/v1 или dashboard-ссылку.
    # PostgREST тогда отвечает PGRST125 Invalid path. Отрезаем до https://xxx.supabase.co
    for suffix in ("/rest/v1", "/auth/v1", "/realtime/v1", "/storage/v1"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    # dashboard-ссылка вида https://supabase.com/dashboard/project/<ref> -> чиним
    if "supabase.com/dashboard/project/" in u:
        try:
            ref = u.split("/dashboard/project/")[1].split("/")[0].split("?")[0]
            if ref:
                return f"https://{ref}.supabase.co"
        except Exception:
            pass
    return u

SUPABASE_URL = _clean_supabase_url(os.getenv("SUPABASE_URL"))
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip().strip('"').strip("'")

# Paper Trading Config
PAPER_PORTFOLIO_FILE = "portfolio.json"
# Ключ слепка в Supabase. ВАЖНО: у каждого бота свой, иначе два бота на одной
# базе перезаписывают друг друга (сделки появляются и пропадают).
# Задай в Render → Environment: первый бот PF_IDEBOT, второй PF_QYO7.
PORTFOLIO_SNAPSHOT_MINT = (os.getenv("PORTFOLIO_SNAPSHOT_MINT", "") or "").strip().strip('"').strip("'") or "PORTFOLIO_STATE_V2.4"
INITIAL_BALANCE_USD = 120.0  # торговый пул ($150 депо - $30 газ)    # Стартовый капитал
REINVEST_PERCENT = 5.0  # Было 3%: цель $10/день требует сайз. 5% пула на сделку
VIRTUAL_POSITION_SIZE_USD = 15.0
TRADE_AMOUNT_USD = 15.0  # Базовый ордер $15
# === ДИНАМИЧЕСКИЙ СТУПЕНЧАТЫЙ РЕИНВЕСТ И ЗАЩИТА ЛИКВИДНОСТИ ===
DYNAMIC_REINVEST_ENABLED = True      # Включить ступенчатый реинвест прибыли
BASE_TRADE_AMOUNT_USD = 15.0         # Базовый размер сделки (минимальный пол $15)
MAX_POSITION_SIZE_USD = 25.0         # Жесткий потолок на 1 сделку (не более $25 в один мемкоин)
REINVEST_MIN_POOL_LIQ_USD = 15000.0  # Мин. ликвидность пула для входа > $15 (защита от сдвига цены)
SCOUT_SIZE_USD = 15.0    # Ордер $15 (полный вход, защита через строгий входной ончейн-анализ)
LOTTERY_SIZE_USD = 15.0
MAX_CONCURRENT_POSITIONS = 10  # концентрация капитала: максимум 10 ракет  # Режим Снайпера: максимум 5 сделок одновременно

# Risk Management
MAX_DAILY_LOSS_USD = 50.0
KILL_SWITCH_ENABLED = False  # ОТКЛЮЧЕНО для тестов (не ставить торговлю на паузу при дневном минусе)
MAX_DAILY_LOSS_PCT = 0.35
STOP_LOSS_PCT = -0.16  # Стоп -16% после 90с (не выбивает ракеты вроде Chameleon +788% на первой теневой свече -13%)

# === ЗАЩИТА ОТ СЕРИИ МИНУСОВ (STREAK CIRCUIT BREAKER) ===
STREAK_COOLDOWN_ENABLED = True       # Включить паузу после серии минусов
STREAK_MAX_CONSECUTIVE_LOSSES = 4    # 4 минуса подряд
STREAK_COOLDOWN_MINUTES = 3.0        # Длительность паузы: 3 минуты вместо 12, чтобы бот не засыпал на полдня

# === СКОЛЬЗЯЩИЙ БРЕЙКЕР + ТРОТТЛИНГ ПО ПРОСАДКЕ ===
ROLLING_BREAKER_ENABLED = False      # ОТКЛЮЧЕНО: 45-минутная пауза парализовала бота во время рыночных импульсов
ROLLING_BREAKER_WINDOW = 8           # смотрим последние 8 закрытых сделок
ROLLING_BREAKER_LOSS_USD = 8.0       # если их сумма <= -$8 -> пауза входов
ROLLING_BREAKER_PAUSE_MIN = 5.0      # резервная пауза
DD_THROTTLE_ENABLED = False          # ОТКЛЮЧЕНО: сохраняем полный размер сделки $15
DD_THROTTLE_START_USD = 15.0         # просадка от пика

# === IN-MEMORY STREAMING SNIPER (RAM FILTER + AUDIT) ===
SNIPER_ENTRIES_ENABLED = True             # Активировать вход по In-Memory стриму (без задержек DexScreener)
STREAM_WINDOW_SEC = 20                   # Скользящее окно скорости в RAM (20 секунд)
STREAM_MIN_UNIQUE_BUYERS = 10            # Минимум 10 уникальных покупателей за 20 сек
STREAM_MIN_BUY_SOL = 4.0                 # Минимум 4.0 SOL реального объема покупок
STREAM_MAX_TOP_BUYER_RATIO = 0.35        # Доля крупнейшего покупателя <= 35% (защита от одного кита / wash-trade)
STREAM_MIN_BS_RATIO = 2.0                # Соотношение Buy/Sell volume >= 2.0 (покупки в 2+ раза превышают продажи)
STREAM_MAX_CURVE_PROGRESS = 65.0         # Не входить если кривая > 65% (защита от покупки прямо перед стеной миграции)
STREAM_ENTRY_SIZE_USD = 15.0             # Размер билета на раннем входе ($15)
STREAM_SLIPPAGE = 0.02                   # 2% проскальзывание на входе
STREAM_MAX_AGE_SEC = 120                 # Очистка из RAM токенов без активности старше 2 минут

VIP_ENTRIES_ENABLED = True   # ВКЛЮЧЕНО: VIP-полоса для супер-моментума (HUMANOID +141%, JEETJAK +60%), защищена ончейн-аудитом и 1м-фильтром отката
TIME_EXIT_MINUTES = 60  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Take-Profit: частичные продажи и мунбэг ОТКЛЮЧЕНЫ — позиция закрывается целиком (100%)
# по лестнице Profit Floor / трейлингу. Раньше TP1 продавал 40% на +30%, а остаток 60%
# часто отдавал профит обратно (HUMANOID: пик +141%, итог всего +36.8%).
TP1_TRIGGER_PCT = 99.0   # Выключено (мунбэг убран)
TP1_SELL_FRAC = 0.0
TP2_TRIGGER_PCT = 99.0   # Выключено: не режем ракету на +120%, 80% едет целиком
TP2_SELL_FRAC = 0.0
MOONBAG_TRIGGER_PCT = 99.0 # Выключено: позиция уже летит как чистый 80% раннер
MOONBAG_SELL_FRAC = 0.0

# Trailing Stop & Profit Protection (Сужающийся параболический трейлинг: чем выше пик, тем плотнее зажимаем профит!)
TRAILING_ACTIVATION_PCT = 0.20  # Трейлинг с +20% (раньше +25%)
TRAILING_DISTANCE_PCT = 0.12    # 12% на разгоне (+20..+40%)
TRAILING_RUNNER_DISTANCE_PCT = 0.12 # 12% на раннерах (+80%..+150%)
SCALP_PROFIT_ENABLED = False    # Скальп-срез +5% выключен

# Whale Dump Guard & Crash Protection
WHALE_DUMP_GUARD_PCT = 0.22     # Резкий слив >22% за <15с (защита от ложных срабатываний на обычной волатильности)
EVM_RUNNER_CRASH = 0.25         # Сброс на 25% за 60с (требует подтверждения тика для раннеров)
EVM_NORMAL_CRASH = 0.22         # Сброс на 22%

# Breakeven: пик был +22%+, а цена вернулась ко входу — выходим в безубыток
BREAKEVEN_PCT = 0.22

# Infant Dump: защита от настоящего дев-рага в первые минуты (не режет рабочие откаты -13%..-18%)
PREFETCH_TTL_SEC = 10  # Пакетный кэш DexScreener (до 30 токенов за запрос): сек жизни данных для анализа
SOLANA_MIN_AGE_MIN = 1.5  # 1.5 мин (flash-rug зона дева до 90с, далее ловим волну)
SOLANA_MAX_AGE_HOURS = 24.0  # Не покупать старые тухлые монеты старше 24ч (только свежие волны и ракеты)
INFANT_WINDOW_MIN = 2.0
INFANT_DUMP_PCT = -0.25  # Резать в первые 2 мин только реальный дамп -25%+ (рабочие тени -12%..-18% выживают)

# Stagnant: живым пулам даём 15 мин на раскачку (кейс Chaton: консолидация 15м -> рост +62%), мёртвые режем на 10-й минуте
STAGNANT_LOSS_MIN = 15
STAGNANT_LOSS_MIN_FAST = 10
STAGNANT_HOLD_MIN = 25

# Filtering
AI_MODE = "degen" # "sniper" (строго 80-90% уверенности) или "degen"
MIN_LIQUIDITY = 12000  # Снижено для скальпинга обычных монет
MAX_LIQUIDITY = 50000000

# AI Аналитика
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip() # Получить бесплатно на https://aistudio.google.com/

# Birdeye API Key
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")


# === JITO BLOCK ENGINE (MEV Protection) ===
USE_JITO_EXECUTION = True # Поставь True, когда будешь готов торговать на реальные деньги
JITO_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
JITO_TIP_AMOUNT_SOL = 0.0005 # Чаевые валидатору (минимум 0.0001)
JITO_TIP_ACCOUNT = "96gYZGLnJYVFmbjzopPSU6QiCRK4rPdTuQ8hB1aP442b" # Официальный Jito Tip Account
LUNARCRUSH_API_KEY = "syvh43mkvrzrw54sor6kc5r6dmtyj5fhi4jd38ht"

# === ROBINHOOD CHAIN (ОТКЛЮЧЕНО: 100% скам Uniswap V4 без локеров/аудита) ===
ROBINHOOD_ENABLED = False
ROBINHOOD_ENTRIES_ENABLED = False
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_DS_SLUG = "robinhood"  # slug DexScreener (НЕ 4663 - тот вернёт пусто!)
ROBINHOOD_GT_NETWORK = "robinhood"  # slug GeckoTerminal
ROBINHOOD_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
ROBINHOOD_EXPLORER = "https://robinhoodchain.blockscout.com"
ROBINHOOD_MIN_LIQUIDITY = 2500.0  # Порог $2.5k: стандартные пулы на Robinhood стартуют с 1 ETH (~$2.5k-$3.5k)
ROBINHOOD_MIN_AGE_MIN = 1.0  # 1.0 мин база (Scout режим берет с 0.3м при подтвержденных сделках)
ROBINHOOD_MAX_CREATOR_PCT = 0.20  # Блок токенов, где у создателя >20% (FEE 35.5%, Speed 100% honeypot)
ROBINHOOD_MAX_OWNER_PCT = 0.20
ROBINHOOD_SCAN_INTERVAL = 20  # 20с: быстрый опрос с батч-префетчем
EVM_MIN_M5_PCT = 0.8  # Ранние импульсы от +0.8% пускаем при перевесе покупателей
EVM_ACCUM_ENABLED = True  # Включено для накопления на растущем часовом тренде (WOLFPACK +4350%, HDONNI +2024%)
EVM_NEW_POOL_PAGES = 2  # 2 страницы вместо 8 (убирает очередь мертвых пулов и ускоряет GeckoTerminal в 4 раза)
EVM_RESCAN_COOLDOWN = 60  # Перепроверка через 60с
EVM_MAX_MINTS = 120  # Лимит активных кандидатов за цикл скана


# === BASE (EVM L2, там сидят мемы с fomo.family: musebook, DELTA...) ===
# Тот же EVM-движок, slug DexScreener "base".
BASE_ENABLED = True
BASE_MIN_LIQUIDITY = 2500.0  # Порог $2.5k: стандартные Clanker/Uniswap v3 мемы на Base стартуют с ~$2.5k-$3.5k
BASE_SCAN_INTERVAL = 20  # 20с: быстрый поиск ранних ракет Base

# === BSC (Binance Smart Chain: включено с обязательным GoPlus + Honeypot.is аудитом и проверкой блокировки LP) ===
BSC_ENABLED = True
BSC_MIN_LIQUIDITY = 3000.0
BSC_SCAN_INTERVAL = 20
BSC_MIN_LP_LOCKED_PCT = 0.70  # Не менее 70% LP должно быть заблокировано (PinkSale/Unicrypt) или сожжено (0xdead)

# === ETHEREUM (Отключено по запросу: газ $3-$15 съедает всю прибыль на скальпе) ===
ETHEREUM_ENABLED = False
ETHEREUM_ENTRIES_ENABLED = False

# === РЕАЛЬНЫЕ КОМИССИИ СЕТЕЙ (EVM L2 vs BSC vs Solana) ===
# Формат: (обычный_выход_малый, обычный_выход_большой, экстренный_стоп, cap_frac)
# Base/Robinhood: FIFO-секвенсор, публичного мемпула и Jito-взяток нет, чистый L2-газ ~$0.01-$0.04
# BSC: BNB-газ ~$0.10-$0.25, мемпул открытый, но взяток Jito нет
# Solana: Priority Fees + Jito Micro-tips ~$0.05-$0.25
EVM_CHAIN_FEES = {
    "base": (0.015, 0.025, 0.04, 0.005),
    "robinhood": (0.01, 0.02, 0.03, 0.005),
    "bsc": (0.10, 0.15, 0.25, 0.02),
    "solana": (0.05, 0.10, 0.25, 0.03),
    "ethereum": (2.0, 5.0, 12.0, 0.20),
}

# --- Сайзинг: база $6, conviction x2 ---
# Тир решает ПОДТВЕРЖДЁННЫЙ импульс рынка (m5 + вести), а не скор модели
# (модель всем ставит 100%, а катастрофы были именно VIP-100%)
CONVICTION_MULT = 2.0  # conviction-вход едет удвоенным
CONVICTION_MIN_M5_PCT = 0.20  # m5 от +20%
CONVICTION_MAX_M5_PCT = 0.60  # m5 до +60% (выше - вершина)
CONVICTION_MIN_BUYSELL = 2.0  # покупки >= продаж x2
CONVICTION_MIN_LIQ = 30000  # пул от $30к
MAX_DEPLOYED_PCT = 0.60  # суммарно в рынке не больше 60% капитала (сдерживает тиринг)
ROCKET_VETO_ENABLED = True  # Включено вето RKT >= 0.25: режет мертвечину и затухающие пулы (ROBINCAT 0.06, HYPURRT 0.10, COVENANT 0.14)
ROCKET_VETO_MIN = 0.25

# === GROWTH MODE (тренд старых токенов, пока нет ракет) ===
# Логика: ракеты ловятся импульсом m5, а зрелые капы едут часами. Отдельный режим:
# вход в откат часового тренда, широкие стопы, удержание часами, мало сделок.
GROWTH_ENABLED = False
GROWTH_WATCHLIST = [  # ликвидные Solana-капы (путать не с чем, рага не будет)
    "So11111111111111111111111111111111111111112",  # SOL
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",  # RAY
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",  # ORCA
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
GROWTH_SIZE_USD = 15.0  # Ордер $15 (по запросу пользователя, было 6.0)
GROWTH_INTERVAL = 75  # 75 сек: оперативный опрос откатов вместо 5 мин (в окне 5 мин кэша пулов)

SNIPER_MIN_UNIQUE_BUYERS = 10
SNIPER_BUYERS_WINDOW_MIN = 5
VIP_MAX_M5_PCT = 0.60  # Было 1.00 - брали вершину +100%. 60% - компромисс: ракеты пропускаем, вертикали нет
PULLBACK_MIN_M5_PCT = 0.025  # Было 0.05: не отсекаем зарождающийся импульс (+2.5%..+4.9%) как флет
PULLBACK_M1_MIN_PCT = -0.15  # Но и не летим в падающий нож (максимум -15% за минуту)
PULLBACK_M1_MAX_PCT = 0.20  # Поднято до 20%: позволяет заходить в реальный пробойный импульс (breakout), а не ждать затухания
PULLBACK_MAX_H1_PCT = 25.00  # До +2500% за час (выпускники Pump.fun имеют +400%..+2000% от цены создания кривой)
LOTTERY_ENABLED = False  # Отключено: лотерея на вертикалях m5>60% вела в мгновенные дампы девов (SI, pablo)
LOTTERY_MIN_M5_PCT = 0.60  # Было 0.80: закрываем мертвую зону m5 60-80% (VIP_MAX 60% + лотерея от 60%)
LOTTERY_SIZE_MULT = 0.30  # $10 * 0.30 = $3.0 билет (было 0.25 = $2.5)
WHALE_CONSENSUS = 2  # вход на 2-м ките (3-й = уже поздно)
WHALE_MAX_RUNUP = 1.35  # цена не должна вырасти >35% с момента покупки первого кита

# === FAST STREAM (0-сек discovery, идея DoradoDevs/solana-pumpfun-sniper-bot) ===
FAST_STREAM_ENABLED = True
FAST_WINDOW_SEC = 120   # окно ранней тяги: уникальные buyers за первые 2 мин
FAST_MIN_BUYERS = 6     # >=6 уникальных покупателей за 2 мин = горячий
FAST_MIN_TRADES = 10    # или >=10 трейдов за окно
FAST_EARLY_MAX_AGE_SEC = 180  # early-микро-вход только если токену <3 мин (DS ещё пуст)

# === ДОП. ИСТОЧНИКИ (портировано из прим; фильтры режут мусор, как раньше) ===
# Jupiter Tokens API v2 (free key: portal.jup.ag). Без ключа jup_loop спит.
JUP_API_KEY = (os.getenv("JUPITER_API") or os.getenv("JUP_API_KEY") or os.getenv("JUPITER_API_KEY") or "").strip().strip('"').strip("'")
JUP_INTERVAL = 30  # 30 сек: более частый скан трендов Jupiter

# TG-превью коллов: ОТКЛЮЧЕНО (защита от скам-коллов и дев-дампов)
TG_PREVIEW_ENABLED = False
TG_PREVIEW_CHANNELS = ["pumpfunmemecalls", "pumpfunearlytrending", "cherrytrending"]
TG_PREVIEW_INTERVAL = 90

# fomoapi.io WS: ОТКЛЮЧЕНО
FOMO_API_KEY = os.getenv("FOMO_API_KEY", "")
FOMO_WS_ENABLED = False
FOMO_FOLLOW_TRADERS = []
FOMO_MIN_USD = 100
FOMO_BOARDS_INTERVAL = 7200
USE_FOMO_SIGNALS = False  # ОТКЛЮЧЕНО: исключает попадание любых токсичных памп-энд-дамп коллов в бота
MAX_FOMO_BUYS_PER_PASS = 3  # бёрст-контроль: не больше 3 входов за проход очереди
TG_MAX_SIZE_USD = 5.0  # кэп TG/FOMO-входа: $5 (защита от памп-энд-дампа каналов)
TG_VIP_SIZE_USD = 5.0
ROB_MAX_SIZE_USD = 25.0  # Robinhood: потолок до $25 при реинвесте прибыли
BSC_MAX_SIZE_USD = 25.0  # BSC: потолок до $25 при реинвесте прибыли
BSC_MIN_LIQUIDITY = 3000.0  # Пулы от $3к на BSC (вместо $6к/$12к)
BSC_WEAK_SIZE_USD = 15.0   # Сайз $15 по запросу пользователя (ставка не снижается)

# EVM tracking intervals (адаптивный опрос открытых позиций без 429)
EVM_TRACK_INTERVAL = 2.5  # Быстрый трекинг при открытых сделках (2.5с вместо 8с для мгновенных стопов)
EVM_IDLE_TRACK_INTERVAL = 6.0  # Опрос в режиме ожидания (нет открытых позиций)

# EVM factory WSS (новые пулы Base/BSC за секунды, без ключей, PublicNode)
EVM_WSS_ENABLED = True

# CoinGecko trending -> Solana-минты для GROWTH (без ключей, кэш 30 мин)
COINGECKO_ENABLED = True

# === SCOUT (ранний рукав: возраст 1-12 мин + velocity WSS, SPEC-кейс) ===
# Триггер по WSS-реестру (без DS): тяга есть, вертикаль m5 ещё не обязательно видна.
# Подтверждение по DS/Gecko: ликва, m1 не дамп, h24 не перегрев. Билет $3.
SCOUT_ENABLED = True
SCOUT_INTERVAL = 20  # опрос реестра каждые 20с
SCOUT_MIN_AGE_SEC = 60  # младше 1 мин — шум создания, DS пуст
SCOUT_MAX_AGE_SEC = 720  # старше 12 мин — уже сканер/lottery, не скаут
SCOUT_MIN_BUYERS = 5  # ≥5 уникальных покупателей WSS за окно
SCOUT_MIN_TRADES = 15  # или ≥15 трейдов WSS
SCOUT_MIN_LIQ = 15000  # пул от $15k (slippage $3 < 2%)
SCOUT_SIZE_USD = 15.0
SCOUT_MAX_POS = 3  # не больше 3 скаутов открыто
SCOUT_RECOOLDOWN = 300  # перепроверка отклонённых через 5 мин
SCOUT_BIRTH_FALLBACK = False  # birth-добор без WSS тяги ВЫКЛ: тащил раги v0/0 (AMM/XRPN/CLTR)

# === SECURITY GATES (бесплатно, без ключей, кэш 1ч, fail-open) ===
GOPLUS_ENABLED = True
GOPLUS_MAX_SELL_TAX = 0.10
GOPLUS_CACHE_SEC = 3600
