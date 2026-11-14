"""Offline punctuation/lyrics/translation integration, also run in the EXE."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import threading
from unittest.mock import patch

PARTS = [
    'はい、えっと、友達と一緒に、',
    'えー、やりたいことは街歩きなんですけど、',
    'あの、よく私、友達の写真を見ていて、',
    'お、駅前とか公園、お店に行く写真を見て、',
    '楽しそうだと思ったので、',
    '今度は一緒に出かけたいと思います。',
]
TRANSLATIONS = ['好，那个，我想和朋友一起，', '一起去逛街，', '我常看朋友拍的照片，',
                '像车站、公园和店铺的照片，', '觉得看起来很开心，', '所以下次想一起出去。']


def make_tokens(parts=PARTS):
    tokens, clock = [], 100
    for part in parts:
        for char in part:
            tokens.append({'text': char, 'start_ms': clock, 'end_ms': clock + 60, 'speaker': '1'})
            clock += 100
        clock += 200
    return {'tokens': tokens}, clock / 1000 + 1


def run_punctuation_check(folder: Path):
    from engine import Settings
    from soniox_asr import parsed_result, parse_tokens
    from stage_catalog import initial_library
    from lyrics import save_library
    from subtitle_jobs import create_job, process_job, load_job, save_job
    from subtitles import make_ass
    from workflow_checks import local_api, response

    payload, duration = make_tokens()
    report = {}
    cues = parse_tokens(payload, duration, report=report)
    assert [c.text for c in cues] == PARTS
    assert report['clause_split_count'] == 1 and report['length_limits'] is None
    cursor = 0
    for cue, part in zip(cues, PARTS):
        rows = payload['tokens'][cursor:cursor + len(part)]
        assert (cue.start, cue.end) == (rows[0]['start_ms'] / 1000, rows[-1]['end_ms'] / 1000)
        cursor += len(part)
    # Selecting a lyric stage must not recombine unmatched MC comma clauses.
    library = folder / 'punctuation-lyrics.json'
    save_library(initial_library(), str(library))
    settings = Settings(deepseek=True, translate=True, deepseek_key='offline-punctuation-key',
                        lyrics_library=str(library), lyric_stage='stage-69')
    target = folder / 'punctuation-output.ass'
    final, final_report = parsed_result({'transcript': payload, 'duration': duration}, target, settings=settings)
    assert [c.text for c in final] == PARTS

    def responder(body, number):
        data = json.loads(body['messages'][1]['content'])
        assert [row['text'] for row in data['subtitles']] == PARTS
        assert all(row['tasks'] == ['proofread', 'translate'] for row in data['subtitles'])
        assert '显示语段' in body['messages'][0]['content']
        return 200, response([{'id': i + 1, 'japanese': jp, 'chinese': zh}
                              for i, (jp, zh) in enumerate(zip(PARTS, TRANSLATIONS))])

    with local_api(responder) as (base, calls):
        path, document = create_job(None, target, final, settings)
        # Saved records from older releases may still contain this obsolete
        # option. Re-export must ignore it for both languages.
        document['options']['line_width'] = 10
        save_job(path, document)
        result = process_job(path, replace(settings, deepseek_url=base))
        assert len(calls) == 1 and result.remaining == 0
        saved = load_job(path)
        assert [row['translation'] for row in saved['cues']] == TRANSLATIONS
        events = [line.split(',', 9) for line in target.read_text('utf-8-sig').splitlines()
                  if line.startswith('Dialogue:')]
        assert len(events) == len(PARTS) * 2
        for jp, zh in zip(events[::2], events[1::2]):
            assert jp[1:3] == zh[1:3] and {jp[3], zh[3]} == {'japanese', 'chinese'}
            assert r'\N' not in jp[-1] and r'\N' not in zh[-1]
    return {'commas_split_display_clauses': True, 'fillers_and_lists_stay_with_content': True,
            'native_token_intervals': True, 'lyrics_do_not_remerge_clauses': True,
            'one_batch_translates_each_clause': True, 'bilingual_times_match': True,
            'maximum_fixture_lines_per_language': 1, 'legacy_line_width_ignored': True, 'output_clauses': len(cues)}
