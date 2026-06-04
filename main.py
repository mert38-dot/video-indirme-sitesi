from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import yt_dlp, os, tempfile, re
from pathlib import Path

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# Cookie'yi uygulama başlarken yaz
def _write_cookies():
    raw = os.environ.get("YT_COOKIES", "")
    if not raw:
        return None
    path = "/tmp/videoget_cookies.txt"
    lines = [l.rstrip("\r\n") for l in raw.splitlines()]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path

COOKIE_PATH = "/tmp/videoget_cookies.txt"
COOKIE_FILE = _write_cookies()

def cookie_file():
    return COOKIE_FILE if COOKIE_FILE and os.path.exists(COOKIE_FILE) else None

class CookieUpdate(BaseModel):
    password: str
    cookies: str

@app.post("/admin/update-cookies")
def update_cookies(req: CookieUpdate):
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pass or req.password != admin_pass:
        raise HTTPException(403, "Yanlış şifre")
    lines = [l.rstrip("\r\n") for l in req.cookies.splitlines()]
    content = "\n".join(lines) + "\n"
    with open(COOKIE_PATH, "w") as f:
        f.write(content)
    global COOKIE_FILE
    COOKIE_FILE = COOKIE_PATH
    return {"ok": True, "message": "Cookie güncellendi"}

@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return """<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8">
<title>Cookie Güncelle</title>
<style>*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui;background:#0d0d0d;color:#e8e8e8;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}
.card{width:100%;max-width:560px;background:#161616;border:1px solid #222;border-radius:16px;padding:28px}
h2{margin-bottom:20px;font-size:1.2rem}
label{display:block;font-size:.8rem;color:#666;margin-bottom:6px;margin-top:16px}
input,textarea{width:100%;padding:12px;background:#111;border:1px solid #222;border-radius:10px;color:#e8e8e8;font-size:.85rem;outline:none}
textarea{height:180px;resize:vertical;font-family:monospace;font-size:.75rem}
button{margin-top:16px;width:100%;padding:13px;background:#fff;color:#000;border:none;border-radius:10px;font-weight:700;cursor:pointer;font-size:.9rem}
.msg{margin-top:12px;padding:10px;border-radius:8px;font-size:.85rem;display:none}
.ok{background:#0d1f0d;color:#4ade80;border:1px solid #1a3a1a}
.err{background:#1a0a0a;color:#f87171;border:1px solid #3a1a1a}
</style></head><body>
<div class="card">
  <h2>🍪 YouTube Cookie Güncelle</h2>
  <p style="color:#555;font-size:.82rem">cookies.txt içeriğini buraya yapıştırın. Her ~2 haftada bir yenilenmelidir.</p>
  <label>Admin Şifre</label>
  <input type="password" id="pw" placeholder="ADMIN_PASSWORD">
  <label>cookies.txt içeriği</label>
  <textarea id="ck" placeholder="# Netscape HTTP Cookie File..."></textarea>
  <button onclick="send()">Güncelle</button>
  <div class="msg" id="msg"></div>
</div>
<script>
async function send(){
  const r=await fetch('/admin/update-cookies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:document.getElementById('pw').value,cookies:document.getElementById('ck').value})});
  const d=await r.json();
  const m=document.getElementById('msg');
  m.style.display='block';
  m.className='msg '+(r.ok?'ok':'err');
  m.textContent=r.ok?'✅ Cookie güncellendi! Artık YouTube çalışır.':'❌ '+d.detail;
}
</script></body></html>"""


class AnalyzeReq(BaseModel):
    url: str


def opts_for(url, **extra):
    """Platform'a göre yt-dlp seçeneklerini döner."""
    base = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "concurrent_fragment_downloads": 5,
        "buffersize": 1024 * 64,
        "http_chunk_size": 1024 * 1024 * 10,
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


YT_CLIENTS = ["android", "ios", "tv_embedded", "web_creator", "web"]

def extract_info_yt(url, base_opts):
    """YouTube için birden fazla client dener, ilk çalışanı döner."""
    last_err = None
    for client in YT_CLIENTS:
        try:
            opts = {**base_opts, "extractor_args": {"youtube": {"player_client": [client]}}}
            with yt_dlp.YoutubeDL(opts) as y:
                return y.extract_info(url, download=False)
        except Exception as e:
            last_err = e
            continue
    raise last_err

@app.post("/analyze")
def analyze(req: AnalyzeReq):
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "https:// ile başlayan geçerli bir link girin.")

    is_yt = "youtube.com" in url or "youtu.be" in url
    base = opts_for(url)

    try:
        if is_yt:
            info = extract_info_yt(url, base)
        else:
            with yt_dlp.YoutubeDL(base) as y:
                info = y.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
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
        dl_opts = opts_for(url,
            format=f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
            outtmpl=out,
            merge_output_format="mp4",
            postprocessors=[{"key":"FFmpegVideoConvertor","preferedformat":"mp4"}],
        )
        ext, mime = "mp4", "video/mp4"

    is_yt = "youtube.com" in url or "youtu.be" in url
    last_err = None
    clients = YT_CLIENTS if is_yt else [None]

    for client in clients:
        try:
            if client:
                run_opts = {**dl_opts, "extractor_args": {"youtube": {"player_client": [client]}}}
            else:
                run_opts = dl_opts
            with yt_dlp.YoutubeDL(run_opts) as y:
                y.download([url])
            last_err = None
            break
        except Exception as e:
            last_err = e
            continue

    if last_err:
        raise HTTPException(500, str(last_err)[:300])

    files = [f for f in Path(tmp).iterdir() if f.is_file() and f.stat().st_size > 0]
    if not files:
        raise HTTPException(500, "Dosya oluşturulamadı.")

    filepath = str(files[0])
    safe = re.sub(r"[^\x00-\x7F]", "", title)  # ASCII dışı karakterleri kaldır
    safe = re.sub(r"[^\w\s\-]", "", safe)[:60].strip() or "video"

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
