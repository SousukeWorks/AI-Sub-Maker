"""Offline two-task audio/context/timeline/export verification for the EXE."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

FIRST='Opening RAP\n朝の駅で星を数えよう\nみんな笑って広場へ向かおう'
SECOND='夜の空には赤い船が飛ぶ\n新しい朝を仲間と迎えよう'


def fixture_settings(folder,base):
    from engine import Settings
    from lyrics import save_library
    p=Path(folder)/'split-lyrics.json'
    save_library({'schema':1,'stages':[{'id':'two','name':'自作二部公演','songs':[
        {'id':'a','title':'第一の歌','japanese':FIRST,'chinese':''},
        {'id':'b','title':'第二の歌','japanese':SECOND,'chinese':''}]}]},str(p.resolve()))
    return Settings(soniox_url=base,soniox_key='offline-soniox-key',soniox_split_enabled=True,
                    soniox_split_time='00:01.500',soniox_second_song='b',lyric_stage='two',lyrics_library=str(p.resolve()),
                    deepseek=True,translate=True,deepseek_url=base,deepseek_key='offline-deepseek-key')


def options():
    return {'segmented':True,'complete_translation':True,'tokens_by_job':{
        1:[{'text':'最初の話です。','start_ms':200,'end_ms':700,'speaker':'1'}],
        2:[{'text':'続きの話です。','start_ms':100,'end_ms':800,'speaker':'1'}]}}


def run_split_check(folder):
    from diagnostics import create_fixture
    from engine import run
    from soniox_asr import transcribe,import_result
    from soniox_checks import fake_soniox
    from subtitle_jobs import load_job
    folder=Path(folder).resolve()
    source,target=folder/'two-parts.mp4',folder/'two-parts.ass'
    create_fixture(source)
    # A failed older single-task record must not block split mode or be rewritten.
    old=target.with_suffix('.soniox.json');old.write_text('{"status":"error","error":"old context too long"}','utf-8')
    previous=old.read_bytes()
    with fake_soniox(options()) as (base,calls):
        settings=fixture_settings(folder,base)
        run(source,target,settings,threading.Event(),lambda _:None,lambda _:None)
        creates=[body for method,route,body in calls if method=='POST' and route=='/v1/transcriptions']
        assert len(creates)==2
        assert FIRST in creates[0]['context']['text'] and SECOND not in creates[0]['context']['text']
        assert SECOND in creates[1]['context']['text'] and FIRST not in creates[1]['context']['text']
        assert '[02 第二の歌]' in creates[1]['context']['text']
        chats=[body for method,route,body in calls if method=='POST' and route=='/v1/chat/completions']
        assert len(chats)==1
        data=json.loads(chats[0]['messages'][1]['content'])
        assert [song['japanese'] for song in data['stage_lyrics']['songs']]==[FIRST,SECOND]
        master=target.with_name(target.stem+'.split.soniox.json')
        state=json.loads(master.read_text('utf-8-sig'))
        assert state['status']=='completed' and 2.9 < state['duration'] < 3.2
        assert [(t['start_ms'],t['end_ms']) for t in state['transcript']['tokens']]==[(200,700),(1600,2300)]
        assert [t['speaker'] for t in state['transcript']['tokens']]==['1/1','2/1']
        before=len(calls)
        with patch('soniox_split.extract_flac_parts',side_effect=AssertionError('Decoded cached parts')):
            cues=transcribe(source,target,settings,threading.Event(),lambda _:None,lambda _:None)
        assert len(calls)==before and len(cues)==2
        with patch('soniox_asr.Client.request',side_effect=AssertionError('Network on import')):
            _,job=import_result(master,folder/'split-import.ass',settings)
        assert len(job['cues'])==2
        events=[line.split(',',9) for line in target.read_text('utf-8-sig').splitlines() if line.startswith('Dialogue:')]
        assert len(events)==4
        for jp,zh in zip(events[::2],events[1::2]):
            assert jp[1:3]==zh[1:3] and {jp[3],zh[3]}=={'japanese','chinese'}
            assert r'\N' not in jp[-1] and r'\N' not in zh[-1]
    assert old.read_bytes()==previous
    return {'two_asr_tasks':True,'ordered_lyrics_partition':True,'rap_retained':True,'second_part_offset':True,
            'speaker_ids_independent':True,'one_whole_deepseek_request':True,'bilingual_matching_times':True,
            'completed_cache_without_api':True,'merged_record_importable':True,'old_failed_record_untouched':True}


def run_split_gui_check(folder):
    import config
    from app import App
    from diagnostics import create_fixture
    from soniox_checks import fake_soniox
    folder=Path(folder).resolve()
    source=folder/'gui-split.mp4';create_fixture(source)
    with fake_soniox(options()) as (base,calls):
        settings=fixture_settings(folder,base)
        with patch.object(config,'load',return_value=(settings,False,'')),patch.object(config,'save') as saved, \
             patch('app.messagebox.showinfo'),patch('app.messagebox.showerror') as error:
            window=App(start_maximized=False)
            try:
                window.update();window.withdraw()
                assert window.split_panel.song_picker.get().endswith('第二の歌')
                assert window.vars['soniox_split_enabled'].get()
                window.source.set(str(source));window.target.set(str(folder/'gui-split.ass'))
                window.start_button.invoke()
                deadline=time.monotonic()+20
                while window.busy and time.monotonic()<deadline:
                    window.update();time.sleep(.01)
                window.update()
                assert not window.busy and not error.called,error.call_args
                assert saved.called and saved.call_args.args[0].soniox_split_time=='00:01.500'
                assert sum(method=='POST' and route=='/v1/transcriptions' for method,route,_ in calls)==2
                assert sum(method=='POST' and route=='/v1/chat/completions' for method,route,_ in calls)==1
            finally:
                window.destroy()
    return {'controls_restored':True,'gui_to_two_asr_jobs':True,'gui_to_one_translation_request':True,'settings_saved':True}
