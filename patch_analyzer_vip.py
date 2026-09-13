import re

with open('analyzer.py', 'r') as f:
    content = f.read()

# Add check_hyper_rocket_momentum method
vip_method = '''    def check_hyper_rocket_momentum(self, pair_data: dict) -> bool:
        """
        VIP-полоса для Гипер-Ракет:
        Ищет аномальные всплески покупок (>50 покупок) и объема (>$30,000) в первые 5 минут.
        Позволяет пропустить стандартные жесткие фильтры.
        """
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        volume_m5 = pair_data.get("volume", {}).get("m5", 0)
        
        # > 50 покупок И > $30k объема в 5-минутном окне
        if buys_m5 >= 50 and volume_m5 >= 30000:
            return True
        return False
        
    async def analyze_token(self, mint: str) -> bool:'''

content = content.replace('    async def analyze_token(self, mint: str) -> bool:', vip_method)

# Modify analyze_token_xgboost
old_xgboost = '''        # 2. Проверяем возраст токена (XGBoost обучен на свежих монетах)
        created_at = pair_data.get("pairCreatedAt")
        if created_at:
            import time
            age_minutes = (time.time() * 1000 - created_at) / (1000 * 60)
            if age_minutes > 15:  # Игнорируем токены старше 15 минут
                return False'''

new_xgboost = '''        is_vip = self.check_hyper_rocket_momentum(pair_data)
        if is_vip:
            print(f"🚀🚀🚀 [HYPER-ROCKET BYPASS] Токен {mint} летит в космос! Игнорируем карантин возраста и соцсетей.")
            
        # 2. Проверяем возраст токена (только для обычных монет)
        created_at = pair_data.get("pairCreatedAt")
        if created_at and not is_vip:
            import time
            age_minutes = (time.time() * 1000 - created_at) / (1000 * 60)
            if age_minutes > 15:  # Игнорируем токены старше 15 минут
                return False'''

content = content.replace(old_xgboost, new_xgboost)

old_sybil = '''                                if counts.most_common(1)[0][1] >= 3:
                                    print(f"🚫 Мусор: Обнаружен Jito-бандл (Sybil attack) у {mint}.")
                                    return False'''

new_sybil = '''                                if counts.most_common(1)[0][1] >= 3:
                                    if not is_vip:
                                        print(f"🚫 Мусор: Обнаружен Jito-бандл (Sybil attack) у {mint}.")
                                        return False
                                    else:
                                        print(f"⚠️ ВНИМАНИЕ: {mint} имеет Jito-бандл, но пропускается по VIP-квоте (Hyper-Rocket)!")'''

content = content.replace(old_sybil, new_sybil)

old_conf = '''        import config; threshold = 75.0 if getattr(config, "AI_MODE", "sniper") == "sniper" else 65.0; return conf >= threshold'''

new_conf = '''        import config
        threshold = 75.0 if getattr(config, "AI_MODE", "sniper") == "sniper" else 65.0
        if is_vip:
            threshold = 70.0 # Снижаем порог уверенности для ракет
            print(f"🔥 [VIP] Порог XGBoost снижен до {threshold}%")
            
        return conf >= threshold'''

content = content.replace(old_conf, new_conf)

with open('analyzer.py', 'w') as f:
    f.write(content)
