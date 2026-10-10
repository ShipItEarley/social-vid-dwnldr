"""X/Twitter extraction with local video transfers, without live posts or accounts."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app
import worker
from yt_dlp.extractor.twitter import TwitterIE


class TwitterTests(unittest.TestCase):
    def test_browser_session_is_opt_in_and_no_youtube_runtime_is_required(self):
        url = 'https://x.com/creator/status/123'
        for browser in ('', 'chrome'):
            with self.subTest(browser=browser), patch('worker.shutil.which', return_value=None), patch('worker.YoutubeDL') as ydl:
                worker.download(url, Path('/tmp/twitter-test'), browser, lambda event: None)
                options = ydl.call_args.args[0]
                self.assertEqual(options.get('cookiesfrombrowser'), (browser,) if browser else None)
                self.assertNotIn('js_runtimes', options)
                ydl.return_value.__enter__.return_value.extract_info.assert_called_once_with(url, download=True)

    def test_errors_identify_twitter_and_explain_recovery(self):
        site = worker.site_name(app.normalize_url('https://twitter.com/creator/status/123'))
        self.assertEqual(site, 'Twitter')
        for error in ('Login required', 'Rate limit exceeded', 'Private post', 'Could not decrypt cookies'):
            with self.subTest(error=error):
                message = worker.friendly_error(error, site)
                self.assertIn('Twitter', message)
                self.assertIn('browser', message)
                self.assertNotIn('Instagram', message)
        self.assertIn('No downloadable video', worker.friendly_error('No video could be found in this tweet', site))
        self.assertIn('No downloadable video', worker.friendly_error('Media #2 is not a video', site))
        self.assertIn('deleted posts cannot be downloaded', worker.friendly_error('Requested tweet is unavailable', site))

    def test_twitter_post_videos_and_selected_video_download_through_real_extractor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            fixtures = {}
            for media_id, color in (('11', 'blue'), ('13', 'red')):
                fixture = source / f'{media_id}.mp4'
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=96x96:d=0.3',
                                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.3', '-c:v', 'libx264',
                                '-c:a', 'aac', '-shortest', str(fixture)], check=True)
                fixtures[media_id] = fixture.read_bytes()

            class Handler(SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(source)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            manager = app.DownloadManager(root / 'downloads', root / 'history.json', worker.download)
            origin = f'http://127.0.0.1:{server.server_port}'

            def video(media_id):
                return {'id_str': media_id, 'type': 'video', 'video_info': {
                    'duration_millis': 300, 'variants': [{'url': f'{origin}/{media_id}.mp4', 'bitrate': 100000}]}}

            status = {'full_text': 'Two clips #test', 'user': {'name': 'Creator', 'screen_name': 'creator'},
                      'extended_entities': {'media': [video('11'), {'id_str': '12', 'type': 'photo'}, video('13')]}}
            try:
                with patch.object(TwitterIE, '_extract_status', return_value=status):
                    for suffix, expected_names in (
                        ('', ['Creator - Two clips.mp4', 'Creator - Two clips (2).mp4']),
                        ('/video/3', ['Creator - Two clips (3).mp4']),
                    ):
                        with self.subTest(suffix=suffix):
                            job = manager.submit(f'https://twitter.com/creator/status/123{suffix}?s=20')
                            result = self.wait(manager, job['id'])
                            self.assertEqual(result['status'], 'complete', result['error'])
                            self.assertEqual([file['name'] for file in result['files']], expected_names)
                            self.assertEqual(Path(result['folder']), manager.destination / 'Twitter')
                            for file, media_id in zip(result['files'], ['13'] if suffix else ['11', '13']):
                                self.assertEqual(file['kind'], 'video')
                                self.assertEqual(manager.file(job['id'], file['name']).read_bytes(), fixtures[media_id])
                    for suffix in ('/video/2', '/video/4'):
                        with self.subTest(suffix=suffix):
                            job = manager.submit(f'https://x.com/creator/status/123{suffix}')
                            result = self.wait(manager, job['id'])
                            self.assertEqual(result['status'], 'error')
                            self.assertEqual(result['files'], [])
                with patch.object(TwitterIE, '_extract_status', return_value={'full_text': 'Text only'}):
                    job = manager.submit('https://x.com/creator/status/456')
                    result = self.wait(manager, job['id'])
                    self.assertEqual(result['status'], 'error')
                    self.assertEqual(result['files'], [])
                    self.assertIn('No video', result['error'])
            finally:
                manager.close()
                server.shutdown()
                server.server_close()

    def wait(self, manager, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = manager.get(job_id)
            if result['status'] in ('complete', 'error'):
                return result
            time.sleep(.01)
        self.fail('Download did not finish')


if __name__ == '__main__':
    unittest.main()
