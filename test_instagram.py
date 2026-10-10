import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, PropertyMock

import app
import worker
from instagram_media import InstagramMediaIE, InstagramStoryMediaIE


class InstagramMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.photo = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')
        (cls.root / 'photo.image').write_bytes(cls.photo)
        # A broken smaller candidate proves that the largest original is preferred.
        (cls.root / 'small.image').write_text('not an image')
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=96x96:d=0.2',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2', '-c:v', 'libx264',
                        '-c:a', 'aac', '-shortest', str(cls.root / 'video.mp4')], check=True)

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(cls.root)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def item(self, pk=1001, photo=True, broken=False):
        return {'pk': pk, 'media_type': 1 if photo else 2,
                'user': {'username': 'creator', 'full_name': 'Creator', 'pk': '42'},
                'caption': {'text': 'A day by the sea #travel'},
                'image_versions2': {'candidates': [
                    {'url': self.origin + '/small.image', 'width': 100, 'height': 100},
                    {'url': self.origin + ('/missing.image' if broken else '/photo.image'), 'width': 2000, 'height': 2000}]},
                'video_versions': [] if photo else [{'url': self.origin + '/video.mp4', 'width': 96, 'height': 96, 'type': 101}]}

    def run_download(self, url, product=None, stories=None):
        self.temp_job = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_job.cleanup)
        root = Path(self.temp_job.name)

        def download(url, folder, browser, emit):
            if '/stories/' in url:
                with patch.object(InstagramStoryMediaIE, '_download_webpage', return_value='{"user":{"pk":"42"}}'), \
                     patch.object(InstagramStoryMediaIE, '_download_json', return_value={'reels': {'42': {
                         'user': {'username': 'creator', 'full_name': 'Creator'}, 'items': stories}}}):
                    worker.download(url, folder, browser, emit)
            else:
                def response(url, *args, **kwargs):
                    if 'graphql' in url:
                        return {'data': {'xig_polaris_media': {'if_not_gated_logged_out': product}}}
                    return {'status': 'ok'}
                with patch.object(InstagramMediaIE, '_real_initialize', lambda self: setattr(self, '_lsd_token', 'fixture')), \
                     patch.object(InstagramMediaIE, '_can_impersonate', new_callable=PropertyMock, return_value=True), \
                     patch.object(InstagramMediaIE, '_download_json', side_effect=response):
                    worker.download(url, folder, browser, emit)
        manager = app.DownloadManager(root / 'Documents' / 'Video-Download', root / 'history.json', download)
        self.addCleanup(manager.close)
        job = manager.submit(url)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = manager.get(job['id'])
            if result['status'] in ('complete', 'error'):
                return manager, result
            time.sleep(.01)
        self.fail('Instagram download did not finish')

    def test_public_single_photo_downloads_largest_available_image(self):
        manager, result = self.run_download('https://www.instagram.com/p/ABC/', product=self.item())
        self.assertEqual(result['status'], 'complete', result.get('error'))
        self.assertEqual(result['files'][0]['name'], 'A day by the sea.png')
        self.assertEqual(manager.file(result['id'], result['files'][0]['name']).read_bytes(), self.photo)
        self.assertEqual(Path(result['folder']), manager.destination / 'Instagram')

    def test_mixed_carousel_saves_photos_and_videos_in_original_order(self):
        product = self.item()
        product.update(media_type=8, carousel_media=[self.item(1001), self.item(1002), self.item(1003, photo=False), self.item(1004)])
        manager, result = self.run_download('https://www.instagram.com/p/ABC/', product=product)
        self.assertEqual(result['status'], 'complete', result.get('error'))
        self.assertEqual([item['name'] for item in result['files']], [
            'A day by the sea - 01.png', 'A day by the sea - 02.png', 'A day by the sea - 03.mp4', 'A day by the sea - 04.png'])
        self.assertEqual([item['kind'] for item in result['files']], ['photo', 'photo', 'video', 'photo'])
        self.assertEqual(Path(result['folder']), manager.destination / 'Instagram' / 'A day by the sea')
        self.assertEqual(len(list((manager.destination / 'Instagram').iterdir())), 1)
        video = manager.file(result['id'], result['files'][2]['name'])
        self.assertEqual(video.read_bytes(), (self.root / 'video.mp4').read_bytes())

    def test_photo_and_video_carousels_get_caption_folders(self):
        for photo in (True, False):
            with self.subTest(photo=photo):
                product = self.item()
                product.update(media_type=8, carousel_media=[self.item(1001, photo=photo), self.item(1002, photo=photo)])
                manager, result = self.run_download('https://www.instagram.com/p/ABC/', product=product)
                self.assertEqual(result['status'], 'complete', result.get('error'))
                folder = manager.destination / 'Instagram' / 'A day by the sea'
                self.assertEqual(Path(result['folder']), folder)
                extension = 'png' if photo else 'mp4'
                self.assertEqual([item['name'] for item in result['files']],
                                 [f'A day by the sea - {index:02d}.{extension}' for index in (1, 2)])
                expected = self.photo if photo else (self.root / 'video.mp4').read_bytes()
                for item in result['files']:
                    saved = manager.file(result['id'], item['name'])
                    self.assertEqual(saved.parent, folder)
                    self.assertEqual(saved.read_bytes(), expected)

    def test_carousel_without_caption_uses_creator_for_folder_name(self):
        product = self.item()
        product.update(caption=None, media_type=8, carousel_media=[self.item(1001), self.item(1002)])
        manager, result = self.run_download('https://www.instagram.com/p/ABC/', product=product)
        self.assertEqual(result['status'], 'complete', result.get('error'))
        self.assertEqual(Path(result['folder']), manager.destination / 'Instagram' / 'Video by Creator')

    def test_full_story_link_saves_only_the_selected_photo(self):
        manager, result = self.run_download('https://www.instagram.com/stories/creator/1002/',
                                           stories=[self.item(1001, photo=False), self.item(1002), self.item(1003)])
        self.assertEqual(result['status'], 'complete', result.get('error'))
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['files'][0]['kind'], 'photo')
        self.assertEqual(manager.file(result['id'], result['files'][0]['name']).read_bytes(), self.photo)

    def test_story_collection_saves_photos_and_videos(self):
        manager, result = self.run_download('https://www.instagram.com/stories/creator/',
                                           stories=[self.item(1001), self.item(1002, photo=False)])
        self.assertEqual(result['status'], 'complete', result.get('error'))
        self.assertEqual([item['kind'] for item in result['files']], ['photo', 'video'])
        self.assertEqual(Path(result['folder']), manager.destination / 'Instagram')

    def test_expired_specific_story_is_an_actionable_error(self):
        _, result = self.run_download('https://www.instagram.com/stories/creator/9999/', stories=[self.item()])
        self.assertEqual(result['status'], 'error')
        self.assertIn('expired', result['error'])
        self.assertEqual(result['files'], [])

    def test_missing_photo_does_not_stop_other_carousel_items(self):
        product = self.item()
        product.update(media_type=8, carousel_media=[self.item(1001), self.item(1002, broken=True), self.item(1003)])
        manager, result = self.run_download('https://www.instagram.com/p/ABC/', product=product)
        self.assertEqual(result['status'], 'error')
        self.assertIn('Saved 2 of 3 items', result['error'])
        self.assertEqual([item['name'] for item in result['files']], ['A day by the sea - 01.png', 'A day by the sea - 03.png'])
        self.assertEqual(Path(result['folder']), manager.destination / 'Instagram' / 'A day by the sea')

    def test_partial_carousel_with_one_saved_photo_still_gets_a_folder(self):
        product = self.item()
        product.update(media_type=8, carousel_media=[self.item(1001, broken=True), self.item(1002)])
        manager, result = self.run_download('https://www.instagram.com/p/ABC/', product=product)
        self.assertEqual(result['status'], 'error')
        self.assertIn('Saved 1 of 2 items', result['error'])
        self.assertEqual(Path(result['folder']), manager.destination / 'Instagram' / 'A day by the sea')
        self.assertEqual([item['name'] for item in result['files']], ['A day by the sea - 02.png'])
        self.assertEqual(manager.file(result['id'], result['files'][0]['name']).read_bytes(), self.photo)

    def test_inaccessible_video_does_not_save_its_cover_as_a_photo(self):
        item = self.item(photo=False)
        item['video_versions'] = []
        _, result = self.run_download('https://www.instagram.com/p/ABC/', product=item)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['files'], [])

    def test_instagram_story_options_use_only_the_explicitly_selected_session(self):
        url = 'https://www.instagram.com/stories/creator/123/'
        public = worker.download_options(Path('/tmp/story'), '', lambda event: None, url)
        self.assertNotIn('cookiesfrombrowser', public)
        signed_in = worker.download_options(Path('/tmp/story'), 'chrome', lambda event: None, url)
        self.assertEqual(signed_in['cookiesfrombrowser'], ('chrome',))
        self.assertTrue(signed_in['noplaylist'])


if __name__ == '__main__':
    unittest.main()
