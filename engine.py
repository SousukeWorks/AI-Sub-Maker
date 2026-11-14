"""Soniox-only Japanese ASR and single-request bilingual subtitle processing."""
from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from media import audio_info, check_cancel
from subtitles import atomic_write, make_ass


def data_dir() -> Path:
    override = os.environ.get('AI_SUB_MAKER_DATA_DIR') or os.environ.get('JP_ASS_DATA_DIR')
    if override:
        return Path(override)
    base = Path(os.environ.get('LOCALAPPDATA') or str(Path.home()))
    preferred, legacy = base / 'AI-Sub-Maker', base / 'JpAssMaker'
    # Existing installations keep their saved settings and Windows-encrypted keys.
    return legacy if legacy.is_dir() and not preferred.exists() else preferred


@dataclass
class Settings:
    temp_folder: str = ""
    audio_index: int = 0
    soniox_url: str = "https://api.soniox.com/v1"
    soniox_key: str = ""
    soniox_model: str = "stt-async-v5"
    soniox_diarize: bool = True
    deepseek: bool = False
    translate: bool = True
    deepseek_url: str = "https://api.deepseek.com"
    deepseek_key: str = ""
    deepseek_model: str = "deepseek-flash"
    font: str = "Yu Gothic"
    font_size: int = 48
    natural_sentences: bool = True
    continuation_pause: float = 1.2
    lyrics_library: str = ""
    lyric_stage: str = ""
    member_library: str = ""
    use_member_glossary: bool = True
    use_member_colors: bool = True
    chorus_font_color: str = '#FF69B4'
    chorus_outline_color: str = '#FFFFFF'
    chorus_shadow_color: str = '#000000'


def audio_temp_dir(settings: Settings) -> Path | None:
    value = settings.temp_folder.strip()
    if not value:
        return None  # The caller uses the ASS output folder, not the system disk.
    folder = Path(value).expanduser()
    if not folder.is_absolute():
        raise ValueError("临时音频目录请填写完整的绝对路径，或点击浏览选择文件夹")
    if folder.exists() and not folder.is_dir():
        raise ValueError("临时音频目录是文件，请重新选择文件夹")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise RuntimeError(f"无法创建临时音频目录：{folder}。请检查空间和写入权限。") from None
    return folder


def endpoint(base: str, route: str) -> str:
    base = base.strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API 地址应为 http(s) 地址，且不能包含账户、查询参数或片段")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 API 请使用 HTTPS；本机服务可以使用 HTTP")
    return base if base.endswith("/" + route) else base + "/" + route


def post_json(url, key, cancel, *, body=None, read_timeout=90):
    import requests

    check_cancel(cancel)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        response = requests.post(url, headers=headers, json=body, timeout=(15, read_timeout), allow_redirects=False)
    except requests.RequestException as exc:
        # Never put the request object / authorization / key in logs.
        raise RuntimeError("API 连接失败或等待超时，请检查地址、网络和代理设置") from None
    check_cancel(cancel)
    with response:
        if response.status_code != 200:
            hints = {401: "API Key 无效", 402: "账户余额不足", 403: "没有访问权限", 404: "接口路径或模型不存在", 413: "请求内容超过服务大小限制", 429: "请求过快或配额不足"}
            raise RuntimeError(f"API 返回 HTTP {response.status_code}：{hints.get(response.status_code, '请检查服务状态、模型及参数')}。")
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError("API 没有返回有效 JSON") from None
    if not isinstance(result, dict):
        raise RuntimeError("API 返回格式错误，应为 JSON 对象")
    return result


def run(source: Path, target: Path, settings: Settings, cancel=None, progress=None, log=None, on_job=None) -> int:
    cancel = cancel or threading.Event()
    progress = progress or (lambda value: None)
    log = log or (lambda message: None)
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise ValueError("请选择存在的 MP4 / MP3 媒体文件")
    if source.resolve() == target.resolve() or target.suffix.lower() != ".ass":
        raise ValueError("输出必须是独立的 .ass 字幕文件")
    if (settings.deepseek or settings.translate) and not settings.deepseek_key.strip():
        raise ValueError("生成双语字幕或校正日语需要 DeepSeek API Key，请到“翻译”填写；仅需日语时可关闭双语翻译和校正。")
    from lyrics import selected_stage
    selected_stage(settings)
    from member_terms import member_reference
    member_reference(settings)
    from member_colors import export_palette
    coloring = export_palette(settings)
    audio_info(source)
    check_cancel(cancel)
    from soniox_asr import transcribe
    cues = transcribe(source, target, settings, cancel, lambda value: progress(value * .8), log)
    from text_cleanup import clean_cues
    cues = clean_cues(cues)
    if not cues:
        raise ValueError("没有检测到有效日语字幕，请检查所选音轨。")
    if settings.deepseek or settings.translate:
        # Durable recovery copy before sending any text to a remote model.
        raw = target.with_name(target.stem + ".raw.ass")
        if raw.exists():
            descriptor, name = tempfile.mkstemp(prefix=target.stem + ".raw-", suffix=".ass", dir=target.parent)
            os.close(descriptor)
            raw = Path(name)
        atomic_write(raw, make_ass(cues, settings.font, settings.font_size, coloring=coloring))
        log(f"原始字幕已保存：{raw}")
        from subtitle_jobs import ProcessingIncomplete, create_job, process_job
        path, _ = create_job(source, target, cues, settings, raw)
        log(f"处理记录：{path}")
        if on_job:
            on_job(path)
        result = process_job(path, settings, cancel=cancel, progress=lambda value: progress(0.8 + value * 0.2), log=log, on_job=on_job)
        if result.remaining:
            raise ProcessingIncomplete(result)
        progress(1)
        log(f"完成：{len(cues)} 条字幕 → {target}")
        return len(cues)
    check_cancel(cancel)
    from subtitle_jobs import create_job, export_job, save_job
    path, document = create_job(source, target, cues, settings)
    document['state'] = 'completed'
    result = export_job(path, document)
    document['last_output'] = result.output.name
    save_job(path, document)
    if on_job:
        on_job(path)
    progress(1)
    kind = "日中双语字幕" if settings.translate else "日语字幕"
    log(f"完成：{len(cues)} 条{kind} → {target}")
    return len(cues)
