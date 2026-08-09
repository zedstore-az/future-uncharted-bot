import os, re, zipfile, shutil, asyncio, subprocess
from pathlib import Path
from aiohttp import web
import requests
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "8000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "future-uncharted-secret")
WORK = Path("work"); WORK.mkdir(exist_ok=True)

def run(cmd):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    if p.returncode: raise RuntimeError(p.stdout[-4000:])
    return p.stdout

def parse_prompts(root):
    f=root/"VISUAL_PROMPTS.txt"
    if not f.exists(): return []
    out=[]
    for line in f.read_text(encoding="utf-8",errors="ignore").splitlines():
        s=line.strip()
        if not s or s.startswith("#"): continue
        m=re.match(r"^(?:SHOT\s*)?(\d{1,3})\s*[:\-]\s*(.+)$",s,re.I)
        out.append((int(m.group(1)),m.group(2).strip()) if m else (len(out)+1,s))
    return out

def generate_image(prompt,out):
    if not POLLINATIONS_API_KEY: return False
    url="https://gen.pollinations.ai/image/"+requests.utils.quote(
        prompt+", cinematic documentary frame, realistic, 16:9, no text, no watermark",safe="")
    r=requests.get(url,params={"model":"flux","width":1280,"height":720,"key":POLLINATIONS_API_KEY},timeout=180)
    r.raise_for_status(); out.write_bytes(r.content); return True

def make_video(root,out_mp4):
    images=[]
    for ext in ("*.jpg","*.jpeg","*.png","*.webp"): images += list(root.rglob(ext))
    if not images:
        prompts=parse_prompts(root)
        if not prompts: raise RuntimeError("ZIP-də media və VISUAL_PROMPTS.txt yoxdur.")
        gen=root/"_generated"; gen.mkdir(exist_ok=True)
        for n,p in prompts:
            target=gen/f"{n:03d}.jpg"
            if not generate_image(p,target):
                raise RuntimeError("ZIP-də hazır görüntü yoxdur. AI görüntüləri üçün POLLINATIONS_API_KEY lazımdır.")
            images.append(target)
    work=root/"_clips"; work.mkdir(exist_ok=True); clips=[]
    for i,img in enumerate(sorted(images),1):
        clip=work/f"clip_{i:03d}.mp4"
        run(["ffmpeg","-y","-loop","1","-i",str(img),"-t","5","-vf",
             "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
             "-r","30","-an",str(clip)])
        clips.append(clip)
    concat=work/"concat.txt"
    concat.write_text("\n".join(f"file '{c.resolve()}'" for c in clips),encoding="utf-8")
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(out_mp4)])
    return out_mp4

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ZIP-i göndər. Mən açıb MP4 hazırlamağa çalışacağam.")

async def handle_zip(update:Update,context:ContextTypes.DEFAULT_TYPE):
    doc=update.message.document
    if not doc or not doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text("ZIP faylı göndər."); return
    job=WORK/str(update.effective_user.id)
    shutil.rmtree(job,ignore_errors=True); job.mkdir(parents=True)
    await update.message.reply_text("ZIP gəldi. Açıb yoxlayıram…")
    f=await doc.get_file(); zp=job/"input.zip"; await f.download_to_drive(str(zp))
    root=job/"package"; root.mkdir()
    with zipfile.ZipFile(zp) as z: z.extractall(root)
    out=job/"FUTURE_UNCHARTED_FINAL.mp4"
    try:
        await update.message.reply_text("Montaj başlayır…")
        await asyncio.to_thread(make_video,root,out)
        with open(out,"rb") as fh: await update.message.reply_video(video=fh,caption="Hazırdır 🎬")
    except Exception as e:
        await update.message.reply_text("Video hazırlana bilmədi:\n"+str(e)[:3500])
    finally: shutil.rmtree(job,ignore_errors=True)

async def health(request): return web.Response(text="OK")

async def telegram_webhook(request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return web.Response(status=403,text="forbidden")
    data=await request.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return web.Response(text="OK")

async def on_startup(app):
    await application.bot.set_webhook(
        url=f"{BASE_URL}/telegram/{WEBHOOK_SECRET}",
        secret_token=WEBHOOK_SECRET
    )

async def on_cleanup(app):
    await application.bot.delete_webhook(drop_pending_updates=False)
    await application.shutdown()

BASE_URL=os.environ.get("BASE_URL","").rstrip("/")
if not BASE_URL: raise SystemExit("BASE_URL environment variable is missing.")

application=Application.builder().token(BOT_TOKEN).updater(None).build()
application.add_handler(CommandHandler("start",start))
application.add_handler(MessageHandler(filters.Document.ALL,handle_zip))

async def init_app():
    await application.initialize()
    await application.start()
    app=web.Application()
    app.router.add_get("/",health)
    app.router.add_post("/telegram/{secret}",telegram_webhook)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app

if __name__=="__main__":
    web.run_app(init_app(),host="0.0.0.0",port=PORT)
