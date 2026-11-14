"""Editable local lyric references and conservative native-token alignment."""
from __future__ import annotations

import bisect
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from stage_catalog import initial_library
from subtitles import Cue, atomic_write
from text_cleanup import clean_start, has_words


def library_path(value=''):
    if value.strip():
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError('歌词库请使用完整路径')
        return path
    return Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent / 'lyrics-library.json'


def validate_library(document):
    if not isinstance(document, dict) or document.get('schema') != 1 or not isinstance(document.get('stages'), list):
        raise ValueError('歌词库格式无效，需要 schema=1 和 stages 列表')
    stage_ids = set()
    total = 0
    for stage in document['stages']:
        if not isinstance(stage, dict) or not isinstance(stage.get('id'), str) or not stage['id'] or stage['id'] in stage_ids:
            raise ValueError('公演编号为空或重复')
        stage_ids.add(stage['id'])
        if not isinstance(stage.get('name'), str) or not stage['name'].strip() or not isinstance(stage.get('songs'), list):
            raise ValueError('公演名称或曲目列表无效')
        song_ids = set()
        for song in stage['songs']:
            total += 1
            if not isinstance(song, dict) or not isinstance(song.get('id'), str) or not song['id'] or song['id'] in song_ids:
                raise ValueError('曲目编号为空或重复')
            song_ids.add(song['id'])
            if not isinstance(song.get('title'), str) or not song['title'].strip():
                raise ValueError('曲目名称不能为空')
            for key in ('japanese', 'chinese'):
                if not isinstance(song.get(key, ''), str) or len(song.get(key, '')) > 100000:
                    raise ValueError('单曲歌词格式无效或过长')
    if total > 5000 or len(document['stages']) > 200:
        raise ValueError('歌词库超过 200 套公演或 5000 首曲目的限制')
    return document


def load_library(value=''):
    path = library_path(value)
    if not path.exists():
        if value.strip():
            raise ValueError(f'歌词库不存在：{path}')
        return initial_library()
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('歌词库超过 16 MB')
    return validate_library(json.loads(path.read_text(encoding='utf-8-sig')))


def save_library(document, value=''):
    validate_library(document)
    atomic_write(library_path(value), json.dumps(document, ensure_ascii=False, indent=2))


def lyric_lines(text):
    # LRC clocks are metadata, not the recording's absolute performance times.
    result = []
    for line in text.splitlines():
        line = re.sub(r'^(?:\s*\[\d+:\d+(?:[.:]\d+)?\])+', '', line).strip()
        if re.match(r'^\[(?:ar|ti|al|by|offset|re|ve):', line, re.I):
            continue
        line = clean_start(line)
        if has_words(line):
            result.append(line)
    return result


def selected_stage(settings):
    key = getattr(settings, 'lyric_stage', '')
    if not key:
        return None
    document = load_library(getattr(settings, 'lyrics_library', ''))
    stage = next((stage for stage in document['stages'] if stage['id'] == key), None)
    if stage is None:
        raise ValueError('所选公演已不在歌词库中，请重新选择')
    for song in stage['songs']:
        chinese = lyric_lines(song.get('chinese', ''))
        if chinese and len(chinese) != len(lyric_lines(song.get('japanese', ''))):
            raise ValueError(f'《{song["title"]}》中文与日文行数不一致，请在歌词库修正或清空中文')
    return stage


def normalized(text):
    return ''.join(c.casefold() for c in unicodedata.normalize('NFKC', text)
                   if unicodedata.category(c)[0] in 'LN')


def find_matches(units, stage):
    """Require two neighboring lyric lines, >=18 characters, no long silence.

    A unit is a native token (or an indivisible existing subtitle on retry).
    Match boundaries must coincide with unit boundaries; never distribute times
    by character count. Repeated choruses may produce multiple native spans.
    """
    stream, owners, starts, ends = '', [], {}, {}
    pieces = []
    for index, unit in enumerate(units):
        word = normalized(unit['text'])
        if not word:
            continue
        starts[len(owners)] = index
        pieces.append(word)
        owners.extend([index] * len(word))
        ends[len(owners)] = index
    stream = ''.join(pieces)
    if not stream:
        return []
    positions = defaultdict(list)
    for i in range(len(stream) - 2):
        positions[stream[i:i + 3]].append(i)
    start_positions, end_positions = sorted(starts), sorted(ends)
    candidates = []
    for song in stage['songs']:
        japanese = lyric_lines(song.get('japanese', ''))
        chinese = lyric_lines(song.get('chinese', ''))
        if chinese and len(chinese) != len(japanese):
            raise ValueError(f'《{song["title"]}》中文与日文行数不一致，请在歌词库修正或清空中文')
        for line_index, text in enumerate(japanese):
            query = normalized(text)
            if not 4 <= len(query) <= 120:
                continue
            offsets = Counter()
            for i in range(len(query) - 2):
                for at in positions.get(query[i:i + 3], []):
                    offsets[at - i] += 1
            seen = set()
            # All exact repetitions plus the strongest approximate anchors.
            probes = [offset for offset, _ in offsets.most_common(16)]
            at = stream.find(query)
            while at >= 0 and len(probes) < 150:
                probes.append(at)
                at = stream.find(query, at + len(query))
            for offset in set(probes):
                lefts = start_positions[max(0, bisect.bisect_left(start_positions, offset - 2)):
                                        bisect.bisect_right(start_positions, offset + 2)]
                for left in lefts:
                    rights = end_positions[bisect.bisect_left(end_positions, left + max(4, len(query) - 3)):
                                          bisect.bisect_right(end_positions, left + len(query) + 3)]
                    best = None
                    for right in rights:
                        a, b = starts[left], ends[right]
                        if (a, b) in seen:
                            continue
                        value = stream[left:right]
                        ratio = 1.0 if query == value else SequenceMatcher(None, query, value, autojunk=False).ratio()
                        if ratio < (.88 if len(query) >= 8 else 1.0):
                            continue
                        if units[b]['end'] <= units[a]['start'] or units[b]['end'] - units[a]['start'] > 20:
                            continue
                        if any(units[k]['start'] - units[k - 1]['end'] > 2.0 for k in range(a + 1, b + 1)):
                            continue
                        entry = dict(first=a, last=b, start=units[a]['start'], end=units[b]['end'],
                                     score=ratio, japanese=text, chinese=chinese[line_index] if chinese else '',
                                     song=song['title'], song_id=song['id'], line=line_index + 1,
                                     stage=stage['name'], stage_id=stage['id'])
                        if best is None or (ratio, -abs(len(query) - len(value))) > (best['score'], -best['_delta']):
                            best = {**entry, '_delta': abs(len(query) - len(value))}
                    if best:
                        seen.add((best['first'], best['last']))
                        candidates.append(best)
    supported = set()
    by_line = defaultdict(list)
    for index, row in enumerate(candidates):
        by_line[row['song_id'], row['line']].append((index, row))
    for index, row in enumerate(candidates):
        for next_index, following in by_line.get((row['song_id'], row['line'] + 1), []):
            if (row['last'] < following['first'] and -.05 <= following['start'] - row['end'] <= 12
                    and len(normalized(row['japanese'])) + len(normalized(following['japanese'])) >= 18):
                supported.update((index, next_index))
    accepted, occupied = [], set()
    for index in sorted(supported, key=lambda i: (-candidates[i]['score'], candidates[i]['first'])):
        row = candidates[index]
        indices = set(range(row['first'], row['last'] + 1))
        if indices & occupied:
            continue
        occupied.update(indices)
        row.pop('_delta', None)
        accepted.append(row)
    return sorted(accepted, key=lambda row: row['first'])


def apply_to_tokens(payload, duration, settings, *, cancel=None):
    from soniox_asr import parse_tokens
    from media import check_cancel
    stage = selected_stage(settings)
    if stage is None:
        return None, None
    if cancel is not None:
        check_cancel(cancel)
    tokens = [token for token in payload['tokens'] if token['text'] not in {'<end>', '<fin>'}
              and token.get('translation_status') != 'translation']
    units = [{'text': token['text'], 'start': min(duration, token['start_ms'] / 1000),
              'end': min(duration, token['end_ms'] / 1000)} for token in tokens]
    matches = find_matches(units, stage)
    cues, cursor, trusted = [], 0, {}
    def remaining(a, b):
        if a < b:
            report = {}
            remaining_cues = parse_tokens({'tokens': tokens[a:b]}, duration, report=report,
                                         natural_sentences=settings.natural_sentences,
                                         continuation_pause=settings.continuation_pause)
            for cue, sentence in zip(remaining_cues, report['sentences']):
                trusted[id(cue)] = sentence['speaker_known']
            cues.extend(remaining_cues)
    for match in matches:
        if cancel is not None:
            check_cancel(cancel)
        remaining(cursor, match['first'])
        span = tokens[match['first']:match['last'] + 1]
        speakers = {str(token.get('speaker', '')) for token in span if token.get('speaker') is not None}
        reference = {key: match[key] for key in ('stage', 'stage_id', 'song', 'song_id', 'line', 'japanese', 'chinese', 'score')}
        cues.append(Cue(match['start'], match['end'], clean_start(match['japanese']),
                        speaker=next(iter(speakers)) if len(speakers) == 1 else '', lyric=reference))
        trusted[id(cues[-1])] = all(token.get('speaker') not in (None, '') for token in span if token['text'].strip())
        match['recognized'] = ''.join(token['text'] for token in span)
        cursor = match['last'] + 1
    remaining(cursor, len(tokens))
    from sentence_fragments import join_cues
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end))
    cues, sentence_joins = join_cues(ordered, known_speakers=[trusted[id(c)] for c in ordered])
    return cues, {
        'schema': 1, 'stage': stage['name'], 'matched_lines': len(matches), 'matches': matches,
        'songs_with_lyrics': sum(bool(lyric_lines(song.get('japanese', ''))) for song in stage['songs']),
        'sentence_joins': sentence_joins, 'sentence_join_count': len(sentence_joins),
        'note': '仅连续多行高匹配度内容替换为歌词；边界取自原生 token。未匹配部分保留识别文字，不补写漏唱歌词。'}


def apply_to_job(document, settings, selected):
    """Existing retry IDs and times stay stable; never touch successful rows."""
    stage = selected_stage(settings)
    if stage is None:
        return 0
    matches = find_matches(document['cues'], stage)
    count = 0
    for match in matches:
        if match['first'] != match['last']:
            continue  # Reimport native JSON to resegment multi-cue lyric lines.
        row = document['cues'][match['first']]
        if row.get('lyric', {}).get('sentence_joined'):
            continue  # The performed full sentence must not shrink back to a library line.
        if row['id'] not in selected or row['proofread']['status'] == 'success' or row['translate']['status'] == 'success':
            continue
        row['lyric'] = {key: match[key] for key in ('stage', 'stage_id', 'song', 'song_id', 'line', 'japanese', 'chinese', 'score')}
        row['text'] = clean_start(match['japanese'])
        count += 1
    return count


