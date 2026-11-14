"""Offline punctuation-free dialogue and bilingual export regression fixture."""
from dataclasses import replace
import json
from pathlib import Path

PARTS = ['はい お話はありませんか', 'はい 今日は来てくれてありがとう',
         'とても楽しかったです', 'また見てください', 'ありがとうございました',
         'おめでとう', 'ありがとう。']
TRANSLATIONS = ['好，还有话要说吗', '好，感谢今天能来', '非常开心',
                '请再看看', '非常感谢', '恭喜', '谢谢。']


def fixture(parts=PARTS, *, gap=0, blanks=True):
    clock, tokens = 100, []
    for index, part in enumerate(parts):
        if index:
            if blanks:
                tokens.append({'text': ' ', 'start_ms': clock, 'end_ms': clock + gap, 'speaker': '1'})
            clock += gap + 40
        for char in part:
            tokens.append({'text': char, 'start_ms': clock, 'end_ms': clock + 60, 'speaker': '1'})
            clock += 100
    return {'tokens': tokens}, clock / 1000 + 1


def run_utterance_check(folder: Path):
    from engine import Settings
    from soniox_asr import parsed_result
    from lyrics import save_library
    from stage_catalog import initial_library
    from subtitle_jobs import create_job, process_job, load_job
    from workflow_checks import local_api, response

    payload, duration = fixture()
    library = folder / 'utterance-lyrics.json'
    save_library(initial_library(), str(library))
    settings = Settings(deepseek=True, translate=True, deepseek_key='offline-utterance-key',
                        lyrics_library=str(library), lyric_stage='stage-69')
    target = folder / 'utterance-output.ass'
    cues, report = parsed_result({'transcript': payload, 'duration': duration}, target, settings=settings)
    assert [c.text for c in cues] == PARTS, [c.text for c in cues]
    assert report['length_limits'] is None
    starts = {t['start_ms']/1000 for t in payload['tokens'] if t['text'].strip()}
    ends = {t['end_ms']/1000 for t in payload['tokens'] if t['text'].strip()}
    assert all(c.start in starts and c.end in ends for c in cues)

    def responder(body, number):
        rows = json.loads(body['messages'][1]['content'])['subtitles']
        assert [r['text'] for r in rows] == PARTS
        return 200, response([{'id': i+1, 'japanese': jp, 'chinese': zh}
                              for i, (jp, zh) in enumerate(zip(PARTS, TRANSLATIONS))])

    with local_api(responder) as (base, calls):
        path, _ = create_job(None, target, cues, settings)
        result = process_job(path, replace(settings, deepseek_url=base))
        assert result.remaining == 0 and len(calls) == 1
        assert [r['translation'] for r in load_job(path)['cues']] == TRANSLATIONS
        events = [line.split(',', 9) for line in target.read_text('utf-8-sig').splitlines()
                  if line.startswith('Dialogue:')]
        assert len(events) == len(PARTS)*2
        for jp, zh in zip(events[::2], events[1::2]):
            assert jp[1:3] == zh[1:3] and {jp[3], zh[3]} == {'japanese', 'chinese'}
            assert r'\N' not in jp[-1] and r'\N' not in zh[-1]
    return {'missing_punctuation_split_by_grammar': True, 'no_fixed_size_limits': True,
            'native_token_intervals': True, 'sentence_built_before_translation': True,
            'one_batch_translates_each_clause': True, 'bilingual_times_match': True,
            'no_forced_line_breaks': True, 'output_clauses': len(cues)}
