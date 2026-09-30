"""yt-dlp subprocess: stdout is a stream of small JSON progress events."""
import json
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL


def friendly_error(error, site='Instagram'):
    message = re.sub(r'\x1b\[[0-9;]*m', '', str(error))
    lower = message.lower()
    if any(word in lower for word in ('login', 'log in', 'rate-limit', 'rate limit', 'not available', 'private')):
        return (f'{site} could not provide this video. Choose a browser where you are signed into '
                f'{site}, open the link there, then try again. If you are rate-limited, wait before retrying.')
    if any(word in lower for word in ('no video', 'no formats', 'requested format')):
        return 'No downloadable video found. This post may contain only photos.'
    if any(word in lower for word in ('cookie', 'keychain', 'decrypt')):
        return (f'Could not read the selected browser session. Sign into {site} in that browser, '
                'or choose another browser. macOS may ask to allow Keychain access.')
    return message.removeprefix('ERROR: ')[:800]


class QuietLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def download(url, folder, browser, emit):
    last = 0

    def progress(data):
        nonlocal last
        now = time.monotonic()
        if data.get('status') == 'finished':
            emit({'status': 'downloading', 'progress': 99})
            return
        if data.get('status') != 'downloading' or now - last < .2:
            return
        last = now
        total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
        percent = min(99, data.get('downloaded_bytes', 0) / total * 100) if total else 0
        emit({'status': 'downloading', 'progress': round(percent, 1)})

    with YoutubeDL(download_options(folder, browser, progress)) as downloader:
        downloader.extract_info(url, download=True)


def download_options(folder, browser, progress):
    options = {
        'quiet': True, 'no_warnings': True, 'logger': QuietLogger(),
        'progress_hooks': [progress], 'socket_timeout': 20,
        'retries': 1, 'fragment_retries': 1, 'extractor_retries': 1,
        'paths': {'home': str(folder), 'temp': str(Path(folder) / '.partial')},
        'outtmpl': '%(uploader_id|video)s-%(id)s.%(ext)s',
        'restrictfilenames': True, 'windowsfilenames': True,
        'format': 'b[ext=mp4]/bv*+ba/b' if shutil.which('ffmpeg') else 'b[ext=mp4]/b',
        'merge_output_format': 'mp4',
        'ignoreerrors': False, 'overwrites': False,
    }
    if browser:
        options['cookiesfrombrowser'] = (browser,)
    return options


if __name__ == '__main__':
    def emit(event):
        print(json.dumps(event), flush=True)

    try:
        download(sys.argv[1], Path(sys.argv[2]), sys.argv[3], emit)
    except Exception as exc:
        site = 'TikTok' if (urlsplit(sys.argv[1]).hostname or '').endswith('tiktok.com') else 'Instagram'
        emit({'error': friendly_error(exc, site=site)})
        sys.exit(1)
