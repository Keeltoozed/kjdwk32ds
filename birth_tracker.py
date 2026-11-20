import json
import os
import time
import threading


BAD_CREATORS_FILE = "bad_creators.json"


class BirthTracker:
    def __init__(self):
        self.tokens = {}      # mint -> timestamp
        self.graduated = {}   # mint -> timestamp миграции (приоритет сканера!)
        self.waiting_room = {}  # mint -> {"ts": float, "pool": str, "creator": str, "init_dev_pct": float|None}
        self.mint_creators = {}       # mint -> creator_wallet
        self.creator_mints = {}        # creator_wallet -> {mint: timestamp} (защита от ложного спама при повторном сканировании одного токена)
        self.creator_launches = {}    # creator_wallet -> [ts, ...] (последний час, backward-compat)
        self.bad_creators = self._load_bad_creators()
        self.lock = threading.Lock()

    def _load_bad_creators(self) -> set:
        try:
            if os.path.exists(BAD_CREATORS_FILE):
                with open(BAD_CREATORS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return {str(x).strip() for x in data if str(x).strip()}
        except Exception:
            pass
        return set()

    def _save_bad_creators_unlocked(self):
        try:
            with open(BAD_CREATORS_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(self.bad_creators), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_creator(self, mint: str, creator: str = ""):
        c = str(creator or "").strip()
        m = str(mint or "").strip()
        if not c:
            return
        now = time.time()
        with self.lock:
            if m:
                self.mint_creators[m] = c
                if len(self.mint_creators) > 5000:
                    self.mint_creators.clear()
            mints_map = self.creator_mints.setdefault(c, {})
            # Учитываем реальные уникальные токены (mint), а не повторные опросы одной и той же монеты
            if m:
                if m not in mints_map:
                    mints_map[m] = now
            else:
                mints_map[f"gen_{int(now)}"] = now

            # Храним запуски только за последний час
            for _k in list(mints_map.keys()):
                if (now - mints_map[_k]) > 3600.0:
                    del mints_map[_k]

            self.creator_launches[c] = list(mints_map.values())
            if len(self.creator_mints) > 3000:
                self.creator_mints = {
                    k: v for k, v in self.creator_mints.items()
                    if v and any((now - t) <= 3600.0 for t in v.values())
                }
                self.creator_launches = {k: list(v.values()) for k, v in self.creator_mints.items()}

    def record_bad_creator(self, creator_or_mint: str, reason: str = ""):
        key = str(creator_or_mint or "").strip()
        if not key:
            return
        with self.lock:
            creator = self.mint_creators.get(key) or ("" if key.endswith("pump") else key)
            if not creator or creator in self.bad_creators:
                return
            self.bad_creators.add(creator)
            self._save_bad_creators_unlocked()
            print(f"☠️ [BAD-CREATOR] Занесён в чёрный список дев {creator[:8]}... ({reason or 'rug'})")

    def check_creator_risk(self, mint: str, creator: str = "", is_rug_hunter: bool = False) -> tuple:
        """0ms проверка репутации создателя токена (bad_creators.json + серийный спам-завод)."""
        now = time.time()
        with self.lock:
            c = str(creator or "").strip() or self.mint_creators.get(str(mint or "").strip(), "")
            if not c:
                return True, ""
            if c in self.bad_creators:
                return False, f"blacklisted-rugger-creator {c[:8]}"
            if not is_rug_hunter:
                mints_map = self.creator_mints.get(c)
                if mints_map is not None:
                    distinct_mints = [m_k for m_k, t_val in mints_map.items() if (now - t_val) <= 3600.0]
                    num_launches = len(distinct_mints)
                else:
                    launches = [t for t in (self.creator_launches.get(c) or []) if (now - t) <= 3600.0]
                    num_launches = len(launches)
                if num_launches >= 3:
                    return False, f"serial-rugger-factory ({num_launches} distinct launches in 1h by {c[:8]})"
        return True, ""

    def add_token(self, mint: str, creator: str = ""):
        with self.lock:
            self.tokens[mint] = time.time()
        if creator:
            self.record_creator(mint, creator)

    def remove_token(self, mint: str):
        """Удалить мертвый или отсканированный токен из трекера, чтобы не опрашивать повторно."""
        with self.lock:
            self.tokens.pop(mint, None)

    def get_age(self, mint: str):
        """Возвращает возраст токена в минутах с момента появления (или None, если токен не отслеживается)."""
        now = time.time()
        with self.lock:
            ts = self.tokens.get(mint)
            if ts is None:
                ts = self.graduated.get(mint)
            if ts is not None:
                return (now - ts) / 60.0
            return None

    def get_mature_tokens(self, min_age_minutes: int, max_age_minutes: int):
        now = time.time()
        min_age_seconds = min_age_minutes * 60
        max_age_seconds = max_age_minutes * 60
        with self.lock:
            mature = []
            to_remove = []
            for mint, timestamp in self.tokens.items():
                age = now - timestamp
                if age >= min_age_seconds and age <= max_age_seconds:
                    mature.append(mint)
                elif age > max_age_seconds:
                    to_remove.append(mint)
            for mint in to_remove:
                del self.tokens[mint]
            return mature

    # Лимиты очереди миграций (защита от распухания при панике/раге):
    GRAD_MAX = 200   # больше — выкидываем самые старые (FIFO) с логом
    GRAD_TTL = 900   # 15 мин: DS в пик индексирует и по 15-20 мин, 10 было впритык
    WAIT_ROOM_MAX = 400
    WAIT_ROOM_TTL = 2700  # 45 мин наблюдение за выпускниками PumpSwap ("Waiting Room")

    def add_graduated(self, mint: str, pool: str = "", creator: str = ""):
        """Миграция завершена (кёрв 100% / subscribeMigration): токен — первый в очереди сканера + Waiting Room."""
        now = time.time()
        if creator:
            self.record_creator(mint, creator)
        with self.lock:
            self.graduated[mint] = now
            if len(self.graduated) > self.GRAD_MAX:
                drop = len(self.graduated) - self.GRAD_MAX
                for _m in sorted(self.graduated, key=self.graduated.get)[:drop]:
                    del self.graduated[_m]
                print(f"⚠️ [MIGRATION QUEUE] Переполнение: выкинуто {drop} старых (очередь {self.GRAD_MAX}).")
            meta = self.waiting_room.get(mint)
            if meta is None:
                self.waiting_room[mint] = {
                    "ts": now,
                    "pool": str(pool or ""),
                    "creator": str(creator or ""),
                    "init_dev_pct": None,
                }
            else:
                if pool and not meta.get("pool"):
                    meta["pool"] = str(pool)
                if creator and not meta.get("creator"):
                    meta["creator"] = str(creator)
            if len(self.waiting_room) > self.WAIT_ROOM_MAX:
                stale = sorted(self.waiting_room.items(), key=lambda kv: kv[1].get("ts", 0.0))[:len(self.waiting_room) - self.WAIT_ROOM_MAX]
                for _km, _ in stale:
                    self.waiting_room.pop(_km, None)

    def add_to_waiting_room(self, mint: str, pool: str = "", creator: str = ""):
        """FIX: этот метод вызывался из grok_jev.py и main.py, но не существовал (AttributeError глотался try/except).
        Ставит токен в Waiting Room: через 90с он попадёт в grok_jev.crawl_universe()
        (get_waiting_room_graduates) и будет перепроверен, когда карантин по возрасту снимется.
        Существующую запись не перезаписывает, чтобы не сбрасывать таймер ожидания."""
        if not mint:
            return
        now = time.time()
        with self.lock:
            meta = self.waiting_room.get(mint)
            if meta is None:
                self.waiting_room[mint] = {
                    "ts": now,
                    "pool": str(pool or ""),
                    "creator": str(creator or ""),
                    "init_dev_pct": None,
                }
            else:
                if pool and not meta.get("pool"):
                    meta["pool"] = str(pool)
                if creator and not meta.get("creator"):
                    meta["creator"] = str(creator)
            if len(self.waiting_room) > self.WAIT_ROOM_MAX:
                stale = sorted(self.waiting_room.items(), key=lambda kv: kv[1].get("ts", 0.0))[:len(self.waiting_room) - self.WAIT_ROOM_MAX]
                for _km, _ in stale:
                    self.waiting_room.pop(_km, None)

    def get_waiting_room_graduates(self, min_age_sec: float = 20.0, max_age_sec: float = 2700.0) -> list:
        """Возвращает мигрировавшие монеты для быстрого снайпинга пробоя миграции (Waiting Room)."""
        now = time.time()
        with self.lock:
            expired = [m for m, info in self.waiting_room.items() if (now - info.get("ts", 0.0)) > self.WAIT_ROOM_TTL]
            for m in expired:
                self.waiting_room.pop(m, None)
            ready = [
                (info.get("ts", 0.0), m)
                for m, info in self.waiting_room.items()
                if min_age_sec <= (now - info.get("ts", 0.0)) <= max_age_sec
            ]
            ready.sort(reverse=True)
            return [m for _, m in ready]

    def check_creator_dumped(self, mint: str, current_dev_pct: float) -> tuple:
        """Проверяет правило Auditor 'dev sold 0': если создатель держал >= 2% на миграции и уже слил > 50% стека в рынок."""
        if current_dev_pct < 0:
            return False, ""
        with self.lock:
            meta = self.waiting_room.get(mint)
            if not meta:
                self.waiting_room[mint] = {
                    "ts": time.time(),
                    "pool": "",
                    "creator": "",
                    "init_dev_pct": float(current_dev_pct),
                }
                return False, ""
            init_pct = meta.get("init_dev_pct")
            if init_pct is None:
                meta["init_dev_pct"] = float(current_dev_pct)
                return False, ""
            if init_pct >= 2.0 and current_dev_pct < (init_pct * 0.50) and (init_pct - current_dev_pct) >= 1.2:
                return True, f"dev dumping post-migration ({init_pct:.1f}% -> {current_dev_pct:.1f}%)"
            if current_dev_pct > init_pct:
                meta["init_dev_pct"] = float(current_dev_pct)
            return False, ""

    def is_graduated(self, mint: str) -> bool:
        """Проверяет, зафиксирована ли миграция токена в очереди graduated или waiting_room."""
        if not mint:
            return False
        with self.lock:
            return mint in self.graduated or mint in self.waiting_room

    def graduated_age(self, mint: str) -> float:
        """Сколько секунд назад зафиксирована миграция (для метрики time_to_index)."""
        with self.lock:
            ts = self.graduated.get(mint)
            if ts is None and mint in self.waiting_room:
                ts = self.waiting_room[mint].get("ts")
            return time.time() - ts if ts else -1.0

    def remove_graduated(self, mint: str):
        """Удалить токен из очереди graduated и waiting_room навсегда (скам/зомби/слит)."""
        if not mint:
            return
        with self.lock:
            self.graduated.pop(mint, None)
            self.waiting_room.pop(mint, None)

    def drain_graduated(self, max_age_sec: int = None) -> list:
        """Забрать свежие миграции (дефолт TTL 15 мин) и почистить старые."""
        if max_age_sec is None:
            max_age_sec = self.GRAD_TTL
        now = time.time()
        with self.lock:
            out = [m for m, ts in self.graduated.items() if now - ts <= max_age_sec]
            self.graduated = {m: ts for m, ts in self.graduated.items()
                              if now - ts <= max_age_sec}
            return out


birth_tracker = BirthTracker()
