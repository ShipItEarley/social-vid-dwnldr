from functools import partial
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import app
import worker
from media_names import short_title
from tiktok_photos import TikTokMediaIE, extract_tiktok
from photo_downloads import image_extension
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class WorkerTests(unittest.TestCase):
    def test_tiktok_video_metadata_keeps_the_existing_video_parser(self):
        data = {'id': '123', 'imagePost': {}}
        with patch('yt_dlp.extractor.tiktok.TikTokIE._parse_aweme_video_web', return_value={'formats': ['video']}) as parse:
            result = TikTokMediaIE()._parse_aweme_video_web(data, 'https://www.tiktok.com/@creator/video/123', '123')
        self.assertEqual(result, {'formats': ['video']})
        parse.assert_called_once()

    def test_tiktok_share_links_resolve_to_photos_and_reject_nonposts(self):
        downloader = Mock()
        info = {'id': '123', 'slideshow_images': []}
        downloader.extract_info.side_effect = [
            {'url': 'https://www.tiktok.com/@creator/photo/123?tracking=1'}, info]
        self.assertEqual(extract_tiktok(downloader, 'https://vm.tiktok.com/abc/'), info)
        self.assertEqual(downloader.extract_info.call_args.kwargs['ie_key'], 'TikTokMedia')
        downloader.extract_info.side_effect = [{'url': 'https://example.com/'}]
        with self.assertRaisesRegex(RuntimeError, 'full post link'):
            extract_tiktok(downloader, 'https://www.tiktok.com/t/abc/')

    def test_photo_link_without_slide_metadata_never_downloads_just_the_music(self):
        downloader = Mock()
        downloader.extract_info.return_value = {'formats': [{'vcodec': 'none'}]}
        with self.assertRaisesRegex(RuntimeError, 'did not provide the photos'):
            extract_tiktok(downloader, 'https://www.tiktok.com/@creator/photo/123')

    def test_image_extensions_come_from_bytes_and_reject_html(self):
        self.assertEqual(image_extension(b'\xff\xd8\xff\xe0'), '.jpg')
        self.assertEqual(image_extension(b'\x89PNG\r\n\x1a\n'), '.png')
        self.assertEqual(image_extension(b'RIFFxxxxWEBP'), '.webp')
        with self.assertRaises(ValueError):
            image_extension(b'<html>Sign in to TikTok</html>')

    def test_slideshow_downloads_original_images_in_order_with_mirror_fallback(self):
        self.run_slideshow_fixture()

    def test_slideshow_failure_preserves_other_slides_and_never_publishes_partial_images(self):
        self.run_slideshow_fixture(fail_second=True)

    def run_slideshow_fixture(self, fail_second=False):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'original.image').write_bytes(png)
            (root / 'login.image').write_text('<html>Please sign in</html>')

            class Handler(SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    if self.path == '/truncated.image':
                        self.send_response(200)
                        self.send_header('Content-Length', str(len(png) + 100))
                        self.end_headers()
                        self.wfile.write(png[:40])
                    else:
                        super().do_GET()

            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            origin = f'http://127.0.0.1:{server.server_port}'
            data = {'id': '123', 'desc': 'A weekend away #travel', 'author': {'uniqueId': 'creator'},
                    'imagePost': {'images': [
                        {'imageURL': {'urlList': [origin + '/missing.image', origin + '/original.image']}},
                        {'imageURL': {'urlList': [origin + '/login.image', origin + ('/truncated.image' if fail_second else '/original.image')]}},
                        {'imageURL': {'urlList': [origin + '/original.image']}},
                    ]}}
            page = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">' + json.dumps({
                '__DEFAULT_SCOPE__': {'webapp.video-detail': {'statusCode': 0, 'itemInfo': {'itemStruct': data}}}
            }) + '</script>'
            response = Mock(url='https://www.tiktok.com/@creator/video/123', extensions={})
            events = []

            def download(url, folder, browser, emit):
                def capture(event):
                    events.append(event)
                    emit(event)
                with patch.object(TikTokMediaIE, '_download_webpage_handle', return_value=(page, response)):
                    worker.download(url, folder, browser, capture)

            manager = app.DownloadManager(root / 'downloads', root / 'history.json', download)
            try:
                job = manager.submit('https://www.tiktok.com/@creator/photo/123')
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    result = manager.get(job['id'])
                    if result['status'] in ('complete', 'error'):
                        break
                    time.sleep(.01)
                self.assertEqual(result['status'], 'error' if fail_second else 'complete', result.get('error'))
                expected = [1, 3] if fail_second else [1, 2, 3]
                self.assertEqual([file['name'] for file in result['files']],
                                 [f'A weekend away - {index:02d}.png' for index in expected])
                for file in result['files']:
                    self.assertEqual(manager.file(job['id'], file['name']).read_bytes(), png)
                self.assertEqual(result['media_type'], 'photo')
                self.assertEqual(result['total_files'], 3)
                self.assertEqual(result['completed_files'], len(expected))
                self.assertEqual(Path(result['folder']), manager.destination / 'TikTok' / 'A weekend away')
                self.assertEqual(len(list(Path(result['folder']).iterdir())), len(expected))
                if fail_second:
                    self.assertIn('Saved 2 of 3 photos', result['error'])
                    self.assertIn('slide(s) 2', result['error'])
                percentages = [event['progress'] for event in events if 'progress' in event]
                self.assertEqual(percentages, sorted(percentages))
            finally:
                manager.close()

    def test_short_titles_keep_readable_words_and_use_captions_when_needed(self):
        for info, expected in (
            ({'title': 'Me at the zoo', 'id': 'jNQXAC9IVRw'}, 'Me at the zoo'),
            ({'title': 'Video by creator', 'description': 'Three ways to cook better pasta #cooking #tips'}, 'Three ways to cook better pasta'),
            ({'title': 'Video 1', 'uploader': 'Jane'}, 'Video by Jane'),
            ({'id': '123456', 'title': '123456', 'description': 'A useful caption'}, 'A useful caption'),
            ({'title': 'A title\nwith\tunsafe / path: characters?'}, 'A title with unsafe path characters'),
            ({'title': 'Café in São Paulo'}, 'Café in São Paulo'),
            ({'title': '... /// #tags https://example.com', 'uploader': 'Jane'}, 'Video by Jane'),
            ({'title': 'CON'}, 'Video CON'),
            ({}, 'Untitled video'),
        ):
            with self.subTest(info=info):
                self.assertEqual(short_title(info), expected)

    def test_long_titles_are_capped_without_cutting_words_and_fit_filename_byte_limits(self):
        title = 'This is a helpful video about cooking dinner with simple ingredients you have at home'
        shortened = short_title({'title': title})
        self.assertLessEqual(len(shortened), 60)
        self.assertTrue(title.startswith(shortened + ' '))
        self.assertEqual(short_title({'title': 'a' * 100}), 'a' * 60)
        self.assertLessEqual(len(short_title({'title': '🎬' * 100}).encode('utf-8')), 180)

    def test_prefers_a_complete_mp4_with_audio_over_split_streams(self):
        self.assertTrue(hasattr(worker, 'download_options'), 'Complete MP4 format selection is missing')
        with tempfile.TemporaryDirectory() as temporary:
            options = worker.download_options(Path(temporary), '', lambda event: None)
            info = {'id': 'test', 'title': 'test', 'extractor': 'test', 'formats': [
                {'format_id': 'audio', 'url': 'https://example.com/audio.m4a', 'ext': 'm4a', 'vcodec': 'none', 'acodec': 'aac'},
                {'format_id': 'complete', 'url': 'https://example.com/video.mp4', 'ext': 'mp4', 'vcodec': 'h264', 'acodec': 'aac', 'height': 1080},
                {'format_id': 'split', 'url': 'https://example.com/split.mp4', 'ext': 'mp4', 'vcodec': 'vp9', 'acodec': 'none', 'height': 1440},
            ]}
            with YoutubeDL(options) as downloader:
                result = downloader.process_ie_result(info, download=False)
            self.assertEqual(result['format_id'], 'complete')
            self.assertEqual(result['acodec'], 'aac')

    def test_login_errors_have_an_actionable_message(self):
        self.assertTrue(hasattr(worker, 'friendly_error'), 'Downloader error handling is missing')
        message = worker.friendly_error('ERROR: Instagram requires login or rate-limit reached')
        self.assertIn('signed', message)
        self.assertIn('browser', message)

    def test_tiktok_login_errors_name_tiktok(self):
        import inspect
        self.assertIn('site', inspect.signature(worker.friendly_error).parameters,
                      'Download errors cannot identify TikTok yet')
        message = worker.friendly_error('TikTok is requiring login', site='TikTok')
        self.assertIn('TikTok', message)
        self.assertNotIn('Instagram', message)

    def test_photo_posts_have_a_useful_error(self):
        self.assertTrue(hasattr(worker, 'friendly_error'), 'Downloader error handling is missing')
        self.assertIn('video', worker.friendly_error('No video formats found').lower())

    def test_youtube_errors_name_youtube_and_explain_recovery(self):
        site = worker.site_name('https://www.youtube.com/watch?v=BaW_jenozKc')
        for error in ('Sign in to confirm you are not a bot', 'Private video. Sign in', 'Please log in'):
            with self.subTest(error=error):
                message = worker.friendly_error(error, site)
                self.assertIn('YouTube', message)
                self.assertIn('browser', message)
                self.assertNotIn('Instagram', message)
        message = worker.friendly_error('Requested format is not available', site)
        self.assertIn('YouTube', message)
        self.assertIn('Update Downloader', message)
        self.assertNotIn('photos', message)

    def test_youtube_enables_runtime_and_downloads_only_selected_video(self):
        url = 'https://www.youtube.com/watch?v=BaW_jenozKc'
        with patch('worker.shutil.which', return_value='/usr/local/bin/runtime'), patch('worker.YoutubeDL') as ydl:
            worker.download(url, Path('/tmp/video-test'), 'chrome', lambda event: None)
        options = ydl.call_args.args[0]
        self.assertTrue(options['noplaylist'])
        self.assertEqual(options['cookiesfrombrowser'], ('chrome',))
        self.assertIn('deno', options['js_runtimes'])
        self.assertIn('node', options['js_runtimes'])
        ydl.return_value.__enter__.return_value.extract_info.assert_called_once_with(url, download=True)

    def test_missing_runtime_has_guidance_before_starting_youtube_download(self):
        with patch('worker.shutil.which', return_value=None), patch('worker.YoutubeDL') as ydl:
            with self.assertRaisesRegex(RuntimeError, 'Deno or Node.js'):
                worker.download('https://www.youtube.com/watch?v=BaW_jenozKc', Path('/tmp/video-test'), '', lambda event: None)
            ydl.assert_not_called()

    def test_youtube_live_and_upcoming_videos_fail_with_guidance_but_recordings_work(self):
        options = worker.download_options(Path('/tmp/video-test'), '', lambda event: None,
                                          'https://www.youtube.com/watch?v=BaW_jenozKc')
        for live_status in ('is_live', 'is_upcoming'):
            with self.subTest(live_status=live_status), YoutubeDL(options) as downloader:
                info = {'id': 'test', 'title': 'test', 'live_status': live_status,
                        'url': 'http://127.0.0.1:1/must-not-download.mp4'}
                with self.assertRaisesRegex(DownloadError, 'recording is available'):
                    downloader.process_ie_result(info, download=True)
        for live_status in ('not_live', 'was_live', 'post_live'):
            self.assertIsNone(options['match_filter']({'live_status': live_status}))

    def test_social_video_options_do_not_limit_multi_video_posts_or_require_runtime(self):
        with patch('worker.shutil.which', return_value=None):
            options = worker.download_options(Path('/tmp/video-test'), '', lambda event: None,
                                              'https://www.instagram.com/p/ABC/')
        self.assertNotIn('noplaylist', options)
        self.assertNotIn('match_filter', options)
        self.assertNotIn('cookiesfrombrowser', options)
        self.assertNotIn('js_runtimes', options)

    def test_without_ffmpeg_selects_a_complete_video_with_audio(self):
        with patch('worker.shutil.which', return_value=None):
            options = worker.download_options(Path('/tmp/video-test'), '', lambda event: None,
                                              'https://www.youtube.com/watch?v=BaW_jenozKc')
        info = {'id': 'test', 'title': 'test', 'formats': [
            {'format_id': 'complete', 'url': 'https://example.com/video.webm', 'ext': 'webm', 'vcodec': 'vp9', 'acodec': 'opus', 'height': 360},
            {'format_id': 'audio', 'url': 'https://example.com/audio.m4a', 'ext': 'm4a', 'vcodec': 'none', 'acodec': 'aac'},
            {'format_id': 'split', 'url': 'https://example.com/split.mp4', 'ext': 'mp4', 'vcodec': 'h264', 'acodec': 'none', 'height': 1080},
        ]}
        with YoutubeDL(options) as downloader:
            result = downloader.process_ie_result(info, download=False)
        self.assertEqual(result['format_id'], 'complete')
        self.assertEqual(result['acodec'], 'opus')

    def test_real_downloader_saves_a_video_and_reports_progress(self):
        self.assertTrue(hasattr(worker, 'download'), 'yt-dlp integration is missing')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            fixture = source / 'fixture.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=96x96:d=0.3',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.3', '-c:v', 'libx264',
                            '-c:a', 'aac', '-shortest', str(fixture)], check=True)
            class SilentHandler(SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SilentHandler, directory=str(source)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                folder = root / 'saved'
                folder.mkdir()
                events = []
                app.ProcessDownloader()(f'http://127.0.0.1:{server.server_port}/fixture.mp4', folder, '', events.append)
                saved = list(folder.glob('*.mp4'))
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0].read_bytes(), fixture.read_bytes())
                self.assertTrue(any(event.get('status') == 'downloading' for event in events))

                # Exercise real yt-dlp filenames for two identically captioned
                # carousel entries, then publish through the actual queue.
                def carousel_download(url, folder, browser, progress):
                    options = worker.download_options(folder, browser, progress)
                    with YoutubeDL(options) as downloader:
                        downloader.add_post_processor(worker.ShortTitlePP(), when='pre_process')
                        downloader.process_ie_result({'_type': 'playlist', 'id': 'carousel',
                            'extractor': 'fixture', 'extractor_key': 'Fixture', 'entries': [
                            {'id': str(index), 'title': 'Video by creator', 'description': 'An afternoon at the beach #travel',
                             'extractor': 'fixture', 'extractor_key': 'Fixture',
                             'url': f'http://127.0.0.1:{server.server_port}/fixture.mp4', 'ext': 'mp4'}
                            for index in (1, 2)
                        ]}, download=True)
                manager = app.DownloadManager(root / 'downloads', root / 'history.json', carousel_download)
                try:
                    job = manager.submit('https://instagram.com/p/ABC/')
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        result = manager.get(job['id'])
                        if result['status'] in ('complete', 'error'):
                            break
                        time.sleep(.01)
                    self.assertEqual(result['status'], 'complete', result.get('error'))
                    self.assertEqual([f['name'] for f in result['files']],
                                     ['An afternoon at the beach.mp4', 'An afternoon at the beach (2).mp4'])
                    for file in result['files']:
                        saved_file = manager.file(job['id'], file['name'])
                        self.assertEqual(saved_file.parent, manager.destination / 'Instagram')
                        self.assertEqual(saved_file.read_bytes(), fixture.read_bytes())
                finally:
                    manager.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_network_failure_does_not_report_a_saved_file(self):
        self.assertTrue(hasattr(worker, 'download'), 'yt-dlp integration is missing')
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with self.assertRaises(RuntimeError):
                app.ProcessDownloader(timeout=10)('http://127.0.0.1:1/missing.mp4', folder, '', lambda event: None)
            self.assertEqual(list(folder.glob('*.mp4')), [])


if __name__ == '__main__':
    unittest.main()
