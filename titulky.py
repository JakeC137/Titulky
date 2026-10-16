"""Transcribe audio with Whisper (locally, on the GPU) and produce Czech .srt subtitles.

Command line:
    titulky.bat episode01.mp3 [more files...] [--lang en] [--model large-v3]
    titulky.bat episode01.mkv --embed       # also make episode01.titulky.mkv with the subtitles inside
    titulky.bat episode01.en.srt            # only translate existing subtitles
    titulky.bat episode01.mkv --only-embed  # put the (corrected) .srt files next to it into the video
    titulky.bat episode01.titulky.mkv --remove-subtitles titulky   # or: all

The windowed app (app.py, packaged as Titulky.exe) uses the same functions.
For each audio/video file it writes <name>.<lang>.srt (original language) and <name>.cs.srt.
Translation uses DeepL when a key is given (or DEEPL_API_KEY is set), otherwise Google Translate.
"""
import argparse
import json
import mimetypes
import os
import re
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from fractions import Fraction
from itertools import chain
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)  # running as Titulky.exe / Titulky.app
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))  # files packed into the app (models on a Mac)
HF_MIRROR = "https://hf-mirror.com"  # used when huggingface.co is blocked
MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]

MAX_CHARS = 84       # per cue, i.e. two lines of ~42
MAX_LINE = 42
MAX_DURATION = 7.0   # seconds a single cue may stay on screen
MIN_DURATION = 1.0
CHUNK_CHARS = 4000   # text sent to the translator per request
PUNCT = (".", "?", "!", ",", ";", ":", "…")

OUT_TAG = ".titulky"  # ep01.mkv -> ep01.titulky.mkv, the copy with the subtitles inside
TRACK_NAME = "Titulky"  # name of the subtitle tracks this app adds, so it can find them again
# Formats that can hold text subtitles: container and subtitle codec. Other videos are saved as .mkv.
CONTAINERS = {".mkv": ("matroska", "subrip"), ".mp4": ("mp4", "mov_text"),
              ".m4v": ("mp4", "mov_text"), ".mov": ("mov", "mov_text"), ".webm": ("webm", "webvtt")}
VIDEO_EXTS = (*CONTAINERS, ".avi", ".ts", ".m2ts", ".mts", ".wmv", ".asf", ".flv", ".mpg", ".mpeg",
              ".vob", ".3gp", ".ogv", ".divx")

# Whisper's language codes (ISO 639-1) -> ISO 639-2/B, the form video files and TVs expect.
MKV_LANGS = dict(pair.split(":") for pair in """
    af:afr am:amh ar:ara as:asm az:aze ba:bak be:bel bg:bul bn:ben bo:tib br:bre bs:bos
    ca:cat cs:cze cy:wel da:dan de:ger el:gre en:eng es:spa et:est eu:baq fa:per fi:fin
    fo:fao fr:fre gl:glg gu:guj ha:hau haw:haw he:heb hi:hin hr:hrv ht:hat hu:hun hy:arm
    id:ind is:ice it:ita ja:jpn jw:jav ka:geo kk:kaz km:khm kn:kan ko:kor la:lat lb:ltz
    ln:lin lo:lao lt:lit lv:lav mg:mlg mi:mao mk:mac ml:mal mn:mon mr:mar ms:may mt:mlt
    my:bur ne:nep nl:dut nn:nno no:nor oc:oci pa:pan pl:pol ps:pus pt:por ro:rum ru:rus
    sa:san sd:snd si:sin sk:slo sl:slv sn:sna so:som sq:alb sr:srp su:sun sv:swe sw:swa
    ta:tam te:tel tg:tgk th:tha tk:tuk tl:tgl tr:tur tt:tat uk:ukr ur:urd uz:uzb vi:vie
    yi:yid yo:yor zh:chi yue:chi""".split())
# QuickTime's (.mov) own language list spells these the other ISO 639-2 way.
MOV_LANGS = {"cze": "ces", "fre": "fra", "slo": "slk", "rum": "ron"}


def writable(folder):
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def find_data_dir():
    """Settings, the log and downloaded models go next to the app, or to the user's own folder when
    that is read-only (Program Files, a write-protected USB stick, a network share). A Mac app keeps
    them in Application Support always: changing anything inside Titulky.app breaks its signature."""
    home = (Path.home() / "Library" / "Application Support" if IS_MAC
            else Path(os.environ.get("LOCALAPPDATA") or Path.home())) / "Titulky"
    candidates = (home,) if IS_MAC and FROZEN else (APP_DIR, home)
    return next((d for d in candidates if writable(d)), APP_DIR)


DATA_DIR = find_data_dir()
MODEL_DIRS = list(dict.fromkeys(d / "models" for d in (BUNDLE_DIR, APP_DIR, DATA_DIR)))


@dataclass
class Cue:
    start: float
    end: float
    text: str


class Cancelled(Exception):
    pass


class Reporter:
    """Progress output for the console; the GUI subclasses this."""

    def __init__(self):
        self._inline = False

    def log(self, msg):
        if self._inline:
            print()
            self._inline = False
        print(msg)

    def progress(self, label, fraction):
        print(f"\r  {label}... {fraction:.0%}", end="", flush=True)
        self._inline = True

    def check_cancel(self):
        pass


def init_runtime():
    """Make the bundled cuBLAS/cuDNN DLLs visible to CTranslate2 on Windows."""
    if os.name != "nt":
        return
    site = Path(getattr(sys, "_MEIPASS", Path(sys.prefix) / "Lib" / "site-packages"))
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        d = site / "nvidia" / sub / "bin"
        if d.is_dir():
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]


# --- transcription -----------------------------------------------------------

def bundled_models():
    """Names of the Whisper models that came with the app or were downloaded before."""
    return sorted({m.parent.name for d in MODEL_DIRS for m in d.glob("*/model.bin")})


def find_model(name, report):
    """Return a local model folder: bundled with the app, in the HF cache, or freshly downloaded."""
    from faster_whisper import download_model
    from faster_whisper.utils import _MODELS
    from huggingface_hub import snapshot_download

    for d in MODEL_DIRS:
        if (d / name / "model.bin").is_file():
            return str(d / name)
    try:
        return download_model(name, local_files_only=True)
    except Exception:
        pass

    local = DATA_DIR / "models" / name
    report.log(f"Model '{name}' didn't come with Titulky, downloading it "
               "(~3 GB for large-v3, ~1.6 GB for turbo)...")
    for endpoint in (None, HF_MIRROR):
        try:
            snapshot_download(_MODELS.get(name, name), local_dir=local,
                              allow_patterns=MODEL_FILES, endpoint=endpoint)
            return str(local)
        except Exception as e:
            report.log(f"  download from {endpoint or 'huggingface.co'} failed: {e}")
    raise RuntimeError("Could not download the Whisper model, check the internet connection.")


def load_model(name, device, report):
    import ctranslate2
    import numpy as np
    from faster_whisper import WhisperModel

    path = find_model(name, report)
    report.log(f"Loading Whisper model '{name}'...")
    if device == "cuda" or (device == "auto" and not IS_MAC):  # Macs have no NVIDIA GPU
        problem = "no NVIDIA graphics card or driver found"
        if ctranslate2.get_cuda_device_count():
            supported = ctranslate2.get_supported_compute_types("cuda")
            # float16 is fastest; int8 runs on older cards and needs half the video memory.
            for compute_type in [t for t in ("float16", "int8_float16", "int8") if t in supported]:
                try:
                    model = WhisperModel(path, device="cuda", compute_type=compute_type)
                    # CUDA libraries load lazily, so run one second of silence to surface errors now.
                    segments, _ = model.transcribe(np.zeros(16000, np.float32), language="en")
                    list(segments)
                    report.log(f"  using the NVIDIA GPU ({compute_type})")
                    return model
                except Exception as e:
                    model = None  # free the video memory before trying the next type
                    problem = str(e)
        if device == "cuda":
            raise RuntimeError(f"can't use the NVIDIA GPU: {problem}")
        report.log(f"  can't use the GPU ({problem})\n"
                   "  using the CPU instead: much slower, the 'turbo' model is recommended")
    # int8 is about twice as fast as float32 on the CPU, where the processor supports it.
    compute_type = "int8" if "int8" in ctranslate2.get_supported_compute_types("cpu") else "float32"
    if IS_MAC:
        report.log(f"  using the processor ({compute_type})")
    return WhisperModel(path, device="cpu", compute_type=compute_type,
                        cpu_threads=max(4, (os.cpu_count() or 8) // 2))


def load_audio(path):
    """Decode any audio/video file to 16 kHz mono float32, the format Whisper expects.

    Done here because faster-whisper's own decoder breaks with current PyAV versions.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    parts = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            parts.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
    parts.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    if not parts:
        raise RuntimeError("no audio found in the file")
    return np.concatenate(parts).astype(np.float32) / 32768.0


def make_cue(words):
    return Cue(words[0].start, words[-1].end, "".join(w.word for w in words).strip())


def split_segment(seg):
    """Split a long Whisper segment into subtitle-sized cues at word boundaries."""
    text = seg.text.strip()
    if not text:
        return []
    if not seg.words or (len(text) <= MAX_CHARS and seg.end - seg.start <= MAX_DURATION):
        return [Cue(seg.start, seg.end, text)]

    cues, buf = [], []
    for w in seg.words:
        too_long = len("".join(x.word for x in buf + [w]).strip()) > MAX_CHARS
        if buf and (too_long or w.end - buf[0].start > MAX_DURATION):
            # Prefer breaking after punctuation in the second half of the buffer.
            cut = len(buf)
            for i in range(len(buf) - 1, len(buf) // 2 - 1, -1):
                if buf[i].word.strip().endswith(PUNCT):
                    cut = i + 1
                    break
            cues.append(make_cue(buf[:cut]))
            buf = buf[cut:]
        buf.append(w)
    if buf:
        cues.append(make_cue(buf))
    return cues


def fix_timing(cues):
    """Keep very short cues on screen long enough to read, without overlapping the next one."""
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else float("inf")
        if c.end - c.start < MIN_DURATION:
            c.end = max(c.end, min(c.start + MIN_DURATION, nxt - 0.05))
        c.end = min(c.end, nxt)


def transcribe(model, path, lang, report):
    report.log("  reading audio...")
    audio = load_audio(path)
    segments, info = model.transcribe(
        audio,
        language=lang,
        beam_size=5,
        vad_filter=True,                    # skip music/silence, fewer hallucinations
        word_timestamps=True,
        condition_on_previous_text=False,   # avoids repetition loops on long audio
    )
    report.log(f"  language: {info.language} ({info.language_probability:.0%}), "
               f"length {info.duration / 60:.1f} min")
    cues = []
    for seg in segments:
        report.check_cancel()
        cues.extend(split_segment(seg))
        report.progress("transcribing", min(seg.end / info.duration, 1))
    report.log(f"  transcribed {len(cues)} subtitles")
    fix_timing(cues)
    return cues, info.language


# --- translation -------------------------------------------------------------

def make_translator(kind, deepl_key=None):
    """Return (name, translate_one, translate_many) for the chosen service."""
    deepl_key = deepl_key or os.environ.get("DEEPL_API_KEY")
    if kind == "auto":
        kind = "deepl" if deepl_key else "google"
    if kind == "deepl":
        import deepl
        if not deepl_key:
            raise RuntimeError("no DeepL API key (get a free one at https://www.deepl.com/pro-api)")
        client = deepl.DeepLClient(deepl_key)
        return (kind,
                lambda text: client.translate_text(text, target_lang="CS").text,
                lambda texts: [r.text for r in client.translate_text(texts, target_lang="CS")])
    return kind, google_translate, lambda texts: [google_translate(t) for t in texts]


def google_translate(text):
    """Free Google Translate endpoint (no key). Keeps line breaks, so whole chunks can be sent."""
    import ssl
    from urllib.error import HTTPError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    import certifi

    # Python on a Mac has no trusted certificates of its own, so add the bundled ones.
    context = ssl.create_default_context()
    context.load_verify_locations(certifi.where())
    time.sleep(0.3)  # stay well under Google's rate limit
    req = Request("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=cs&dt=t",
                  data=urlencode({"q": text}).encode(), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30, context=context) as resp:
            data = json.load(resp)
    except HTTPError as e:
        if e.code == 429:
            raise RuntimeError("Google Translate is refusing requests from this internet connection "
                               "for now (too many requests). Try again later, or use DeepL.") from e
        raise
    return "".join(part[0] for part in data[0] if part[0])


def chunks(texts):
    chunk, size = [], 0
    for t in texts:
        if chunk and size + len(t) + 1 > CHUNK_CHARS:
            yield chunk
            chunk, size = [], 0
        chunk.append(t)
        size += len(t) + 1
    if chunk:
        yield chunk


def translate(cues, kind, report, deepl_key=None):
    name, one, many = make_translator(kind, deepl_key)
    texts = [" ".join(c.text.split()) for c in cues]
    out = []
    # Translate many lines per request so the service sees surrounding context.
    for chunk in chunks(texts):
        report.check_cancel()
        result = one("\n".join(chunk)).split("\n")
        if len(result) != len(chunk):
            result = many(chunk)  # line count drifted, translate cue by cue instead
        for src, r in zip(chunk, result):
            r = r.strip()
            # Lines are translated as separate sentences; undo the capital on a sentence continuation.
            if src[:1].islower() and r[:1].isupper():
                r = r[0].lower() + r[1:]
            out.append(r)
        report.progress(f"translating ({name})", len(out) / len(texts))
    report.log(f"  translated with {name}")
    return [Cue(c.start, c.end, t) for c, t in zip(cues, out)]


# --- SRT files ---------------------------------------------------------------

def ts(sec):
    ms = round(sec * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def parse_ts(s):
    h, m, rest = s.strip().split()[0].split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def wrap(text):
    """Break text into at most two lines, splitting at the space nearest the middle."""
    text = " ".join(text.split())
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if len(text) <= MAX_LINE or not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - len(text) // 2))
    return text[:cut] + "\n" + text[cut + 1:]


def write_srt(cues, path, report):
    # utf-8 with BOM so Windows players detect Czech characters correctly
    with open(path, "w", encoding="utf-8-sig") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{ts(c.start)} --> {ts(c.end)}\n{wrap(c.text)}\n\n")
    report.log(f"  saved {path.name}")


def read_srt(path):
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.strip().splitlines()
        for j, line in enumerate(lines):
            if "-->" in line:
                start, end = (parse_ts(x) for x in line.split("-->"))
                cues.append(Cue(start, end, " ".join(lines[j + 1:]).strip()))
                break
    return cues


# --- subtitles inside the video ------------------------------------------------

def parse_srt_name(path):
    """ep01.cs.srt -> ("ep01", "cze"); ep01.srt -> ("ep01", "und")."""
    stem = Path(path.stem)
    code = stem.suffix[1:].lower()
    lang = MKV_LANGS.get(code) or (code if code in MKV_LANGS.values() else None)
    return (stem.stem, lang) if lang else (path.stem, "und")


def video_base(path):
    """ep01.mkv and ep01.titulky.mkv -> ep01"""
    stem = path.stem
    return stem[:-len(OUT_TAG)] if stem.lower().endswith(OUT_TAG) else stem


def output_path(video):
    """ep01.mp4 -> ep01.titulky.mp4, and a .titulky file is updated in place.

    Formats that can't hold text subtitles (avi, ts, wmv, ...) become .mkv.
    """
    ext = video.suffix.lower()
    return video.with_name(video_base(video) + OUT_TAG + (ext if ext in CONTAINERS else ".mkv"))


def video_for(srt):
    """The video an .srt file belongs to, preferring the .titulky copy; None if there is none."""
    base, _ = parse_srt_name(srt)
    names = [base + OUT_TAG + ext for ext in CONTAINERS] + [base + ext for ext in VIDEO_EXTS]
    return next((srt.parent / n for n in names if (srt.parent / n).is_file()), None)


def videos_for(paths):
    """For the actions that work on videos: swap .srt files for their video, without repeats."""
    return list(dict.fromkeys(video_for(p) or p if p.suffix.lower() == ".srt" else p for p in paths))


def srt_files(video):
    """The subtitles next to `video` (ep01.cs.srt, ep01.en.srt, ...) as (file, language), Czech first."""
    base = video_base(video).lower()
    found = []
    for f in sorted(video.parent.iterdir()):
        if f.suffix.lower() == ".srt" and parse_srt_name(f)[0].lower() == base:
            if "-->" in f.read_text(encoding="utf-8-sig", errors="replace"):  # skip empty ones
                found.append((f, parse_srt_name(f)[1]))
    return sorted(found, key=lambda t: t[1] != "cze")


def is_ours(stream):
    """True for subtitle tracks this app added (MKV keeps the name as title, MP4 as handler name)."""
    return TRACK_NAME in (stream.metadata.get(k) for k in ("title", "name", "handler_name"))


def has_video(container):
    from av.stream import Disposition
    # A cover picture shows up as a one-frame video track, but it doesn't count.
    return any(not s.disposition & Disposition.attached_pic for s in container.streams.video)


def embed(video, report):
    """Put the .srt files next to `video` into name.titulky.<ext>, replacing ones added before."""
    import av

    if video.suffix.lower() == ".srt":
        raise RuntimeError("no video with the same name next to it")
    with av.open(str(video)) as c:
        if not has_video(c):
            report.log("  the file has no video, so there is nothing to put subtitles into")
            return None
    tracks = srt_files(video)
    if not tracks:
        raise RuntimeError(f"no subtitles next to the video, expected e.g. {video_base(video)}.cs.srt")
    report.log(f"  putting {', '.join(p.name for p, _ in tracks)} into the video...")
    out = remux(video, report, tracks, drop=is_ours)
    report.log(f"  saved {out.name}")
    return out


def remove_subtitles(video, report, only_ours=True):
    """Save name.titulky.<ext> without the subtitles this app added, or without any subtitles."""
    import av

    if video.suffix.lower() == ".srt":
        raise RuntimeError("no video with the same name next to it")
    with av.open(str(video)) as c:
        found = [s for s in c.streams.subtitles if is_ours(s) or not only_ours]
        if not has_video(c):
            report.log("  the file has no video, nothing to do")
            return None
    if not found:
        report.log("  no subtitles from Titulky in this file" if only_ours
                   else "  no subtitles in this file")
        return None
    report.log(f"  removing {len(found)} subtitle track{'s' * (len(found) > 1)}...")
    out = remux(video, report, drop=is_ours if only_ours else lambda s: True)
    report.log(f"  saved {out.name}")
    return out


def encode_srt(path, stream, offset):
    """Convert an .srt file to packets in `stream`'s subtitle format, `offset` seconds later.

    FFmpeg does the conversion, so italics etc. survive in MP4's and WebM's own formats too.
    Returns a list of (time in seconds, packet).
    """
    import av

    packets = []
    with av.open(str(path)) as srt:
        decoder = srt.streams.subtitles[0].codec_context
        decoder.open(strict=False)
        stream.codec_context.subtitle_header = decoder.subtitle_header
        for p in srt.demux():
            sub = decoder.decode2(p) if p.size else None
            if not sub:
                continue
            q = stream.codec_context.encode_subtitle(sub)
            q.pts = q.dts = p.pts + round(offset / p.time_base)
            q.duration, q.time_base, q.stream = p.duration, p.time_base, stream
            packets.append((q.pts * q.time_base, q))
    return packets


def remux(video, report, subtitles=(), drop=lambda s: False):
    """Copy `video` to output_path(video), dropping subtitle tracks where drop(track) is true and
    adding `subtitles`, a list of (srt file, language) whose first one becomes the default.

    Works like MKVToolNix: every other track is copied as it is and nothing is re-encoded, so it
    takes about as long as copying the file. Returns the new file.
    """
    import av
    from av.stream import Disposition

    out_path = output_path(video)
    fmt, sub_codec = CONTAINERS[out_path.suffix.lower()]
    if out_path.suffix.lower() != video.suffix.lower():
        report.log(f"  {video.suffix[1:].upper()} can't hold subtitles, saving as {out_path.suffix[1:].upper()}")
    part = out_path.with_name(out_path.name + ".part")  # renamed once complete
    try:
        with ExitStack() as stack:
            src = stack.enter_context(av.open(str(video)))
            dst = stack.enter_context(av.open(
                str(part), "w", format=fmt,
                # MP4 index at the front, so TVs and DLNA servers can start playing right away
                container_options={"movflags": "+faststart"} if fmt in ("mp4", "mov") else None))
            dst.metadata.update(src.metadata)

            # Start the new file at 0 like ffmpeg does; TV recordings (.ts) often start hours in.
            start = Fraction(src.start_time or 0, av.time_base)
            chapters = src.chapters()
            for c in chapters:
                c["start"] -= round(start / c["time_base"])
                c["end"] -= round(start / c["time_base"])
            dst.set_chapters(chapters)

            covers = {s.index: s for s in src.streams.video if s.disposition & Disposition.attached_pic}
            out = {}  # source stream index -> (output stream, timestamp shift)
            for s in src.streams:
                if s.type == "data" or s.index in covers or (s.type == "subtitle" and drop(s)):
                    continue  # data: timecode and similar tracks, which these formats can't hold
                try:
                    o = dst.add_stream_from_template(s, opaque=True)
                except ValueError:
                    name = s.codec_context.name if s.codec_context else s.type
                    report.log(f"  leaving out track {s.index} ({name} {s.type}), "
                               f"{out_path.suffix[1:].upper()} can't hold it")
                    continue
                o.metadata.update(s.metadata)
                # New subtitles become the default instead of any already in the file.
                o.disposition = (s.disposition & ~Disposition.default
                                 if s.type == "subtitle" and subtitles else s.disposition)
                out[s.index] = (o, round(start / s.time_base) if s.time_base else 0)

            # Whisper's times count from the first sample of the audio track it transcribed.
            audio = src.streams.audio[0] if src.streams.audio else None
            offset = (audio.start_time or 0) * audio.time_base - start if audio else 0
            pending = []  # (time in seconds, packet) of the new subtitles
            for i, (srt, lang) in enumerate(subtitles):
                o = dst.add_stream(sub_codec)
                o.metadata.update(language=MOV_LANGS.get(lang, lang) if fmt == "mov" else lang,
                                  title=TRACK_NAME)
                if fmt in ("mp4", "mov"):
                    o.metadata["handler_name"] = TRACK_NAME
                o.disposition = Disposition.default if i == 0 else 0
                pending += encode_srt(srt, o, offset)
            pending.sort(key=lambda x: x[0], reverse=True)  # earliest last, for pop()

            # FFmpeg hands out cover pictures before any other packet, so before the header is
            # written. MKV keeps them as attachments (like mkvmerge), MP4 as cover art.
            packets = src.demux()
            first, pictures = [], []
            for p in packets:
                if p.stream.index not in covers:
                    first = [p]
                    break
                s = covers[p.stream.index]
                if fmt == "matroska":
                    name = s.metadata.get("filename") or (
                        "cover.png" if s.codec_context.name == "png" else "cover.jpg")
                    dst.add_attachment(name, s.metadata.get("mimetype") or mimetypes.guess_type(name)[0],
                                       bytes(p))
                elif fmt == "mp4":
                    o = dst.add_stream_from_template(s, opaque=True)
                    o.disposition = Disposition.attached_pic
                    p.pts = p.dts = 0
                    p.stream = o
                    pictures.append(p)
                else:
                    report.log(f"  leaving out the cover picture, {out_path.suffix[1:].upper()} can't hold it")
            dst.mux(pictures)

            duration = src.duration / av.time_base if src.duration else 0
            clock = 0
            for n, p in enumerate(chain(first, packets)):
                o, shift = out.get(p.stream.index, (None, 0))
                if o is None or not p.size:  # size 0: end-of-stream marker
                    continue
                if p.pts is not None:
                    p.pts -= shift
                if p.dts is not None:
                    p.dts -= shift
                t = p.dts if p.dts is not None else p.pts
                if t is not None:
                    clock = t * p.time_base
                    # Add each subtitle when the video gets to it, keeping the file interleaved.
                    while pending and pending[-1][0] <= clock:
                        dst.mux(pending.pop()[1])
                p.stream = o
                dst.mux(p)
                if n % 500 == 0:
                    report.check_cancel()
                    if duration:
                        report.progress("copying the video", min(max(float(clock) / duration, 0), 1))
            for _, p in reversed(pending):
                dst.mux(p)
        part.replace(out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return out_path


# --- processing --------------------------------------------------------------

def process(path, report, cache, lang=None, model="large-v3", translator="auto",
            device="auto", deepl_key=None, embed_into_video=False):
    """Make subtitles for one file. `cache` keeps the loaded model between calls."""
    t0 = time.time()
    is_srt = path.suffix.lower() == ".srt"
    if is_srt:
        cues = read_srt(path)
        base, source_lang = parse_srt_name(path)
    else:
        if cache.get("key") != (model, device):
            cache.clear()
            cache.update(key=(model, device), model=load_model(model, device, report))
        cues, detected = transcribe(cache["model"], path, lang, report)
        base, source_lang = video_base(path), MKV_LANGS.get(detected, "und")
        write_srt(cues, path.with_name(f"{base}.{detected}.srt"), report)

    if translator == "none" or not cues:
        pass
    elif source_lang == "cze":
        report.log("  already in Czech, nothing to translate")
    else:
        write_srt(translate(cues, translator, report, deepl_key),
                  path.with_name(f"{base}.cs.srt"), report)

    if embed_into_video and cues:
        video = video_for(path) if is_srt else path
        if video:
            embed(video, report)
        else:
            report.log("  no video with the same name next to it, so the subtitles stay in .srt files")
    report.log(f"  finished in {(time.time() - t0) / 60:.1f} min")


def main(argv=None):
    p = argparse.ArgumentParser(description="Transcribe audio and make Czech .srt subtitles.")
    p.add_argument("files", nargs="+", type=Path,
                   help="audio/video files (mp3, wav, mkv, mp4, ...), or .srt files to only translate")
    p.add_argument("--lang", help="spoken language code, e.g. en (default: auto-detect)")
    p.add_argument("--model", default="large-v3",
                   help="Whisper model: large-v3 (most accurate), turbo (faster), medium, small")
    p.add_argument("--translator", choices=["auto", "deepl", "google", "none"], default="auto",
                   help="auto = DeepL if DEEPL_API_KEY is set, otherwise Google")
    p.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--embed", action="store_true",
                        help=f"also save each video as <name>{OUT_TAG}.<ext> with the subtitles "
                             "inside, for TVs and players that don't load .srt files")
    action.add_argument("--only-embed", action="store_true",
                        help="don't transcribe, put the .srt files that are next to each video "
                             "into it, e.g. after correcting them")
    action.add_argument("--remove-subtitles", choices=["titulky", "all"],
                        help="remove the subtitles this app put into the video, or all of them")
    args = p.parse_args(argv)

    init_runtime()
    report, cache, failed = Reporter(), {}, 0
    files = args.files
    if args.only_embed:
        files, job = videos_for(files), embed
    elif args.remove_subtitles:
        files = videos_for(files)
        job = lambda path, r: remove_subtitles(path, r, only_ours=args.remove_subtitles == "titulky")
    else:
        job = lambda path, r: process(path, r, cache, args.lang, args.model, args.translator,
                                      args.device, embed_into_video=args.embed)
    for path in files:
        report.log(f"\n== {path.name}")
        if not path.is_file():
            report.log("  file not found")
            failed += 1
            continue
        try:
            job(path, report)
        except Exception as e:
            report.log(f"  ERROR: {e}")
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
