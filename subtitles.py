"""Timed Japanese cues and safe ASS output. No network or GUI dependencies."""
from __future__ import annotations

import math
import os
import tempfile
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str
    translation: str = ""
    speaker: str = ""
    lyric: dict | None = None
    member_id: str = ''
    chorus: bool = False

    def __post_init__(self):
        if not all(math.isfinite(t) for t in (self.start, self.end)):
            raise ValueError("时间轴包含无效数字")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("字幕结束时间必须晚于开始时间")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("字幕文字不能为空")
        if not isinstance(self.translation, str):
            raise ValueError("翻译文字格式无效")
        if not isinstance(self.speaker, str):
            raise ValueError("说话人格式无效")
        if not isinstance(self.member_id, str) or len(self.member_id) > 100 or type(self.chorus) is not bool:
            raise ValueError('字幕成员或合唱标记无效')
        if self.member_id and self.chorus:
            raise ValueError('同一语段不能同时指定成员和合唱')


def centiseconds(seconds: float) -> int:
    return max(0, int((Decimal(str(seconds)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def ass_time(ticks: int) -> str:
    hours, rest = divmod(ticks, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, fraction = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}"


def ass_text(text: str) -> str:
    # Literal backslashes/braces must never become executable ASS override tags.
    text = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    return " ".join(text.split())


def make_ass(cues: list[Cue], font: str = "Yu Gothic", size: int = 48, *, coloring=None) -> str:
    if any(c in font for c in ",\r\n") or not font.strip():
        raise ValueError("字体名称无效")
    if not 12 <= size <= 120:
        raise ValueError("字号超出范围")
    from ass_colors import extra_styles
    style_rows, style_members, style_ids = extra_styles(coloring, font, size)
    extra = '\n'.join(style_rows)
    color_comment = '; JpAssColorStyles: ' + json.dumps(style_members, ensure_ascii=False, separators=(',', ':')) if style_members else ''
    header = f"""[Script Info]
Title: Japanese subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
{color_comment}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: japanese,{font},{size},&H00FFFFFF,&H000000FF,&H00181818,&H80000000,0,0,0,0,100,100,0,0,1,2.4,0.6,2,80,80,54,1
Style: chinese,Microsoft YaHei,{max(12, round(size * 0.85))},&H00FFFFFF,&H000000FF,&H00181818,&H80000000,0,0,0,0,100,100,0,0,1,2.4,0.6,2,80,80,54,1
{extra}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for cue in sorted(cues, key=lambda c: (c.start, c.end)):
        from text_cleanup import clean_start, has_words
        if not has_words(clean_start(cue.text)):
            continue
        start = centiseconds(cue.start)
        end = max(start + 1, centiseconds(cue.end))
        text = ass_text(clean_start(cue.text))
        chinese = ass_text(clean_start(cue.translation)) if cue.translation.strip() else ""
        japanese_margin = 0
        if chinese:
            japanese_margin = 54 + math.ceil(max(12, round(size * 0.85)) * 1.4) + 10
        speaker = "".join(c for c in cue.speaker if c not in ",\r\n{}\\" and c.isprintable())[:80]
        key = '__chorus__' if cue.chorus else cue.member_id
        japanese_style = style_ids.get((key, 'japanese'), 'japanese')
        chinese_style = style_ids.get((key, 'chinese'), 'chinese')
        if key == '__chorus__':
            speaker = '合唱'
        elif key and coloring and key in coloring.get('members', {}):
            speaker = ''.join(c for c in coloring['members'][key]['name'] if c not in ',\r\n{}\\' and c.isprintable())[:80]
        rows.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{japanese_style},{speaker},0,0,{japanese_margin},,{text}")
        if chinese:
            rows.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{chinese_style},{speaker},0,0,0,,{chinese}")
    return header + "\n".join(rows) + "\n"


def atomic_write(path: Path, content: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".subtitle-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="\n") as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)




