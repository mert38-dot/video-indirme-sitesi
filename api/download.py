from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request, re

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

def referer_for(url):
    if 'twimg.com' in url or 'twitter' in url:
        return 'https://x.com/'
    if 'tiktok' in url:
        return 'https://www.tiktok.com/'
    if 'instagram' in url or 'cdninstagram' in url:
        return 'https://www.instagram.com/'
    if 'googlevideo' in url or 'youtube' in url:
        return 'https://www.youtube.com/'
    return 'https://www.google.com/'


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        cdn_url = params.get('url', [''])[0]
        title   = params.get('title', ['video'])[0]
        ext     = params.get('ext', ['mp4'])[0]

        if not cdn_url.startswith('http'):
            self._err(400, 'Geçersiz URL')
            return

        req = urllib.request.Request(cdn_url, headers={
            'User-Agent': UA,
            'Referer': referer_for(cdn_url),
            'Origin': urlparse(referer_for(cdn_url)).scheme + '://' + urlparse(referer_for(cdn_url)).netloc,
        })
        try:
            with urllib.request.urlopen(req, timeout=55) as resp:
                ct  = resp.headers.get('Content-Type', 'video/mp4')
                cl  = resp.headers.get('Content-Length', '')
                safe = re.sub(r'[^\w\s\-]', '', title)[:60].strip() or 'video'
                self.send_response(200)
                self.send_header('Content-Type', ct)
                if cl:
                    self.send_header('Content-Length', cl)
                self.send_header('Content-Disposition', f'attachment; filename="{safe}.{ext}"')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                while chunk := resp.read(65536):
                    self.wfile.write(chunk)
        except Exception as e:
            self._err(502, str(e)[:200])

    def _err(self, code, msg):
        body = msg.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
