import os
import re
import zipfile
import shutil
import asyncio
import subprocess
import edge_tts
from pathlib import Path

from aiohttp import web

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================
# ENVIRONMENT
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()

PORT = int(os.getenv("PORT", "8000"))

BASE_URL = os.environ.get(
    "BASE_URL",
    ""
).rstrip("/")

WEBHOOK_SECRET = "futureunchartedsecret"

if not BASE_URL:
    raise SystemExit(
        "BASE_URL environment variable is missing."
    )


# =========================
# WORK DIRECTORY
# =========================

WORK = Path("work")

WORK.mkdir(
    exist_ok=True
)


# =========================
# RUN COMMAND
# =========================

def run(cmd):

    process = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if process.returncode != 0:

        raise RuntimeError(
            process.stdout[-5000:]
        )

    return process.stdout


# =========================
# FIND IMAGES
# =========================

def find_media(root):

    images = []

    for ext in (
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.webp"
    ):

        images += list(
            root.rglob(ext)
        )

    def sort_key(path):

        match = re.search(
            r"(\d+)",
            path.stem
        )

        if match:
            return int(match.group(1))

        return 9999

    return sorted(
        images,
        key=sort_key
    )


# =========================
# READ SCRIPT
# =========================

def read_scene_script(root):

    candidates = [
        root / "SCRIPT.txt",
        root / "script.txt",
        root / "VOICEOVER.txt",
        root / "voiceover.txt",
    ]

    text = ""

    for file in candidates:

        if file.exists():

            text = file.read_text(
                encoding="utf-8",
                errors="ignore"
            )

            break

    if not text.strip():
        return []

    matches = re.findall(
        r"SCENE\s+(\d+)\s*:\s*(.*?)(?=\n\s*SCENE\s+\d+\s*:|\Z)",
        text,
        flags=re.I | re.S
    )

    scenes = {}

    for number, body in matches:

        body = " ".join(
            body.split()
        )

        if body:

            scenes[int(number)] = body

    return [
        scenes[number]
        for number in sorted(scenes)
    ]


    
# =========================
# LOCAL ENGLISH TTS
# =========================

def make_voice(text, output):
    async def _tts():
        communicate = edge_tts.Communicate(
            text=text,
            voice="en-US-GuyNeural",
            rate="-5%",
            pitch="+0Hz"
        )
        await communicate.save(str(output))

    asyncio.run(_tts())

    if not output.exists():
        raise RuntimeError("Edge TTS səs yarada bilmədi.")


# =========================
# CREATE SCENE VIDEO
# =========================

def make_scene_clip(image, output, duration=5):
    run([
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image),
        "-t",
        str(duration),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p",
        "-r",
        "24",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(output)
    ])


# =========================
# AMBIENT MUSIC
# =========================

def make_ambient_music(
    output,
    duration
):

    run([
        "ffmpeg",
        "-y",

        "-f",
        "lavfi",

        "-i",
        f"sine=frequency=110:duration={duration}",

        "-f",
        "lavfi",

        "-i",
        f"sine=frequency=164.81:duration={duration}",

        "-filter_complex",

        "[0:a]volume=0.045[a];"
        "[1:a]volume=0.025[b];"
        "[a][b]amix=inputs=2:duration=longest,"
        "lowpass=f=700,"
        "afade=t=in:st=0:d=5,"
        f"afade=t=out:st={max(0, duration - 5)}:d=5[aout]",

        "-map",
        "[aout]",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        str(output)
    ])


# =========================
# SOUND EFFECT
# =========================

def make_sfx(
    output,
    duration
):

    run([
        "ffmpeg",
        "-y",

        "-f",
        "lavfi",

        "-i",
        f"sine=frequency=55:duration={duration}",

        "-af",

        "volume=0.018,"
        "lowpass=f=180,"
        "afade=t=in:st=0:d=2,"
        f"afade=t=out:st={max(0, duration - 3)}:d=3",

        "-c:a",
        "aac",

        "-b:a",
        "96k",

        str(output)
    ])


# =========================
# SHORTS VIDEO
# =========================

def make_shorts_video(input_video, output_video):
    run([
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-t", "60",
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "fps=30",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-b:a", "128k",
        str(output_video)

    ])


# =========================
# FINAL VIDEO
# =========================

def make_final_video(
    root,
    output
):

    images = find_media(root)

    if len(images) != 32:

        raise RuntimeError(
            f"ZIP-də 32 səhnə gözlənilir, "
            f"{len(images)} şəkil tapıldı."
        )

    scenes = read_scene_script(root)

    if len(scenes) != 32:

        raise RuntimeError(
            f"SCRIPT.txt-də 32 SCENE gözlənilir, "
            f"{len(scenes)} tapıldı."
        )

    work = root / "_video_work"

    work.mkdir(
        exist_ok=True
    )

    clips = []
    voices = []

    

    # =========================
    # CREATE 32 SCENES
    # =========================

    for i, (
        image,
        text
    ) in enumerate(
        zip(images[:32], scenes),
        1
    ):

        clip = (
            work /
            f"scene_{i:02d}.mp4"
        )

        voice = (
            work /
            f"voice_{i:02d}.wav"
        )

        print(
            f"Scene {i}/32"
        )

        make_scene_clip(
            image,
            clip,
            duration=5
        )

        make_voice(
            text,
            voice
        )

        clips.append(
            clip
        )

        voices.append(
            voice
        )

    # =========================
    # JOIN VIDEO
    # =========================

    concat = (
        work /
        "concat.txt"
    )

    concat.write_text(
        "\n".join(
            f"file '{c.resolve()}'"
            for c in clips
        ),
        encoding="utf-8"
    )

    silent = (
        work /
        "silent.mp4"
    )

    run([
        "ffmpeg",
        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        str(concat),

        "-c",
        "copy",

        str(silent)
    ])

    # =========================
    # JOIN VOICE
    # =========================

    voice_concat = (
        work /
        "voice_concat.txt"
    )

    voice_concat.write_text(
        "\n".join(
            f"file '{v.resolve()}'"
            for v in voices
        ),
        encoding="utf-8"
    )

    voice = (
        work /
        "voiceover.wav"
    )

    run([
        "ffmpeg",
        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        str(voice_concat),

        "-c:a",
        "pcm_s16le",

        str(voice)
    ])

    # =========================
    # AUDIO
    # =========================

    duration = 160

    music = (
        work /
        "music.m4a"
    )

    sfx = (
        work /
        "sfx.m4a"
    )

    make_ambient_music(
        music,
        duration
    )

    make_sfx(
        sfx,
        duration
    )

    # =========================
    # FINAL VIDEO
    # =========================

    run([
        "ffmpeg",
        "-y",

        "-i",
        str(silent),

        "-i",
        str(voice),

        "-i",
        str(music),

        "-i",
        str(sfx),

        "-filter_complex",

        "[1:a]volume=1.0[vo];"
        "[2:a]volume=0.10[mus];"
        "[3:a]volume=0.05[fx];"
        "[vo][mus][fx]"
        "amix=inputs=3:"
        "duration=longest:"
        "dropout_transition=2"
        "[aout]",

        "-map",
        "0:v",

        "-map",
        "[aout]",

        "-c:v",
        "copy",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-shortest",

        str(output)
    ])

    return output


# =========================
# START COMMAND
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "ZIP-i göndər.\n\n"
        "İçində 32 səhnə şəkli və "
        "SCRIPT.txt olmalıdır."
    )


# =========================
# ZIP HANDLER
# =========================

async def handle_zip(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    document = update.message.document

    if not document:

        await update.message.reply_text(
            "ZIP faylı göndər."
        )

        return

    file_name = (
        document.file_name
        or ""
    )

    if not file_name.lower().endswith(
        ".zip"
    ):

        await update.message.reply_text(
            "Zəhmət olmasa ZIP faylı göndər."
        )

        return

    job = (
        WORK /
        str(update.effective_user.id)
    )

    shutil.rmtree(
        job,
        ignore_errors=True
    )

    job.mkdir(
        parents=True
    )

    await update.message.reply_text(
        "ZIP gəldi. Açıb yoxlayıram…"
    )

    telegram_file = (
        await document.get_file()
    )

    zip_path = (
        job /
        "input.zip"
    )

    await telegram_file.download_to_drive(
        str(zip_path)
    )

    root = (
        job /
        "package"
    )

    root.mkdir()

    try:

        with zipfile.ZipFile(
            zip_path
        ) as archive:

            archive.extractall(
                root
            )

    except zipfile.BadZipFile:

        await update.message.reply_text(
            "ZIP faylı zədəlidir."
        )

        shutil.rmtree(
            job,
            ignore_errors=True
        )

        return

    output = (
        job /
        "FUTURE_UNCHARTED_FINAL.mp4"
    )

    try:

        await update.message.reply_text(
            "Montaj başlayır…\n\n"
            "32 səhnə + voice-over + "
            "music + SFX hazırlanır."
        )

        await asyncio.to_thread(
            make_final_video,
            root,
            output
        )

        with open(
            output,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                caption=(
                    "Future Uncharted — Hazırdır 🎬"
                )
            )

    except Exception as error:

        await update.message.reply_text(
            "Video hazırlana bilmədi:\n\n"
            + str(error)[:3500]
        )

    finally:

        shutil.rmtree(
            job,
            ignore_errors=True
        )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video

    if not video:
        return

    job = WORK / f"shorts_{update.effective_user.id}"

    shutil.rmtree(job, ignore_errors=True)
    job.mkdir(parents=True)

    await update.message.reply_text(
        "🎬 Shorts hazırlanır… (60 saniyə, 9:16)"
    )

    tg_file = await video.get_file()

    input_path = job / "input.mp4"
    output_path = job / "YOUTUBE_SHORTS.mp4"

    await tg_file.download_to_drive(str(input_path))

    try:
        await asyncio.to_thread(
            make_shorts_video,
            input_path,
            output_path
        )

        with open(output_path, "rb") as fh:
            await update.message.reply_video(
                video=fh,
                caption="📱 YouTube Shorts hazırdır 🚀"
            )

    except Exception as e:
        await update.message.reply_text(
            "Shorts hazırlana bilmədi:\n" + str(e)[:3000]
        )

    finally:
        shutil.rmtree(job, ignore_errors=True)


# =========================
# VIDEO → SHORTS HANDLER
# =========================

async def handle_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    video = update.message.video

    if not video:
        return

    job = WORK / f"shorts_{update.effective_user.id}"

    shutil.rmtree(job, ignore_errors=True)
    job.mkdir(parents=True)

    await update.message.reply_text(
        "📱 Shorts hazırlanır… (60 saniyə, 9:16)"
    )

    tg_file = await video.get_file()

    input_path = job / "input.mp4"
    output_path = job / "YOUTUBE_SHORTS.mp4"

    await tg_file.download_to_drive(str(input_path))

    try:
        await asyncio.to_thread(
            make_shorts_video,
            input_path,
            output_path
        )

        with open(output_path, "rb") as fh:
            await update.message.reply_video(
                video=fh,
                caption="📱 YouTube Shorts hazırdır 🚀"
            )

    except Exception as e:
        await update.message.reply_text(
            "Shorts hazırlana bilmədi:\n\n" + str(e)[:3000]
        )

    finally:
        shutil.rmtree(job, ignore_errors=True)
        
# =========================
# HEALTH
# =========================

async def health(request):

    return web.Response(
        text="OK"
    )


# =========================
# WEBHOOK
# =========================

async def telegram_webhook(
    request
):

    data = await request.json()

    update = Update.de_json(
        data,
        application.bot
    )

    await application.update_queue.put(
        update
    )

    return web.Response(
        text="OK"
    )


# =========================
# STARTUP
# =========================

async def on_startup(app):

    await application.bot.set_webhook(

        url=(
            f"{BASE_URL}/telegram/"
            f"{WEBHOOK_SECRET}"
        ),

        secret_token=WEBHOOK_SECRET
    )


# =========================
# CLEANUP
# =========================

async def on_cleanup(app):

    await application.bot.delete_webhook(
        drop_pending_updates=False
    )

    await application.stop()

    await application.shutdown()


# =========================
# TELEGRAM APP
# =========================

application = (
    Application
    .builder()
    .token(BOT_TOKEN)
    .updater(None)
    .build()
)

application.add_handler(
    CommandHandler(
        "start",
        start
    )
)

application.add_handler(
    MessageHandler(
        filters.Document.ALL,
        handle_zip
    )
)

application.add_handler(
    MessageHandler(
        filters.VIDEO,
        handle_video
    )
)
# =========================
# WEB APP
# =========================

async def init_app():

    await application.initialize()

    await application.start()

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_post(
        "/telegram/{secret}",
        telegram_webhook
    )

    app.on_startup.append(
        on_startup
    )

    app.on_cleanup.append(
        on_cleanup
    )

    return app


# =========================
# RUN
# =========================

if __name__ == "__main__":

    web.run_app(
        init_app(),
        host="0.0.0.0",
        port=PORT
    )
