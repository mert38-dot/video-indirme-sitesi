from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yt_dlp, os, tempfile, re
from pathlib import Path

# Cookies dosyasını Railway env'den oluştur (youtube için gerekli)
_COOKIE_FILE = None
def _cookie_file():
    global _COOKIE_FILE
    if _COOKIE_FILE and os.path.exists(_COOKIE_FILE):
        return _COOKIE_FILE
    content = os.environ.get("YT_COOKIES", "")
    if content:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        tmp.write(content)
        tmp.close()
        _COOKIE_FILE = tmp.name
    return _COOKIE_FILE

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


class AnalyzeReq(BaseModel):
    url: str


def ydl(**kw):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "extractor_args": {
            "twitter": {"api": ["syndication"]},
            "youtube": {"player_client": ["tv_embedded", "web_embedded"]},
        },
    }
    cf = _cookie_file()
    if cf:
        opts["cookiefile"] = cf
    opts.update(kw)
    return opts


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.post("/analyze")
def analyze(req: AnalyzeReq):
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "https:// ile başlayan geçerli bir link girin.")
    try:
        with yt_dlp.YoutubeDL(ydl()) as y:
            info = y.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        raise HTTPException(400, "Platform desteklenmiyor veya link hatalı." if "Unsupported" in msg else msg[:300])
    except Exception as e:
        raise HTTPException(500, str(e)[:300])

    fmts = info.get("formats", [])

    # progressive = video+audio birleşik (ffmpeg gerektirmez, boyut bilgisi güvenilir)
    progressive = [f for f in fmts if f.get("vcodec","none") != "none" and f.get("acodec","none") != "none" and f.get("height")]
    audio = [f for f in fmts if f.get("acodec","none") != "none" and f.get("vcodec","none") == "none"]

    # her kalite için en iyi progressive formatı bul
    prog_by_height = {}
    for f in progressive:
        h = f["height"]
        cur = prog_by_height.get(h)
        if not cur or (f.get("filesize") or f.get("filesize_approx") or 0) > (cur.get("filesize") or cur.get("filesize_approx") or 0):
            prog_by_height[h] = f

    # progressive yoksa video-only + audio tahmini kullan
    best_audio_size = 0
    if audio:
        ba = max(audio, key=lambda x: x.get("abr") or 0)
        best_audio_size = ba.get("filesize") or ba.get("filesize_approx") or 0

    seen, opts = set(), []
    for f in sorted(fmts, key=lambda x: x.get("height") or 0, reverse=True):
        h = f.get("height")
        if not h or f.get("vcodec","none") == "none" or h in seen:
            continue
        seen.add(h)
        # progressive varsa onun boyutunu kullan, yoksa video+audio tahmini
        pf = prog_by_height.get(h)
        if pf:
            s = pf.get("filesize") or pf.get("filesize_approx") or 0
        else:
            vs = f.get("filesize") or f.get("filesize_approx") or 0
            s = vs + best_audio_size if vs else 0
        opts.append({"label": f"{h}p", "height": h, "size": f"{s/1048576:.1f} MB" if s else "~boyut hesaplanamadı", "type": "video"})
        if len(opts) >= 5:
            break

    if audio:
        ba = max(audio, key=lambda x: x.get("abr") or 0)
        s = ba.get("filesize") or ba.get("filesize_approx") or 0
        opts.append({"label": "MP3 (Ses)", "height": "audio", "size": f"{s/1048576:.1f} MB" if s else "Boyut bilinmiyor", "type": "audio"})

    if not opts:
        raise HTTPException(400, "İndirilebilir format bulunamadı.")

    return {"title": info.get("title", "Video"), "thumbnail": info.get("thumbnail"), "formats": opts, "url": url}


@app.get("/download")
def download(url: str, height: str, title: str = "video"):
    if not url.startswith("http"):
        raise HTTPException(400, "Geçersiz URL")

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "%(title)s.%(ext)s")
    is_audio = height == "audio"

    if is_audio:
        opts = ydl(format="bestaudio[ext=m4a]/bestaudio/best", outtmpl=out)
        ext, mime = "m4a", "audio/mp4"
    else:
        h = int(height)
        opts = ydl(
            format=f"best[height<={h}][ext=mp4]/best[height<={h}]/best",
            outtmpl=out,
        )
        ext, mime = "mp4", "video/mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
    except Exception as e:
        raise HTTPException(500, str(e)[:300])

    files = [f for f in Path(tmp).iterdir() if f.is_file() and f.stat().st_size > 0]
    if not files:
        raise HTTPException(500, "Dosya oluşturulamadı. FFmpeg kurulu olduğundan emin olun.")

    filepath = str(files[0])
    safe = re.sub(r"[^\w\s\-]", "", title)[:60].strip() or "video"

    def stream():
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                yield chunk
        try:
            os.remove(filepath)
            os.rmdir(tmp)
        except Exception:
            pass

    return StreamingResponse(stream(), media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{safe}.{ext}"'})
