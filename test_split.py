from dataclasses import replace
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from diagnostics import create_fixture
from media import Cancelled,extract_flac_parts,pcm_blocks,RATE
from soniox_asr import transcribe,ApiError
from soniox_checks import fake_soniox
from soniox_split import parse_time,split_plan,merge_transcripts
from split_checks import fixture_settings,options,run_split_check,run_split_gui_check,FIRST,SECOND


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name).resolve()
        self.source=self.folder/'source.mp4';self.target=self.folder/'out.ass'
        create_fixture(self.source)

    def call(self,settings,cancel=None):
        return transcribe(self.source,self.target,settings,cancel or threading.Event(),lambda _:None,lambda _:None)

    def test_time_formats_and_invalid_values(self):
        for text,expected in [('90',90),('01:30',90),('1:02:03.125',3723.125),('0:01.5',1.5)]:
            self.assertEqual(parse_time(text),expected)
        for text in ('','0','-1','nan','inf','1:60','1:00:60','1:2:3:4','一小时'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_time(text)

    def test_context_partition_is_complete_and_ordered(self):
        settings=fixture_settings(self.folder,'http://localhost/v1')
        seconds,contexts,identities=split_plan(settings,3)
        self.assertEqual(seconds,1.5)
        self.assertEqual([s['japanese'] for c in contexts for s in c['songs']],[FIRST,SECOND])
        self.assertEqual(contexts[1]['first_song_number'],2)
        for invalid in (replace(settings,soniox_second_song='missing'),replace(settings,soniox_second_song='a'),replace(settings,lyric_stage='')):
            with self.assertRaises(ValueError):split_plan(invalid)
        with self.assertRaises(ValueError):split_plan(settings,1)

    def test_flac_split_preserves_every_sample_and_video_offset(self):
        cancel=threading.Event()
        original=b''.join(pcm_blocks(self.source,0,cancel))
        targets=[self.folder/'a.flac',self.folder/'b.flac']
        durations=extract_flac_parts(self.source,targets,1.5000625,0,cancel,lambda _:None)
        left=b''.join(pcm_blocks(targets[0],0,cancel));right=b''.join(pcm_blocks(targets[1],0,cancel))
        self.assertEqual(left+right,original)
        self.assertEqual(len(left),round(1.5000625*RATE)*2)
        self.assertEqual(durations[0],len(left)/2/RATE)
        self.assertEqual(left[:14000*2],b'\0'*(14000*2))

    def test_out_of_audio_boundary_fails_without_upload(self):
        with fake_soniox(options()) as (base,calls):
            settings=replace(fixture_settings(self.folder,base),soniox_split_time='3.5')
            with self.assertRaisesRegex(ValueError,'实际音轨'):
                self.call(settings)
            self.assertFalse(calls)

    def test_two_jobs_one_translation_and_merged_import(self):
        self.assertTrue(run_split_check(self.folder)['one_whole_deepseek_request'])

    def test_gui_controls_drive_both_jobs_and_one_translation(self):
        self.assertTrue(run_split_gui_check(self.folder)['settings_saved'])

    def test_failed_second_part_resume_does_not_repeat_completed_first(self):
        opts=options();opts['poll_statuses']={2:'error'}
        with fake_soniox(opts) as (base,calls):
            settings=fixture_settings(self.folder,base)
            with self.assertRaises(RuntimeError):self.call(settings)
            first=self.target.with_name(self.target.stem+'.part1.soniox.json')
            preserved=first.read_bytes()
            opts['poll_statuses'].clear();opts['tokens_by_job'][3]=opts['tokens_by_job'][2]
            cues=self.call(settings)
            self.assertEqual(len(cues),2)
            creates=[body for method,route,body in calls if method=='POST' and route=='/v1/transcriptions']
            self.assertEqual(len(creates),3)
            self.assertEqual(sum(FIRST in b['context']['text'] for b in creates),1)
            self.assertEqual(first.read_bytes(),preserved)

    def test_ambiguous_second_create_never_reposts(self):
        opts=options();opts['create_statuses']={2:500}
        with fake_soniox(opts) as (base,calls):
            settings=fixture_settings(self.folder,base)
            with self.assertRaises(ApiError):self.call(settings)
            before=len(calls)
            with self.assertRaisesRegex(RuntimeError,'结果不明'):self.call(settings)
            self.assertEqual(len(calls),before)

    def test_cancelled_after_first_completion_resumes_second(self):
        opts=options();cancel=threading.Event()
        with fake_soniox(opts) as (base,calls):
            settings=fixture_settings(self.folder,base)
            original_merge=None
            from soniox_asr import _transcribe_single
            def wrapped(*args,**kwargs):
                result=_transcribe_single(*args,**kwargs)
                if kwargs['segment']['part']==1:cancel.set()
                return result
            with patch('soniox_asr._transcribe_single',side_effect=wrapped),self.assertRaises(Cancelled):
                self.call(settings,cancel)
            self.assertEqual(sum(m=='POST' and r=='/v1/transcriptions' for m,r,_ in calls),1)
            self.call(settings)
            self.assertEqual(sum(m=='POST' and r=='/v1/transcriptions' for m,r,_ in calls),2)

    def test_changed_boundary_is_rejected_before_new_paid_work(self):
        with fake_soniox(options()) as (base,calls):
            settings=fixture_settings(self.folder,base);self.call(settings)
            before=len(calls)
            with self.assertRaisesRegex(ValueError,'新的 ASS'):
                self.call(replace(settings,soniox_split_time='1.6'))
            self.assertEqual(len(calls),before)

    def test_completed_children_rebuild_missing_master_without_network_or_decode(self):
        with fake_soniox(options()) as (base,calls):
            settings=fixture_settings(self.folder,base);self.call(settings)
            master=self.target.with_name(self.target.stem+'.split.soniox.json')
            master.unlink()
            before=len(calls)
            with patch('soniox_split.extract_flac_parts',side_effect=AssertionError('Unexpected decode')):
                cues=self.call(settings)
            self.assertEqual(len(cues),2)
            self.assertEqual(len(calls),before)

    def test_unknown_speaker_ids_are_scoped_and_control_tokens_safe(self):
        states=[{'duration':1,'transcript':{'tokens':[{'text':'はい。','start_ms':100,'end_ms':300},
                                                  {'text':'<end>','start_ms':None,'end_ms':None}]}}]*2
        payload=merge_transcripts(states,1)
        self.assertEqual(payload['tokens'][0]['speaker'],'1/?')
        self.assertEqual(payload['tokens'][2]['speaker'],'2/?')
        self.assertFalse(payload['tokens'][2]['speaker_known'])
