"""Build bounded Soniox vocabulary context from the selected stage and wordbook."""
from collections import deque
from copy import deepcopy
import hashlib
from itertools import zip_longest
import json
import re
import unicodedata

# Soniox allows 8,000 tokens. No public tokenizer is provided. Use a smaller
# UTF-8 byte budget for the decoded context, including its JSON structure,
# rather than treating their approximate character count as an exact limit.
MAX_CONTEXT_BYTES = 7000
LYRIC_RESERVE_BYTES = 2400


def serialize(context):
    return json.dumps(context, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def context_size(context):
    return len(serialize(context).encode('utf-8'))


def unique(values):
    seen = set()
    for value in values:
        text = ' '.join(unicodedata.normalize('NFKC', value).split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            yield text


def interleave(groups):
    for row in zip_longest(*groups):
        yield from (value for value in row if value is not None)


def spread(lines):
    """Include the beginning, middle and end before filling intervening lines."""
    if not lines:
        return []
    result, seen = [], set()
    for at in (0, len(lines) // 2, len(lines) - 1):
        if at not in seen:
            result.append(lines[at])
            seen.add(at)
    queue = deque([(0, len(lines))])
    while queue:
        a, b = queue.popleft()
        if a >= b:
            continue
        middle = (a + b) // 2
        if middle not in seen:
            result.append(lines[middle])
            seen.add(middle)
        queue.extend(((a, middle), (middle + 1, b)))
    return result


def build_context(settings):
    summary = {'enabled': bool(getattr(settings, 'soniox_send_context', True)),
               'included': False, 'stage': '', 'song_titles': 0, 'terms_sent': 0,
               'terms_available': 0, 'lyric_lines_sent': 0, 'lyric_lines_available': 0,
               'lyrics_sampled': False, 'vocabulary_sampled': False, 'utf8_bytes': 0, 'sha256': ''}
    if not summary['enabled']:
        return None, summary
    from lyrics import selected_stage, lyric_lines
    from member_terms import member_reference
    stage = selected_stage(settings)
    reference = member_reference(settings)
    songs = stage['songs'] if stage else []
    titles = list(unique(song['title'] for song in songs))
    song_lines = [list(unique(lyric_lines(song.get('japanese', '')))) for song in songs]
    # Scan every song before applying the capacity limit. A late song's English
    # refrain must not disappear just because earlier songs fill the excerpts.
    english = [list(unique(word for line in lines
                           for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", line)))
               for lines in song_lines]
    members = reference['members'] if reference else []
    terms = reference['terms'] if reference else []
    names = list(interleave([[member['japanese_name'], *member.get('nicknames_japanese', [])]
                             for member in members]))
    custom = [term['japanese_term'] for term in terms]
    vocabulary = list(unique([*titles, *interleave([list(interleave(english)), custom, names])]))
    summary.update(stage=stage['name'] if stage else '', song_titles=len(titles),
                   terms_available=len(vocabulary), lyric_lines_available=sum(map(len, song_lines)))
    if not vocabulary and not any(song_lines):
        return None, summary

    context = {'general': [{'key': 'language', 'value': 'Japanese'},
                           {'key': 'domain', 'value': 'Live stage songs and spoken dialogue' if stage else 'Speech transcription'}],
               'terms': []}
    if stage:
        context['general'].append({'key': 'performance', 'value': stage['name'][:160]})
    terms_budget = MAX_CONTEXT_BYTES - LYRIC_RESERVE_BYTES if any(song_lines) else MAX_CONTEXT_BYTES
    for value in vocabulary:
        context['terms'].append(value)
        if context_size(context) > terms_budget:
            context['terms'].pop()

    excerpts = [[] for _ in songs]
    prefix = ('Partial lyrics for vocabulary reference. The recording also contains spoken dialogue. '
              'Transcribe only what is audible; do not insert unspoken lyrics.\n')

    def render():
        return prefix + '\n\n'.join('[' + songs[i]['title'] + ']\n' + '\n'.join(lines)
                                     for i, lines in enumerate(excerpts) if lines)

    for item in interleave([[(i, line) for line in spread(lines)] for i, lines in enumerate(song_lines)]):
        index, line = item
        excerpts[index].append(line)
        context['text'] = render()
        if context_size(context) > MAX_CONTEXT_BYTES:
            excerpts[index].pop()
    if any(excerpts):
        context['text'] = render()
    else:
        context.pop('text', None)
    if not context['terms']:
        context.pop('terms')
    if not context.get('terms') and not context.get('text'):
        return None, summary
    summary.update(included=True, terms_sent=len(context.get('terms', [])),
                   lyric_lines_sent=sum(map(len, excerpts)), utf8_bytes=context_size(context),
                   sha256=hashlib.sha256(serialize(context).encode('utf-8')).hexdigest())
    summary['lyrics_sampled'] = summary['lyric_lines_sent'] < summary['lyric_lines_available']
    summary['vocabulary_sampled'] = summary['terms_sent'] < summary['terms_available']
    return deepcopy(context), summary


def describe(summary):
    if not summary.get('enabled'):
        return 'Soniox 识别上下文已关闭。'
    if not summary.get('included'):
        return 'Soniox 识别上下文：当前没有可发送的歌词或已启用词条。'
    result = (f"Soniox 识别上下文：{summary['stage'] or '当前词库'}；"
              f"词条 {summary['terms_sent']}/{summary['terms_available']}，"
              f"歌词摘录 {summary['lyric_lines_sent']}/{summary['lyric_lines_available']} 行（去重后）。")
    if summary['lyrics_sampled'] or summary['vocabulary_sampled']:
        result += ' 已按接口容量选取；英文词已从所有选中歌曲中提取，歌词按曲目均衡取样。'
    return result
