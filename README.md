# Social Video Downloader

Download Instagram Reels, Instagram video posts, and TikTok videos through a small local browser app. Paste a link, follow the progress, and open the saved video in Finder.

The app runs at `http://127.0.0.1:8765` and saves videos to `~/Downloads/Instagram/` by default. Each download gets its own folder. History is preserved between launches.

## Requirements

- Python 3.10 or newer.
- macOS for the double-click launchers. The Python server can also run from a terminal on Linux or Windows.
- FFmpeg is optional for merging separate video and audio streams, and required to run the video integration test.

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

## Download a video

1. Paste an Instagram or TikTok video link.
2. Leave **Session** set to **Public link** and click **Download video**.
3. When the download finishes, click **Show in Finder** or **Save a copy**.

Supported links:

- Instagram `/reel/`, `/reels/`, `/p/`, and `/tv/` links.
- TikTok full video URLs such as `tiktok.com/@creator/video/<id>`.
- TikTok short links from `tiktok.com/t/`, `vm.tiktok.com`, and `vt.tiktok.com`.

The platform is detected automatically. Instagram posts with multiple videos save each available video. Profile pages, Instagram stories, TikTok photo posts, and live streams are not supported.

If the platform requires a login, open the video in a browser where you are signed into that platform. Then select that browser under **Session** and retry. Browser-session support uses yt-dlp's [local cookie extraction](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp). The app reads cookies locally only when you select a browser; it does not collect passwords or export a cookie file. macOS may ask for Keychain access, and browser permissions can affect session access.

## Settings

Choose another save directory or port:

```sh
.venv/bin/python app.py --destination "$HOME/Downloads/Social Videos" --port 8766
```

Add `--no-browser` to start without opening a tab.

The downloader prefers a complete MP4 containing both video and audio. When no complete MP4 is available, it can merge separate streams if FFmpeg is installed. Files retain the source codec, and the app does not upscale or re-encode them.

Each attempt has a three-minute limit. Login requirements, removed posts, and rate limits appear as errors with a retry action.

## Updates

Double-click **Update Downloader.command** on macOS, or run:

```sh
.venv/bin/python -m pip install --upgrade -r requirements.txt
```

Restart the app after updating. The download engine is [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Tests

With the dependencies and FFmpeg installed:

```sh
.venv/bin/python -m unittest -v test_app.py test_worker.py
```

Tests cover link validation, the download queue, history and recovery, error handling, file isolation, local request protections, MP4 format selection, and an actual download from a locally generated video fixture. Tests do not require an Instagram or TikTok account or fetch live social posts.

## Local data

The server binds only to `127.0.0.1`. Downloads go directly from the platform's servers to your computer. The app does not use an external download service.

History lives in `.state/history.json`. Virtual environments, history, credentials files, caches, and downloaded media are excluded from Git. Source code, static assets, launchers, documentation, and tests are the repository contents.
