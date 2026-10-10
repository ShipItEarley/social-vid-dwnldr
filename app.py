"""Local social video downloader. Run with the project's virtualenv Python."""
import argparse
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit, quote
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

from media_names import short_title, site_name

ROOT = Path(__file__).resolve().parent
DEFAULT_DESTINATION = Path.home() / 'Documents' / 'Video-Download'
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm', '.mkv', '.m4v'}
PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | PHOTO_EXTENSIONS
BROWSERS = ('', 'chrome', 'safari', 'firefox', 'edge', 'brave')


def normalize_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError('Paste an Instagram, TikTok, YouTube, or X/Twitter link.')
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
    parsed = urlsplit(value)
    host = parsed.netloc.lower()
    instagram_hosts = ('instagram.com', 'www.instagram.com', 'm.instagram.com')
    tiktok_hosts = ('tiktok.com', 'www.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com')
    youtube_hosts = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
                     'youtu.be', 'www.youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com')
    twitter_hosts = ('x.com', 'www.x.com', 'm.x.com', 'mobile.x.com',
                     'twitter.com', 'www.twitter.com', 'm.twitter.com', 'mobile.twitter.com')
    if parsed.scheme not in ('http', 'https') or host not in instagram_hosts + tiktok_hosts + youtube_hosts + twitter_hosts:
        raise ValueError('Use a link from instagram.com, tiktok.com, youtube.com, youtu.be, x.com, or twitter.com.')
    if host in twitter_hosts:
        match = re.fullmatch(r'/(?:([A-Za-z0-9_]{1,15}|i/web)/status|statuses)/([0-9]+)(?:/video/([1-9][0-9]*))?/?', parsed.path)
        if not match:
            raise ValueError('Use an X/Twitter post link containing /status/ and its ID, rather than a profile, photo, or Space link.')
        username, post_id, video_index = match.groups()
        suffix = f'/video/{video_index}' if video_index else ''
        return f'https://x.com/{username or "i/web"}/status/{post_id}{suffix}'
    if host in youtube_hosts:
        video_id = ''
        if host in ('youtu.be', 'www.youtu.be'):
            match = re.fullmatch(r'/([A-Za-z0-9_-]{11})/?', parsed.path)
            video_id = match[1] if match else ''
        elif host in ('youtube-nocookie.com', 'www.youtube-nocookie.com'):
            match = re.fullmatch(r'/embed/([A-Za-z0-9_-]{11})/?', parsed.path)
            video_id = match[1] if match else ''
        elif parsed.path in ('/watch', '/watch/'):
            values = parse_qs(parsed.query, keep_blank_values=True).get('v', [])
            if len(values) == 1:
                video_id = values[0]
        else:
            match = re.fullmatch(r'/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})/?', parsed.path)
            video_id = match[1] if match else ''
        if parsed.path.rstrip('/') == '/embed/videoseries' or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError('Use a YouTube video, Shorts, or youtu.be share link, rather than a channel, playlist, or clip link.')
        # Always download one full video, ignoring playlist context, timestamps, and tracking.
        return f'https://www.youtube.com/watch?v={video_id}'
    if host in tiktok_hosts:
        if host in ('vm.tiktok.com', 'vt.tiktok.com'):
            match = re.fullmatch(r'/([A-Za-z0-9_]+)/?', parsed.path)
            if match:
                return f'https://{host}/{match[1]}/'
        else:
            match = re.fullmatch(r'/t/([A-Za-z0-9_]+)/?', parsed.path)
            if match:
                return f'https://www.tiktok.com/t/{match[1]}/'
            match = re.fullmatch(r'/@([A-Za-z0-9_.-]+)/(video|photo)/(\d+)/?', parsed.path)
            if match:
                return f'https://www.tiktok.com/@{match[1]}/{match[2]}/{match[3]}'
        raise ValueError('Use a TikTok video, photo slideshow, or short share link, rather than a profile link.')
    story = re.fullmatch(r'/stories/([A-Za-z0-9_.]+)(?:/(\d+))?/?', parsed.path)
    if story:
        username, story_id = story.groups()
        if username == 'highlights' and not story_id:
            raise ValueError('Paste the full Instagram highlight link, including its ID.')
        suffix = f'{story_id}/' if story_id else ''
        return f'https://www.instagram.com/stories/{username}/{suffix}'
    match = re.fullmatch(r'/(reel|reels|p|tv)/([A-Za-z0-9_-]+)/?', parsed.path)
    if not match:
        raise ValueError('Use an Instagram Reel, photo/video post, or story link, rather than a profile link.')
    kind, shortcode = match.groups()
    return f'https://www.instagram.com/{"reel" if kind == "reels" else kind}/{shortcode}/'


class ProcessDownloader:
    """Keep slow network/cookie extraction out of the HTTP server."""
    def __init__(self, timeout=None):
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
            timeout = self.timeout
            if timeout is None:
                timeout = 1800 if urlsplit(url).hostname == 'www.youtube.com' else 180
            timer = threading.Timer(timeout, process.kill)
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
    def __init__(self, destination, history_path, downloader=None, legacy_destination=None):
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
            if 'folder' not in job:
                folder = self.destination / job['id']
                if not folder.exists() and legacy_destination is not None:
                    folder = Path(legacy_destination).expanduser().resolve() / job['id']
                job['folder'] = str(folder)
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
                       folder=str(self.destination / site_name(url)),
                       status='queued', progress=0, files=[], error=None,
                       created_at=datetime.now(timezone.utc).isoformat())
            self.jobs[job['id']] = job
            self._save()
            self.pending.put(job['id'])
            return copy.deepcopy(job)

    def _update(self, job_id, values):
        with self.lock:
            self.jobs[job_id].update(values)

    def folder(self, job_id):
        folder = Path(self.get(job_id)['folder'])
        if not folder.is_absolute() or folder.resolve() != folder:
            raise FileNotFoundError('Download folder moved or is a symbolic link.')
        return folder

    def _publish_files(self, job_id, staging, files):
        folder = self.folder(job_id)
        sources = [source for source in sorted(staging.iterdir())
                   if not source.is_symlink() and source.is_file()
                   and source.suffix.lower() in MEDIA_EXTENSIONS and source.stat().st_size > 0]
        job = self.get(job_id)
        if sources and job.get('collection_title'):
            title = short_title({'title': job['collection_title']})
            number = 1
            while True:
                collection = folder / (title + (f' ({number})' if number > 1 else ''))
                try:
                    collection.mkdir()
                    break
                except FileExistsError:
                    number += 1
            folder = collection
            self._update(job_id, {'folder': str(folder), 'collection_title': folder.name})
        for source in sources:
            stem = re.sub(r'^\d{3,} - ', '', source.stem) or 'Untitled video'
            number = 1
            while True:
                suffix = f' ({number})' if number > 1 else ''
                path = folder / f'{stem}{suffix}{source.suffix}'
                try:
                    # Same-volume hard link publishes the complete file atomically,
                    # and fails if another download or existing file owns the name.
                    os.link(source, path)
                    break
                except FileExistsError:
                    number += 1
            files.append(dict(name=path.name, size=path.stat().st_size,
                              kind='photo' if path.suffix.lower() in PHOTO_EXTENSIONS else 'video',
                              url=f'/files/{job_id}/{quote(path.name)}'))
            self._update(job_id, {'files': files.copy()})
            with self.lock:
                self._save()

    def _work(self):
        while True:
            job_id = self.pending.get()
            if job_id is None:
                return
            job = self.get(job_id)
            error = None
            files = []
            self._update(job_id, {'status': 'reading'})
            try:
                folder = self.folder(job_id)
                folder.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.download-', dir=folder) as temporary:
                    staging = Path(temporary)
                    try:
                        self.downloader(job['url'], staging, job['browser'],
                                        lambda event: self._update(job_id, event))
                    finally:
                        self._publish_files(job_id, staging, files)
            except Exception as exc:
                error = str(exc)[:1000]
            if not files and not error:
                error = 'No downloadable photos or videos found.'
            self._update(job_id, dict(status='error' if error else 'complete', error=error,
                                     files=files, progress=100 if not error else self.get(job_id)['progress']))
            with self.lock:
                self._save()

    def file(self, job_id, name):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', job_id) or Path(name).name != name:
            raise FileNotFoundError(name)
        try:
            job = self.get(job_id)
            folder = self.folder(job_id)
        except KeyError:
            raise FileNotFoundError(name) from None
        path = folder / name
        if (name not in [f['name'] for f in job.get('files', [])] or path.is_symlink() or
                path.resolve().parent != folder or not path.is_file()):
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
                    self.respond(404, {'error': 'Media file not found.'})
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
                    reveal = None
                    if body.get('id'):
                        folder = manager.folder(body['id'])
                        files = manager.get(body['id']).get('files', [])
                        if files:
                            reveal = manager.file(body['id'], files[0]['name'])
                    if sys.platform == 'darwin':
                        subprocess.run(['open', '-R', str(reveal)] if reveal else ['open', str(folder)], check=True)
                    elif sys.platform == 'win32':
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
    parser = argparse.ArgumentParser(description='Local Instagram, TikTok, YouTube, and X/Twitter media downloader')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--destination', default=str(DEFAULT_DESTINATION))
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if is_running(args.port):
        address = f'http://127.0.0.1:{args.port}'
        print(f'Video Downloader is already running: {address}')
        if not args.no_browser:
            webbrowser.open(address)
        return 0
    legacy = (Path.home() / 'Downloads' / 'Instagram'
              if Path(args.destination).expanduser().resolve() in
              (DEFAULT_DESTINATION.resolve(), (Path.home() / 'Downloads').resolve()) else None)
    manager = DownloadManager(args.destination, ROOT / '.state' / 'history.json', legacy_destination=legacy)
    for platform in ('TikTok', 'Instagram', 'YouTube', 'Twitter'):
        (manager.destination / platform).mkdir(exist_ok=True)
    try:
        server = create_server(manager, args.port)
    except OSError:
        manager.close()
        print(f'Port {args.port} is busy. Try a different port with --port 8766.', file=sys.stderr)
        return 1
    address = f'http://127.0.0.1:{server.server_port}'
    print(f'Video Downloader: {address}\nSaved files: {manager.destination}\nPress Control-C to quit.', flush=True)
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
