import json
import os
import time

REGISTRY_FILE = "birth_registry.json"

class BirthTracker:
    def __init__(self):
        self.registry = {}
        self.load()

    def load(self):
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE, 'r') as f:
                    self.registry = json.load(f)
            except:
                self.registry = {}

    def save(self):
        # Очищаем старые токены (старше 2 часов), чтобы файл не пух
        current_time = time.time()
        self.registry = {k: v for k, v in self.registry.items() if (current_time - v) / 60 <= 120}
        try:
            with open(REGISTRY_FILE, 'w') as f:
                json.dump(self.registry, f)
        except:
            pass

    def add_token(self, mint: str):
        if mint not in self.registry:
            self.registry[mint] = time.time()
            self.save()

    def get_mature_tokens(self, min_age_mins: int = 40, max_age_mins: int = 45):
        """Возвращает список токенов, которым сейчас от 40 до 45 минут"""
        current_time = time.time()
        mature = []
        for mint, birth_time in self.registry.items():
            age_mins = (current_time - birth_time) / 60
            if min_age_mins <= age_mins <= max_age_mins:
                mature.append(mint)
        return mature
