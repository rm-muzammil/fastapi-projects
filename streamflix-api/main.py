from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from app.routers.upload import router as upload_router
import hmac, hashlib, time

app = FastAPI(title="Streamflix API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://172.22.235.3:3000", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Include routers
app.include_router(upload_router)

# ── Key server (from Phase 3) ─────────────────────────────────────────────────
AES_KEYS = {
    "4e609c885ca24dd9f5bd2cb76705f8e1": "9c1f2fc16177d9b279fe3b3255dfe459"
}
SIGNING_SECRET = "change-this-in-production"

@app.get("/keys/{key_id}")
async def get_key(key_id: str, request: Request):
    if key_id not in AES_KEYS:
        raise HTTPException(status_code=404, detail="Key not found")
    key_bytes = bytes.fromhex(AES_KEYS[key_id])
    return Response(content=key_bytes, media_type="application/octet-stream")

@app.get("/health")
async def health():
    return {"status": "ok", "keys_loaded": len(AES_KEYS)}

# ── Detailed health check ─────────────────────────────────────────────────────
@app.get("/health/detailed")
async def health_detailed():
    from arq.connections import RedisSettings, create_pool
    from redis.exceptions import ConnectionError as RedisConnectionError

    checks = {"api": "ok", "redis": "unknown", "disk": "unknown"}

    # Check Redis
    try:
        r = await create_pool(RedisSettings(host="localhost", port=6379))
        await r.ping()
        await r.close()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)[:50]}"

    # Check disk space (warn if less than 1GB free)
    import shutil
    free_gb = shutil.disk_usage("/").free / (1024 ** 3)
    checks["disk"] = "ok" if free_gb > 1 else f"warning: only {free_gb:.1f}GB free"
    checks["disk_free_gb"] = round(free_gb, 2)

    # Overall status
    all_ok = all(v == "ok" for k, v in checks.items() if k != "disk_free_gb")
    status_code = 200 if all_ok else 503

    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=checks)
