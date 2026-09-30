import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import app


class URLTests(unittest.TestCase):
    def normalize(self, url):
        self.assertTrue(hasattr(app, 'normalize_url'), 'Instagram link validation is missing')
        return app.normalize_url(url)

    def test_accepts_reels_and_removes_tracking(self):
        self.assertEqual(self.normalize(' instagram.com/reel/ABC_123-x/?igsh=tracking '),
                         'https://www.instagram.com/reel/ABC_123-x/')

    def test_accepts_video_posts(self):
        self.assertEqual(self.normalize('https://m.instagram.com/p/ABC123/#fragment'),
                         'https://www.instagram.com/p/ABC123/')

    def test_accepts_tiktok_video_links_and_removes_tracking(self):
        self.assertEqual(self.accepted(' tiktok.com/@creator.name/video/738831760238/?tracking=1 '),
                         'https://www.tiktok.com/@creator.name/video/738831760238')

    def test_accepts_tiktok_share_links(self):
        for supplied, expected in (
            ('https://www.tiktok.com/t/ZP8Example123/?_r=1', 'https://www.tiktok.com/t/ZP8Example123/'),
            ('https://vm.tiktok.com/ZMabc123/#tracking', 'https://vm.tiktok.com/ZMabc123/'),
            ('https://vt.tiktok.com/ZMabc123/', 'https://vt.tiktok.com/ZMabc123/'),
        ):
            with self.subTest(url=supplied):
                self.assertEqual(self.accepted(supplied), expected)

    def accepted(self, url):
        try:
            return self.normalize(url)
        except ValueError as exc:
            self.fail(f'Supported link rejected: {exc}')

    def test_rejects_tiktok_profiles_photos_and_false_hosts(self):
        for url in ('https://www.tiktok.com/@creator', 'https://www.tiktok.com/@creator/live',
                    'https://www.tiktok.com/@creator/photo/738831760238',
                    'https://www.tiktok.com/tag/video', 'https://www.tiktok.com/t/',
                    'https://tiktok.com.evil.com/t/Z123/', 'https://www.tiktok.com:9000/t/Z123/',
                    'https://www.tiktok.com@evil.com/t/Z123/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.normalize(url)

    def test_rejects_other_hosts_and_unsupported_links(self):
        for url in ('https://evil.com/reel/abc/', 'https://instagram.com.evil.com/p/abc/',
                    'https://instagram.com@evil.com/p/abc/', 'https://www.instagram.com/person/',
                    'https://www.instagram.com/stories/person/1/', 'file:///tmp/video.mp4',
                    'https://instagram.com:9000/p/abc/', '', None, 123):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.normalize(url)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(app, 'DownloadManager'), 'Download queue is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manager(self, download):
        manager = app.DownloadManager(self.root / 'downloads', self.root / 'history.json', download)
        self.addCleanup(manager.close)
        return manager

    def wait(self, manager, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job['status'] in ('complete', 'error'):
                return job
            time.sleep(0.01)
        self.fail('Download never reached a terminal state')

    def test_download_tracks_progress_and_keeps_history_after_restart(self):
        def download(url, folder, browser, progress):
            progress({'status': 'downloading', 'progress': 50})
            (folder / 'creator-ABC.mp4').write_bytes(b'video content')

        manager = self.manager(download)
        job = manager.submit('https://instagram.com/reel/ABC/')
        result = self.wait(manager, job['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['progress'], 100)
        self.assertEqual(result['files'][0]['name'], 'creator-ABC.mp4')
        self.assertEqual(manager.file(job['id'], 'creator-ABC.mp4').read_bytes(), b'video content')
        manager.close()
        restarted = self.manager(download)
        self.assertEqual(restarted.get(job['id'])['status'], 'complete')

    def test_failure_is_reported_and_does_not_stop_next_download(self):
        def download(url, folder, browser, progress):
            if '/bad/' in url:
                raise RuntimeError('Instagram requires login')
            (folder / 'video.mp4').write_bytes(b'video')

        manager = self.manager(download)
        bad = manager.submit('https://instagram.com/p/bad/')
        good = manager.submit('https://instagram.com/p/good/')
        self.assertEqual(self.wait(manager, bad['id'])['status'], 'error')
        self.assertIn('login', manager.get(bad['id'])['error'])
        self.assertEqual(self.wait(manager, good['id'])['status'], 'complete')

    def test_tiktok_share_links_use_the_same_download_flow(self):
        def download(url, folder, browser, progress):
            self.assertEqual(url, 'https://www.tiktok.com/t/ZP8Example123/')
            (folder / 'tiktok-video.mp4').write_bytes(b'video')
        manager = self.manager(download)
        try:
            job = manager.submit('https://www.tiktok.com/t/ZP8Example123/?tracking=1')
        except ValueError as exc:
            self.fail(f'TikTok download could not be queued: {exc}')
        result = self.wait(manager, job['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['files'][0]['name'], 'tiktok-video.mp4')

    def test_no_video_is_an_error(self):
        manager = self.manager(lambda *args: None)
        job = manager.submit('https://instagram.com/p/photo/')
        self.assertEqual(self.wait(manager, job['id'])['status'], 'error')
        self.assertIn('video', manager.get(job['id'])['error'].lower())

    def test_partial_downloads_are_preserved_but_reported_as_errors(self):
        def download(url, folder, browser, progress):
            (folder / 'first.mp4').write_bytes(b'video')
            raise RuntimeError('Second video unavailable')

        manager = self.manager(download)
        job = manager.submit('https://instagram.com/p/multiple/')
        result = self.wait(manager, job['id'])
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['files'][0]['name'], 'first.mp4')

    def test_rejects_invalid_browser_before_starting_download(self):
        manager = self.manager(lambda *args: self.fail('Invalid browser reached downloader'))
        for browser in ('anything', ['chrome'], 123):
            with self.subTest(browser=browser), self.assertRaises(ValueError):
                manager.submit('https://instagram.com/p/ABC/', browser)

    def test_never_serves_paths_outside_job_folder(self):
        def download(url, folder, browser, progress):
            (folder / 'video.mp4').write_bytes(b'video')
            (folder / 'link.mp4').symlink_to(self.root / 'secret.mp4')
        (self.root / 'secret.mp4').write_bytes(b'secret')
        manager = self.manager(download)
        job = manager.submit('https://instagram.com/p/ABC/')
        result = self.wait(manager, job['id'])
        self.assertEqual([f['name'] for f in result['files']], ['video.mp4'])
        for name in ('../../secret.mp4', 'link.mp4', 'missing.mp4'):
            with self.subTest(name=name), self.assertRaises(FileNotFoundError):
                manager.file(job['id'], name)

    def test_interrupted_jobs_are_marked_and_can_be_resubmitted(self):
        self.root.joinpath('history.json').write_text(json.dumps([{
            'id': 'abc', 'url': 'https://www.instagram.com/p/abc/', 'status': 'downloading',
            'files': [], 'progress': 40, 'created_at': '2026-09-30T12:00:00Z', 'browser': ''
        }]))
        manager = self.manager(lambda *args: None)
        self.assertEqual(manager.get('abc')['status'], 'error')
        self.assertIn('interrupted', manager.get('abc')['error'].lower())


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(app, 'create_server'), 'Local HTTP app is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)

        def download(url, folder, browser, progress):
            (folder / 'video.mp4').write_bytes(b'local video')

        self.manager = app.DownloadManager(root / 'downloads', root / 'history.json', download)
        self.server = app.create_server(self.manager, port=0)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.manager.close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        conn.close()
        return status, response_headers, data

    def headers(self):
        return {'Content-Type': 'application/json', 'Origin': self.origin,
                'X-App-Token': self.server.token}

    def test_app_serves_ui_and_config(self):
        status, headers, page = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Video Downloader', page)
        status, headers, body = self.request('GET', '/api/state')
        state = json.loads(body)
        self.assertEqual(state['token'], self.server.token)
        self.assertEqual(state['jobs'], [])

    def test_ui_explains_both_supported_platforms(self):
        _, _, page = self.request('GET', '/')
        self.assertIn(b'TikTok', page)
        _, _, script = self.request('GET', '/app.js')
        self.assertIn(b'TikTok', script)

    def test_relaunch_recognizes_existing_app(self):
        self.assertTrue(hasattr(app, 'is_running'), 'Launcher instance detection is missing')
        self.assertTrue(app.is_running(self.server.server_port))

    def test_submit_job_and_download_saved_file(self):
        status, headers, body = self.request('POST', '/api/downloads',
            json.dumps({'url': 'https://instagram.com/p/ABC/'}), self.headers())
        self.assertEqual(status, 202)
        job = json.loads(body)
        for _ in range(100):
            if self.manager.get(job['id'])['status'] == 'complete':
                break
            time.sleep(0.01)
        status, headers, body = self.request('GET', f"/files/{job['id']}/video.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, b'local video')
        self.assertIn('attachment', headers['Content-Disposition'])

    def test_foreign_sites_cannot_trigger_downloads(self):
        headers = self.headers() | {'Origin': 'https://example.com'}
        status, _, _ = self.request('POST', '/api/downloads', '{}', headers)
        self.assertEqual(status, 403)
        self.assertEqual(self.manager.list(), [])

    def test_mutations_require_session_token(self):
        status, _, _ = self.request('POST', '/api/downloads', '{}',
                                   {'Content-Type': 'application/json'})
        self.assertEqual(status, 403)

    def test_unrecognized_host_cannot_read_token(self):
        status, _, _ = self.request('GET', '/api/state', headers={'Host': 'evil.com'})
        self.assertEqual(status, 403)

    def test_invalid_requests_return_useful_errors(self):
        for payload in ('{', '[]', '{"url": "https://example.com/"}', '{"url": 4}'):
            with self.subTest(payload=payload):
                status, _, data = self.request('POST', '/api/downloads', payload, self.headers())
                self.assertEqual(status, 400)
                self.assertIn('error', json.loads(data))


if __name__ == '__main__':
    unittest.main()
