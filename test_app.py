import http.client
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

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

    def test_accepts_instagram_photos_stories_and_highlights(self):
        for supplied, expected in (
            ('instagram.com/p/Photo123/?img_index=2', 'https://www.instagram.com/p/Photo123/'),
            ('instagram.com/stories/creator.name/12345/?igsh=tracking', 'https://www.instagram.com/stories/creator.name/12345/'),
            ('https://m.instagram.com/stories/creator/', 'https://www.instagram.com/stories/creator/'),
            ('https://www.instagram.com/stories/highlights/9876/', 'https://www.instagram.com/stories/highlights/9876/'),
        ):
            with self.subTest(url=supplied):
                self.assertEqual(self.accepted(supplied), expected)
        for url in ('instagram.com/stories/highlights/', 'instagram.com/stories/creator/123/extra',
                    'instagram.com/stories/creator/not-a-number', 'instagram.com/stories/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.normalize(url)

    def test_default_destination_is_documents_video_download(self):
        self.assertEqual(app.DEFAULT_DESTINATION, Path.home() / 'Documents' / 'Video-Download')

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

    def test_youtube_link_variations_normalize_to_one_full_video(self):
        video_id = 'BaW_jenozKc'
        for url in (
            f'https://www.youtube.com/watch?v={video_id}',
            f' youtube.com/watch?v={video_id}&si=tracking ',
            f'http://YOUTUBE.COM/watch?feature=share&v={video_id}&t=20s',
            f'https://www.youtube.com/watch/?v={video_id}#t=30',
            f'https://m.youtube.com/watch?v={video_id}&list=PLexample&index=4',
            f'https://music.youtube.com/watch?v={video_id}&list=RDexample',
            f'https://youtu.be/{video_id}?si=tracking&t=30',
            f'www.youtu.be/{video_id}/?list=PLexample',
            f'https://youtube.com/shorts/{video_id}?feature=share',
            f'https://m.youtube.com/shorts/{video_id}/',
            f'https://www.youtube.com/embed/{video_id}?start=30',
            f'https://youtube-nocookie.com/embed/{video_id}',
            f'https://www.youtube-nocookie.com/embed/{video_id}/?rel=0',
            f'https://www.youtube.com/live/{video_id}?si=tracking',
            f'https://www.youtube.com/v/{video_id}',
            'https://youtube.com/watch?v=ab_cd-EF123',
        ):
            with self.subTest(url=url):
                expected_id = 'ab_cd-EF123' if 'ab_cd-EF123' in url else video_id
                self.assertEqual(self.accepted(url), f'https://www.youtube.com/watch?v={expected_id}')

    def test_rejects_nonvideo_or_ambiguous_youtube_links(self):
        for url in (
            'https://youtube.com/', 'https://youtube.com/@creator',
            'https://youtube.com/channel/UCexample', 'https://youtube.com/@creator/live',
            'https://youtube.com/playlist?list=PLexample',
            'https://youtube.com/clip/UgkxExample', 'https://youtube.com/results?search_query=video',
            'https://youtube.com/watch', 'https://youtube.com/watch?list=PLexample',
            'https://youtube.com/watch?v=', 'https://youtube.com/watch?v=too-short',
            'https://youtube.com/watch?v=BaW_jenozKcX',
            'https://youtube.com/watch?v=BaW_jenozKc&v=abcdefghijk',
            'https://youtube.com/watch?v=&v=BaW_jenozKc',
            'https://youtube.com/watch?v=BaW_jenozK%21',
            'https://youtu.be/', 'https://youtu.be/BaW_jenozKc/extra',
            'https://youtube.com/shorts/', 'https://youtube.com/shorts/too-short',
            'https://youtube.com/embed/videoseries?list=PLexample',
            'https://www.youtube-nocookie.com/embed/videoseries/?list=PLexample',
            'https://youtube-nocookie.com/watch?v=BaW_jenozKc',
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'YouTube'):
                self.normalize(url)

    def test_rejects_false_youtube_hosts_and_non_web_schemes(self):
        for url in (
            'https://youtube.com.evil.com/watch?v=BaW_jenozKc',
            'https://youtube.com@evil.com/watch?v=BaW_jenozKc',
            'https://evil.com@youtube.com/watch?v=BaW_jenozKc',
            'https://www.youtube.com:9000/watch?v=BaW_jenozKc',
            'https://youtu.be.evil.com/BaW_jenozKc',
            'https://www.youtube-nocookie.com.evil.com/embed/BaW_jenozKc',
            'ftp://youtube.com/watch?v=BaW_jenozKc',
            'https://youtube.com/redirect?q=https://evil.com/video.mp4',
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.normalize(url)

    def test_accepts_tiktok_photo_links_and_removes_tracking(self):
        self.assertEqual(self.accepted(' tiktok.com/@willgoviral/photo/7687004136411286798/?photo_id=2 '),
                         'https://www.tiktok.com/@willgoviral/photo/7687004136411286798')

    def test_rejects_tiktok_profiles_malformed_posts_and_false_hosts(self):
        for url in ('https://www.tiktok.com/@creator', 'https://www.tiktok.com/@creator/live',
                    'https://www.tiktok.com/@creator/photo/not-a-number',
                    'https://www.tiktok.com/@creator/photo/123/extra',
                    'https://www.tiktok.com/tag/video', 'https://www.tiktok.com/t/',
                    'https://tiktok.com.evil.com/t/Z123/', 'https://www.tiktok.com:9000/t/Z123/',
                    'https://www.tiktok.com@evil.com/t/Z123/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.normalize(url)

    def test_rejects_other_hosts_and_unsupported_links(self):
        for url in ('https://evil.com/reel/abc/', 'https://instagram.com.evil.com/p/abc/',
                    'https://instagram.com@evil.com/p/abc/', 'https://www.instagram.com/person/',
                    'https://www.instagram.com/stories/person/not-an-id/', 'file:///tmp/video.mp4',
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

    def test_youtube_uses_queue_browser_session_and_persisted_history(self):
        def download(url, folder, browser, progress):
            self.assertEqual(url, 'https://www.youtube.com/watch?v=BaW_jenozKc')
            self.assertEqual(browser, 'firefox')
            progress({'status': 'downloading', 'progress': 60})
            (folder / 'youtube-video.mp4').write_bytes(b'video')
        manager = self.manager(download)
        job = manager.submit('https://youtu.be/BaW_jenozKc?si=tracking&t=20', 'firefox')
        result = self.wait(manager, job['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['progress'], 100)
        manager.close()
        restarted = self.manager(download)
        self.assertEqual(restarted.get(job['id']), result)
        self.assertEqual(restarted.file(job['id'], 'youtube-video.mp4').read_bytes(), b'video')

    def test_youtube_has_more_time_to_download_and_explicit_timeouts_are_respected(self):
        for url, override, expected in (
            ('https://www.youtube.com/watch?v=BaW_jenozKc', None, 1800),
            ('https://www.instagram.com/reel/ABC/', None, 180),
            ('https://www.tiktok.com/t/example/', None, 180),
            ('https://www.youtube.com/watch?v=BaW_jenozKc', 10, 10),
        ):
            with self.subTest(url=url, override=override):
                process = Mock(stdout=StringIO(''))
                process.wait.return_value = 0
                with patch('app.subprocess.Popen', return_value=process), patch('app.threading.Timer') as timer:
                    app.ProcessDownloader(timeout=override)(url, self.root, '', lambda event: None)
                self.assertEqual(timer.call_args.args[0], expected)

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

    def test_slideshow_photos_are_ordered_published_and_preserved_after_restart(self):
        def download(url, folder, browser, progress):
            progress({'media_type': 'photo', 'total_files': 3, 'completed_files': 3})
            for index, extension in ((3, 'webp'), (1, 'jpg'), (2, 'png')):
                (folder / f'{index:03d} - My slideshow - {index:02d}.{extension}').write_bytes(b'photo')
            (folder / '004.part').write_bytes(b'incomplete')
            (folder / 'metadata.json').write_text('{}')
        manager = self.manager(download)
        job = manager.submit('https://www.tiktok.com/@creator/photo/123')
        result = self.wait(manager, job['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([file['name'] for file in result['files']],
                         ['My slideshow - 01.jpg', 'My slideshow - 02.png', 'My slideshow - 03.webp'])
        self.assertTrue(all(file['kind'] == 'photo' for file in result['files']))
        manager.close()
        restarted = self.manager(download)
        self.assertEqual(restarted.get(job['id']), result)
        for file in result['files']:
            self.assertEqual(restarted.file(job['id'], file['name']).read_bytes(), b'photo')
        self.assertEqual(len(list((manager.destination / 'TikTok').iterdir())), 3)

    def test_partial_slideshow_is_an_error_and_keeps_completed_photos(self):
        def download(url, folder, browser, progress):
            (folder / '001 - Slideshow - 01.jpg').write_bytes(b'photo')
            (folder / '002.part').write_bytes(b'incomplete')
            raise RuntimeError('Saved 1 of 2 photos. Could not download slide 2.')
        manager = self.manager(download)
        result = self.wait(manager, manager.submit('https://www.tiktok.com/@creator/photo/123')['id'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('1 of 2', result['error'])
        self.assertEqual([file['name'] for file in result['files']], ['Slideshow - 01.jpg'])

    def test_slideshow_folders_are_named_safely_and_never_combined(self):
        def download(url, folder, browser, progress):
            progress({'collection_title': '../A weekend away', 'media_type': 'photo'})
            (folder / '001 - A weekend away - 01.jpg').write_bytes(b'photo')
        manager = self.manager(download)
        results = [self.wait(manager, manager.submit('https://www.tiktok.com/@creator/photo/123')['id']) for _ in range(2)]
        for result, name in zip(results, ('A weekend away', 'A weekend away (2)')):
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(Path(result['folder']), manager.destination / 'TikTok' / name)
            self.assertEqual(manager.file(result['id'], result['files'][0]['name']).read_bytes(), b'photo')
        manager.close()
        restarted = self.manager(download)
        self.assertEqual(restarted.get(results[0]['id']), results[0])

    def test_individual_photos_stay_directly_in_platform_folder(self):
        def download(url, folder, browser, progress):
            progress({'media_type': 'photo', 'total_files': 1, 'collection_title': None})
            (folder / '001 - My photo.jpg').write_bytes(b'photo')
        manager = self.manager(download)
        for url, platform in (('https://www.tiktok.com/@creator/photo/123', 'TikTok'),
                              ('https://www.instagram.com/p/Photo123/', 'Instagram')):
            result = self.wait(manager, manager.submit(url)['id'])
            self.assertEqual(Path(result['folder']), manager.destination / platform)
            self.assertEqual(result['files'][0]['name'], 'My photo.jpg')

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

    def test_files_go_directly_into_the_correct_platform_folders(self):
        def download(url, folder, browser, progress):
            (folder / '001 - A useful video title.mp4').write_bytes(b'video')
        manager = self.manager(download)
        for url, platform in (
            ('https://instagram.com/reel/ABC/', 'Instagram'),
            ('https://youtu.be/jNQXAC9IVRw', 'YouTube'),
            ('https://vm.tiktok.com/Zexample/', 'TikTok'),
        ):
            with self.subTest(platform=platform):
                job = manager.submit(url)
                result = self.wait(manager, job['id'])
                self.assertEqual(result['status'], 'complete')
                self.assertEqual([f['name'] for f in result['files']], ['A useful video title.mp4'])
                expected = manager.destination / platform / 'A useful video title.mp4'
                self.assertEqual(manager.file(job['id'], expected.name), expected)
                self.assertEqual(list(expected.parent.iterdir()), [expected])

    def test_duplicate_titles_and_existing_files_are_never_overwritten(self):
        def download(url, folder, browser, progress):
            (folder / '001 - Same title.mp4').write_bytes(url.encode())
            (folder / '002 - Same title.mp4').write_bytes(b'second carousel video')
        manager = self.manager(download)
        platform = manager.destination / 'Instagram'
        platform.mkdir()
        (platform / 'Same title.mp4').write_bytes(b'existing user file')
        first = manager.submit('https://instagram.com/p/ABC/')
        first = self.wait(manager, first['id'])
        second = manager.submit('https://instagram.com/p/DEF/')
        second = self.wait(manager, second['id'])
        self.assertEqual([f['name'] for f in first['files']], ['Same title (2).mp4', 'Same title (3).mp4'])
        self.assertEqual([f['name'] for f in second['files']], ['Same title (4).mp4', 'Same title (5).mp4'])
        self.assertEqual((platform / 'Same title.mp4').read_bytes(), b'existing user file')
        self.assertEqual(manager.file(first['id'], 'Same title (2).mp4').read_bytes(), first['url'].encode())
        with self.assertRaises(FileNotFoundError):
            manager.file(first['id'], 'Same title (4).mp4')
        with self.assertRaises(FileNotFoundError):
            manager.file(first['id'], 'Same title.mp4')

    def test_legacy_downloads_stay_accessible_after_default_root_changes(self):
        destination = self.root / 'Downloads'
        old_folder = destination / 'Instagram' / 'oldjob'
        old_folder.mkdir(parents=True)
        (old_folder / 'old-video.mp4').write_bytes(b'old download')
        history = self.root / 'history.json'
        history.write_text(json.dumps([dict(id='oldjob', url='https://www.instagram.com/p/ABC/',
            status='complete', progress=100, browser='', files=[{'name': 'old-video.mp4'}])]))
        manager = app.DownloadManager(destination, history, legacy_destination=destination / 'Instagram')
        self.addCleanup(manager.close)
        self.assertEqual(manager.file('oldjob', 'old-video.mp4'), old_folder.resolve() / 'old-video.mp4')
        manager.close()
        # Persisted folders also survive choosing another destination later.
        restarted = app.DownloadManager(self.root / 'elsewhere', history)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.file('oldjob', 'old-video.mp4').read_bytes(), b'old download')

    def test_legacy_custom_destination_keeps_its_original_job_folder(self):
        old_folder = self.root / 'downloads' / 'oldjob'
        old_folder.mkdir(parents=True)
        (old_folder / 'old-video.mp4').write_bytes(b'old custom download')
        self.root.joinpath('history.json').write_text(json.dumps([dict(id='oldjob',
            status='complete', files=[{'name': 'old-video.mp4'}])]))
        manager = self.manager(lambda *args: None)
        self.assertEqual(manager.file('oldjob', 'old-video.mp4').read_bytes(), b'old custom download')

    def test_folder_creation_failure_does_not_stop_the_queue(self):
        def download(url, folder, browser, progress):
            (folder / '001 - Good video.mp4').write_bytes(b'video')
        manager = self.manager(download)
        (manager.destination / 'Instagram').write_text('not a folder')
        bad = manager.submit('https://instagram.com/reel/ABC/')
        good = manager.submit('https://youtu.be/jNQXAC9IVRw')
        self.assertEqual(self.wait(manager, bad['id'])['status'], 'error')
        self.assertEqual(self.wait(manager, good['id'])['status'], 'complete')

    def test_platform_folder_symlinks_are_not_followed(self):
        manager = self.manager(lambda *args: self.fail('Symlink reached downloader'))
        outside = self.root / 'outside'
        outside.mkdir()
        (manager.destination / 'Instagram').symlink_to(outside, target_is_directory=True)
        job = manager.submit('https://instagram.com/reel/ABC/')
        result = self.wait(manager, job['id'])
        self.assertEqual(result['status'], 'error')
        self.assertEqual(list(outside.iterdir()), [])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(app, 'create_server'), 'Local HTTP app is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)

        def download(url, folder, browser, progress):
            if '/photo/' in url:
                (folder / '001 - Slideshow - 01.jpg').write_bytes(b'local photo')
            else:
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

    def test_ui_explains_supported_platforms(self):
        _, _, page = self.request('GET', '/')
        self.assertIn(b'TikTok', page)
        self.assertIn(b'YouTube', page)
        self.assertIn(b'Shorts', page)
        _, _, script = self.request('GET', '/app.js')
        self.assertIn(b'TikTok', script)
        self.assertIn(b'YouTube', script)

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

    def test_youtube_share_link_can_be_submitted_and_saved_through_api(self):
        status, _, body = self.request('POST', '/api/downloads',
            json.dumps({'url': 'https://youtube.com/shorts/BaW_jenozKc?si=tracking'}), self.headers())
        self.assertEqual(status, 202)
        job = json.loads(body)
        self.assertEqual(job['url'], 'https://www.youtube.com/watch?v=BaW_jenozKc')
        for _ in range(100):
            if self.manager.get(job['id'])['status'] == 'complete':
                break
            time.sleep(0.01)
        self.assertEqual(self.manager.get(job['id'])['status'], 'complete')
        status, _, body = self.request('GET', f"/files/{job['id']}/video.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, b'local video')

    def test_tiktok_slideshow_can_be_submitted_and_photo_served_as_an_image(self):
        status, _, body = self.request('POST', '/api/downloads',
            json.dumps({'url': 'https://www.tiktok.com/@willgoviral/photo/7687004136411286798'}), self.headers())
        self.assertEqual(status, 202)
        job = json.loads(body)
        for _ in range(100):
            result = self.manager.get(job['id'])
            if result['status'] == 'complete':
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'complete')
        status, headers, body = self.request('GET', result['files'][0]['url'])
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/jpeg')
        self.assertEqual(body, b'local photo')
        self.assertIn('attachment', headers['Content-Disposition'])

    def test_playlist_without_a_video_is_rejected_before_queueing(self):
        status, _, body = self.request('POST', '/api/downloads',
            json.dumps({'url': 'https://youtube.com/playlist?list=PLexample'}), self.headers())
        self.assertEqual(status, 400)
        self.assertIn('YouTube', json.loads(body)['error'])
        self.assertEqual(self.manager.list(), [])

    def test_foreign_sites_cannot_trigger_downloads(self):
        headers = self.headers() | {'Origin': 'https://example.com'}
        status, _, _ = self.request('POST', '/api/downloads', '{}', headers)
        self.assertEqual(status, 403)
        self.assertEqual(self.manager.list(), [])

    def test_show_in_finder_reveals_the_video_in_its_platform_folder(self):
        job = self.manager.submit('https://youtu.be/jNQXAC9IVRw')
        for _ in range(100):
            if self.manager.get(job['id'])['status'] == 'complete':
                break
            time.sleep(0.01)
        with patch('app.sys.platform', 'darwin'), patch('app.subprocess.run') as run:
            status, _, _ = self.request('POST', '/api/open-folder', json.dumps({'id': job['id']}), self.headers())
        self.assertEqual(status, 200)
        expected = self.manager.destination / 'YouTube' / 'video.mp4'
        run.assert_called_once_with(['open', '-R', str(expected)], check=True)

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
