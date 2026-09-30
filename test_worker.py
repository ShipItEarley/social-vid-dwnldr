from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import app
import worker
from yt_dlp import YoutubeDL


class WorkerTests(unittest.TestCase):
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
