"""Read TikTok slides with yt-dlp's session and save the original image bytes."""
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp.extractor.tiktok import TikTokIE, TikTokVMIE
from media_names import short_title
from photo_downloads import save_image


class TikTokMediaIE(TikTokIE):
    # Reuse upstream page/challenge handling, including its /video metadata route
    # for photo posts. Keep this adapter covered when updating yt-dlp.
    _VALID_URL = TikTokIE._VALID_URL.replace('/video', '/(?:video|photo)')

    def _parse_aweme_video_web(self, data, webpage_url, video_id, extract_flat=False):
        if data.get('imagePost'):
            return {
                'id': video_id, 'title': data.get('desc'),
                'description': data.get('desc'),
                'uploader': (data.get('author') or {}).get('uniqueId'),
                'slideshow_images': (data.get('imagePost') or {}).get('images') or [],
                'http_headers': {'Referer': webpage_url},
            }
        return super()._parse_aweme_video_web(data, webpage_url, video_id, extract_flat)


def extract_tiktok(downloader, url):
    if TikTokVMIE.suitable(url):
        result = downloader.extract_info(url, download=False, process=False, ie_key=TikTokVMIE.ie_key())
        url = result['url']
    if not TikTokMediaIE.suitable(url):
        raise RuntimeError('This TikTok share link did not open a video or photo post. Copy the full post link and retry.')
    downloader.add_info_extractor(TikTokMediaIE())
    info = downloader.extract_info(url, download=False, process=False, ie_key=TikTokMediaIE.ie_key())
    if '/photo/' in urlsplit(url).path and 'slideshow_images' not in info:
        raise RuntimeError('TikTok did not provide the photos. Open the post in your signed-in browser, then retry with that session.')
    return info


def save_photos(downloader, info, folder, emit):
    slides = info['slideshow_images']
    if not isinstance(slides, list) or not slides:
        raise RuntimeError('No downloadable photos found in this TikTok slideshow. Try your signed-in browser session.')
    title = short_title({**info, 'title': info.get('title') or f"Photos by {info.get('uploader') or 'TikTok'}"})
    total = len(slides)
    saved = 0
    failed = []
    emit({'status': 'downloading', 'media_type': 'photo', 'total_files': total,
          'collection_title': title if total > 1 else None,
          'completed_files': 0, 'progress': 0})
    for index, slide in enumerate(slides, 1):
        urls = (slide.get('imageURL') or {}).get('urlList') if isinstance(slide, dict) else None
        try:
            suffix = f' - {index:02d}' if total > 1 else ''
            save_image(downloader, urls, Path(folder) / f'{index:03d} - {title}{suffix}', info.get('http_headers'))
            saved += 1
        except RuntimeError:
            failed.append(str(index))
        emit({'status': 'downloading', 'completed_files': saved,
              'progress': round(min(99, index / total * 100), 1)})
    if failed:
        raise RuntimeError(f'Saved {saved} of {total} photos. Could not download slide(s) {", ".join(failed)}. '
                           'Try again, or choose your signed-in browser session.')
