"""Local Instagram and TikTok downloader. Run with the project's virtualenv Python."""
import argparse
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from urllib.parse import unquote, urlsplit, quote
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm', '.mkv', '.m4v'}
BROWSERS = ('', 'chrome', 'safari', 'firefox', 'edge', 'brave')


def normalize_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError('Paste an Instagram or TikTok video link.')
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
    parsed = urlsplit(value)
    host = parsed.netloc.lower()
    instagram_hosts = ('instagram.com', 'www.instagram.com', 'm.instagram.com')
    tiktok_hosts = ('tiktok.com', 'www.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com')
    if parsed.scheme not in ('http', 'https') or host not in instagram_hosts + tiktok_hosts:
        raise ValueError('Use a link from instagram.com or tiktok.com.')
    if host in tiktok_hosts:
        if host in ('vm.tiktok.com', 'vt.tiktok.com'):
            match = re.fullmatch(r'/([A-Za-z0-9_]+)/?', parsed.path)
            if match:
                return f'https://{host}/{match[1]}/'
        else:
            match = re.fullmatch(r'/t/([A-Za-z0-9_]+)/?', parsed.path)
            if match:
                return f'https://www.tiktok.com/t/{match[1]}/'
            match = re.fullmatch(r'/@([A-Za-z0-9_.-]+)/video/(\d+)/?', parsed.path)
            if match:
                return f'https://www.tiktok.com/@{match[1]}/video/{match[2]}'
        raise ValueError('Use a TikTok video link or a short share link, rather than a profile or photo link.')
    match = re.fullmatch(r'/(reel|reels|p|tv)/([A-Za-z0-9_-]+)/?', parsed.path)
    if not match:
        raise ValueError('Use a Reel or video-post link, rather than a profile or story link.')
    kind, shortcode = match.groups()
    return f'https://www.instagram.com/{"reel" if kind == "reels" else kind}/{shortcode}/'


class ProcessDownloader:
    """Keep slow network/cookie extraction out of the HTTP server."""
    def __init__(self, timeout=180):
        self.timeout = timeout
        self.process = None
        self.lock = threading.Lock()
        self.closed = False

    def __call__(self, url, folder, browser, progress):
        with tempfile.TemporaryFile() as errors:
            with self.lock:
                if self.closed:
                    raise RuntimeError('Download interrupted. Try the link again.')
                self.process = process = subprocess.Popen(
                    [sys.executable, str(ROOT / 'worker.py'), url, str(folder), browser],
                    stdout=subprocess.PIPE, stderr=errors, text=True)
            timer = threading.Timer(self.timeout, process.kill)
            timer.start()
            error = None
            try:
                for line in process.stdout:
                    event = json.loads(line)
                    if event.get('error'):
                        error = event['error']
                    else:
                        progress(event)
                code = process.wait()
                if code:
                    raise RuntimeError(error or 'Download stopped or timed out. Try again, or choose your signed-in browser.')
            finally:
                timer.cancel()
                process.stdout.close()
                with self.lock:
                    self.process = None

    def close(self):
        with self.lock:
            self.closed = True
            if self.process and self.process.poll() is None:
                self.process.terminate()


class DownloadManager:
    def __init__(self, destination, history_path, downloader=None):
        self.destination = Path(destination).expanduser().resolve()
        self.destination.mkdir(parents=True, exist_ok=True)
        self.history_path = Path(history_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.downloader = downloader or ProcessDownloader()
        self.lock = threading.RLock()
        self.pending = queue.Queue()
        self.closed = False
        try:
            saved = json.loads(self.history_path.read_text())
            self.jobs = {item['id']: item for item in saved
                         if isinstance(item, dict) and re.fullmatch(r'[a-zA-Z0-9_-]+', item.get('id', ''))}
        except (OSError, ValueError, TypeError, KeyError):
            self.jobs = {}
        for job in self.jobs.values():
            if job.get('status') not in ('complete', 'error'):
                job.update(status='error', error='Download interrupted when the app closed. Try the link again.')
        self._save()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def _save(self):
        temp = self.history_path.with_suffix('.tmp')
        temp.write_text(json.dumps(list(self.jobs.values()), indent=2))
        temp.replace(self.history_path)

    def list(self):
        with self.lock:
            return copy.deepcopy(list(reversed(self.jobs.values())))

    def get(self, job_id):
        with self.lock:
            return copy.deepcopy(self.jobs[job_id])

    def submit(self, url, browser=''):
        url = normalize_url(url)
        if not isinstance(browser, str) or browser not in BROWSERS:
            raise ValueError('Choose one of the listed browsers, or Public link.')
        with self.lock:
            if self.closed:
                raise ValueError('The app is closing. Reopen it to download.')
            if sum(job['status'] in ('queued', 'reading', 'downloading') for job in self.jobs.values()) >= 10:
                raise ValueError('The queue is full. Wait for a download to finish.')
            job = dict(id=secrets.token_hex(6), url=url, browser=browser,
                       status='queued', progress=0, files=[], error=None,
                       created_at=datetime.now(timezone.utc).isoformat())
            self.jobs[job['id']] = job
            self._save()
            self.pending.put(job['id'])
            return copy.deepcopy(job)

    def _update(self, job_id, values):
        with self.lock:
            self.jobs[job_id].update(values)

    def _files(self, job_id):
        folder = self.destination / job_id
        return [dict(name=path.name, size=path.stat().st_size,
                     url=f'/files/{job_id}/{quote(path.name)}')
                for path in sorted(folder.iterdir())
                if path.is_file() and not path.is_symlink() and
                path.suffix.lower() in VIDEO_EXTENSIONS and path.stat().st_size > 0]

    def _work(self):
        while True:
            job_id = self.pending.get()
            if job_id is None:
                return
            job = self.get(job_id)
            folder = self.destination / job_id
            folder.mkdir(exist_ok=True)
            error = None
            self._update(job_id, {'status': 'reading'})
            try:
                self.downloader(job['url'], folder, job['browser'],
                                lambda event: self._update(job_id, event))
            except Exception as exc:
                error = str(exc)[:1000]
            files = self._files(job_id)
            if not files and not error:
                error = 'No downloadable video found. This post may contain only photos.'
            self._update(job_id, dict(status='error' if error else 'complete', error=error,
                                     files=files, progress=100 if not error else self.get(job_id)['progress']))
            with self.lock:
                self._save()

    def file(self, job_id, name):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', job_id) or Path(name).name != name:
            raise FileNotFoundError(name)
        try:
            job = self.get(job_id)
        except KeyError:
            raise FileNotFoundError(name) from None
        path = self.destination / job_id / name
        if (name not in [f['name'] for f in job.get('files', [])] or path.is_symlink() or
                path.resolve().parent != (self.destination / job_id).resolve() or not path.is_file()):
            raise FileNotFoundError(name)
        return path

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        if hasattr(self.downloader, 'close'):
            self.downloader.close()
        self.pending.put(None)
        self.thread.join(timeout=2)


def create_server(manager, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self, mutation=False):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            origins = {'http://' + host for host in hosts}
            if (self.headers.get('Host') not in hosts or
                    self.headers.get('Origin', '') not in origins | {''} or
                    self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                self.respond(403, {'error': 'Open this app from its local address.'})
                return False
            if mutation and self.headers.get('X-App-Token') != self.server.token:
                self.respond(403, {'error': 'Reload the app and try again.'})
                return False
            return True

        def respond(self, status, data, content_type='application/json', extra=None):
            body = json.dumps(data).encode() if content_type == 'application/json' else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            if path == '/api/state':
                self.respond(200, dict(token=self.server.token, jobs=manager.list(),
                                       destination=str(manager.destination), app_id=str(ROOT)))
            elif path in ('/', '/app.js', '/style.css', '/favicon.svg'):
                name = {'/': 'index.html'}.get(path, path[1:])
                content_type = mimetypes.guess_type(name)[0] or 'text/plain'
                self.respond(200, (ROOT / 'static' / name).read_bytes(), content_type)
            elif path.startswith('/files/'):
                try:
                    _, _, job_id, filename = path.split('/', 3)
                    file = manager.file(job_id, unquote(filename))
                    self.send_response(200)
                    self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'video/mp4')
                    self.send_header('Content-Length', str(file.stat().st_size))
                    self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(file.name))
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    with file.open('rb') as stream:
                        while block := stream.read(1024 * 1024):
                            self.wfile.write(block)
                except (FileNotFoundError, ValueError):
                    self.respond(404, {'error': 'Video file not found.'})
            else:
                self.respond(404, {'error': 'Not found.'})

        def do_POST(self):
            if not self.allowed(mutation=True):
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8192 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('Send a small JSON request.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object.')
                if self.path == '/api/downloads':
                    self.respond(202, manager.submit(body.get('url'), body.get('browser', '')))
                elif self.path == '/api/open-folder':
                    folder = manager.destination
                    if body.get('id'):
                        manager.get(body['id'])
                        folder = folder / body['id']
                    if sys.platform == 'darwin':
                        subprocess.run(['open', str(folder)], check=True)
                    elif sys.platform == 'win32':
                        import os
                        os.startfile(folder)
                    else:
                        subprocess.run(['xdg-open', str(folder)], check=True)
                    self.respond(200, {'ok': True})
                else:
                    self.respond(404, {'error': 'Not found.'})
            except (ValueError, TypeError, KeyError) as exc:
                self.respond(400, {'error': str(exc) or 'Invalid request.'})
            except (OSError, subprocess.SubprocessError):
                self.respond(500, {'error': 'Could not open the folder. Use the save location shown in the app.'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.token = secrets.token_urlsafe(24)
    return server


def is_running(port):
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/state', timeout=1) as response:
            return json.load(response).get('app_id') == str(ROOT)
    except (OSError, URLError, ValueError, AttributeError):
        return False


def main():
    parser = argparse.ArgumentParser(description='Local Instagram and TikTok video downloader')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--destination', default=str(Path.home() / 'Downloads' / 'Instagram'))
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if is_running(args.port):
        address = f'http://127.0.0.1:{args.port}'
        print(f'Video Downloader is already running: {address}')
        if not args.no_browser:
            webbrowser.open(address)
        return 0
    manager = DownloadManager(args.destination, ROOT / '.state' / 'history.json')
    try:
        server = create_server(manager, args.port)
    except OSError:
        manager.close()
        print(f'Port {args.port} is busy. Try a different port with --port 8766.', file=sys.stderr)
        return 1
    address = f'http://127.0.0.1:{server.server_port}'
    print(f'Video Downloader: {address}\nVideos: {manager.destination}\nPress Control-C to quit.', flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.close()
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
