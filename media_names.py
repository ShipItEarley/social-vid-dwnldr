"""Readable platform folders and short, portable video names."""
import re
import unicodedata
from urllib.parse import urlsplit


TITLE_LIMIT = 60


def site_name(url):
    """Identify URLs already normalized by the app."""
    host = urlsplit(url).hostname or ''
    if host == 'www.youtube.com':
        return 'YouTube'
    if host in ('www.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'):
        return 'TikTok'
    if host == 'x.com':
        return 'Twitter'
    return 'Instagram'


def short_title(info):
    title = info.get('title') or ''
    # Instagram often supplies "Video by creator" instead of the actual caption.
    generic = (title == str(info.get('id')) or
               re.fullmatch(r'(?:Video|Photo|Post)(?: \d+| by .+)?', title, re.IGNORECASE))
    creator = info.get('uploader') or info.get('channel') or info.get('uploader_id')
    kind = 'Photo' if info.get('media_kind') == 'photo' else 'Video'
    creator_title = f'{kind} by {creator}' if creator else None
    candidates = [info.get('description'), creator_title, title] if generic else [title, info.get('description'), creator_title]
    candidates += [f'Untitled {kind.lower()}']
    for value in candidates:
        if not value or value == str(info.get('id')):
            continue
        value = unicodedata.normalize('NFC', value)
        value = re.sub(r'https?://\S+|(?<!\w)#\w+', '', value)
        value = ''.join(' ' if unicodedata.category(char).startswith('C') else char for char in value)
        value = re.sub(r'[<>:"/\\|?*]', ' ', value)
        value = re.sub(r'\s+', ' ', value).strip(' .-_')
        if not value:
            continue
        if len(value) > TITLE_LIMIT:
            prefix = value[:TITLE_LIMIT]
            value = prefix.rsplit(' ', 1)[0] if value[TITLE_LIMIT] != ' ' and ' ' in prefix else prefix
        # Leave room for extensions, collision suffixes, and temporary format IDs
        # on filesystems with a 255-byte filename limit.
        while len(value.encode('utf-8')) > 180:
            value = value[:-1]
        value = value.rstrip(' .-_')
        if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
            value = ('Video ' + value)[:TITLE_LIMIT]
        return value
    return 'Untitled video'
