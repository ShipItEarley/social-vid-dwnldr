"""Download original photos using the extractor's existing HTTP session."""
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError


def image_extension(header):
    if header.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return '.webp'
    raise ValueError('The server did not return a supported image.')


def save_image(downloader, urls, stem, headers=None):
    """Try original-image mirrors; never publish HTML or a partial transfer."""
    part = Path(str(stem) + '.part')
    for url in urls if isinstance(urls, list) else []:
        try:
            if not isinstance(url, str) or urlsplit(url).scheme not in ('http', 'https'):
                continue
            with downloader.urlopen(Request(url, headers=headers or {})) as response:
                expected = int(response.headers.get('Content-Length') or 0)
                header = response.read(32)
                extension = image_extension(header)
                written = len(header)
                with part.open('wb') as stream:
                    stream.write(header)
                    while block := response.read(256 * 1024):
                        stream.write(block)
                        written += len(block)
                if expected and written != expected:
                    raise ValueError('Incomplete photo download.')
            target = Path(str(stem) + extension)
            part.replace(target)
            return target
        except (RequestError, OSError, ValueError):
            continue
        finally:
            part.unlink(missing_ok=True)
    raise RuntimeError('Could not download the photo from any available image URL.')
