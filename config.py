import os
from dotenv import load_dotenv

load_dotenv()

# Dexscreener API (Только самые свежие бусты и профили, без старых мамонтов 2024 года)
DEXSCREENER_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1" 
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
INITIAL_BALANCE_USD = 100.0  # Стартовый депозит для разгона ($100 -> $8,000+)
REINVEST_PERCENT = 20.0  # 20% пула на сделку по стратегии автора (@savipww)
VIRTUAL_POSITION_SIZE_USD = 20.0
TRADE_AMOUNT_USD = 20.0  # Базовый ордер $20
# === ЭКСПОНЕНЦИАЛЬНЫЙ РАЗГОН ДЕПОЗИТА (COMPOUNDING $100 -> $8,000+) ===
COMPOUNDING_ENABLED = True             # Разгон депозита по стратегии автора (@savipww)
COMPOUNDING_PCT = 0.22                 # 22% от текущего капитала в сделку (20-25% риск/билет)
MAX_COMPOUNDING_SIZE_USD = 2500.0      # Динамический потолок (масштабируется с ростом баланса)
DYNAMIC_REINVEST_ENABLED = True        # Включить ступенчатый реинвест прибыли
BASE_TRADE_AMOUNT_USD = 15.0           # Базовый размер сделки (минимальный пол $15)
MAX_POSITION_SIZE_USD = 2500.0         # Масштабируемый потолок для разгона
REINVEST_MIN_POOL_LIQ_USD = 12000.0    # Мин. ликвидность пула для входа > $15 (защита от сдвига цены)
SCOUT_SIZE_USD = 15.0                  # Ордер $15
LOTTERY_SIZE_USD = 15.0
MAX_CONCURRENT_POSITIONS = 5           # Режим Снайпера: максимум 5 сделок одновременно (5 x 20% = 100% капитала)

# Risk Management
MAX_DAILY_LOSS_USD = 50.0
KILL_SWITCH_ENABLED = False  # ОТКЛЮЧЕНО для тестов (не ставить торговлю на паузу при дневном минусе)
MAX_DAILY_LOSS_PCT = 0.35
STOP_LOSS_PCT = -0.30  # Стоп -30% после 90с (не выбивает ракеты вроде Chameleon +788% на первой теневой свече -13%)

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
STREAM_WINDOW_SEC = 60                   # Скользящее окно скорости в RAM (60 секунд)
STREAM_MIN_UNIQUE_BUYERS = 6             # Минимум 6 уникальных покупателей за окно (ранняя органика, не 3 фейк-кошелька дева)
STREAM_MIN_BUY_SOL = 2.5                 # Минимум 2.5 SOL реального объема покупок (не копейки 0.8 SOL)
STREAM_MAX_TOP_BUYER_RATIO = 0.50        # Доля крупнейшего покупателя <= 50% (защита от одного кита-манипулятора)
STREAM_MIN_BS_RATIO = 1.8                # Соотношение Buy/Sell volume >= 1.8 (мощный перевес покупателей)
STREAM_MAX_CURVE_PROGRESS = 75.0         # Не входить перед самой миграцией (макс 75%, было зажато до 40%)
STREAM_ENTRY_SIZE_USD = 15.0             # Размер билета на раннем входе ($15)
STREAM_SLIPPAGE = 0.08                   # 8% реалистичное проскальзывание на быстром входе pump.fun (было нереалистичные 2%)
STREAM_MAX_AGE_SEC = 900                 # Даем 15 минут органическим токенам набрать кривую (было 5 мин, из-за чего выкидывались хорошие медленные старты)
STREAM_GC_MIN_PROGRESS = 4.0             # Чистим только если за 15 мин кривая < 4% (мертвый токен)
STREAM_GC_IDLE_SEC = 180                 # Если ноль трейдов 180с (3 мин) и прогресс < 4% — чистим
STREAM_DEV_DUMP_RATIO = 0.25             # Если кошельки первых транзакций продали > 25% общего объёма — скам
STREAM_MAX_DEV_BUY_TOKENS = 100_000_000  # Максимум 10% supply (100M из 1B) на выкупе дева при создании
STREAM_MIN_ENTRY_PROGRESS = 3.0          # Минимум 3% кривой перед входом (~2.5 SOL реального объёма — подтверждение интереса, а не фейк 0.5%)
STREAM_MOMENTUM_MIN_RATIO = 0.40         # Вторая половина окна должна содержать >= 40% объёма (моментум не затухает)

VIP_ENTRIES_ENABLED = True   # ВКЛЮЧЕНО: VIP-полоса для супер-моментума (HUMANOID +141%, JEETJAK +60%), защищена ончейн-аудитом и 1м-фильтром отката
TIME_EXIT_MINUTES = 60  # Если за 30 минут нет пампа - выходим
TIME_EXIT_PROFIT_REQ = 0.0

# Take-Profit: частичные продажи и мунбэг ПОЛНОСТЬЮ ОТКЛЮЧЕНЫ — позиция едет целиком (100%)
# по лестнице Profit Floor и трейлингу прямо до вершины, как у автора (Grok Desk)!
# Раньше TP1 продавал 50% на +40%, из-за чего позиция урезалась вдвое ($15 -> $7.5),
# и на ракетах недополучалась львиная доля профита.
PARTIAL_TP_ENABLED = False     # Полный запрет частичных фиксаций / мунбэгов
TP1_TRIGGER_PCT = 999.0        # Отключено
TP1_SELL_FRAC = 0.0
TP2_TRIGGER_PCT = 999.0        # Отключено
TP2_SELL_FRAC = 0.0
MOONBAG_TRIGGER_PCT = 999.0    # Отключено
MOONBAG_SELL_FRAC = 0.0

# Trailing Stop & Profit Protection (Сужающийся параболический трейлинг: чем выше пик, тем плотнее зажимаем профит!)
TRAILING_ACTIVATION_PCT = 0.50  # Трейлинг с +20% (раньше +25%)
TRAILING_DISTANCE_PCT = 0.20    # 12% на разгоне (+20..+40%)
TRAILING_RUNNER_DISTANCE_PCT = 0.25 # 12% на раннерах (+80%..+150%)
SCALP_PROFIT_ENABLED = False    # Скальп-срез +5% выключен

# Whale Dump Guard & Crash Protection
WHALE_DUMP_GUARD_PCT = 0.22     # Резкий слив >22% за <15с (защита от ложных срабатываний на обычной волатильности)
EVM_RUNNER_CRASH = 0.25         # Сброс на 25% за 60с (требует подтверждения тика для раннеров)
EVM_NORMAL_CRASH = 0.22         # Сброс на 22%

# Breakeven: пик был +28%+, а после 6 минут цена вернулась ко входу — выходим в безубыток
BREAKEVEN_PCT = 0.28

# Infant Dump: защита от настоящего дев-рага в первые минуты (не режет рабочие откаты -13%..-18%)
PREFETCH_TTL_SEC = 60  # Пакетный кэш DexScreener (до 30 токенов за запрос): 60с жизни для быстрого разбора пачки в RAM
SOLANA_MIN_AGE_MIN = 2.0  # 1.5 мин (flash-rug зона дева до 90с, далее ловим волну)
SOLANA_MAX_AGE_HOURS = 24.0
SOLANA_MAX_AGE_MIN = 180.0      # Максимум 3 часа входа  # Не покупать старые тухлые монеты старше 24ч (только свежие волны и ракеты)
INFANT_WINDOW_MIN = 2.0
INFANT_DUMP_PCT = -0.40  # Резать в первые 2 мин только реальный дамп -25%+ (рабочие тени -12%..-18% выживают)

# Stagnant: живым пулам даём 15 мин на раскачку (кейс Chaton: консолидация 15м -> рост +62%), мёртвые режем на 10-й минуте
STAGNANT_LOSS_MIN = 15
STAGNANT_LOSS_MIN_FAST = 10
STAGNANT_HOLD_MIN = 25

# Filtering
AI_MODE = "degen" # "sniper" (строго 80-90% уверенности) или "degen"
MIN_LIQUIDITY = 20000  # Снижено для скальпинга обычных монет
MAX_LIQUIDITY = 50000000

# AI Аналитика: Groq (бесплатный сверхбыстрый API) + xAI Grok + Gemini
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip().strip('"').strip("'")
XAI_API_KEY = (os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or "").strip().strip('"').strip("'")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip() # Получить бесплатно на https://aistudio.google.com/

# === GROK BOT x JEV ENGINE (THE FUNNEL & THE LINE) ===
JEV_ENABLED = True
JEV_MIN_AGE_MIN = 15.0           # 15 минут для зрелых AMM пулов
JEV_EARLY_MIN_AGE_MIN = 2.0      # 2-3 минуты для pump.fun / early scout
JEV_MIN_LIQUIDITY_USD = 12000.0  # $12,000 минимальная ликвидность пула
JEV_MIN_VOLUME_H1 = 40000.0      # $40,000 часовой объем
JEV_MIN_MCAP_USD = 45000.0       # $45,000 рыночная капитализация
JEV_MIN_TRADES_H1 = 250          # 250 сделок в час
JEV_MAX_TOP_WALLET = 0.05        # 5% лимит на крупнейший кошелек (за вычетом пула)
JEV_MAX_TOP_10 = 0.25            # 25% лимит на топ-10 кошельков (за вычетом пула)

# SOFT AI Criteria (0.0 to 1.0)
JEV_MIN_ACCOUNT_IS_PROJECT = 0.75
JEV_MAX_RECYCLED_ACCOUNT = 0.30
JEV_MAX_CONCENTRATION_RISK = 0.35
JEV_MAX_MOMENTUM_SPENT = 0.60
JEV_MIN_LIQUIDITY_FITS_TICKET = 0.70
JEV_MAX_DEV_STILL_LOADED = 0.25

# FILLS & RISK: "We bid the flush, not the candle" & "The stop lives under the wick"
JEV_BID_THE_FLUSH_ENABLED = True
JEV_STOP_UNDER_WICK_ENABLED = True
JEV_MAX_M1_ENTRY_PCT = 3.5       # Не влетать в зеленую палку (m1 <= +3.5%)
JEV_DEFAULT_WICK_STOP_PCT = -0.06 # -6% дефолтный стоп под фитиль отката

# Birdeye API Key
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_CHAINS = ["solana", "base"]  # Фокус только на Solana и Base

# === JITO BLOCK ENGINE (MEV Protection) ===
USE_JITO_EXECUTION = True # Поставь True, когда будешь готов торговать на реальные деньги
JITO_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
JITO_TIP_AMOUNT_SOL = 0.0005 # Чаевые валидатору (минимум 0.0001)
JITO_TIP_ACCOUNT = "96gYZGLnJYVFmbjzopPSU6QiCRK4rPdTuQ8hB1aP442b" # Официальный Jito Tip Account
LUNARCRUSH_API_KEY = "syvh43mkvrzrw54sor6kc5r6dmtyj5fhi4jd38ht"

# === ROBINHOOD CHAIN (ОТКЛЮЧЕНО: фокус только на Solana и Base) ===
ROBINHOOD_ENABLED = False
ROBINHOOD_ENTRIES_ENABLED = False
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_DS_SLUG = "robinhood"  # slug DexScreener (НЕ 4663 - тот вернёт пусто!)
ROBINHOOD_GT_NETWORK = "robinhood"  # slug GeckoTerminal
ROBINHOOD_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
ROBINHOOD_EXPLORER = "https://robinhoodchain.blockscout.com"
ROBINHOOD_MIN_LIQUIDITY = 10000.0  # Порог $2.5k: стандартные пулы на Robinhood стартуют с 1 ETH (~$2.5k-$3.5k)
ROBINHOOD_MIN_AGE_MIN = 1.0  # 1.0 мин база (Scout режим берет с 0.3м при подтвержденных сделках)
ROBINHOOD_MAX_CREATOR_PCT = 0.20  # Блок токенов, где у создателя >20% (FEE 35.5%, Speed 100% honeypot)
ROBINHOOD_MAX_OWNER_PCT = 0.20
ROBINHOOD_SCAN_INTERVAL = 20  # 20с: быстрый опрос с батч-префетчем
EVM_MIN_M5_PCT = 0.8  # Ранние импульсы от +0.8% пускаем при перевесе покупателей
EVM_ACCUM_ENABLED = True  # Включено для накопления на растущем часовом тренде (WOLFPACK +4350%, HDONNI +2024%)
EVM_NEW_POOL_PAGES = 2  # 2 страницы вместо 8 (убирает очередь мертвых пулов и ускоряет GeckoTerminal в 4 раза)
EVM_RESCAN_COOLDOWN = 60  # Перепроверка через 60с
EVM_MAX_MINTS = 120  # Лимит активных кандидатов за цикл скана


# === BASE (EVM L2: активен, 100% фокус вместе с Solana) ===
BASE_ENABLED = True
BASE_MIN_LIQUIDITY = 4000.0  # Порог $4k: Clanker/Uniswap v3 мемы на Base стартуют с ~$3.5k-$12k ликвидности
BASE_SCAN_INTERVAL = 15  # 15с: быстрый поиск ранних ракет Base
BASE_MAX_SIZE_USD = 25.0  # Полноценный размер ордера для Base

# === BSC (ОТКЛЮЧЕНО: фокус только на Solana и Base) ===
BSC_ENABLED = False
BSC_MIN_LIQUIDITY = 4000.0
BSC_SCAN_INTERVAL = 20
BSC_MIN_LP_LOCKED_PCT = 0.70

# === ETHEREUM (ОТКЛЮЧЕНО: фокус только на Solana и Base) ===
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
MAX_DEPLOYED_PCT = 0.85  # суммарно в рынке до 85% капитала (до 6-7 позиций при балансе $120)
ROCKET_VETO_ENABLED = False  # Включено вето RKT >= 0.25: режет мертвечину и затухающие пулы (ROBINCAT 0.06, HYPURRT 0.10, COVENANT 0.14)
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
PULLBACK_MIN_M5_PCT = 0.025  # 2.5%: не отсекаем растущие монеты (+2.5%..+4.9%) как флет
PULLBACK_M1_MIN_PCT = -0.05  # Но и не летим в падающий нож (максимум -5% за минуту)
PULLBACK_M1_MAX_PCT = 0.40  # Поднято до 40%: позволяет заходить в реальный пробойный импульс (breakout), а не ждать затухания
PULLBACK_MAX_H1_PCT = 3500.0  # До +3500% за час: выпускники Pump.fun имеют +400%..+2500% от цены создания кривой
PULLBACK_MAX_M5_PCT = 1.20  # До +120% за 5м при активных покупках (не режет реальные ракеты вроде vamp +51%)
MIN_VOLUME_M5 = 2500
MIN_TXNS_M5 = 12
MIN_BUY_SELL_RATIO = 1.15  # Преобладание покупателей над продавцами
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
TG_MAX_SIZE_USD = 15.0  # кэп TG/FOMO-входа: $15 (на уровне Base/BSC)
TG_VIP_SIZE_USD = 15.0  # размер входа для VIP-сигналов (Solana) увеличен до $15
ROB_MAX_SIZE_USD = 25.0  # Robinhood: потолок до $25 при реинвесте прибыли
BSC_MAX_SIZE_USD = 25.0  # BSC: потолок до $25 при реинвесте прибыли
BSC_MIN_LIQUIDITY = 4000.0  # Пулы от $4k на BSC (раньше стояла опечатка 30000.0)
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

# === SCANNER PIPELINE (Быстрый параллельный разбор кандидатов) ===
SCAN_CONCURRENCY = 4              # Параллельный разбор токенов в окне (до 4 задач одновременно)
SCAN_LOOP_SLEEP_SEC = 8           # Пауза между циклами сканера (8 секунд вместо 30)
SCAN_AFTER_ENTRY_SLEEP_SEC = 15   # Пауза после входа в позицию (15 секунд)
SCAN_LATEST_TTL_SEC = 20          # TTL кэша fetch_latest_tokens (20 секунд)
SOLANA_RESCAN_COOLDOWN = 180      # Кулдаун пересканирования старых токенов (180 секунд)
SOLANA_FRESH_RESCAN_COOLDOWN = 45 # Кулдаун для свежих токенов (<30 минут) и миграций (45 секунд)
