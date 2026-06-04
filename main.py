from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yt_dlp, os, tempfile, re
from pathlib import Path

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# Cookie dosyasını env var'dan oluştur (YouTube için)
_COOKIE_FILE = None
def cookie_file():
    global _COOKIE_FILE
    if _COOKIE_FILE and os.path.exists(_COOKIE_FILE):
        return _COOKIE_FILE
    content = os.environ.get("YT_COOKIES", "")
    if content:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        f.write(content)
        f.close()
        _COOKIE_FILE = f.name
    return _COOKIE_FILE


class AnalyzeReq(BaseModel):
    url: str


def opts_for(url, **extra):
    """Platform'a göre yt-dlp seçeneklerini döner."""
    base = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "format": "best[ext=mp4]/best",  # FFmpeg gerektirmeyen tek dosya format
    }
    u = url.lower()
    if "twitter.com" in u or "x.com" in u or "twimg.com" in u:
        base["extractor_args"] = {"twitter": {"api": ["syndication"]}}
    cf = cookie_file()
    if cf:
        base["cookiefile"] = cf
    base.update(extra)
    return base


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.post("/analyze")
def analyze(req: AnalyzeReq):
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "https:// ile başlayan geçerli bir link girin.")
    try:
        with yt_dlp.YoutubeDL(opts_for(url)) as y:
            info = y.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Sign in" in msg or "bot" in msg:
            raise HTTPException(400, "Bu platform giriş gerektiriyor. Şu an desteklenemiyor.")
        if "Unsupported" in msg:
            raise HTTPException(400, "Bu platform desteklenmiyor.")
        raise HTTPException(400, msg[:300])
    except Exception as e:
        raise HTTPException(500, str(e)[:300])

    fmts = info.get("formats", [])
    progressive = [f for f in fmts if f.get("vcodec","none") != "none" and f.get("acodec","none") != "none" and f.get("height")]
    audio_only = [f for f in fmts if f.get("acodec","none") != "none" and f.get("vcodec","none") == "none"]

    prog_by_height = {}
    for f in progressive:
        h = f["height"]
        cur = prog_by_height.get(h)
        if not cur or (f.get("filesize") or f.get("filesize_approx") or 0) > (cur.get("filesize") or cur.get("filesize_approx") or 0):
            prog_by_height[h] = f

    best_audio_size = 0
    if audio_only:
        ba = max(audio_only, key=lambda x: x.get("abr") or 0)
        best_audio_size = ba.get("filesize") or ba.get("filesize_approx") or 0

    seen, result = set(), []
    for f in sorted(fmts, key=lambda x: x.get("height") or 0, reverse=True):
        h = f.get("height")
        if not h or f.get("vcodec","none") == "none" or h in seen:
            continue
        seen.add(h)
        pf = prog_by_height.get(h)
        if pf:
            s = pf.get("filesize") or pf.get("filesize_approx") or 0
        else:
            vs = f.get("filesize") or f.get("filesize_approx") or 0
            s = vs + best_audio_size if vs else 0
        result.append({"label": f"{h}p", "height": h,
                        "size": f"{s/1048576:.1f} MB" if s else "—", "type": "video"})
        if len(result) >= 5:
            break

    if audio_only:
        ba = max(audio_only, key=lambda x: x.get("abr") or 0)
        s = ba.get("filesize") or ba.get("filesize_approx") or 0
        result.append({"label": "Ses (M4A)", "height": "audio",
                       "size": f"{s/1048576:.1f} MB" if s else "—", "type": "audio"})

    if not result:
        raise HTTPException(400, "İndirilebilir format bulunamadı.")

    return {"title": info.get("title", "Video"), "thumbnail": info.get("thumbnail"),
            "formats": result, "url": url}


@app.get("/download")
def download(url: str, height: str, title: str = "video"):
    if not url.startswith("http"):
        raise HTTPException(400, "Geçersiz URL")

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "%(title)s.%(ext)s")
    is_audio = height == "audio"

    if is_audio:
        dl_opts = opts_for(url, format="bestaudio[ext=m4a]/bestaudio/best", outtmpl=out)
        ext, mime = "m4a", "audio/mp4"
    else:
        h = int(height)
        dl_opts = opts_for(url, format=f"best[height<={h}][ext=mp4]/best[height<={h}]/best", outtmpl=out)
        ext, mime = "mp4", "video/mp4"

    try:
        with yt_dlp.YoutubeDL(dl_opts) as y:
            y.download([url])
    except Exception as e:
        raise HTTPException(500, str(e)[:300])

    files = [f for f in Path(tmp).iterdir() if f.is_file() and f.stat().st_size > 0]
    if not files:
        raise HTTPException(500, "Dosya oluşturulamadı.")

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
