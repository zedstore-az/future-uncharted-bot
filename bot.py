import os
import zipfile
import shutil
import asyncio
import subprocess
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8000"))

WORK = Path("work")
WORK.mkdir(exist_ok=True)


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


def run_ffmpeg(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stdout[-5000:])

    return result.stdout


def find_media(root):
    images = []
    videos = []

    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        images.extend(root.rglob(ext))

    for ext in ("*.mp4", "*.mov", "*.mkv", "*.webm"):
        videos.extend(root.rglob(ext))

    return sorted(images), sorted(videos)


def create_video_from_images(images, output):
    clips_dir = output.parent / "clips"
    clips_dir.mkdir(exist_ok=True)

    clips = []

    for index, image in enumerate(images, 1):

        clip = clips_dir / f"clip_{index:04d}.mp4"

        run_ffmpeg([
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image),
            "-t",
            "5",
            "-vf",
            (
                "scale=1280:720:"
                "force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
                "format=yuv420p"
            ),
            "-r",
            "30",
            "-an",
            str(clip)
        ])

        clips.append(clip)

    concat_file = clips_dir / "concat.txt"

    concat_file.write_text(
        "\n".join(
            f"file '{clip.resolve()}'"
            for clip in clips
        ),
        encoding="utf-8"
    )

    run_ffmpeg([
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output)
    ])


def create_video_from_videos(videos, output):
    clips_dir = output.parent / "video_clips"
    clips_dir.mkdir(exist_ok=True)

    normalized = []

    for index, video in enumerate(videos, 1):

        clip = clips_dir / f"video_{index:04d}.mp4"

        run_ffmpeg([
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vf",
            (
                "scale=1280:720:"
                "force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
                "format=yuv420p"
            ),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            str(clip)
        ])

        normalized.append(clip)

    concat_file = clips_dir / "concat.txt"

    concat_file.write_text(
        "\n".join(
            f"file '{video.resolve()}'"
            for video in normalized
        ),
        encoding="utf-8"
    )

    run_ffmpeg([
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output)
    ])


def add_subtitles(video, srt, output):
    subtitle_path = str(srt.resolve())
    subtitle_path = subtitle_path.replace("\\", "/")
    subtitle_path = subtitle_path.replace(":", "\\:")

    run_ffmpeg([
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"subtitles='{subtitle_path}'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "copy",
        str(output)
    ])


def build_video(root, output):

    images, videos = find_media(root)

    # Heç bir hazır görüntü yoxdursa
    if not images:
