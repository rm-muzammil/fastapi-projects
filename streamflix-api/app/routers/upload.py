from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import aiofiles, uuid, os
from pathlib import Path
from arq.connections import RedisSettings, create_pool
from redis.exceptions import ConnectionError as RedisConnectionError

INPUT_DIR      = Path("/home") / os.environ.get("USER", "rm") / "projects/streamflix/input"
REDIS_SETTINGS = RedisSettings(host="localhost", port=6379)
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}

router = APIRouter(prefix="/upload", tags=["upload"])

async def get_redis():
    """Get Redis connection with graceful failure."""
    try:
        redis = await create_pool(REDIS_SETTINGS)
        return redis
    except (RedisConnectionError, OSError, Exception):
        return None

@router.post("/")
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type {ext} not allowed")

    job_id   = str(uuid.uuid4())[:8]
    filename = f"{job_id}{ext}"
    filepath = INPUT_DIR / filename

    # Save file first — even if Redis is down, don't lose the upload
    try:
        async with aiofiles.open(filepath, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                await f.write(chunk)
    except OSError as e:
        raise HTTPException(507, f"Storage error: {e}")

    # Try to enqueue — fail gracefully if Redis is down
    redis = await get_redis()
    if not redis:
        # File is saved — return a pending state
        # Operator can manually requeue when Redis recovers
        return JSONResponse(status_code=202, content={
            "job_id":   job_id,
            "status":   "saved_pending_queue",
            "filename": filename,
            "message":  "File saved. Queue unavailable — will process when recovered.",
        })

    await redis.enqueue_job("transcode_video", job_id=job_id, filename=filename)
    await redis.hset(f"job:{job_id}", mapping={
        "status": "queued", "filename": filename, "progress": "0"
    })
    await redis.close()

    return JSONResponse({
        "job_id":   job_id,
        "status":   "queued",
        "poll_url": f"/upload/jobs/{job_id}",
        "message":  "Transcoding started in background",
    })

@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    redis = await get_redis()

    # Redis is down — return degraded response, not 500
    if not redis:
        return JSONResponse(status_code=503, content={
            "job_id":  job_id,
            "status":  "unknown",
            "message": "Job store temporarily unavailable. Try again shortly.",
        })

    job = await redis.hgetall(f"job:{job_id}")
    await redis.close()

    if not job:
        raise HTTPException(404, "Job not found")

    return {k.decode(): v.decode() for k, v in job.items()}
