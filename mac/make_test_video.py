"""Make a small test video from a sound file, for the Mac build's smoke test.

    python mac/make_test_video.py speech.aiff test.mp4
"""
import sys

import av
import numpy as np

src, dst = sys.argv[1:3]
with av.open(src) as inp, av.open(dst, "w") as out:
    seconds = inp.duration / av.time_base + 1
    video = out.add_stream("mpeg4", rate=25)
    video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
    audio = out.add_stream("aac", rate=48000)
    audio.layout = "stereo"
    packets = []
    for i in range(int(seconds * 25)):
        frame = av.VideoFrame.from_ndarray(np.full((180, 320, 3), 60, np.uint8), format="rgb24")
        frame.pts = i
        packets += video.encode(frame)
    packets += video.encode(None)
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000, frame_size=1024)
    for frame in inp.decode(audio=0):
        for f in resampler.resample(frame):
            packets += audio.encode(f)
    for f in resampler.resample(None):
        packets += audio.encode(f)
    packets += audio.encode(None)
    for p in sorted(packets, key=lambda p: (p.dts or 0) * p.time_base):
        out.mux(p)
