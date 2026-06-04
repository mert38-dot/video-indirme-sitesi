from http.server import BaseHTTPRequestHandler
import json, re
import yt_dlp

URL_RE = re.compile(r'^https?://.+\..{2,}', re.I)


def build(url):
    url = (url or "").strip()
    if not URL_RE.match(url):
        return 400, {"detail": "Geçersiz URL formatı. https:// ile başlayan tam bir link girin."}

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 20}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Unsupported URL" in msg:
            return 400, {"detail": "Bu platform desteklenmiyor veya link hatalı."}
        return 400, {"detail": msg[:300]}
    except Exception as e:
        return 500, {"detail": str(e)[:300]}

    fmts = info.get("formats", [])

    # Progressive (video+ses tek dosya) — Vercel'de birleştirme olmadığı için bunlar gerekli
    progressive = [
        f for f in fmts
        if f.get("url")
        and f.get("vcodec", "none") != "none"
        and f.get("acodec", "none") != "none"
        and f.get("height")
    ]
    audio = [
        f for f in fmts
        if f.get("url") and f.get("acodec", "none") != "none"
        and f.get("vcodec", "none") == "none"
    ]

    seen, options = set(), []
    for f in sorted(progressive, key=lambda x: x.get("height") or 0, reverse=True):
        h = f["height"]
        if h in seen:
            continue
        seen.add(h)
        size = f.get("filesize") or f.get("filesize_approx") or 0
        options.append({
            "label": f"{h}p",
            "url": f["url"],
            "ext": f.get("ext", "mp4"),
            "size": f"{size/1048576:.1f} MB" if size else "Boyut bilinmiyor",
            "type": "video",
        })
        if len(options) >= 5:
            break

    if audio:
        ba = max(audio, key=lambda x: x.get("abr") or 0)
        size = ba.get("filesize") or ba.get("filesize_approx") or 0
        options.append({
            "label": "Ses (M4A/MP3)",
            "url": ba["url"],
            "ext": ba.get("ext", "m4a"),
            "size": f"{size/1048576:.1f} MB" if size else "Boyut bilinmiyor",
            "type": "audio",
        })

    # Hiç progressive yoksa en iyi tek-dosya formatı dene
    if not options:
        cands = [f for f in fmts if f.get("url") and f.get("vcodec", "none") != "none"]
        if cands:
            best = max(cands, key=lambda x: x.get("height") or 0)
            size = best.get("filesize") or best.get("filesize_approx") or 0
            options.append({
                "label": f"{best.get('height','Video')}p" if best.get("height") else "Video",
                "url": best["url"],
                "ext": best.get("ext", "mp4"),
                "size": f"{size/1048576:.1f} MB" if size else "Boyut bilinmiyor",
                "type": "video",
            })

    if not options:
        return 400, {"detail": "Bu videoda doğrudan indirilebilir format bulunamadı."}

    return 200, {
        "title": info.get("title", "Video"),
        "thumbnail": info.get("thumbnail"),
        "formats": options,
    }


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"detail": "Geçersiz istek."})
        code, payload = build(data.get("url"))
        self._send(code, payload)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
