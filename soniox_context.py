"""Send complete ordered lyrics and the enabled Japanese wordbook to Soniox."""
from copy import deepcopy
import hashlib
from itertools import zip_longest
import json
import re
import unicodedata

# Soniox's documented limit is in tokens, not bytes. Its tokenizer is not
# public: send full references for server validation, never silently sample.
MAX_CONTEXT_TOKENS = 8000


def serialize(context):
    return json.dumps(context, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def context_size(context):
    return len(serialize(context).encode('utf-8'))


def unique(values):
    seen = set()
    for value in values:
        text = ' '.join(unicodedata.normalize('NFKC', value).split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            yield text


def interleave(groups):
    for row in zip_longest(*groups):
        yield from (value for value in row if value is not None)


def build_context(settings):
    summary = {'enabled': bool(getattr(settings, 'soniox_send_context', True)), 'included': False,
               'stage': '', 'song_titles': 0, 'terms_sent': 0, 'terms_available': 0,
               'lyric_lines_sent': 0, 'lyric_lines_available': 0, 'lyrics_sampled': False,
               'vocabulary_sampled': False, 'utf8_bytes': 0, 'sha256': '', 'lyrics_complete': True,
               'lyrics_order': 'stage_song_then_original_line', 'server_context_token_limit': MAX_CONTEXT_TOKENS}
    if not summary['enabled']:
        return None, summary
    from lyric_context import stage_reference, describe_reference, ordered_text
    from member_terms import member_reference
    stage = stage_reference(settings)
    reference = member_reference(settings)
    songs = stage['songs'] if stage else []
    titles = list(unique(song['title'] for song in songs))
    english = list(unique(word for song in songs for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", song['japanese'])))
    members = reference['members'] if reference else []
    terms = reference['terms'] if reference else []
    names = list(interleave([[member['japanese_name'], *member.get('nicknames_japanese', [])] for member in members]))
    vocabulary = list(unique([*titles, *english, *(term['japanese_term'] for term in terms), *names]))
    details = describe_reference(stage)
    summary.update(stage=details['stage'], song_titles=len(titles), terms_available=len(vocabulary),
                   terms_sent=len(vocabulary), lyric_lines_available=details['lines'], lyric_lines_sent=details['lines'],
                   songs_with_lyrics=details['songs_with_lyrics'], lyrics_sha256=details['sha256'])
    text = ordered_text(stage)
    if not vocabulary and not text:
        return None, summary
    context = {'general': [{'key': 'language', 'value': 'Japanese'},
                           {'key': 'domain', 'value': 'Live stage songs and spoken dialogue' if stage else 'Speech transcription'}]}
    if stage:
        context['general'].append({'key': 'performance', 'value': stage['stage'][:160]})
    if vocabulary:
        context['terms'] = vocabulary
    if text:
        context['text'] = ('Complete user-provided lyrics in performance order, including RAP and repeated verses. '
                           'Reference only; transcribe only audible words and do not insert unperformed lyrics.\n\n' + text)
    summary.update(included=True, utf8_bytes=context_size(context),
                   sha256=hashlib.sha256(serialize(context).encode('utf-8')).hexdigest())
    return deepcopy(context), summary


def describe(summary):
    if not summary.get('enabled'):
        return 'Soniox 识别上下文已关闭。'
    if not summary.get('included'):
        return 'Soniox 识别上下文：当前没有可发送的歌词或已启用词条。'
    return (f"Soniox 识别上下文：{summary['stage'] or '当前词库'}；词条 {summary['terms_sent']}/{summary['terms_available']}，"
            f"完整歌词 {summary['lyric_lines_sent']}/{summary['lyric_lines_available']} 行。"
            '按公演曲目和原行顺序，保留 RAP、重复歌词；不抽样、不截断。'
            'Soniox 服务端上限为 8000 tokens，若拒绝超限将报错，不自动删减或分次重试。')
