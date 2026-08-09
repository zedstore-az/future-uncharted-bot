import os, re, zipfile, shutil, asyncio, subprocess
from pathlib import Path
from aiohttp import web
import requests

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import asyncio
import edge_tts

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "8000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "future-uncharted-secret")
WORK = Path("work")
WORK.mkdir(exist_ok=True)


def run(cmd):
    p = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    if p.returncode:
        raise RuntimeError(p.stdout[-5000:])
    return p.stdout


def find_media(root):
    images = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        images += list(root.rglob(ext))

    return sorted(
        images,
        key=lambda p: (
            int(re.search(r"(\d+)", p.stem).group(1))
            if re.search(r"(\d+)", p.stem)
            else 9999
        )
    )


def read_scene_script(root):
    candidates = [
        root / "SCRIPT.txt",
        root / "script.txt",
        root / "VOICEOVER.txt",
        root / "voiceover.txt",
    ]

    text = ""
    for f in candidates:
        if f.exists():
            text = f.read_text(encoding="utf-8", errors="ignore")
            break

    if not text.strip():
        return []

    matches = re.findall(
        r"SCENE\s+(\d+)\s*:\s*(.*?)(?=\n\s*SCENE\s+\d+\s*:|\Z)",
        text,
        flags=re.I | re.S,
    )

    scenes = {}
    for number, body in matches:
        body = " ".join(body.split())
        if body:
            scenes[int(number)] = body

    return [scenes[n] for n in sorted(scenes)]


def make_scene_clip(image, output, duration=5):
    run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image),
        "-t", str(duration),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
        "zoompan=z='min(zoom+0.0008,1.08)':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        "d=150:s=1280x720:fps=30,format=yuv420p",
        "-an",
        str(output)
    ])


def make_voice(text, output):
    async def _run():
        communicate = edge_tts.Communicate(
            text=text,
            voice="en-US-GuyNeural"
        )
        await communicate.save(str(output))

    asyncio.run(_run())

def make_ambient_music(output, duration):
    run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=110:duration={duration}",
        "-f", "lavfi",
        "-i", f"sine=frequency=164.81:duration={duration}",
        "-filter_complex",
        "[0:a]volume=0.045[a];"
        "[1:a]volume=0.025[b];"
        "[a][b]amix=inputs=2:duration=longest,"
        "lowpass=f=700,"
        "afade=t=in:st=0:d=5,"
        f"afade=t=out:st={max(0, duration-5)}:d=5[aout]",
        "-map", "[aout]",
        "-c:a", "aac",
        "-b:a", "128k",
        str(output)
    ])


def make_sfx(output, duration):
    run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=55:duration={duration}",
        "-af",
        "volume=0.018,lowpass=f=180,"
        "afade=t=in:st=0:d=2,"
        f"afade=t=out:st={max(0, duration-3)}:d=3",
        "-c:a", "aac",
        "-b:a", "96k",
        str(output)
    ])


def make_final_video(root, output):
    images = find_media(root)[:32]

    if len(images) != 32:
        raise RuntimeError(
            f"ZIP-də 32 səhnə gözlənilir, {len(images)} şəkil tapıldı."
        )

    scenes = read_scene_script(root)

    if len(scenes) != 32:
        raise RuntimeError(
            f"SCRIPT.txt-də 32 SCENE gözlənilir, {len(scenes)} tapıldı."
        )

    work = root / "_video_work"
    work.mkdir(exist_ok=True)

    clips = []
    voices = []

    for i, (image, text) in enumerate(zip(images, scenes), 1):
        clip = work / f"scene_{i:02d}.mp4"
        voice = work / f"voice_{i:02d}.mp3"

        make_scene_clip(image, clip, duration=5)
        make_voice(text, voice)

        clips.append(clip)
        voices.append(voice)

    concat = work / "concat.txt"
    concat.write_text(
        "\n".join(f"file '{c.resolve()}'" for c in clips),
        encoding="utf-8"
    )

    silent = work / "silent.mp4"

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat),
        "-c", "copy",
        str(silent)
    ])

    voice_concat = work / "voice_concat.txt"
    voice_concat.write_text(
        "\n".join(f"file '{v.resolve()}'" for v in voices),
        encoding="utf-8"
    )

    voice = work / "voiceover.mp3"

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(voice_concat),
        "-c:a", "libmp3lame",
        "-b:a", "160k",
        str(voice)
    ])

    duration = 160

    music = work / "music.m4a"
    sfx = work / "sfx.m4a"

    make_ambient_music(music, duration)
    make_sfx(sfx, duration)

    run([
        "ffmpeg", "-y",
        "-i", str(silent),
        "-i", str(voice),
        "-i", str(music),
        "-i", str(sfx),
        "-filter_complex",
        "[1:a]volume=1.0[vo];"
        "[2:a]volume=0.10[mus];"
        "[3:a]volume=0.05[fx];"
        "[vo][mus][fx]amix=inputs=3:duration=longest:"
        "dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output)
    ])

    return output


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ZIP-i göndər. İçində 32 səhnə və SCRIPT.txt olsun."
    )


async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document

    if not doc or not doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text("ZIP faylı göndər.")
        return

    job = WORK / str(update.effective_user.id)

    shutil.rmtree(job, ignore_errors=True)
    job.mkdir(parents=True)

    await update.message.reply_text("ZIP gəldi. Açıb yoxlayıram…")

    f = await doc.get_file()
    zp = job / "input.zip"

    await f.download_to_drive(str(zp))

    root = job / "package"
    root.mkdir()

    with zipfile.ZipFile(zp) as z:
        z.extractall(root)

    out = job / "FUTURE_UNCHARTED_FINAL.mp4"

    try:
        await update.message.reply_text(
            "Montaj başlayır… 32 səhnə + voice-over + music + SFX."
        )

        await asyncio.to_thread(
            make_final_video,
            root,
            out
        )

        with open(out, "rb") as fh:
            await update.message.reply_video(
                video=fh,
                caption="Future Uncharted — Hazırdır 🎬"
            )

    except Exception as e:
        await update.message.reply_text(
            "Video hazırlana bilmədi:\n" + str(e)[:3500]
        )

    finally:
        shutil.rmtree(job, ignore_errors=True)


async def health(request):
    return web.Response(text="OK")


async def telegram_webhook(request):
    if request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    ) != WEBHOOK_SECRET:
        return web.Response(status=403, text="forbidden")

    data = await request.json()

    await application.update_queue.put(
        Update.de_json(data, application.bot)
    )

    return web.Response(text="OK")


async def on_startup(app):
    await application.bot.set_webhook(
        url=f"{BASE_URL}/telegram/{WEBHOOK_SECRET}",
        secret_token=WEBHOOK_SECRET
    )


async def on_cleanup(app):
    await application.bot.delete_webhook(
        drop_pending_updates=False
    )
    await application.shutdown()


BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

if not BASE_URL:
    raise SystemExit(
        "BASE_URL environment variable is missing."
    )


application = (
    Application
    .builder()
    .token(BOT_TOKEN)
    .updater(None)
    .build()
)

application.add_handler(
    CommandHandler("start", start)
)

application.add_handler(
    MessageHandler(filters.Document.ALL, handle_zip)
)


async def init_app():
    await application.initialize()
    await application.start()

    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_post(
        "/telegram/{secret}",
        telegram_webhook
    )

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app


if __name__ == "__main__":
    web.run_app(
        init_app(),
        host="0.0.0.0",
        port=PORT
    )
