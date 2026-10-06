# Social Video Downloader

Download Instagram photos, Reels, video posts and stories, TikTok videos and photo slideshows, and YouTube videos through a small local browser app. Paste a link, follow the progress, and open the saved files in Finder.

The app runs at `http://127.0.0.1:8765` and saves files inside `~/Documents/Video-Download/`, with `TikTok`, `Instagram`, and `YouTube` subfolders. Videos and individual photos go directly into their platform folder. Each TikTok slideshow gets a separate folder named from its caption, with its photos numbered in order. History is preserved between launches.

```text
Documents/Video-Download/
  TikTok/
    My video.mp4
    My photo.jpg
    A weekend away/
      A weekend away - 01.jpg
      A weekend away - 02.jpg
  Instagram/
    A day by the sea.jpg
    My Reel.mp4
  YouTube/
    Me at the zoo.mp4
```

## Requirements

- Python 3.10 or newer.
- macOS for the double-click launchers. The Python server can also run from a terminal on Linux or Windows.
- FFmpeg is optional for merging separate video and audio streams, and required to run the video integration test.
- For YouTube, install [Deno](https://docs.deno.com/runtime/getting_started/installation/) (2.3.0+) or [Node.js](https://nodejs.org/en/download/) (22+). The app enables both runtimes, preferring Deno. Its `yt-dlp[default]` dependency supplies the matching JavaScript challenge scripts; see [yt-dlp's YouTube setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS). FFmpeg is strongly recommended because YouTube often serves video and audio separately.

On macOS with Homebrew, `brew install deno ffmpeg` supplies the recommended YouTube tools. Restart the app after installing them.

## Start on macOS

Double-click **Start Video Downloader.command** in Finder. On the first launch, it creates a virtual environment and installs the Python dependencies. It then opens the app in your default browser.

Keep the Terminal window open while using the app. Press **Control-C** there to stop it. Running the launcher again opens the existing app.

## Start from a terminal

From this repository's folder on macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

On Windows:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

## Download a video, photo, slideshow, or story

1. Paste an Instagram photo, Reel, video-post or story link, a TikTok video/photo link, or a YouTube video link.
2. Leave **Session** set to **Public link** and click **Download**.
3. When the download finishes, click **Show in Finder** or **Save a copy**.

Supported links:

- Instagram `/reel/`, `/reels/`, `/p/`, and `/tv/` links, including photos and mixed photo/video carousels.
- Instagram `/stories/<username>/<story-id>/` links for one specific photo or video story. `/stories/<username>/` saves the currently available stories, and `/stories/highlights/<id>/` saves a highlight collection.
- TikTok full video URLs such as `tiktok.com/@creator/video/<id>`.
- TikTok photo slideshow URLs such as `tiktok.com/@creator/photo/<id>`.
- TikTok short links from `tiktok.com/t/`, `vm.tiktok.com`, and `vt.tiktok.com`.
- YouTube watch links: `youtube.com/watch?v=<video-id>`, including `www`, mobile (`m.youtube.com`), and YouTube Music links.
- YouTube short share links: `youtu.be/<video-id>`.
- YouTube Shorts (`/shorts/<video-id>`), embeds (`/embed/<video-id>`, including `youtube-nocookie.com`), legacy `/v/<video-id>` links, and recorded streams (`/live/<video-id>`).

The platform is detected automatically. Links can be pasted with or without `https://`. YouTube links are normalized to one watch URL: tracking parameters, timestamps, and playlist context are removed, so the app saves the selected video in full. A watch link containing `list=` downloads only that video, not the playlist.

TikTok slideshows save every available photo as an individual image in the original order, using names such as `TikTok/A weekend away/A weekend away - 01.jpg`. Full photo URLs and short share links work. A single-photo TikTok post saves directly in `TikTok/`. Images retain their original available quality and format (JPEG, PNG, or WebP); the app does not turn slideshows into videos or download their background music. If a slide fails, completed photos remain available and the app reports the incomplete download. Duplicate slideshow titles get separate folders such as `A weekend away (2)`.

Instagram carousels save their photos and videos in order, directly in `Instagram/`. Photo downloads select the largest available image. For stories and private posts, select a browser where you are signed into an account that can view the content. Expired or removed stories cannot be recovered. If one carousel item fails, other completed items remain available and the app reports the incomplete download.

Profile and channel pages, YouTube playlist-only and clip links, active live streams, and upcoming streams are not supported. For a YouTube clip, open the original video and copy its video link.

If the platform requires a login, open the video in a browser where you are signed into that platform. Then select that browser under **Session** and retry. Browser-session support uses yt-dlp's [local cookie extraction](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp). The app reads cookies locally only when you select a browser; it does not collect passwords or export a cookie file. macOS may ask for Keychain access, and browser permissions can affect session access.

## Settings

Choose another save directory or port:

```sh
.venv/bin/python app.py --destination "$HOME/Downloads/Social Videos" --port 8766
```

The selected directory is the parent of the three platform folders. Titles are capped at 60 characters, shortened at a word boundary when possible, and cleaned of hashtags, links, and filename-unsafe characters. If no useful title or caption is available, the app uses the creator's name or `Untitled video`. Very long Unicode names are also limited by byte length. Duplicate names get a suffix such as ` (2)`; existing files are never overwritten.

History stores each download's actual folder, including slideshow subfolders. Files from older save locations remain accessible until moved with their history updated. **Save a copy** and **Show in Finder** work for photos and videos.

Add `--no-browser` to start without opening a tab.

The downloader prefers a complete MP4 containing both video and audio. When no complete MP4 is available, it can merge separate streams if FFmpeg is installed. Files retain the source codec, and the app does not upscale or re-encode them.

YouTube attempts have a 30-minute limit to accommodate longer videos; Instagram and TikTok attempts have a three-minute limit. Login requirements, removed posts, and rate limits appear as errors with a retry action. Missing YouTube runtimes and unavailable formats include setup or update guidance.

## Updates

Double-click **Update Downloader.command** on macOS, or run:

```sh
.venv/bin/python -m pip install --upgrade -r requirements.txt
```

Restart the app after updating. The download engine is [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Tests

With the dependencies and FFmpeg installed:

```sh
.venv/bin/python -m unittest -v test_app.py test_worker.py test_instagram.py
```

Tests cover link validation, the Documents default, platform and slideshow folders, filename/folder collisions, legacy history, error handling, file isolation, local request protections, YouTube runtime and live-stream handling, and real local media transfers. Instagram fixtures cover single photos, mixed carousels, specific story selection, story collections, expired stories, and incomplete downloads. Slideshow checks cover page metadata, original image bytes, ordering, mirror fallback, misleading file extensions, partial transfers, HTML responses, and image serving. Tests do not require platform accounts or fetch live social posts.

## Local data

The server binds only to `127.0.0.1`. Downloads go directly from the platform's servers to your computer. The app does not use an external download service.

History lives in `.state/history.json`. Virtual environments, history, credentials files, caches, and downloaded media are excluded from Git. Source code, static assets, launchers, documentation, and tests are the repository contents.
