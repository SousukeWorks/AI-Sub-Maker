"""Offline display-phrase, full lyric-context and bilingual export checks."""
from dataclasses import replace
import json
from pathlib import Path

LYRIC_LINES = ['星の広場には', '誰も迷わない', 'Hello', 'Hello',
               'ここで会えるかもしれない', 'それが夢だと', 'わかっているけど']
LYRIC_PARTS = [' '.join(LYRIC_LINES[:2]), 'Hello Hello', LYRIC_LINES[4], ' '.join(LYRIC_LINES[5:])]
RAP = 'Opening RAP\nOne, two!\nGo!\nGo!\n最後まで一緒に歩こう'


def library_data():
    return {'schema': 1, 'stages': [{'id': 'phrases', 'name': '自作フレーズ公演', 'songs': [
        {'id': 'intro', 'title': '自作導入曲', 'japanese': RAP, 'chinese': ''},
        {'id': 'song', 'title': '自作星の歌', 'japanese': '\n'.join(LYRIC_LINES), 'chinese': ''}]}]}


def run_phrase_check(folder: Path):
    from engine import Settings
    from lyrics import save_library, apply_to_tokens
    from lyric_context import stage_reference
    from soniox_context import build_context
    from soniox_asr import parse_tokens
    from subtitle_jobs import create_job, process_job, load_job
    from utterance_checks import fixture
    from workflow_checks import local_api, response

    library = folder/'phrase-lyrics.json'
    save_library(library_data(), str(library))
    original = library.read_bytes()
    settings = Settings(lyrics_library=str(library), lyric_stage='phrases',
                        deepseek=True, translate=True, deepseek_key='offline-phrase-key')
    payload, duration = fixture(LYRIC_LINES)
    cues, report = apply_to_tokens(payload, duration, settings)
    assert [c.text for c in cues] == LYRIC_PARTS, [c.text for c in cues]
    assert report['matched_display_units'] == 4
    assert all(c.start in {t['start_ms']/1000 for t in payload['tokens']} and
               c.end in {t['end_ms']/1000 for t in payload['tokens']} for c in cues)
    assert [c.text for c in parse_tokens(payload, duration)] == LYRIC_PARTS
    reference = stage_reference(settings)
    soniox_context, summary = build_context(settings)
    assert summary['lyrics_complete'] and RAP in soniox_context['text']
    assert '\n'.join(LYRIC_LINES) in soniox_context['text']
    assert soniox_context['text'].index(RAP) < soniox_context['text'].index('\n'.join(LYRIC_LINES))
    translations = ['星星广场上无人迷路', 'Hello Hello', '也许可以在这里相见', '虽然知道那是梦想']

    def responder(body, number):
        data = json.loads(body['messages'][1]['content'])
        assert data['stage_lyrics'] == reference
        assert data['stage_lyrics']['songs'][0]['japanese'] == RAP
        assert [row['text'] for row in data['subtitles']] == LYRIC_PARTS
        return 200, response([{'id': i+1, 'japanese': jp, 'chinese': zh}
                              for i,(jp,zh) in enumerate(zip(LYRIC_PARTS,translations))])

    with local_api(responder) as (base, calls):
        path, _ = create_job(None, folder/'phrase-output.ass', cues, settings)
        result = process_job(path, replace(settings, deepseek_url=base))
        assert result.remaining == 0 and len(calls) == 1
        saved = load_job(path)
        snapshot = json.loads((folder/saved['deepseek_lyrics_context']).read_text('utf-8-sig'))
        assert snapshot['stage_lyrics'] == reference
        assert [c['translation'] for c in saved['cues']] == translations
        events = [line.split(',',9) for line in result.output.read_text('utf-8-sig').splitlines() if line.startswith('Dialogue:')]
        assert len(events) == 8
        for i,(jp,zh) in enumerate(zip(events[::2],events[1::2])):
            assert jp[1:3] == zh[1:3] and {jp[3],zh[3]} == {'japanese','chinese'}
            assert r'\N' not in jp[-1] and r'\N' not in zh[-1]
    assert library.read_bytes() == original
    return {'song_four_display_units': True, 'native_boundaries': True, 'one_deepseek_request': True,
            'bilingual_four_matching_intervals': True, 'no_forced_line_breaks': True,
            'full_lyrics_both_apis': True, 'rap_and_repeats_preserved': True, 'request_snapshot_verified': True}
