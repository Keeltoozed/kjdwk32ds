import asyncio
import aiohttp
import time
import base64
from solana.rpc.async_api import AsyncClient
from solders.system_program import TransferParams, transfer
from solders.instruction import Instruction
from solders.transaction import VersionedTransaction
from solders.pubkey import Pubkey

# Jito Block Engine endpoints
JITO_ENGINES = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles",
]

# Random tip accounts provided by Jito validators
TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiCR5M6c8vdc2J2T9U2oA9",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvVkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jcyNCybVYYK6DwvWqjKee8pbKdQaCGlzMvXv",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwTc53",
    "DttWaMuVvTiduZRnguLF7FsBog8xVbMz35WuEx5wnW1e",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBn1HyeVMy"
]

class JitoExecutor:
    """
    Интеграция Jito Block Engine для отправки бандлов (bundles) 
    и защиты от MEV (сэндвич-атак).
    """
    def __init__(self, rpc_url: str):
        self.solana_client = AsyncClient(rpc_url)
        self.timeout_sec = 3.0 # Оптимизация 3 секунды: если дольше - цена уже ушла

    def get_tip_instruction(self, sender_pubkey: Pubkey, tip_lamports: int = 1000000) -> Instruction:
        """Создает инструкцию для оплаты чаевых Jito."""
        import random
        tip_account = Pubkey.from_string(random.choice(TIP_ACCOUNTS))
        return transfer(TransferParams(
            from_pubkey=sender_pubkey, 
            to_pubkey=tip_account, 
            lamports=tip_lamports
        ))

    async def send_bundle(self, tx: VersionedTransaction) -> str:
        """Отправка транзакции как бандла Jito в обход мемпула."""
        serialized_tx = base64.b64encode(bytes(tx)).decode('utf-8')
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [[serialized_tx]]
        }

        async with aiohttp.ClientSession() as session:
            tasks = []
            for engine in JITO_ENGINES:
                tasks.append(session.post(engine, json=payload))
            
            # Отправляем во все эндпоинты Jito одновременно для минимизации задержек
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            bundle_id = None
            for res in responses:
                if not isinstance(res, Exception) and res.status == 200:
                    data = await res.json()
                    if "result" in data:
                        bundle_id = data["result"]
                        break
            
            if not bundle_id:
                raise Exception("Failed to send bundle to any Jito engine")
            
            return bundle_id

    async def execute_and_confirm(self, tx: VersionedTransaction) -> bool:
        """Отправляет бандл и ждет 3 секунды подтверждения."""
        try:
            bundle_id = await self.send_bundle(tx)
            signature = tx.signatures[0]
            print(f"📦 Jito Bundle Sent. Signature: {signature}. Waiting max {self.timeout_sec}s...")
            
            start_time = time.time()
            while time.time() - start_time < self.timeout_sec:
                status = await self.solana_client.get_signature_statuses([signature])
                if status.value[0] is not None:
                    if status.value[0].confirmation_status in ["confirmed", "finalized"]:
                        if status.value[0].err is None:
                            print(f"✅ TX Confirmed instantly via Jito: {signature}")
                            return True
                        else:
                            print(f"❌ TX Failed: {status.value[0].err}")
                            return False
                await asyncio.sleep(0.4) # Поллинг каждые 400мс
                
            print(f"⏰ TX {signature} dropped after {self.timeout_sec}s. Protected from slippage!")
            return False
            
        except Exception as e:
            print(f"⚠️ Jito execution error: {e}")
            return False
