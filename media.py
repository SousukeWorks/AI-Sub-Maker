"""Decode MP4 to mono PCM with PyAV's bundled FFmpeg; preserve stream offsets."""
from __future__ import annotations

from pathlib import Path

RATE = 16000


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("任务已取消")


def pcm_blocks(path: Path, audio_index: int, cancel):
    import av

    with av.open(str(path)) as container:
        tracks = container.streams.audio
        if audio_index >= len(tracks):
            raise ValueError(f"媒体只有 {len(tracks)} 条音轨，无法选择第 {audio_index + 1} 条")
        stream = tracks[audio_index]
        # The container start is the player's time zero, not necessarily audio PTS zero.
        origin = (container.start_time or 0) / av.time_base
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        written = 0

        def convert(frame):
            nonlocal written
            data = frame.to_ndarray().tobytes()
            position = round((float(frame.pts * frame.time_base) - origin) * RATE) if frame.pts is not None else written
            if position > written:
                gap = position - written
                while gap:
                    check_cancel(cancel)
                    count = min(gap, RATE)
                    yield b"\0" * (count * 2)
                    written += count
                    gap -= count
            trim = max(0, written - position)
            data = data[trim * 2:]
            if data:
                written += len(data) // 2
                yield data

        for frame in container.decode(stream):
            check_cancel(cancel)
            for output in resampler.resample(frame):
                yield from convert(output)
        for output in resampler.resample(None):
            yield from convert(output)


def audio_info(path: Path) -> tuple[int, float]:
    import av

    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("所选媒体没有音轨")
        duration = float(container.duration / av.time_base) if container.duration else 0.0
        return len(container.streams.audio), duration








def extract_flac(source: Path, target: Path, audio_index: int, cancel, progress):
    """Lossless compressed upload, with the same video-relative PCM clock as WAV."""
    import av
    import numpy as np
    _, duration = audio_info(source)
    frames = 0
    with av.open(str(target), "w", format="flac") as output:
        stream = output.add_stream("flac", rate=RATE)
        stream.layout = "mono"
        for block in pcm_blocks(source, audio_index, cancel):
            samples = np.frombuffer(block, dtype=np.int16).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = RATE
            frame.pts = frames
            frames += samples.shape[1]
            if frames > 300 * 60 * RATE:
                raise ValueError("Soniox 单次最多处理 300 分钟音频，请先裁剪视频")
            for packet in stream.encode(frame):
                output.mux(packet)
            progress(min(.99, frames / RATE / duration) if duration else 0)
        for packet in stream.encode(None):
            output.mux(packet)
    if not frames:
        raise ValueError("音轨为空，无法解码语音")
    return frames / RATE
