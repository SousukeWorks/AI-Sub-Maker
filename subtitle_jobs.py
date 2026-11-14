"""Durable subtitle proofreading/translation checkpoints and selective retries."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from media import Cancelled, check_cancel
from subtitles import Cue, atomic_write, make_ass

STAGES = ("proofread", "translate")
LABELS = {"proofread": "校对", "translate": "翻译"}
RETRYABLE = {"pending", "failed"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_path(target: Path) -> Path:
    return Path(target).with_suffix(".retry.json")


def _stage(enabled: bool) -> dict:
    return {"status": "pending" if enabled else "skipped", "attempts": 0, "error": "", "history": []}


def unfinished(row: dict) -> bool:
    if row.get('suppressed'):
        return False
    return any(row[stage]["status"] in RETRYABLE for stage in STAGES)


@dataclass(frozen=True)
class JobResult:
    record: Path
    output: Path
    total: int
    remaining: int
    attempted: int = 0


class ProcessingIncomplete(RuntimeError):
    def __init__(self, result: JobResult):
        self.result = result
        super().__init__(f"有 {result.remaining} 条字幕的校对或翻译尚未完成。失败记录：{result.record}；部分字幕：{result.output}")


def _filename(value, suffix):
    if not isinstance(value, str) or not value or any(c in value for c in '/\\:\0') or value in {".", ".."} or not value.lower().endswith(suffix):
        raise ValueError("处理记录中的输出文件名无效")
    return value


def load_job(path: Path) -> dict:
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("处理记录过大，无法打开")
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise ValueError("无法识别此处理记录的格式")
    _filename(document.get("output_file"), ".ass")
    if document.get("last_output"):
        _filename(document["last_output"], ".ass")
    rows = document.get("cues")
    if not isinstance(rows, list) or not rows:
        raise ValueError("处理记录中没有字幕")
    options = document.get("options")
    if not isinstance(options, dict) or type(options.get("proofread")) is not bool or type(options.get("translate")) is not bool:
        raise ValueError("处理记录中的步骤设置无效")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 or row["id"] in seen:
            raise ValueError("处理记录中的字幕编号无效")
        seen.add(row["id"])
        if any(type(row.get(key)) not in {int, float} for key in ("start", "end")):
            raise ValueError("处理记录中的时间轴格式无效")
        Cue(row["start"], row["end"], row["original"])
        Cue(row["start"], row["end"], row["text"], row["translation"], row.get("speaker", ""),
            member_id=row.get('member_id', ''), chorus=row.get('chorus', False))
        for stage in STAGES:
            state = row.get(stage)
            if not isinstance(state, dict) or state.get("status") not in {*RETRYABLE, "success", "skipped"}:
                raise ValueError("处理记录中的步骤状态无效")
            if type(state.get("attempts")) is not int or state["attempts"] < 0 or not isinstance(state.get("error"), str) or not isinstance(state.get("history"), list):
                raise ValueError("处理记录中的重试历史无效")
            if options[stage] and state["status"] == "skipped":
                raise ValueError("处理记录中已启用的步骤被错误跳过")
            if not options[stage] and state["status"] != "skipped":
                raise ValueError("处理记录中未启用的步骤状态无效")
            if stage == "translate" and state["status"] == "success" and not row["translation"].strip():
                raise ValueError("处理记录中的成功翻译为空")
    # Reuse the exporter validation for the saved appearance settings.
    make_ass([], options.get("font", "Yu Gothic"), options.get("font_size", 48), coloring=options.get('coloring'))
    return document


def save_job(path: Path, document: dict):
    document["updated_at"] = now()
    atomic_write(Path(path), json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False))


def create_job(source: Path | None, target: Path, cues: list[Cue], settings, raw_file: Path | None = None) -> tuple[Path, dict]:
    target = Path(target).resolve()
    path = record_path(target)
    if path.exists():
        path = target.with_name(target.stem + f".retry-{uuid.uuid4().hex[:8]}.json")
    from text_cleanup import clean_cues
    from member_colors import export_palette, annotate_identities
    cues = clean_cues(cues)
    document = {
        "schema": 1, "job_id": uuid.uuid4().hex, "created_at": now(), "updated_at": now(),
        "state": "processing", "source_file": str(source.resolve()) if source else "", "output_file": target.name,
        "raw_file": raw_file.name if raw_file else "",
        # Credentials and remote endpoint addresses are deliberately absent from checkpoint files.
        "options": {"proofread": settings.deepseek, "translate": settings.translate, "font": settings.font,
                    "font_size": settings.font_size, 'coloring': export_palette(settings)},
        "cues": [{"id": index + 1, "start": cue.start, "end": cue.end, "original": cue.text,
                  "text": cue.text, "translation": "", "speaker": cue.speaker, "proofread": _stage(settings.deepseek),
                  "translate": _stage(settings.translate), **({'lyric': cue.lyric} if cue.lyric else {}),
                  **({'member_id': cue.member_id, 'chorus': cue.chorus, 'role_source': 'manual'} if cue.member_id or cue.chorus else {})}
                 for index, cue in enumerate(cues)],
    }
    if not document["cues"]:
        raise ValueError("没有字幕可以创建处理记录")
    annotate_identities(document, settings)
    save_job(path, document)
    return path, document


@contextmanager
def _lock(path: Path):
    """OS releases the lock on crash; the small lock file can safely remain."""
    with path.with_suffix(".lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("此处理记录正在另一个窗口中使用，请等待其完成后再重试。") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def redact_error(error, settings) -> str:
    text = str(error)
    for key in (settings.deepseek_key, settings.soniox_key):
        if key:
            text = text.replace(key, "[隐藏]")
    return text[:1200] or "请求失败，未返回有效结果"


def _decode_partial(content: str, batch: list[dict], tasks: dict) -> tuple[dict, dict]:
    from deepseek_response import decode_rows
    from text_cleanup import clean_start, has_words
    rows, issue = decode_rows(content)
    expected = {row["id"]: row for row in batch}
    texts, errors, seen = {}, {}, set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] not in expected:
            continue
        index = row["id"]
        if index in seen:
            for stage in tasks[index]:
                errors[index, stage] = "API 返回了重复的字幕编号"
                texts.pop((index, stage), None)
            continue
        seen.add(index)
        for stage in tasks[index]:
            text = row.get("japanese" if stage == "proofread" else "chinese")
            if isinstance(text, str):
                text = clean_start(text)
            if not isinstance(text, str) or not has_words(text) or len(text) > max(200, len(expected[index]["text"]) * 3):
                errors[index, stage] = f"API 返回的本条{LABELS[stage]}文字为空、格式错误或过长"
            else:
                texts[index, stage] = text.strip()
    for index in expected:
        for stage in tasks[index]:
            if (index, stage) not in texts and (index, stage) not in errors:
                reason = issue or ('输出未完整结束：' + str(getattr(content, 'metadata', {}).get('finish_reason'))
                                  if getattr(content, 'metadata', {}).get('finish_reason', 'stop') != 'stop' else '')
                errors[index, stage] = (reason + '；' if reason else '') + "API 未返回本条字幕的完整结果"
    return texts, errors


def export_job(path: Path, document: dict) -> JobResult:
    remaining = sum(unfinished(row) for row in document["cues"])
    target = Path(path).parent / _filename(document["output_file"], ".ass")
    if remaining:
        target = target.with_name(target.stem + ".partial.ass")
    cues = []
    for row in document["cues"]:
        if row.get('suppressed'):
            continue
        translation = row["translation"]
        if document["options"]["translate"] and row["translate"]["status"] != "success":
            translation = "待校对，尚未翻译" if row["proofread"]["status"] in RETRYABLE else "翻译未完成，待重试"
        cues.append(Cue(row["start"], row["end"], row["text"], translation, row.get("speaker", ""),
                        member_id=row.get('member_id', ''), chorus=row.get('chorus', False)))
    options = document["options"]
    atomic_write(target, make_ass(cues, options["font"], options["font_size"], coloring=options.get('coloring')))
    return JobResult(Path(path), target, len(cues), remaining)


def process_job(path: Path, settings, cue_ids=None, mode="auto", *, cancel=None, progress=None, log=None, on_job=None) -> JobResult:
    from deepseek_request import request_processing

    path = Path(path).resolve()
    cancel = cancel or threading.Event()
    progress = progress or (lambda _: None)
    log = log or (lambda _: None)
    on_job = on_job or (lambda _: None)
    if mode not in {"auto", *STAGES}:
        raise ValueError("重试步骤无效")
    if not settings.deepseek_key.strip():
        raise ValueError("请先在“翻译”填写 DeepSeek API Key")
    with _lock(path):
        document = load_job(path)
        from member_colors import export_palette, annotate_identities
        document['options']['coloring'] = export_palette(settings)
        from member_terms import member_reference
        identity_reference = member_reference(settings) if settings.use_member_colors else {}
        all_ids = {row["id"] for row in document["cues"]}
        selected = all_ids if cue_ids is None else set(cue_ids)
        if not selected or not selected <= all_ids or any(type(index) is not int for index in selected):
            raise ValueError("请选择存在的字幕编号")
        from text_cleanup import clean_start, has_words
        for row in document['cues']:
            if not has_words(clean_start(row['text'])):
                row['suppressed'] = '仅含标点，不发送 API，不导出'
            elif row['id'] in selected:
                row['text'] = clean_start(row['text'])
                row['translation'] = clean_start(row['translation'])
        from lyrics import apply_to_job
        matched = apply_to_job(document, settings, selected)
        if matched:
            log(f'当前歌词库匹配 {matched} 条待处理字幕，原有编号和时间轴保留；重新导入 Soniox JSON 可按词级时间重断句。')
        document["state"] = "processing"
        tasks, batch = {}, []
        for row in document["cues"]:
            if row["id"] not in selected or row.get('suppressed'):
                continue
            stages = [stage for stage in STAGES if (mode == "auto" or stage == mode) and row[stage]["status"] in RETRYABLE]
            if "translate" in stages and row["proofread"]["status"] in RETRYABLE and "proofread" not in stages:
                stages.remove("translate")
            if stages:
                tasks[row["id"]] = stages
                batch.append(row)
        attempted = sum(len(stages) for stages in tasks.values())

        def checkpoint():
            save_job(path, document)
            on_job(path)

        checkpoint()
        try:
            if batch:
                check_cancel(cancel)
                when = now()
                for row in batch:
                    for stage in tasks[row["id"]]:
                        row[stage]["attempts"] += 1
                        row[stage]["error"] = "请求中断或尚未完成，可重试"
                checkpoint()
                log(f"一次请求处理 {len(batch)} 条字幕，校对与翻译按需合并，请稍候……")
                try:
                    items = [(row["id"], Cue(row["start"], row["end"], row["text"], speaker=row.get("speaker", ""))) for row in batch]
                    first = next(index for index, row in enumerate(document["cues"]) if row["id"] == batch[0]["id"])
                    context = [row["text"] for row in document["cues"][max(0, first - 2):first]]
                    from sentence_context import conversation_context
                    conversation = conversation_context(document["cues"], tasks, maximum=settings.continuation_pause)
                    references = {row['id']: row['lyric'] for row in batch if row.get('lyric')}
                    from lyric_context import stage_reference, describe_reference
                    full_lyrics = stage_reference(settings)
                    lyric_summary = describe_reference(full_lyrics)
                    if lyric_summary['songs_with_lyrics']:
                        lyric_path = path.with_name(path.stem + '.deepseek-lyrics.json')
                        atomic_write(lyric_path, json.dumps({'schema': 1, 'summary': lyric_summary, 'stage_lyrics': full_lyrics}, ensure_ascii=False, indent=2))
                        document['deepseek_lyrics_context'] = lyric_path.name
                        checkpoint()
                        log(f"DeepSeek 完整歌词：{lyric_summary['stage']}，{lyric_summary['songs_with_lyrics']} 首、{lyric_summary['lines']} 行；保留 RAP 与重复段落，按公演顺序一次发送。快照：{lyric_path}")
                    content = request_processing(items, tasks, settings, cancel, context, conversation=conversation, lyrics=references, stage_lyrics=full_lyrics)
                    identity_reference = getattr(content, 'member_glossary', identity_reference)
                    # Save the returned text and essential completion metadata
                    # BEFORE parsing. No headers, URLs, keys or request prompts.
                    response_path = path.with_name(path.stem + '.deepseek-' + uuid.uuid4().hex[:10] + '.json')
                    diagnostic = {'schema': 1, 'at': now(), 'cue_ids': [row['id'] for row in batch],
                                  **getattr(content, 'metadata', {}), 'content': str(content)}
                    serialized = json.dumps(diagnostic, ensure_ascii=False, indent=2)
                    for secret in (settings.deepseek_key, settings.soniox_key):
                        if secret:
                            serialized = serialized.replace(secret, '[隐藏]')
                    atomic_write(response_path, serialized)
                    document.setdefault('responses', []).append(response_path.name)
                    checkpoint()
                    texts, errors = _decode_partial(content, batch, tasks)
                    from deepseek_response import decode_rows
                    _, issue = decode_rows(content)
                    if issue or getattr(content, 'metadata', {}).get('finish_reason', 'stop') != 'stop':
                        log(f'API 响应不完整或格式损坏，已保存诊断并保留有效条目：{response_path}')
                    for row in batch:
                        reference = row.get('lyric')
                        if reference:
                            if 'proofread' in tasks[row['id']]:
                                texts[row['id'], 'proofread'] = clean_start(reference['japanese'])
                                errors.pop((row['id'], 'proofread'), None)
                            if 'translate' in tasks[row['id']] and reference.get('chinese'):
                                texts[row['id'], 'translate'] = clean_start(reference['chinese'])
                                errors.pop((row['id'], 'translate'), None)
                    from member_terms import apply_member_terms
                    apply_member_terms(texts, errors, batch, tasks, getattr(content, 'member_glossary', None))
                except Cancelled:
                    for row in batch:
                        for stage in tasks[row["id"]]:
                            state = row[stage]
                            state["status"] = "pending"
                            state["history"].append({"at": when, "result": "cancelled", "error": "用户取消，结果未保存"})
                            state["error"] = "用户取消，待重试"
                    raise
                except Exception as exc:
                    texts = {}
                    errors = {(row["id"], stage): redact_error(exc, settings) for row in batch for stage in tasks[row["id"]]}
                for row in batch:
                    for stage in tasks[row["id"]]:
                        state = row[stage]
                        key = row["id"], stage
                        if stage == "translate" and row["proofread"]["status"] in RETRYABLE:
                            cause = errors.get((row['id'], 'proofread'), '')
                            errors[key] = "本条日语校对未完成，译文需随校对重新确认" + (f'；校对原因：{cause}' if cause else '')
                            texts.pop(key, None)
                        if key in texts:
                            text = texts[key]
                            state["status"], state["error"] = "success", ""
                            if stage == "proofread":
                                changed = row["text"] != text
                                row["text"] = text
                                if changed and document["options"]["translate"] and row["translate"]["status"] == "success":
                                    row["translation"] = ""
                                    row["translate"]["status"] = "pending"
                                    row["translate"]["error"] = "日语已更新，等待重新翻译"
                            else:
                                row["translation"] = text
                            state["history"].append({"at": when, "result": "success", "error": ""})
                        else:
                            state["status"], state["error"] = "failed", errors[key]
                            state["history"].append({"at": when, "result": "failed", "error": state["error"]})
                            log(f"第 {row['id']} 条{LABELS[stage]}失败：{state['error']}")
                checkpoint()
            annotate_identities(document, settings, reference=identity_reference or {})
            document["state"] = "incomplete" if any(unfinished(row) for row in document["cues"]) else "completed"
            result = export_job(path, document)
            document["last_output"] = result.output.name
            checkpoint()
            progress(1)
            return replace(result, attempted=attempted)
        except Cancelled:
            document["state"] = "cancelled"
            result = export_job(path, document)
            document["last_output"] = result.output.name
            checkpoint()
            raise


def import_japanese_ass(source: Path, target: Path, settings) -> tuple[Path, dict]:
    """Import this tool's earlier raw ASS output without decoding the video again."""
    def parse_time(value):
        parts = value.strip().split(":")
        if len(parts) != 3:
            raise ValueError("ASS 时间格式无效")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

    cues, style_members = [], {}
    lines = Path(source).read_text(encoding="utf-8-sig").splitlines()
    for line in lines:
        if line.startswith('; JpAssColorStyles: '):
            try:
                value = json.loads(line.split(': ', 1)[1])
                if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                    style_members = value
            except ValueError:
                pass
    for line in lines:
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(":", 1)[1].lstrip().split(",", 9)
        if len(fields) != 10 or fields[3].lower() == 'chinese' or fields[3].lower().startswith('chinese_'):
            continue
        text = fields[9].split(r"\N{\rChinese}", 1)[0]
        text = re.sub(r"\{[^}]*\}", "", text).replace(r"\N", " ").replace(r"\n", " ").replace(r"\h", " ").strip()
        if text:
            identity = style_members.get(fields[3], '')
            chorus = identity == '__chorus__' or fields[3].lower() == 'japanese_chorus'
            cues.append(Cue(parse_time(fields[1]), parse_time(fields[2]), text, speaker=fields[4],
                            member_id=identity if not chorus else '', chorus=chorus))
    if not cues:
        raise ValueError("ASS 文件中没有可导入的日语字幕，请选择原始日语 .raw.ass")
    return create_job(None, target, cues, settings, Path(source))
