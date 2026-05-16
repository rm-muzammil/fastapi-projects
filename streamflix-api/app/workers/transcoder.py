import asyncio, os, json
from pathlib import Path
from arq.connections import RedisSettings

# Base paths
INPUT_DIR   = Path("/home") / os.environ.get("USER", "rm") / "projects/streamflix/input"
OUTPUT_DIR  = Path("/home") / os.environ.get("USER", "rm") / "projects/streamflix/output"
HLS_DIR     = Path("/home") / os.environ.get("USER", "rm") / "projects/nextjs/streamflix/public/hls_content"
KEY_INFO    = Path("/home") / os.environ.get("USER", "rm") / "projects/streamflix/hls_encrypted/key.info"

QUALITIES = [
    {"name": "360p",  "height": 360,  "crf": 28, "audio": "96k"},
    {"name": "720p",  "height": 720,  "crf": 23, "audio": "128k"},
    {"name": "1080p", "height": 1080, "crf": 20, "audio": "192k"},
]

async def run_cmd(cmd: str, ctx: dict, job_id: str, step: str):
    """Run a shell command, update job progress in Redis."""
    redis = ctx["redis"]
    await redis.hset(f"job:{job_id}", mapping={"step": step, "status": "processing"})
    
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed at {step}: {stderr.decode()[-500:]}")
    
    return stdout.decode()

async def transcode_video(ctx: dict, job_id: str, filename: str):
    """
    Main worker function. ARQ calls this automatically when a job is dequeued.
    ctx is injected by ARQ — contains redis connection and worker settings.
    """
    redis     = ctx["redis"]
    input_path = INPUT_DIR / filename

    if not input_path.exists():
        await redis.hset(f"job:{job_id}", mapping={
            "status": "failed", "error": f"File not found: {filename}"
        })
        return

    # Mark job as started
    await redis.hset(f"job:{job_id}", mapping={
        "status": "processing", "filename": filename, "progress": "0"
    })

    try:
        for i, q in enumerate(QUALITIES):
            name   = q["name"]
            height = q["height"]
            crf    = q["crf"]
            audio  = q["audio"]

            out_mp4 = OUTPUT_DIR / name / f"{job_id}.mp4"
            out_mp4.parent.mkdir(parents=True, exist_ok=True)

            hls_dir = HLS_DIR / job_id / name
            hls_dir.mkdir(parents=True, exist_ok=True)

            # Step 1: transcode to target quality
            await run_cmd(
                f'ffmpeg -y -i "{input_path}" '
                f'-vf scale=-2:{height} -c:v libx264 -crf {crf} -preset fast '
                f'-c:a aac -b:a {audio} -g 180 '  # -g 180 = keyframe every 6s at 30fps
                f'"{out_mp4}"',
                ctx, job_id, f"transcoding_{name}"
            )

            # Step 2: segment into HLS
            await run_cmd(
                f'ffmpeg -y -i "{out_mp4}" '
                f'-c copy -f hls -hls_time 6 -hls_playlist_type vod '
                f'-hls_key_info_file "{KEY_INFO}" '
                f'-hls_segment_filename "{hls_dir}/seg_%03d.ts" '
                f'"{hls_dir}/index.m3u8"',
                ctx, job_id, f"segmenting_{name}"
            )

            # Update progress (0%, 33%, 66%)
            progress = int(((i + 1) / len(QUALITIES)) * 100)
            await redis.hset(f"job:{job_id}", "progress", str(progress))

        # Write master playlist
        master = HLS_DIR / job_id / "master.m3u8"
        master.write_text(
            "#EXTM3U\n#EXT-X-VERSION:3\n\n"
            f"#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360\n360p/index.m3u8\n\n"
            f"#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n720p/index.m3u8\n\n"
            f"#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080\n1080p/index.m3u8\n"
        )

        # Mark complete
        await redis.hset(f"job:{job_id}", mapping={
            "status": "done",
            "progress": "100",
            "stream_url": f"/hls_content/{job_id}/master.m3u8"
        })

    except Exception as e:
        await redis.hset(f"job:{job_id}", mapping={
            "status": "failed", "error": str(e)[:500]
        })

# ARQ worker settings — ARQ reads this automatically
class WorkerSettings:
    functions     = [transcode_video]
    redis_settings = RedisSettings(host="localhost", port=6379)
    max_jobs      = 2   # max 2 transcoding jobs at once (CPU bound)
    job_timeout   = 600 # 10 min max per job
