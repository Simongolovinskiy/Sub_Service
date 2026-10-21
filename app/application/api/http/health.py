import asyncio
from typing import Literal

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.application.api.http.schemas import HealthResponse

router = APIRouter(tags=['Health'])


@router.get('/healthz', response_model=HealthResponse, responses={503: {'model': HealthResponse}})
async def healthz(request: Request, probe: Literal['readiness', 'liveness'] = 'readiness') -> JSONResponse:
    if probe == 'liveness':
        return JSONResponse({'status': 'alive'}, headers={'Cache-Control': 'no-store'})
    runtime = request.app.state.runtime
    ready = runtime.initialized and not runtime.stopping.is_set()
    if ready:
        try:
            async with asyncio.timeout(1):
                pool = await request.app.state.container.get(asyncpg.Pool)
                async with pool.acquire() as conn:
                    await conn.fetchval('SELECT 1')
        except Exception:
            ready = False
    return JSONResponse({'status': 'ready' if ready else 'not_ready'}, status_code=200 if ready else 503,
                        headers={'Cache-Control': 'no-store'})
