"""Include original photos in yt-dlp's Instagram post and story extraction."""
from pathlib import Path

from yt_dlp.extractor.instagram import InstagramIE, InstagramStoryIE
from yt_dlp.utils import DownloadError

from media_names import short_title
from photo_downloads import save_image


class InstagramPhotosMixin:
    def _extract_product_media(self, product_media):
        info = super()._extract_product_media(product_media)
        # A video's poster is not a photo. Only items identified as photos qualify.
        if product_media.get('media_type') == 1:
            candidates = (product_media.get('image_versions2') or {}).get('candidates') or []
            candidates = sorted((item for item in candidates if isinstance(item, dict)),
                                key=lambda item: (item.get('width') or 0) * (item.get('height') or 0),
                                reverse=True)
            urls = [item['url'] for item in candidates if isinstance(item.get('url'), str)]
            info.update(photo_urls=urls, media_kind='photo')
            # Upstream post/story readers require a nonempty formats list. These
            # image descriptors are intercepted before yt-dlp's video processing.
            info['formats'] = [{'url': url, 'format_id': 'photo'} for url in urls] or [{'format_id': 'missing-photo'}]
        return info


class InstagramMediaIE(InstagramPhotosMixin, InstagramIE):
    pass


class InstagramStoryMediaIE(InstagramPhotosMixin, InstagramStoryIE):
    pass


def download_instagram(downloader, url, folder, emit, progress_state):
    story = InstagramStoryIE.suitable(url)
    extractor = InstagramStoryMediaIE if story else InstagramMediaIE
    downloader.add_info_extractor(extractor())
    info = downloader.extract_info(url, download=False, process=False, ie_key=extractor.ie_key())
    entries = list(info.get('entries') or []) if info and info.get('_type') == 'playlist' else [info] if info else []
    if not entries:
        raise RuntimeError('No available Instagram stories found. The story may have expired or been removed. '
                           'Open it in your signed-in browser, then retry with that session.' if story else
                           'No downloadable photos or videos found in this Instagram post.')
    total = len(entries)
    kind = 'photo' if all('photo_urls' in entry for entry in entries) else 'media'
    emit({'status': 'downloading', 'media_type': kind, 'total_files': total, 'completed_files': 0, 'progress': 0})
    saved = 0
    failed = []
    progress_state['total'] = total
    for index, entry in enumerate(entries, 1):
        progress_state['index'] = index - 1
        entry = dict(entry)
        if story and not entry.get('description') and not entry.get('title'):
            entry['title'] = info.get('title') or 'Instagram story'
        title = short_title(entry)
        suffix = f' - {index:02d}' if total > 1 else ''
        try:
            if 'photo_urls' in entry:
                save_image(downloader, entry['photo_urls'], Path(folder) / f'{index:03d} - {title}{suffix}',
                           entry.get('http_headers'))
            else:
                entry.update(media_index=index, download_title=title + suffix)
                downloader.process_ie_result(entry, download=True)
            saved += 1
        except (RuntimeError, DownloadError) as exc:
            failed.append((index, str(exc)))
        emit({'status': 'downloading', 'completed_files': saved,
              'progress': round(min(99, index / total * 100), 1)})
    if failed:
        if total == 1:
            raise RuntimeError(failed[0][1])
        raise RuntimeError(f'Saved {saved} of {total} items. Could not download item(s) '
                           f'{", ".join(str(index) for index, _ in failed)}. '
                           'Try again, or choose your signed-in browser session.')
