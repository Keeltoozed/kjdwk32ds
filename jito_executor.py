import requests
import base64
import config

class JitoExecutor:
    """
    Интеграция с Jito Block Engine.
    Позволяет отправлять бандлы (Bundles) напрямую лидерам слотов Jito.
    Это защищает от сэндвич-атак (MEV) и гарантирует включение в блок
    даже при сильной перегрузке сети.
    """
    
    @staticmethod
    def build_and_send_bundle(encoded_tx: str) -> bool:
        """
        Отправляет заранее собранную транзакцию (в формате base64) как Jito Bundle.
        Внимание: В транзакции УЖЕ должна быть зашита инструкция перевода 
        чаевых (Tip) на JITO_TIP_ACCOUNT.
        """
        if not getattr(config, "USE_JITO_EXECUTION", False):
            print("⚠️ Jito Execution отключен в конфиге (USE_JITO_EXECUTION = False). Работаем в Paper Mode.")
            return False
            
        print("⚡ [JITO] Отправка бандла...")
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [
                [encoded_tx]
            ]
        }
        
        try:
            url = getattr(config, "JITO_ENGINE_URL", "https://mainnet.block-engine.jito.wtf/api/v1/bundles")
            resp = requests.post(url, json=payload, timeout=5)
            data = resp.json()
            
            if "result" in data:
                bundle_id = data["result"]
                print(f"✅ [JITO] Бандл успешно отправлен! ID: {bundle_id}")
                return True
            else:
                print(f"❌ [JITO] Ошибка отправки бандла: {data.get('error', data)}")
                return False
                
        except Exception as e:
            print(f"❌ [JITO] Ошибка соединения с Block Engine: {e}")
            return False

# Пример использования
if __name__ == "__main__":
    print("Модуль Jito Executor готов к работе!")
