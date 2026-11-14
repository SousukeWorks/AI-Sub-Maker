"""Stream one picture plus a selected audio track into an H.264/AAC MP4."""
from __future__ import annotations

import os
import tempfile
from fractions import Fraction
from pathlib import Path

from media import audio_info, check_cancel


def export_image_video(source, image, target, audio_index=0, *, cancel, progress=lambda _: None):
    import av
    import numpy as np

    source, image, target = Path(source), Path(image), Path(target)
    if not source.is_file() or not image.is_file():
        raise ValueError("请先选择存在的媒体文件和画面图片")
    if target.suffix.lower() != ".mp4" or target.resolve() in {source.resolve(), image.resolve()}:
        raise ValueError("请选择独立的 .mp4 输出文件")
    tracks, expected = audio_info(source)
    if not 0 <= audio_index < tracks:
        raise ValueError(f"媒体只有 {tracks} 条音轨，请检查音轨序号")
    check_cancel(cancel)
    with av.open(str(image)) as picture:
        try:
            frame = next(picture.decode(video=0))
        except (StopIteration, IndexError):
            raise ValueError("所选图片无法读取") from None
        scale = min(1920 / frame.width, 1080 / frame.height)
        width, height = max(1, round(frame.width * scale)), max(1, round(frame.height * scale))
        pixels = frame.reformat(width=width, height=height, format="rgb24").to_ndarray()
    canvas = np.zeros((1080, 1920, 3), dtype=np.uint8)
    x, y = (1920 - width) // 2, (1080 - height) // 2
    canvas[y:y + height, x:x + width] = pixels
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".picture-video-", suffix=".mp4", dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    written = 0
    try:
        with av.open(str(source)) as input_file, av.open(str(temporary), "w", format="mp4", options={"movflags": "+faststart"}) as output:
            original = input_file.streams.audio[audio_index]
            rate = original.codec_context.sample_rate or 48000
            if rate not in (32000, 44100, 48000):
                rate = 48000
            layout = "mono" if len(original.codec_context.layout.channels) == 1 else "stereo"
            origin = (input_file.start_time or 0) / av.time_base
            audio = output.add_stream("aac", rate=rate)
            audio.layout = layout
            audio.bit_rate = 192000
            video = output.add_stream("libx264", rate=1)
            video.width, video.height, video.pix_fmt = 1920, 1080, "yuv420p"
            video.codec_context.time_base = Fraction(1, 1000)
            video.codec_context.max_b_frames = 0
            video.options = {"preset": "veryfast", "tune": "stillimage,zerolatency", "crf": "20"}
            resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
            next_picture = 0
            pending_picture = None

            def picture_packet(packet):
                nonlocal pending_picture
                if pending_picture is not None:
                    pending_picture.duration = max(1, round(packet.pts * packet.time_base / pending_picture.time_base) - pending_picture.pts)
                    output.mux(pending_picture)
                pending_picture = packet

            def write_audio(samples):
                nonlocal written, next_picture
                check_cancel(cancel)
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples), format="fltp", layout=layout)
                frame.sample_rate, frame.time_base, frame.pts = rate, Fraction(1, rate), written
                written += frame.samples
                while next_picture / 1000 < written / rate:
                    check_cancel(cancel)
                    still = av.VideoFrame.from_ndarray(canvas, format="rgb24")
                    still.pts, still.time_base = next_picture, Fraction(1, 1000)
                    for packet in video.encode(still):
                        picture_packet(packet)
                    next_picture += 1000
                for packet in audio.encode(frame):
                    output.mux(packet)
                progress(min(.99, written / rate / expected) if expected else 0)

            def consume(frame):
                samples = frame.to_ndarray()
                position = round((float(frame.pts * frame.time_base) - origin) * rate) if frame.pts is not None else written
                while position > written:
                    count = min(rate, position - written)
                    write_audio(np.zeros((samples.shape[0], count), dtype=np.float32))
                trim = max(0, written - position)
                if trim < samples.shape[1]:
                    write_audio(samples[:, trim:])

            for frame in input_file.decode(original):
                check_cancel(cancel)
                for normalized in resampler.resample(frame):
                    consume(normalized)
            for normalized in resampler.resample(None):
                consume(normalized)
            if not written:
                raise ValueError("媒体音轨为空，未生成视频")
            for packet in audio.encode(None):
                output.mux(packet)
            for packet in video.encode(None):
                picture_packet(packet)
            if pending_picture is not None:
                pending_picture.duration = max(1, round(Fraction(written, rate) / pending_picture.time_base) - pending_picture.pts)
                output.mux(pending_picture)
        check_cancel(cancel)
        os.replace(temporary, target)
        progress(1)
        return written / rate
    finally:
        temporary.unlink(missing_ok=True)
