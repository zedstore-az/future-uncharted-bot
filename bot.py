import os
import re
import zipfile
import shutil
import asyncio
import subprocess
import time

from pathlib import Path
from aiohttp import web
from gtts import gTTS

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

WEBHOOK_SECRET = "futureunchartedsecret"
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

if not BASE_URL:
    raise SystemExit("BASE_URL environment variable is missing.")


# =========================
# WORK DIRECTORY
# =========================

WORK = Path("work")
WORK.mkdir(exist_ok=True)


# =========================
# RUN FFMPEG
# =========================

def run(cmd):
    process = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if process.returncode != 0:
        raise RuntimeError(process.stdout[-5000:])

    return process.stdout


# =========================
# FIND IMAGES
# =========================

def find_media(root):
    images = []

    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        images += list(root.rglob(ext))

    def sort_key(path):
        match = re.search(r"(\d+)", path.stem)

        if match:
            return int(match.group(1))

        return 9999

    return sorted(images, key=sort_key)


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

        body = " ".join(body.split())

        if body:
            scenes[int(number)] = body

    return [
        scenes[number]
        for number in sorted(scenes)
    ]


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

        "scale=1280:720:"
        "force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
        "zoompan="
        "z='min(zoom+0.0008,1.08)':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        "d=150:"
        "s=1280x720:"
        "fps=30,"
        "format=yuv420p",

        "-an",

        str(output)
    ])


# =========================
# CREATE VOICE
# =========================

def make_voice(text, output):

    # Mətn hissələrə bölünür
    parts = [
        text[i:i + 180]
        for i in range(0, len(text), 180)
    ]

    temp_files = []

    for index, part in enumerate(parts):

        temp_mp3 = str(output) + f".part{index}.mp3"

        success = False

        for attempt in range(5):

            try:

                tts = gTTS(
                    text=part,
                    lang="en",
                    slow=False
                )

                tts.save(temp_mp3)

                success = True

                break

            except Exception:

                if attempt < 4:
                    time.sleep(5)

        if not success:

            raise RuntimeError(
                "TTS səs yaradıla bilmədi. "
                "Google TTS limitinə düşülmüş ola bilər."
            )

        temp_files.append(temp_mp3)

    # Əgər yalnız bir hissədirsə
    if len(temp_files) == 1:

        shutil.copyfile(
            temp_files[0],
            output
        )

    else:

        concat_txt = str(output) + "_concat.txt"

        with open(
            concat_txt,
            "w",
            encoding="utf-8"
        ) as file:

            for mp3 in temp_files:

                file.write(
                    f"file '{os.path.abspath(mp3)}'\n"
                )

        run([
            "ffmpeg",
            "-y",

            "-f",
            "concat",

            "-safe",
            "0",

            "-i",
            concat_txt,

            "-c:a",
            "libmp3lame",

            "-b:a",
            "160k",

            str(output)
        ])

        try:
            os.remove(concat_txt)
        except:
            pass

    # Müvəqqəti faylları sil
    for mp3 in temp_files:

        try:
            os.remove(mp3)
        except:
            pass


# =========================
# AMBIENT MUSIC
# =========================

def make_ambient_music(output, duration):

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

def make_sfx(output, duration):

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
# CREATE FINAL VIDEO
# =========================

def make_final_video(root, output):

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

    for i, (image, text) in enumerate(
        zip(images[:32], scenes),
        1
    ):

        clip = work / f"scene_{i:02d}.mp4"

        voice = work / f"voice_{i:02d}.mp3"

        make_scene_clip(
            image,
            clip,
            duration=5
        )

        make_voice(
            text,
            voice
        )

        clips.append(clip)
        voices.append(voice)

    # =========================
    # JOIN VIDEO
    # =========================

    concat = work / "concat.txt"

    concat.write_text(
        "\n".join(
            f"file '{c.resolve()}'"
            for c in clips
        ),
        encoding="utf-8"
    )

    silent = work / "silent.mp4"

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
    # JOIN VOICES
    # =========================

    voice_concat = work / "voice_concat.txt"

    voice_concat.write_text(
        "\n".join(
            f"file '{v.resolve()}'"
            for v in voices
        ),
        encoding="utf-8"
    )

    voice = work / "voiceover.mp3"

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
        "libmp3lame",

        "-b:a",
        "160k",

        str(voice)
    ])

    # =========================
    # AUDIO
    # =========================

    duration = 160

    music = work / "music.m4a"

    sfx = work / "sfx.m4a"

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
# TELEGRAM /START
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
# TELEGRAM ZIP HANDLER
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

    file_name = document.file_name or ""

    if not file_name.lower().endswith(".zip"):

        await update.message.reply_text(
            "Zəhmət olmasa ZIP faylı göndər."
        )

        return

    job = WORK / str(
        update.effective_user.id
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

    telegram_file = await document.get_file()

    zip_path = job / "input.zip"

    await telegram_file.download_to_drive(
        str(zip_path)
    )

    root = job / "package"

    root.mkdir()

    try:

        with zipfile.ZipFile(
            zip_path
        ) as archive:

            archive.extractall(root)

    except zipfile.BadZipFile:

        await update.message.reply_text(
            "ZIP faylı zədəlidir və ya düzgün ZIP deyil."
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


# =========================
# HEALTH CHECK
# =========================

async def health(request):

    return web.Response(
        text="OK"
    )


# =========================
# TELEGRAM WEBHOOK
# =========================

async def telegram_webhook(request):

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
# TELEGRAM APPLICATION
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
