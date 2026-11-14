"""Soniox asynchronous ASR. Keep native token clocks and durable paid job IDs."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import requests

from engine import audio_temp_dir, endpoint
from media import check_cancel, extract_flac
from subtitles import Cue, atomic_write
from subtitle_jobs import _lock, redact_error

POLL_INTERVAL = 2
POLL_TIMEOUT = 7200


class ApiError(RuntimeError):
    def __init__(self, message, status=0):
        self.status = status
        super().__init__(message)


def api_url(base):
    base = base.strip().rstrip("/")
    endpoint(base, "models")  # Validate before attaching any credential.
    return base if base.endswith("/v1") else base + "/v1"


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise RuntimeError("Soniox 返回的任务 / 文件编号无效；已停止自动提交，请检查控制台。")
    return value


class Multipart:
    """Known content length, streaming from disk instead of buffering hours of audio."""
    def __init__(self, stream, cancel):
        self.boundary = "jp-ass-" + uuid.uuid4().hex
        prefix = (f'--{self.boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.flac"\r\n'
                  'Content-Type: audio/flac\r\n\r\n').encode("ascii")
        suffix = f"\r\n--{self.boundary}--\r\n".encode("ascii")
        self.parts = [io.BytesIO(prefix), stream, io.BytesIO(suffix)]
        self.index = 0
        self.cancel = cancel
        self.length = len(prefix) + Path(stream.name).stat().st_size + len(suffix)

    def __len__(self):
        return self.length

    def read(self, size=65536):
        check_cancel(self.cancel)
        if size is None or size < 0:
            size = 65536
        data = bytearray()
        while len(data) < size and self.index < len(self.parts):
            block = self.parts[self.index].read(size - len(data))
            if not block:
                self.index += 1
            data.extend(block)
        return bytes(data)


class Client:
    def __init__(self, settings):
        self.base = api_url(settings.soniox_url)
        self.key = settings.soniox_key.strip()
        if not self.key:
            raise ValueError("请在“Soniox”填写 Soniox API Key")

    def request(self, method, route, *, body=None, upload=None):
        headers = {"Authorization": "Bearer " + self.key}
        if upload is not None:
            headers["Content-Type"] = "multipart/form-data; boundary=" + upload.boundary
        try:
            response = requests.request(method, self.base + "/" + route, headers=headers,
                                        json=body, data=upload, timeout=(15, 180 if upload else 60),
                                        allow_redirects=False)
        except requests.RequestException:
            raise ApiError("Soniox 连接失败或超时；已有任务编号会保留，重新生成相同输出可继续等待。") from None
        with response:
            if method == "DELETE" and response.status_code in (200, 204, 404):
                return {}
            if response.status_code not in (200, 201):
                hint = {400: "请求参数无效，请核对模型与参数；Soniox 上下文最多 8000 tokens，完整歌词超限会被拒绝，程序没有截断或自动重试",
                        401: "Key 无效，请检查 Key 和项目区域对应的 Base URL",
                        402: "余额不足，请在 Soniox 控制台充值后继续",
                        403: "无权访问，请检查项目权限与区域",
                        404: "文件、任务或模型不存在，请检查控制台",
                        413: "音频文件超过服务大小限制", 429: "配额不足或请求过快，请稍后继续"}
                raise ApiError(f"Soniox HTTP {response.status_code}：{hint.get(response.status_code, '请求失败，请检查服务状态和参数')}。", response.status_code)
            try:
                data = response.json()
            except ValueError:
                raise ApiError("Soniox 没有返回有效 JSON。") from None
            if not isinstance(data, dict):
                raise ApiError("Soniox 返回格式无效。")
            return data


def check_connection(settings):
    """Read-only authentication check, does not create a billed transcription."""
    result = Client(settings).request("GET", "models")
    models = result.get("models", [])
    names = [row.get("id", row.get("name", "")) for row in models if isinstance(row, dict)]
    if names and settings.soniox_model not in names:
        raise ValueError("连接成功，但当前项目未列出所填 Soniox 模型，请核对模型名称。")
    return "Soniox 连接成功（仅查询模型，未提交收费识别）"


def parse_tokens(payload, duration, *, report=None, natural_sentences=True, continuation_pause=1.2):
    """Keep all text, even tokens for which Soniox supplied only a time point.

    Positive native cue spans are never retimed. Point-only fragments join a
    nearby adjacent turn by the same speaker, or receive a short, flagged display
    interval. The original token clocks remain untouched in the checkpoint.
    """
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Soniox 记录中的音频时长无效")
    tokens = payload.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError("Soniox 结果缺少 tokens 时间戳")
    groups, group = [], []
    zero_tokens = zero_text_tokens = 0
    continued_pauses = []
    from sentence_context import allowed_pause
    from sentence_fragments import can_attach
    from japanese_sentences import open_quote, pause_boundary
    allowed_pause("", continuation_pause)  # Validate before parsing.

    def flush():
        if not group:
            return
        text = "".join(row["text"] for row in group).strip()
        if text:
            groups.append(list(group))
        group.clear()

    for index, row in enumerate(tokens):
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ValueError("Soniox token 文字格式无效")
        text = row["text"]
        if text in {"<end>", "<fin>"} or row.get("translation_status") == "translation":
            continue
        start, end = row.get("start_ms"), row.get("end_ms")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end < start or end > duration * 1000 + 250):
            raise ValueError("Soniox token 时间戳无效或超出音频范围")
        start, end = min(start / 1000, duration), min(end / 1000, duration)
        if start == end:
            zero_tokens += 1
            zero_text_tokens += bool(text.strip())
        speaker = row.get("speaker")
        speaker_known = speaker is not None and str(speaker) != ""
        if speaker is None:
            speaker = group[-1]["speaker"] if group else groups[-1][-1]["speaker"] if groups else ""
        speaker = str(speaker)
        # Untimed punctuation stays with the preceding text, without extending it.
        if start == end and text.strip() and all(c in "、。，．！？!?…」』）)]\"'" for c in text.strip()):
            if group and speaker == group[-1]["speaker"]:
                group.append({"start": start, "end": end, "text": text, "speaker": speaker, "index": index, "speaker_known": speaker_known})
                if text.rstrip().endswith(tuple("。！？!?")) and not open_quote(''.join(w['text'] for w in group)):
                    flush()
                continue
            elif groups and speaker == groups[-1][0]["speaker"] and abs(start - groups[-1][-1]["end"]) <= .45:
                previous = groups[-1][-1]
                groups[-1].append({"start": previous["end"], "end": previous["end"], "text": text, "speaker": speaker, "index": index, "speaker_known": speaker_known})
                continue
        if not text.strip() and not group:
            continue
        if group:
            gap = start - group[-1]['end']
            same_known = speaker_known and group[-1].get('speaker_known') and speaker == group[-1]['speaker']
            split = speaker != group[-1]['speaker']
            punctuation_only = text.strip() and all(c in '、。，．！？!?…」』）)]\"\'' for c in text.strip())
            if gap > .45 and not punctuation_only:
                if natural_sentences and same_known:
                    preview = ''
                    for following in tokens[index:]:
                        if not isinstance(following, dict) or not isinstance(following.get('text'), str):
                            break
                        if following.get('speaker') not in (None, speaker):
                            break
                        preview += following['text']
                        if len(preview) >= 32 or following['text'].rstrip().endswith(tuple('。！？!?')):
                            break
                    split |= pause_boundary(''.join(w['text'] for w in group), preview)
                else:
                    split = True
            if split:
                flush()
        if group and start - group[-1]["end"] > .45:
            continued_pauses.append({"speaker": speaker, "pause_start": group[-1]["end"],
                                     "pause_end": start, "token_index": index,
                                     "long_pause": start - group[-1]["end"] > continuation_pause,
                                     "before": "".join(w["text"] for w in group)})
        group.append({"start": start, "end": end, "text": text, "speaker": speaker, "index": index, "speaker_known": speaker_known})
        if text.rstrip().endswith(tuple("。！？!?")) and not open_quote(''.join(w['text'] for w in group)):
            flush()
    flush()

    def bounds(rows):
        from text_cleanup import has_words
        audible = [row for row in rows if row['text'].strip()] or rows
        spoken = [row for row in audible if has_words(row['text'])]
        if spoken:
            last_word = max(row['end'] for row in spoken)
            audible = [row for row in audible if row['start'] <= last_word + .45]
        return min(r["start"] for r in audible), max(r["end"] for r in audible)

    # Resolve punctuation-delimited point fragments before creating positive cues.
    merged = []
    for index, rows in enumerate(groups):
        start, end = bounds(rows)
        if start == end:
            if merged and merged[-1][0]["speaker"] == rows[0]["speaker"] and 0 <= start - bounds(merged[-1])[1] <= .45:
                merged[-1].extend(rows)
                continue
            if index + 1 < len(groups):
                following = groups[index + 1]
                if following[0]["speaker"] == rows[0]["speaker"] and 0 <= bounds(following)[0] - end <= .45:
                    groups[index + 1] = rows + following
                    continue
        merged.append(rows)

    # The provider may tokenize Japanese one character at a time. Repair small
    # endings split by a length cap or a held sung note, using original clocks.
    attached, suffix_repairs = [], []
    for rows in merged:
        if attached:
            previous = attached[-1]
            def fragment(value):
                start, end = bounds(value)
                return {'start': start, 'end': end, 'text': ''.join(r['text'] for r in value).strip(),
                        'speaker': value[0]['speaker'] if all(r['speaker_known'] for r in value if r['text'].strip()) else ''}
            left, right = fragment(previous), fragment(rows)
            if can_attach(left, right):
                previous.extend(rows)
                suffix_repairs.append({'start': left['start'], 'end': max(left['end'], right['end']),
                                       'before': [left['text'], right['text']],
                                       'after': ''.join(r['text'] for r in previous).strip(),
                                       'token_indices': [r['index'] for r in previous]})
                continue
        attached.append(rows)
    from punctuation_segments import split_rows
    from utterance_segments import split_rows as split_utterances
    sentence_count, clause_splits, merged = len(attached), [], []
    natural_splits = []
    for rows in attached:
        parts = split_rows(rows)
        if natural_sentences:
            expanded = []
            for part in parts:
                units, reasons = split_utterances(part)
                expanded.extend(units)
                natural_splits.extend(reasons)
            parts = expanded
        merged.extend(parts)
        if len(parts) > 1:
            clause_splits.append({'before': ''.join(r['text'] for r in rows).strip(),
                                 'parts': [{'text': ''.join(r['text'] for r in part).strip(),
                                            'start': bounds(part)[0],
                                            'end': bounds(part)[1],
                                            'token_indices': [r['index'] for r in part]} for part in parts]})
    parsed = []
    from text_cleanup import clean_start, has_words
    dropped_punctuation = 0
    for index, rows in enumerate(merged):
        text = clean_start(''.join(r['text'] for r in rows))
        if not has_words(text):
            dropped_punctuation += 1
            continue
        start, end = bounds(rows)
        point = start if end == start else None
        if point is not None:
            # This is a display estimate, not a newly inferred speech boundary.
            start = min(start, max(0, duration - .3))
            end = min(duration, start + .3)
            if index + 1 < len(merged):
                next_start = bounds(merged[index + 1])[0]
                if next_start > start:
                    end = min(end, next_start)
        cue = Cue(start, end, text, speaker=rows[0]["speaker"])
        parsed.append((cue, point, [r["index"] for r in rows]))
    parsed.sort(key=lambda row: (row[0].start, row[0].end))
    if report is not None:
        review = [{"cue_id": index + 1, "start": cue.start, "end": cue.end, "text": cue.text,
                   "speaker": cue.speaker, "native_point": point, "token_indices": indices,
                   "reason": "整段仅有同一个时间点，显示时长为暂估值，请人工复核"}
                  for index, (cue, point, indices) in enumerate(parsed) if point is not None]
        report.update(schema=1, token_count=len(tokens), zero_duration_tokens=zero_tokens,
                      zero_duration_text_tokens=zero_text_tokens, output_cues=len(parsed),
                      review_cues=len(review), review=review, natural_sentences=natural_sentences,
                      continuation_pause=continuation_pause, continued_pauses=continued_pauses,
                      continued_pause_count=len(continued_pauses), punctuation_only_removed=dropped_punctuation,
                      suffix_repairs=suffix_repairs, suffix_repair_count=len(suffix_repairs),
                      length_limits=None, boundary_method='japanese_grammar_spacing_pauses_and_punctuation',
                      natural_split_count=len(natural_splits), natural_splits=natural_splits,
                      source_sentence_count=sentence_count, clause_split_count=len(clause_splits),
                      clause_splits=clause_splits,
                      sentences=[{'start': cue.start, 'end': cue.end, 'text': cue.text, 'speaker': cue.speaker,
                                  'token_indices': indices,
                                  'speaker_known': all(tokens[i].get('speaker') not in (None, '') for i in indices
                                                       if tokens[i]['text'].strip())}
                                 for cue, _, indices in parsed])
    return [cue for cue, _, _ in parsed]


def parsed_result(state, target, log=lambda _: None, *, settings=None):
    report = {}
    cues = parse_tokens(state["transcript"], state["duration"], report=report,
                        natural_sentences=getattr(settings, "natural_sentences", True),
                        continuation_pause=getattr(settings, "continuation_pause", 1.2))
    if settings is not None and settings.lyric_stage:
        from lyrics import apply_to_tokens
        aligned, lyric_report = apply_to_tokens(state['transcript'], state['duration'], settings)
        if aligned is not None:
            from text_cleanup import clean_cues
            cues = clean_cues(aligned)
            lyric_path = Path(target).with_suffix('.lyrics-timing.json')
            atomic_write(lyric_path, json.dumps(lyric_report, ensure_ascii=False, indent=2))
            report['lyrics'] = {'stage': lyric_report['stage'], 'matched_lines': lyric_report['matched_lines'], 'report': lyric_path.name}
            # Old cue IDs no longer identify the new lyric segmentation. Keep
            # native point warnings by their exact position in the new list.
            for item in report['review']:
                item['cue_id'] = next((i + 1 for i, cue in enumerate(cues)
                    if cue.start <= item['native_point'] <= cue.end), None)
            report['output_cues'] = len(cues)
            log(f'歌词辅助：{lyric_report["stage"]} · 已录入歌词 {lyric_report["songs_with_lyrics"]} 首 · 匹配 {lyric_report["matched_lines"]} 行；报告：{lyric_path}')
            if lyric_report['sentence_join_count']:
                log(f'歌词按完整句子归并 {lyric_report["sentence_join_count"]} 处，整句日语统一校对和翻译。')
    report_path = Path(target).with_suffix(".soniox-timing.json")
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    if report["zero_duration_tokens"]:
        log(f"Soniox 返回 {report['zero_duration_text_tokens']} 个零时长文字 token，已保留并合并；{report['review_cues']} 条整段缺少时长，暂设短显示时间，需复核：{report_path}")
    if report["continued_pause_count"]:
        log(f"完整句子断句：保留 {report['continued_pause_count']} 处句中停顿，按词性和句意判断结束，词级时间戳保留。")
    if report['clause_split_count']:
        log(f"标点分段：{report['clause_split_count']} 处长句按逗号等标点划分显示语段，逐段使用原生时间戳并翻译。")
    if report['suffix_repair_count']:
        log(f"断词修复：{report['suffix_repair_count']} 处日语词尾已接回前句，将按完整日语校对和翻译。")
    return cues, report


def import_result(source, target, settings):
    """Recover a completed JSON into text-processing jobs, without any network."""
    from subtitle_jobs import create_job, save_job
    from subtitles import make_ass
    source, target = Path(source), Path(target)
    if target.suffix.lower() != ".ass" or source.resolve() == target.resolve():
        raise ValueError("请选择独立的 ASS 输出文件")
    if source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Soniox 记录过大，无法导入")
    state = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(state, dict) or state.get("schema") != 1 or state.get("status") != "completed":
        raise ValueError("请选择包含 transcript 和 tokens 的已完成 Soniox 记录")
    cues, report = parsed_result(state, target, settings=settings)
    if not cues:
        raise ValueError("Soniox 记录中没有可导入的字幕")
    raw = target.with_name(target.stem + ".raw.ass")
    if raw.exists():
        raw = target.with_name(target.stem + f".raw-{uuid.uuid4().hex[:8]}.ass")
    from member_colors import export_palette
    atomic_write(raw, make_ass(cues, settings.font, settings.font_size, coloring=export_palette(settings)))
    video = Path(state["source"]) if isinstance(state.get("source"), str) and state["source"] else None
    path, document = create_job(video, target, cues, settings, raw)
    document.update(source_soniox_record=str(source.resolve()),
                    soniox_timing_report=target.with_suffix(".soniox-timing.json").name,
                    timing_review_cues=report["review_cues"], state="incomplete")
    save_job(path, document)
    return path, document


def _signature(source, settings, base, *, legacy_context=''):
    stat = source.stat()
    value = [str(source.resolve()), stat.st_size, stat.st_mtime_ns, settings.audio_index,
             base, settings.soniox_model, settings.soniox_diarize, legacy_context]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()


def transcribe(source, target, settings, cancel, progress, log):
    from lyrics import selected_stage
    selected_stage(settings)  # Reject an invalid library before any paid call.
    from sentence_context import allowed_pause
    allowed_pause("", settings.continuation_pause)
    client = Client(settings)
    if not settings.soniox_model.strip():
        raise ValueError("请填写 Soniox 模型名称")
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    path = target.with_suffix(".soniox.json")
    signature = _signature(source, settings, client.base)
    with _lock(path):
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8-sig"))
            old_signature = _signature(source, settings, client.base,
                                       legacy_context=getattr(settings, '_legacy_context_for_cache', ''))
            if state.get("schema") != 1 or state.get("signature") not in {signature, old_signature}:
                raise ValueError("此输出的 Soniox 记录属于另一音频或识别设置，请选择新的 ASS 输出文件名。")
        else:
            state = {"schema": 1, "signature": signature, "reference": "jp-ass-" + uuid.uuid4().hex,
                     "status": "new", "source": str(source.resolve()), "model": settings.soniox_model,
                     "file_id": "", "job_id": ""}

        def save():
            atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False))

        def cleanup():
            for field, route in (("job_id", "transcriptions"), ("file_id", "files")):
                if not state.get(field) or state.get(field + "_deleted"):
                    continue
                try:
                    client.request("DELETE", route + "/" + valid_id(state[field]))
                    state[field + "_deleted"] = True
                    save()
                except Exception as exc:
                    log("Soniox 本次云端资源清理未完成，可在控制台删除：" + redact_error(exc, settings))

        if state.get("status") == "completed":
            cues, report = parsed_result(state, target, log, settings=settings)
            log(f"复用已完成的 Soniox 结果，不再提交收费识别：{path}")
            log('识别上下文沿用原任务；当前词库与歌词提示仅对新识别任务生效。')
            cleanup()
            return cues
        if state.get("status") in {"creating", "create_unknown"} and not state.get("job_id"):
            raise RuntimeError(f"上次收费任务提交结果不明，为避免重复收费已停止。请在 Soniox 控制台按 reference 核查；记录：{path}")
        if state.get("status") == "error":
            cleanup()
            raise RuntimeError(f"Soniox 任务失败：{state.get('error', '')}。请检查控制台；新识别请选择新的输出文件名。")
        check_cancel(cancel)
        if not state.get('job_id'):
            from soniox_context import build_context, describe
            context, summary = build_context(settings)
            state['recognition_context'] = context
            state['recognition_context_summary'] = summary
            save()
            log(describe(summary))
        else:
            log('继续已有 Soniox 任务，识别上下文沿用提交时的内容。')
        if not state.get("file_id"):
            temp_root = audio_temp_dir(settings) or target.parent
            with tempfile.TemporaryDirectory(prefix="jp-ass-soniox-", dir=temp_root) as temp:
                audio = Path(temp) / "audio.flac"
                log("正在提取所选音轨为 FLAC，保留视频时间偏移……")
                state["duration"] = extract_flac(source, audio, settings.audio_index, cancel, lambda p: progress(.02 + p * .13))
                check_cancel(cancel)
                log(f"上传 {state['duration']:.1f} 秒音频到 Soniox（付费识别）……")
                with audio.open("rb") as stream:
                    result = client.request("POST", "files", upload=Multipart(stream, cancel))
                state["file_id"] = valid_id(result.get("id"))
                state["status"] = "uploaded"
                save()  # Save before noticing cancellation after a POST.
        check_cancel(cancel)
        if not state.get("job_id"):
            body = {"model": settings.soniox_model, "file_id": valid_id(state["file_id"]),
                    "language_hints": ["ja"], "enable_language_identification": True,
                    "enable_speaker_diarization": settings.soniox_diarize,
                    "client_reference_id": state["reference"]}
            if state.get('recognition_context'):
                body['context'] = state['recognition_context']
            state["status"] = "creating"
            save()
            try:
                result = client.request("POST", "transcriptions", body=body)
                state["job_id"] = valid_id(result.get("id"))
            except Exception as exc:
                # A lost response may still have created a billed job. Never repeat POST.
                state["status"] = "uploaded" if isinstance(exc, ApiError) and 400 <= exc.status < 500 else "create_unknown"
                state["error"] = redact_error(exc, settings)
                save()
                raise
            state["status"] = "processing"
            save()
        route = "transcriptions/" + valid_id(state["job_id"])
        log(f"等待 Soniox 识别，任务编号：{state['job_id']}；记录：{path}")
        deadline = time.monotonic() + POLL_TIMEOUT
        while True:
            check_cancel(cancel)
            result = client.request("GET", route)
            status = result.get("status")
            if status == "completed":
                break
            if status == "error":
                message = redact_error(result.get("error_message", "识别失败"), settings)
                if "balance" in message.lower() or "insufficient" in message.lower():
                    message = "组织余额不足，请在 Soniox 控制台充值后重新生成。" + message
                state.update(status="error", error=message)
                save()
                cleanup()
                raise RuntimeError("Soniox 识别失败：" + state["error"])
            if status not in {"queued", "processing"}:
                raise RuntimeError("Soniox 任务状态无法识别；记录已保留，请稍后继续。")
            progress(.45)
            if time.monotonic() >= deadline:
                raise RuntimeError("等待 Soniox 超时，任务编号已保留；相同输出文件重新生成可继续等待。")
            cancel.wait(POLL_INTERVAL)
        check_cancel(cancel)
        transcript = client.request("GET", route + "/transcript")
        # Save raw native tokens before parsing or deleting cloud resources.
        state.update(status="completed", transcript=transcript)
        save()
        cues, report = parsed_result(state, target, log, settings=settings)
        state["cues"] = [asdict(cue) for cue in cues]
        save()
        cleanup()
        check_cancel(cancel)
        log(f"Soniox 完成：{len(cues)} 条字幕，按说话人、停顿和标点断句；使用原生词级时间戳与完整句子断句。")
        progress(.78)
        return cues
