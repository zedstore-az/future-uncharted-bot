import os
import re
import zipfile
import shutil
import asyncio
import subprocess
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()

PORT = int(os.getenv("PORT", "8000"))

WORK = Path("work")
WORK.mkdir(exist_ok=True)


# Render üçün sadə HTTP server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


def run(cmd):
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if p.returncode != 0:
        raise RuntimeError(p.stdout[-4000:])

    return p.stdout


def parse_prompts(root):
    f = root / "VISUAL_PROMPTS.txt"

    if not f.exists():
        return []

    out = []

    for line in f.read_text(
        encoding="utf-8",
        errors="ignore"
    ).splitlines():

        s = line.strip()

        if not s or s.startswith("#"):
            continue

        m = re.match(
            r"^(?:SHOT\s*)?(\d{1,3})\s*[:\-]\s*(.+)$",
            s,
            re.I
        )

        if m:
            out.append(
                (
                    int(m.group(1)),
                    m.group(2).strip()
                )
            )
        else:
            out.append(
                (
                    len(out) + 1,
                    s
                )
            )

    return out


def generate_image(prompt, out):
    if not POLLINATIONS_API_KEY:
        return False

    url = (
        "https://gen.pollinations.ai/image/"
        + requests.utils.quote(
            prompt
            + ", cinematic documentary frame, "
              "realistic, 16:9, no text, no watermark",
            safe=""
        )
    )

    r = requests.get(
        url,
        params={
            "model": "flux",
            "width": 1280,
            "height": 720,
            "key": POLLINATIONS_API_KEY
        },
        timeout=180
    )

    r.raise_for_status()

    out.write_bytes(r.content)

    return True


def make_video(root, out_mp4):

    images = []

    for ext in (
        "*.jpg",
        "*.jpeg",
        "*.png",
        "*.webp"
    ):
        images += list(root.rglob(ext))

    videos = []

    for ext in (
        "*.mp4",
        "*.mov",
        "*.mkv",
        "*.webm"
    ):
        videos += list(root.rglob(ext))

    # Əgər ZIP-də hazır media yoxdursa,
    # VISUAL_PROMPTS.txt istifadə olunur.
    if not images and not videos:

        prompts = parse_prompts(root)

        if not prompts:
            raise RuntimeError(
                "ZIP-də media və VISUAL_PROMPTS.txt yoxdur."
            )

        gen = root / "_generated"
        gen.mkdir(exist_ok=True)

        for n, prompt in prompts:

            target = gen / f"{n:03d}.jpg"

            if not generate_image(prompt, target):
                raise RuntimeError(
                    "Hazır görüntü yoxdur. "
                    "AI görüntü yaratmaq üçün "
                    "POLLINATIONS_API_KEY lazımdır."
                )

            images.append(target)

    # Şəkillərdən video hazırlayırıq
    if images:

        work = root / "_clips"
        work.mkdir(exist_ok=True)

        clips = []

        for i, img in enumerate(
            sorted(images),
            1
        ):

            clip = work / f"clip_{i:03d}.mp4"

            run([
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(img),
                "-t",
                "5",
                "-vf",
                "scale=1280:720:"
                "force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
                "format=yuv420p",
                "-r",
                "30",
                "-an",
                str(clip)
            ])

            clips.append(clip)

        concat = work / "concat.txt"

        concat.write_text(
            "\n".join(
                f"file '{c.resolve()}'"
                for c in clips
            ),
            encoding="utf-8"
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
            str(out_mp4)
        ])

        # Subtitle varsa əlavə et
        srt = root / "SUBTITLES_EN.srt"

        if srt.exists():

            subbed = out_mp4.with_name(
                out_mp4.stem + "_sub.mp4"
            )

            sp = str(
                srt.resolve()
            ).replace("\\", "\\\\").replace(
                ":", "\\:"
            )

            run([
                "ffmpeg",
                "-y",
                "-i",
                str(out_mp4),
                "-vf",
                f"subtitles='{sp}'",
                "-c:a",
                "copy",
                str(subbed)
            ])

            shutil.move(
                subbed,
                out_mp4
            )

        return out_mp4

    raise RuntimeError(
        "Uyğun media tapılmadı."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Salam! 👋\n\n"
        "ZIP-i göndər. Mən ZIP-i açıb "
        "materialları tapacaq və MP4 hazırlayacağam. 🎬"
    )


async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):

    doc = update.message.document

    if not doc:
        return

    filename = doc.file_name or ""

    if not filename.lower().endswith(".zip"):

        await update.message.reply_text(
            "Zəhmət olmasa ZIP faylı göndər."
        )

        return

    await update.message.reply_text(
        "ZIP gəldi. 📦\n"
        "Açıb yoxlayıram..."
    )

    job = WORK / str(
        update.effective_user.id
    )

    if job.exists():
        shutil.rmtree(job)

    job.mkdir(parents=True)

    try:

        # ZIP-i Telegram-dan yüklə
        tg_file = await doc.get_file()

        zip_path = job / "input.zip"

        await tg_file.download_to_drive(
            str(zip_path)
        )

        await update.message.reply_text(
            "ZIP açılır və materiallar yoxlanılır..."
        )

        root = job / "package"
        root.mkdir()

        with zipfile.ZipFile(zip_path) as z:
            z.extractall(root)

        out = job / "FUTURE_UNCHARTED_FINAL.mp4"

        await update.message.reply_text(
            "Montaj başlayır... 🎬\n"
            "Bir az gözlə."
        )

        await asyncio.to_thread(
            make_video,
            root,
            out
        )

        # MP4 Telegram-a göndər
        with open(out, "rb") as fh:

            await update.message.reply_video(
                video=fh,
                caption="Hazırdır! 🎬✅"
            )

    except Exception as e:

        await update.message.reply_text(
            "Video hazırlana bilmədi ❌\n\n"
            + str(e)[:3500]
        )

    finally:

        shutil.rmtree(
            job,
            ignore_errors=True
        )


def main():

    if not BOT_TOKEN:

        raise SystemExit(
            "BOT_TOKEN yoxdur."
        )

    # Render üçün HTTP server
    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    print("Future Uncharted Bot başlayır...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_zip
        )
    )

    print("Telegram polling aktivdir.")

    app.run_polling()


if __name__ == "__main__":
    main()
