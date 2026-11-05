import time
import threading


class BirthTracker:
    def __init__(self):
        self.tokens = {}      # mint -> timestamp
        self.graduated = {}   # mint -> timestamp миграции (приоритет сканера!)
        self.waiting_room = {}  # mint -> {"ts": float, "pool": str, "creator": str, "init_dev_pct": float|None}
        self.lock = threading.Lock()

    def add_token(self, mint: str):
        with self.lock:
            self.tokens[mint] = time.time()

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

    def get_waiting_room_graduates(self, min_age_sec: float = 90.0, max_age_sec: float = 2700.0) -> list:
        """Возвращает мигрировавшие монеты, пережившие первые 90с сброса снайперов (Waiting Room)."""
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

    def graduated_age(self, mint: str) -> float:
        """Сколько секунд назад зафиксирована миграция (для метрики time_to_index)."""
        with self.lock:
            ts = self.graduated.get(mint)
            if ts is None and mint in self.waiting_room:
                ts = self.waiting_room[mint].get("ts")
            return time.time() - ts if ts else -1.0

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
