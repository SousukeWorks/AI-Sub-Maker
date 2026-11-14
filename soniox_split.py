"""Two independently resumable ASR tasks with ordered lyric ranges."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import re
import tempfile

from media import RATE, Cancelled, audio_info, check_cancel, extract_flac_parts


def parse_time(value):
    text = str(value).strip()
    if not re.fullmatch(r'\d+(?::\d{1,2}){0,2}(?:\.\d+)?', text):
        raise ValueError('分段时间请填写 秒、分:秒 或 时:分:秒，例如 01:10:00')
    pieces = [float(part) for part in text.split(':')]
    if len(pieces) > 1 and any(part >= 60 for part in pieces[1:]):
        raise ValueError('分段时间的分钟、秒必须小于 60')
    seconds = sum(part * 60**index for index, part in enumerate(reversed(pieces)))
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('分段时间必须大于零')
    seconds = round(seconds * RATE) / RATE
    if seconds <= 0:
        raise ValueError('分段时间必须大于零')
    return seconds


def split_plan(settings, duration=None):
    from lyric_context import stage_reference
    seconds = parse_time(settings.soniox_split_time)
    if duration and seconds >= duration:
        raise ValueError('分段时间必须早于音视频结束时间')
    stage = stage_reference(settings)
    if stage is None:
        raise ValueError('分两段识别需要先选择公演，再选择第二段起始歌曲')
    index = next((i for i,song in enumerate(stage['songs']) if song['song_id'] == settings.soniox_second_song), None)
    if index is None or index == 0:
        raise ValueError('请选择第二段起始歌曲；应为本公演第 2 首或之后的曲目')
    contexts = [{**stage, 'songs': stage['songs'][:index], 'first_song_number': 1},
                {**stage, 'songs': stage['songs'][index:], 'first_song_number': index+1}]
    identities = [{'part':1, 'start':0.0, 'end':seconds, 'second_song':settings.soniox_second_song},
                  {'part':2, 'start':seconds, 'end':None, 'second_song':settings.soniox_second_song}]
    return seconds, contexts, identities


def merge_transcripts(states, split_seconds):
    from soniox_asr import parse_tokens
    tokens = []
    for number, (state, offset) in enumerate(zip(states, (0.0,split_seconds)),1):
        duration = state['duration']
        parse_tokens(state['transcript'],duration)  # Validate each local clock before shifting.
        current_speaker = f'{number}/?'
        for original in state['transcript']['tokens']:
            row = deepcopy(original)
            speaker = row.get('speaker')
            known = speaker is not None and str(speaker) != ''
            if known:
                current_speaker = f'{number}/{speaker}'
            row.update(original_speaker=speaker, speaker=current_speaker, speaker_known=known, source_part=number)
            for key in ('start_ms','end_ms'):
                if type(row.get(key)) in (int,float):
                    row[key] = min(row[key],duration*1000) + offset*1000
            tokens.append(row)
    return {'tokens': tokens}


def transcribe_parts(source, target, settings, cancel, progress, log):
    from engine import audio_temp_dir
    from soniox_asr import Client, _signature, _lock, _transcribe_single, parsed_result
    from subtitles import atomic_write
    from subtitle_jobs import redact_error

    source,target = Path(source),Path(target)
    _,duration = audio_info(source)
    split,contexts,identities = split_plan(settings,duration)
    client = Client(settings)
    target.parent.mkdir(parents=True,exist_ok=True)
    path = target.with_name(target.stem+'.split.soniox.json')
    identity = {'split_seconds':split, 'second_song':settings.soniox_second_song, 'parts':identities}
    signature = _signature(source,settings,client.base,segment=identity)
    part_targets = [target.with_name(target.stem+f'.part{i}.ass') for i in (1,2)]
    part_paths = [p.with_suffix('.soniox.json') for p in part_targets]
    with _lock(path):
        state = json.loads(path.read_text('utf-8-sig')) if path.exists() else {
            'schema':1,'signature':signature,'source':str(source.resolve()),'model':settings.soniox_model,
            'split':identity,'parts':[],'status':'new'}
        if state.get('schema') != 1 or state.get('signature') != signature:
            raise ValueError('此分段记录属于另一音频、分段时间或起始歌曲，请选择新的 ASS 输出文件名')

        def save():
            atomic_write(path,json.dumps(state,ensure_ascii=False,indent=2,allow_nan=False))

        if state.get('status') == 'completed':
            log(f'复用已合并的两段 Soniox 结果，不再提交收费识别：{path}')
            log('识别上下文沿用原任务；当前词库与歌词提示仅对新识别任务生效。')
            return parsed_result(state,target,log,settings=settings)[0]
        # Check both records before any new upload or paid job.
        for part_path,part_identity in zip(part_paths,identities):
            if part_path.exists():
                part_state=json.loads(part_path.read_text('utf-8-sig'))
                expected=_signature(source,settings,client.base,segment=part_identity)
                if part_state.get('schema') != 1 or part_state.get('signature') != expected:
                    raise ValueError('已有分段记录与当前设置不符，请选择新的 ASS 输出文件名')
                if part_state.get('status') in {'creating','create_unknown'} and not part_state.get('job_id'):
                    raise RuntimeError(f'第 {part_identity["part"]} 段上次提交结果不明，为避免重复收费已停止。请在 Soniox 控制台核查；记录：{part_path}')
        log(f'分两段识别：0–{split:g} 秒；{split:g} 秒起。第二段从《{contexts[1]["songs"][0]["title"]}》开始携带歌词；共两个 Soniox 任务。')
        state.update(status='processing',parts=[{'record':p.name,**spec} for p,spec in zip(part_paths,identities)])
        save()
        try:
            temp_root=audio_temp_dir(settings) or target.parent
            with tempfile.TemporaryDirectory(prefix='ai-sub-split-',dir=temp_root) as folder:
                audio_paths=[Path(folder)/f'part{i}.flac' for i in (1,2)]
                lengths=[]

                def prepare(index):
                    if not lengths:
                        check_cancel(cancel)
                        log('按指定时间无损分割音轨，保留视频时间偏移……')
                        lengths.extend(extract_flac_parts(source,audio_paths,split,settings.audio_index,cancel,
                                                        lambda p:progress(.02+.13*p)))
                    return audio_paths[index],lengths[index]

                states=[]
                for index in range(2):
                    check_cancel(cancel)
                    result=_transcribe_single(source,part_targets[index],settings,cancel,
                        lambda p,i=index:progress(.15+.3*i+.3*p),
                        lambda message,i=index:log(f'[第 {i+1} 段] {message}'),
                        segment=identities[index],context_stage=contexts[index],
                        audio_provider=lambda i=index:prepare(i),return_state=True)
                    states.append(result)
                    state['parts'][index].update(status='completed',duration=result['duration'])
                    save()
                transcript=merge_transcripts(states,split)
                state.update(status='completed',duration=sum(s['duration'] for s in states),transcript=transcript,
                             speaker_scope='per_part',recognition_contexts=[s.get('recognition_context') for s in states],
                             recognition_context_summaries=[s.get('recognition_context_summary') for s in states])
                state.pop('error',None)
                save()  # Native merged result survives later parsing, export, or cancellation.
            cues,report=parsed_result(state,target,log,settings=settings)
            state['cues']=[asdict(cue) for cue in cues]
            save()
            progress(.78)
            log(f'两段 Soniox 已合并，第二段时间加回 {split:g} 秒：{path}；DeepSeek 将整场一次校对翻译。')
            return cues
        except Cancelled:
            state['status']='cancelled' if state.get('status') != 'completed' else 'completed'
            save()
            raise
        except Exception as exc:
            if state.get('status') != 'completed':
                state.update(status='incomplete',error=redact_error(exc,settings))
                save()
            raise
