"""Offline checks for Japanese pause joining and contextual batch translation."""
import json
from pathlib import Path


def run_sentence_check(folder: Path):
    from engine import Settings
    from sentence_context import conversation_context
    from soniox_asr import parse_tokens
    from subtitle_jobs import create_job, load_job, process_job
    from workflow_checks import local_api, response

    tokens = [{"text": "思い出を", "start_ms": 100, "end_ms": 800, "speaker": "1"},
              {"text": "話していきたいです。", "start_ms": 1600, "end_ms": 2600, "speaker": "1"}]
    report = {}
    joined = parse_tokens({"tokens": tokens}, 4, report=report)
    assert len(joined) == 1 and joined[0].text == "思い出を話していきたいです。"
    assert joined[0].start == .1 and joined[0].end == 2.6
    assert report["continued_pause_count"] == 1
    split = parse_tokens({"tokens": tokens}, 4, natural_sentences=False)
    assert len(split) == 2
    changed = [dict(tokens[0]), {**tokens[1], "speaker": "2"}]
    assert len(parse_tokens({"tokens": changed}, 4)) == 2

    def responder(body, number):
        data = json.loads(body["messages"][1]["content"])
        assert len(data["subtitles"]) == 1
        context = data["conversation_context"]
        assert [row["id"] for row in context] == [1, 2]
        assert context[0]["text"] == "思い出を" and context[0]["read_only"]
        assert context[1]["speaker"] == "1" and context[1]["possible_continuation"]
        assert "机械添加句号" in body["messages"][0]["content"]
        return 200, response([{"id": 2, "japanese": "話していきたいです。", "chinese": "想和大家聊聊。"}])

    with local_api(responder) as (base, received):
        settings = Settings(deepseek=True, translate=True, deepseek_url=base, deepseek_key="offline-context-key")
        path, _ = create_job(None, folder / "sentence-context.ass", split, settings)
        result = process_job(path, settings, [2])
        saved = load_job(path)
        assert len(received) == 1
        assert saved["cues"][0]["text"] == split[0].text
        assert saved["cues"][0]["translate"]["status"] == "pending"
        assert saved["cues"][1]["translate"]["status"] == "success"
        assert [(r["start"], r["end"]) for r in saved["cues"]] == [(c.start, c.end) for c in split]
    # A sung held note separated from its inflection, and an old six-second
    # cap, both produce ONE complete Japanese input and ONE Chinese output.
    fragments = {'tokens': [
        {'text': 'あらかじめ出入口の確認をお願いいたし', 'start_ms': 100, 'end_ms': 6100, 'speaker': '1'},
        {'text': 'ます。', 'start_ms': 6160, 'end_ms': 6520, 'speaker': '1'},
        {'text': '君が蘇', 'start_ms': 8000, 'end_ms': 9380, 'speaker': '1'},
        {'text': 'る。', 'start_ms': 10640, 'end_ms': 10700, 'speaker': '1'}]}
    repaired = parse_tokens(fragments, 12)
    assert [c.text for c in repaired] == ['あらかじめ出入口の確認をお願いいたします。', '君が蘇る。']

    def merged_response(body, number):
        data = json.loads(body['messages'][1]['content'])
        assert [r['text'] for r in data['subtitles']] == [c.text for c in repaired]
        assert all(r['tasks'] == ['proofread', 'translate'] for r in data['subtitles'])
        return 200, response([{'id': 1, 'japanese': repaired[0].text, 'chinese': '请提前确认出入口的位置。'},
                              {'id': 2, 'japanese': repaired[1].text, 'chinese': '你再次苏醒。'}])

    with local_api(merged_response) as (base, received):
        settings = Settings(deepseek=True, translate=True, deepseek_url=base, deepseek_key='offline-suffix-key')
        path, _ = create_job(None, folder / 'suffix-bilingual.ass', repaired, settings)
        result = process_job(path, settings)
        assert len(received) == 1 and result.remaining == 0
        saved = load_job(path)
        assert [r['translation'] for r in saved['cues']] == ['请提前确认出入口的位置。', '你再次苏醒。']
        events = [line.split(',', 9) for line in result.output.read_text('utf-8-sig').splitlines() if line.startswith('Dialogue:')]
        assert len(events) == 4
        for index in (0, 2):
            assert events[index][1:3] == events[index + 1][1:3]
            assert {events[index][3], events[index + 1][3]} == {'japanese', 'chinese'}
        assert [(r['start'], r['end']) for r in saved['cues']] == [(.1, 6.52), (8, 10.7)]
    return {"unfinished_same_speaker_joined": True, "native_outer_times_preserved": True,
            "different_speakers_separate": True, "disabled_mode_keeps_pauses": True,
            "retry_includes_read_only_neighbours": True, "batch_requests": 1,
            'suffix_bilingual_complete_sentence_request': True, 'bilingual_identical_outer_times': True}
