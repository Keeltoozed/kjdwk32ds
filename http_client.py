import aiohttp
import asyncio

_session = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
        _session = aiohttp.ClientSession(connector=connector)
    return _session

async def close_session():
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
