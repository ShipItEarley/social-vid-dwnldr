# Video Downloader

A small local browser app for downloading social videos. Paste an Instagram Reel/video-post link or TikTok video/share link, download it, and reveal the video in Finder. Files go to `~/Downloads/Instagram/<download-id>/`; history stays in the app's ignored `.state/` folder.

Visual thesis: a quiet off-white work surface, dark type, and one green action color, with the link field as the main focus.

Content plan: compact app header; paste-link form and optional browser-session selector; save destination; recent downloads with progress, errors, retry, and file actions.

Interaction thesis: a short entrance fade, clear button/focus transitions, and smooth progress changes. Reduced-motion preferences disable animation.

The Python standard library serves the app on loopback only. A serial queue runs yt-dlp in a subprocess with a time limit; no passwords are collected. Browser-session use is opt-in for each download. Download history is persisted atomically. Each job has its own folder, and the server only serves verified video files from that folder. Requests require a local host and mutations require a session token. Instagram Reel, post, and legacy TV links are supported, along with TikTok full video URLs and short share links. Profile, story, live, and TikTok photo URLs are rejected with guidance.

Verification: test link validation, job lifecycle and restart recovery, error handling, file isolation, HTTP API protections, a real yt-dlp transfer from a local fixture, and the browser interface. Live platform downloads can be checked separately; login and rate-limit failures are reported as errors.
