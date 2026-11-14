"""End-to-end sentence-first + lyric + bilingual checks, also run in the EXE."""
import json
from pathlib import Path


def run_complete_sentence_check(folder: Path):
    from engine import Settings
    from lyrics import apply_to_tokens, save_library
    from soniox_asr import parse_tokens
    from subtitle_jobs import create_job, process_job, load_job
    from workflow_checks import local_api, response

    # Invented lyrics, with an actual performance adding になる after a line.
    line = '青い空を見上げてうれしい気持ちでいっぱい'
    next_line = '未来へ進む仲間の歌が聞こえる'
    library = folder / 'sentence-lyrics.json'
    save_library({'schema': 1, 'stages': [{'id': 'sentence', 'name': '自作公演', 'songs': [
        {'id': 'song', 'title': '自作曲', 'japanese': line + '\n' + next_line,
         'chinese': '旧译文第一行\n旧译文第二行'}]}]}, str(library))
    tokens, clock = [], 100
    for part, gap in ((line, 0), ('になる。', 2460), (next_line + '。', 900)):
        clock += gap
        for char in part:
            tokens.append({'text': char, 'start_ms': clock, 'end_ms': clock + 80, 'speaker': '1'})
            clock += 120
    duration = clock / 1000 + 1
    settings = Settings(deepseek=True, translate=True, deepseek_key='offline-sentence-key',
                        lyric_stage='sentence', lyrics_library=str(library))
    native_report = {}
    native = parse_tokens({'tokens': tokens}, duration, report=native_report)
    assert native[0].text == line + 'になる。'
    assert native_report['suffix_repair_count'] == 0  # It was never split.
    cues, report = apply_to_tokens({'tokens': tokens}, duration, settings)
    assert len(cues) == 2 and cues[0].text == line + 'になる。'
    assert cues[0].lyric['sentence_joined'] and cues[0].lyric['chinese'] == ''
    assert cues[0].lyric['canonical_parts'][0]['japanese'] == line
    assert cues[0].start == .1 and cues[0].end == native[0].end

    def responder(body, number):
        data = json.loads(body['messages'][1]['content'])
        assert len(data['subtitles']) == 2
        assert data['subtitles'][0]['text'] == line + 'になる。'
        assert data['subtitles'][0]['lyric_reference']['japanese'] == line + 'になる。'
        return 200, response([{'id': 1, 'japanese': cues[0].text, 'chinese': '望着蓝天，心中充满喜悦。'},
                              {'id': 2, 'japanese': cues[1].text, 'chinese': '听见伙伴奔向未来的歌。'}])

    from dataclasses import replace
    with local_api(responder) as (base, received):
        path, _ = create_job(None, folder / 'complete-sentence.ass', cues, settings)
        result = process_job(path, replace(settings, deepseek_url=base))
        saved = load_job(path)
        assert len(received) == 1 and not result.remaining
        assert saved['cues'][0]['translation'] == '望着蓝天，心中充满喜悦。'
        lines = [line.split(',', 9) for line in result.output.read_text('utf-8-sig').splitlines()
                 if line.startswith('Dialogue:')]
        assert len(lines) == 4 and lines[0][1:3] == lines[1][1:3]
        assert {lines[0][3], lines[1][3]} == {'japanese', 'chinese'}
    return {'sentence_built_before_translation': True, 'lyrics_do_not_resplit_sentence': True,
            'whole_sentence_chinese_replaces_old_line_translation': True,
            'bilingual_native_outer_times': True, 'batch_calls': 1,
            'native_boundaries': [(cue.start, cue.end) for cue in native]}
