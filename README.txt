Titulky: Czech subtitles from audio or video
============================================

Start: double-click Titulky.exe, or drop files onto it. Nothing needs to be
installed: everything Titulky needs is inside this folder.

1. Press "Add files..." and pick audio (mp3, wav, m4a, ...) or video (mkv, mp4, avi, ...).
   An .srt file is only translated, e.g. after you corrected the English one.
2. Choose the options and press "Create subtitles".
3. Next to each file you get:
     name.en.srt  original language (the code matches the detected language)
     name.cs.srt  Czech
   If the subtitle file has the same name as the video, players (VLC, MPC-HC,
   PotPlayer, ...) load it automatically. Otherwise add it in the player's
   Subtitles menu.

Subtitles inside the video (for TVs)
   Many TVs and set-top boxes ignore .srt files. Tick "Also put the subtitles
   into the video" and you also get
     name.titulky.mkv  the same video with the subtitle tracks built in
   The copy keeps the format of the original: an MKV gives an MKV, an MP4 an
   MP4 (also MOV and WebM). AVI, TS and other formats can't hold subtitles, so
   they become MKV. Czech is the default track, the original language is the
   second one. Every picture, sound and subtitle track of the original is kept
   as it is and nothing is re-encoded, so this takes about as long as copying
   the file (it needs as much free disk space as the video). The original file
   is never changed. Choose the subtitle track in the TV's subtitle/options
   menu if they don't show up on their own.

   The buttons under "Subtitles inside the video" work on videos that already
   have subtitles. You can add the video or its .srt files to the list:
   - Put the .srt files into the video: after correcting name.cs.srt (e.g. in
     Notepad or Subtitle Edit) this puts all name.*.srt files into
     name.titulky.mkv again, replacing the old Titulky tracks. No transcribing.
   - Remove Titulky's subtitles: takes out only the tracks Titulky added.
   - Remove all subtitles: takes out every subtitle track, e.g. unwanted ones
     that came with the video.
   The result is always name.titulky.mkv (or .mp4, ...); if that file is
   already there, it is updated.

On a Mac
- Open Titulky-mac-arm64.dmg (Macs with an Apple M1, M2, M3, ... chip) or
  Titulky-mac-x86_64.dmg (older Macs with an Intel processor; see Apple menu >
  About This Mac). Drag Titulky into Applications, or start it right from the
  disk image. Nothing else has to be installed or downloaded: the Whisper
  models are inside the app.
- Needs macOS 14 (Sonoma) or newer.
- First start: if macOS says Titulky can't be opened or can't be checked for
  malicious software, open System Settings > Privacy & Security, scroll down
  and click "Open Anyway". This is needed once, because Titulky is not signed
  with a paid Apple developer certificate. (Copied from a USB stick, it
  usually opens right away.)
- Drop files on the Titulky icon in the Dock, or use "Add files...".
- A Mac has no NVIDIA graphics card, so Titulky transcribes on the processor.
  That's why the Mac app comes with the "turbo" model: it is several times
  faster than large-v3 and nearly as accurate.
- Settings and titulky.log are in your Library: in Finder press
  Cmd+Shift+G and type ~/Library/Application Support/Titulky

Moving Titulky to another PC
- Copy the WHOLE Titulky folder (about 5 GB). Titulky.exe does not work on its
  own: it needs the _internal and models folders next to it. To put it on the
  desktop, right-click Titulky.exe > Send to > Desktop (create shortcut)
  instead of dragging the exe there.
- If you move it as a .zip, unpack it first (right-click > Extract all).
  Starting Titulky.exe from inside the zip does not work.
- Needs 64-bit Windows 10 or 11. Nothing else has to be installed.
- "Windows protected your PC": click "More info" > "Run anyway". Titulky is
  not signed, so Windows asks the first time.
- Windows 11 "Smart App Control" blocks all unsigned programs. If it blocks
  Titulky, turn it off in Windows Security > App & browser control.
- Without an NVIDIA graphics card it still works, just slower (see Notes).
- If something goes wrong, titulky.log next to Titulky.exe says what happened.
  (If the folder is read-only, the log and settings go to
  %LOCALAPPDATA%\Titulky instead.)

Notes
- Uses an NVIDIA graphics card if there is one (fast); older or smaller cards
  are used with a lighter setting. Without one it runs on the CPU, which is
  much slower: choose the "turbo" model then.
- Transcription runs offline. Translation needs internet (Google or DeepL).
- DeepL gives better Czech. A free API key is at https://www.deepl.com/pro-api
  (500,000 characters a month, roughly a dozen episodes).
- Subtitles early or late? Shift them in the player (VLC: keys G and H).
- The "turbo" model is downloaded on first use (about 1.6 GB) into the models folder.
