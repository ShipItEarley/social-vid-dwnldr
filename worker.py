"""yt-dlp subprocess: stdout is a stream of small JSON progress events."""
import json
from pathlib import Path
import re
import shutil
import sys
import time

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError

from media_names import short_title, site_name
from tiktok_photos import extract_tiktok, save_photos
from instagram_media import download_instagram


class ShortTitlePP(PostProcessor):
    def run(self, info):
        info['short_title'] = info.get('download_title') or short_title(info)
        return [], info


def friendly_error(error, site='Instagram'):
    message = re.sub(r'\x1b\[[0-9;]*m', '', str(error))
    lower = message.lower()
    if site == 'YouTube' and any(word in lower for word in ('no video', 'no formats', 'requested format')):
        return ('No downloadable YouTube video format was found. Update Downloader, ensure Deno or Node.js 22+ '
                'and FFmpeg are installed, then retry. If the video needs a login, choose your signed-in browser.')
    if any(word in lower for word in ('login', 'log in', 'sign in', 'sign-in', 'rate-limit', 'rate limit', 'not available', 'private')):
        return (f'{site} could not provide this post. Choose a browser where you are signed into '
                f'{site}, open the link there, then try again. If you are rate-limited, wait before retrying.')
    if any(word in lower for word in ('no video', 'no formats', 'requested format')):
        return f'No downloadable photos or videos found. Open the {site} post in your browser and try again.'
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
    progress_state = {'index': 0, 'total': 1}

    def progress(data):
        nonlocal last
        now = time.monotonic()
        if data.get('status') == 'finished':
            emit({'status': 'downloading', 'progress': min(99, (progress_state['index'] + 1) / progress_state['total'] * 100)})
            return
        if data.get('status') != 'downloading' or now - last < .2:
            return
        last = now
        total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
        percent = min(99, data.get('downloaded_bytes', 0) / total * 100) if total else 0
        percent = min(99, (progress_state['index'] + percent / 100) / progress_state['total'] * 100)
        emit({'status': 'downloading', 'progress': round(percent, 1)})

    if site_name(url) == 'YouTube' and not any(shutil.which(name) for name in ('deno', 'node')):
        raise RuntimeError('YouTube downloads need Deno or Node.js 22+. Install one, restart the app, then try again.')
    with YoutubeDL(download_options(folder, browser, progress, url)) as downloader:
        downloader.add_post_processor(ShortTitlePP(), when='pre_process')
        if site_name(url) == 'TikTok':
            info = extract_tiktok(downloader, url)
            if 'slideshow_images' in info:
                save_photos(downloader, info, folder, emit)
            else:
                downloader.process_ie_result(info, download=True)
        elif url.startswith('https://www.instagram.com/'):
            download_instagram(downloader, url, folder, emit, progress_state)
        else:
            downloader.extract_info(url, download=True)


def reject_live_video(info, *, incomplete=False):
    if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
        # Returning a filter message silently skips the video; raise so the UI explains why.
        raise DownloadError('This YouTube video is live or has not started yet. Try again when the recording is available.')


def download_options(folder, browser, progress, url=''):
    options = {
        'quiet': True, 'no_warnings': True, 'logger': QuietLogger(),
        'progress_hooks': [progress], 'socket_timeout': 20,
        'retries': 1, 'fragment_retries': 1, 'extractor_retries': 1,
        'paths': {'home': str(folder), 'temp': str(Path(folder) / '.partial')},
        # The temporary sequence keeps same-titled carousel entries distinct.
        # DownloadManager removes it when publishing to the platform folder.
        'outtmpl': '%(media_index,autonumber)03d - %(short_title)s.%(ext)s',
        'restrictfilenames': False, 'windowsfilenames': True,
        'format': 'b[ext=mp4]/bv*+ba/b' if shutil.which('ffmpeg') else 'b[ext=mp4]/b',
        'merge_output_format': 'mp4',
        'ignoreerrors': False, 'overwrites': False,
    }
    if browser:
        options['cookiesfrombrowser'] = (browser,)
    if url.startswith('https://www.instagram.com/stories/'):
        # A full story link saves only that item; /stories/username/ saves all
        # currently available items. Highlights are a collection by definition.
        options['noplaylist'] = True
    if site_name(url) == 'YouTube':
        options.update(noplaylist=True, match_filter=reject_live_video,
                       js_runtimes={'deno': {}, 'node': {}})
    return options


if __name__ == '__main__':
    def emit(event):
        print(json.dumps(event), flush=True)

    try:
        download(sys.argv[1], Path(sys.argv[2]), sys.argv[3], emit)
    except Exception as exc:
        emit({'error': friendly_error(exc, site=site_name(sys.argv[1]))})
        sys.exit(1)
