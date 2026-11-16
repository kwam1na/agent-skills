"""Owned hosted lifecycle and terminal/archive boundaries; local proof only."""
from __future__ import annotations
import copy
import atexit
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import signal
import shutil
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.test_delivery_views_hosted import hosted, CFG, TOKEN, VALID, FakeAPI
from tests import test_delivery_views_hosted as publisher_fixtures
from tests.delivery_views_fixtures import render, views_common

ROOT=Path(__file__).resolve().parents[1]
_spec=importlib.util.spec_from_file_location('run_delivery_hosted_flow',ROOT/'skills/run-delivery/scripts/run_delivery.py')
flow=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=flow;_spec.loader.exec_module(flow)

OWNED=set()
def stop_owned():
    for directory in tuple(OWNED):
        with contextlib.suppress(OSError,hosted.PublishError):hosted.stop(directory)
atexit.register(stop_owned)

class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.live=self.root/'live';self.live.mkdir()
        self.archives=self.root/'archives';self.archive=self.archives/'closed'/'state';self.archive.mkdir(parents=True)
        self.config=views_common.validate_config(CFG);self.api=FakeAPI()
        self.publisher=hosted.Publisher(self.live,self.config['hosted'],'APP-1',self.api,secret_values=[TOKEN])
        self.document={'state':render.review_state.initial_state('Terminal delivery'),'journal':[]}
        self.document['journal']=[{'id':10,'kind':'delivery.closed','at':'2026-11-15T00:00:00Z','subject':None,'lane':None,'summary':'Closed'}]
        self.document['state'].setdefault('meta',{})['closing']={'started':{'entry':10,'at':'2026-11-15T00:00:00Z'},'finished':{'entry':10,'at':'2026-11-15T00:00:01Z'}}
        (self.archive/'state.json').write_text(json.dumps(self.document['state']))
        (self.archive/'journal.jsonl').write_text(json.dumps(self.document['journal'][0])+'\n')
    def request(self):
        return hosted.request_terminal(self.live,self.archive,self.archives,10,self.document)
    def payload(self):
        return hosted.public.project(self.document,{},self.config,source_fingerprint='f'*64)
    def test_finished_close_mark_is_required_and_pending_cut_is_durable_before_transport(self):
        unfinished=copy.deepcopy(self.document);unfinished['state']['meta']['closing'].pop('finished')
        with self.assertRaises(hosted.PublishError):hosted.request_terminal(self.live,self.archive,self.archives,10,unfinished)
        intent=self.request()
        self.assertEqual(intent['state'],'capture-pending')
        self.assertEqual(hosted.read_json(self.archive/hosted.TERMINAL)['entry'],10)
        self.assertEqual(self.api.calls,[])
        self.assertEqual(hosted.FINAL_RETRY_SECONDS,600)
    def test_terminal_ack_is_bound_to_digest_and_finished_entry_and_mirrored_without_credentials(self):
        self.request()
        result=hosted.terminal_step(self.publisher,lambda:self.payload(),self.archives)
        self.assertEqual(result['state'],'final-published')
        info=hosted.archive_status(self.archive)
        self.assertEqual(info['state'],'final-published');self.assertTrue(info['url'].endswith(self.publisher.identity['publicId']))
        terminal=hosted.read_json(self.archive/hosted.TERMINAL)
        self.assertEqual(terminal['state'],'published');self.assertEqual(terminal['digest'],self.publisher.identity['receipt']['digest'])
        for path in self.archive.glob('hosted-*.json'):self.assertNotIn(TOKEN,path.read_text())
    def test_lost_terminal_ack_and_archive_retry_preserve_exact_body_and_identity(self):
        self.request();self.publisher.step(lambda:copy.deepcopy(VALID))
        self.api.lose=True
        with self.assertRaises(hosted.PublishError):hosted.terminal_step(self.publisher,lambda:self.payload(),self.archives)
        self.assertEqual(hosted.archive_status(self.archive)['state'],'final-publication-pending')
        identity=hosted.read_json(self.archive/hosted.IDENTITY);self.assertIsNotNone(identity['pending'])
        path=self.archive/(hosted.PENDING_PREFIX+identity['pending']['digest']+'.json')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),identity['pending']['digest'])
        retry=hosted.Publisher(self.archive,self.config['hosted'],'APP-1',self.api,secret_values=[TOKEN])
        result=hosted.terminal_step(retry,lambda:(_ for _ in ()).throw(AssertionError('frozen body must be reused')),self.archive)
        self.assertEqual(result['state'],'final-published')
        self.assertEqual(retry.identity['publicId'],identity['publicId'])
    def test_corrupt_or_unreferenced_pending_material_is_not_archived_as_current(self):
        self.request();(self.live/'hosted-pending-unrelated.json').write_text('unrelated')
        hosted.mirror_hosted(self.live,self.archive)
        self.assertFalse((self.archive/'hosted-pending-unrelated.json').exists())
        (self.live/hosted.IDENTITY).write_text('null')
        with self.assertRaises(hosted.PublishError):hosted.mirror_hosted(self.live,self.archive)
    def test_contended_notifications_keep_one_deadline_and_preserve_acknowledged_cut(self):
        (self.live/'state.json').write_text(json.dumps(self.document['state']))
        (self.live/'journal.jsonl').write_text(json.dumps(self.document['journal'][0])+'\n')
        with hosted.lock(self.live/hosted.CONTROL):
            intent=hosted.request_terminal(self.live,self.archive,self.archives,10,self.document,timeout=0)
            notice=self.live/(hosted.TERMINAL_NOTICE_PREFIX+'10.json');before=notice.read_bytes()
            self.assertFalse((self.live/hosted.TERMINAL).exists())
            self.assertEqual(hosted.read_terminal(self.live,self.archives),intent)
            with mock.patch.object(hosted.time,'time',return_value=time.time()+100):
                repeated=hosted.request_terminal(self.live,self.archive,self.archives,10,self.document,timeout=0)
            self.assertEqual(repeated,intent);self.assertEqual(notice.read_bytes(),before)
            result=hosted.terminal_step(self.publisher,lambda:self.payload(),self.archives)
            self.assertEqual(result['state'],'final-published')
            accepted=(self.archive/hosted.TERMINAL).read_bytes()
            hosted.request_terminal(self.live,self.archive,self.archives,10,self.document,timeout=0)
            self.assertEqual((self.archive/hosted.TERMINAL).read_bytes(),accepted)
        self.assertFalse(list(self.archive.glob(hosted.TERMINAL_NOTICE_PREFIX+'*.json')))
    def test_corrupt_notification_is_not_absence_and_unselected_notices_are_ignored(self):
        (self.live/'state.json').write_text(json.dumps(self.document['state']))
        (self.live/'journal.jsonl').write_text(json.dumps(self.document['journal'][0])+'\n')
        unrelated=self.live/(hosted.TERMINAL_NOTICE_PREFIX+'11.json');unrelated.write_text('null')
        self.assertIsNone(hosted.read_terminal(self.live,self.archives))
        selected=self.live/(hosted.TERMINAL_NOTICE_PREFIX+'10.json');selected.write_text('null')
        with self.assertRaises(hosted.PublishError):hosted.read_terminal(self.live,self.archives)
        with self.assertRaises(hosted.PublishError):
            hosted.request_terminal(self.live,self.archive,self.archives,10,self.document,timeout=0)
        self.assertEqual(selected.read_text(),'null')
        selected.unlink();intent=self.request()
        hosted.atomic(selected,{**intent,'state':'published','digest':'0'*64,'sourceFingerprint':'0'*64})
        with self.assertRaises(hosted.PublishError):hosted.read_terminal(self.live,self.archives)
    def test_concurrent_first_notifications_cannot_replace_the_first_durable_deadline(self):
        (self.live/'state.json').write_text(json.dumps(self.document['state']))
        (self.live/'journal.jsonl').write_text(json.dumps(self.document['journal'][0])+'\n')
        notice=self.live/(hosted.TERMINAL_NOTICE_PREFIX+'10.json')
        ready=threading.Event();created=threading.Event();second_done=threading.Event()
        first=[];results=[];errors=[];original=hosted.atomic;now=time.time()
        def atomic(path,value,**kwargs):
            if path!=notice:return original(path,value,**kwargs)
            if threading.current_thread().name=='first-notice':
                first.append(value)
                if not ready.wait(5):raise AssertionError('second producer missing')
                result=original(path,value,**kwargs);created.set()
                if not second_done.wait(5):raise AssertionError('second producer stuck')
                return result
            ready.set()
            if not created.wait(5):raise AssertionError('first producer stuck')
            try:return original(path,value,**kwargs)
            finally:second_done.set()
        def request():
            try:results.append(hosted.request_terminal(self.live,self.archive,self.archives,10,self.document,timeout=1))
            except Exception as error:errors.append(error)
        with mock.patch.object(hosted,'atomic',side_effect=atomic),mock.patch.object(hosted.time,'time',side_effect=lambda:now if threading.current_thread().name=='first-notice' else now+100):
            threads=[threading.Thread(name=name,target=request) for name in ('first-notice','second-notice')]
            for thread in threads:thread.start()
            for thread in threads:thread.join(8)
        self.assertFalse(any(thread.is_alive() for thread in threads));self.assertEqual(errors,[])
        self.assertEqual(len(results),2);self.assertEqual(hosted.read_json(notice),first[0])
        self.assertTrue(all(row['requestedAt']==first[0]['requestedAt'] for row in results))
    def test_final_retry_window_expires_without_renewing_or_relabelling_completion(self):
        intent=self.request();intent['requestedAt']=1;intent['retryUntil']=600001
        hosted.atomic(self.live/hosted.TERMINAL,intent)
        with mock.patch.object(hosted.time,'time',return_value=601):
            result=hosted.terminal_step(self.publisher,lambda:self.payload(),self.archives)
        self.assertEqual(result['state'],'final-publication-pending');self.assertEqual(self.api.calls,[])
        self.assertIsNone(self.publisher.identity['receipt'])
    def test_delayed_old_mirror_cannot_replace_acknowledged_archive(self):
        self.request();paused=threading.Event();release=threading.Event();errors=[]
        original=hosted.atomic
        def atomic(path,value):
            if path==self.archive/hosted.IDENTITY and threading.current_thread().name=='old-mirror':
                paused.set()
                if not release.wait(5):raise AssertionError('mirror release missing')
            return original(path,value)
        def run(fn):
            try:fn()
            except Exception as error:errors.append(error)
        with mock.patch.object(hosted,'atomic',side_effect=atomic):
            old=threading.Thread(name='old-mirror',target=run,args=(lambda:hosted.mirror_hosted(self.live,self.archive),));old.start()
            self.assertTrue(paused.wait(3))
            new=threading.Thread(target=run,args=(lambda:hosted.terminal_step(self.publisher,lambda:self.payload(),self.archives),));new.start()
            deadline=time.monotonic()+3
            while hosted.read_json(self.live/hosted.TERMINAL)['state']!='published' and time.monotonic()<deadline:time.sleep(.01)
            release.set();old.join(5);new.join(5)
        self.assertFalse(old.is_alive() or new.is_alive());self.assertEqual(errors,[])
        self.assertEqual(hosted.archive_status(self.archive)['state'],'final-published')

class LifecycleTests(unittest.TestCase):
    run_quietly=True
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.state=self.root/'state';self.state.mkdir()
        self.archive=self.root/'archives'/'one'/'state';self.archive.mkdir(parents=True)
        self.views=copy.deepcopy(CFG)
        self.c=SimpleNamespace(root=self.root,main=self.root,state=self.state,release=ROOT,delivery='APP-1',
            flow_log=self.root/'flow',flow_raw={'cleanup':{'archive':str(self.root/'archives')}},
            shelf=SimpleNamespace(json=lambda name:self.views if name=='.agents/delivery-views.json' else {'state':{'directory':'state'}}))
    def test_binding_shelf_config_is_materialized_and_only_uploader_receives_token(self):
        applied=self.root/'.agent-skills';applied.mkdir();other=self.root/'other-generation';other.mkdir()
        (applied/'current').symlink_to(other)
        with mock.patch.dict(os.environ,{'FIXTURE_PUBLISH_TOKEN':TOKEN}),mock.patch.object(flow,'run') as run:
            run.return_value=subprocess.CompletedProcess([],0,json.dumps({'state':'pending'}),'')
            flow.start_hosted(self.c,resume=True)
            command=run.call_args.args[0];environment=run.call_args.kwargs['env']
            self.assertIn('start',command);self.assertIn('--resume',command)
            self.assertEqual(command[command.index('--generation')+1],str(ROOT))
            self.assertEqual(command[command.index('--generation-target')+1],str(ROOT.resolve()))
            self.assertEqual(environment['FIXTURE_PUBLISH_TOKEN'],TOKEN)
            self.assertNotIn('FIXTURE_PUBLISH_TOKEN',os.environ)
            self.assertNotIn(TOKEN,''.join(p.read_text() for p in self.state.glob('*.json')))
    def test_close_budget_and_durable_request_survive_unavailable_capture_or_upload(self):
        self.assertEqual(flow.HOSTED_CLOSE_SECONDS,30)
        document={'state':{'meta':{'closing':{'started':{'entry':10},'finished':{'entry':10}}}},
                  'journal':[{'id':10,'kind':'delivery.closed'}]}
        (self.archive/'state.json').write_text(json.dumps(document['state']));(self.archive/'journal.jsonl').write_text(json.dumps(document['journal'][0])+'\n')
        with mock.patch.object(flow,'start_hosted',return_value={'state':'failed'}),mock.patch.object(flow,'hosted_info',return_value={'state':'final-publication-pending'}):
            result=flow.finalize_hosted(self.c,self.archive.parent,{'id':10},seconds=0)
        self.assertEqual(result['state'],'final-publication-pending')
        self.assertTrue((self.archive/hosted.TERMINAL).exists())
    def test_held_archive_mirror_lock_cannot_extend_close_budget(self):
        document={'state':{'meta':{'closing':{'started':{'entry':10},'finished':{'entry':10}}}},
                  'journal':[{'id':10,'kind':'delivery.closed'}]}
        (self.archive/'state.json').write_text(json.dumps(document['state']))
        (self.archive/'journal.jsonl').write_text(json.dumps(document['journal'][0])+'\n')
        ready=self.root/'lock-held'
        child=subprocess.Popen([sys.executable,'-c',
            'import fcntl,pathlib,sys,time;f=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX);pathlib.Path(sys.argv[2]).touch();time.sleep(60)',
            str(self.archive/'hosted-mirror.lock'),str(ready)],start_new_session=True)
        def cleanup():
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)
        self.addCleanup(cleanup)
        deadline=time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(ready.exists());result=[]
        with mock.patch.object(flow,'start_hosted',return_value={'state':'pending'}):
            thread=threading.Thread(target=lambda:result.append(flow.finalize_hosted(self.c,self.archive.parent,{'id':10},seconds=.2)))
            thread.start();thread.join(2);finished=not thread.is_alive()
            cleanup();thread.join(5)
        self.assertTrue(finished,'close waited for the archive mirror lock')
        self.assertEqual(result[0]['state'],'final-publication-pending')
        self.assertTrue((self.archive/hosted.TERMINAL).exists())
    def test_archive_status_needs_no_token_and_never_starts_uploader(self):
        hosted.Publisher(self.archive,self.views['hosted'],'APP-1')
        with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(flow,'start_hosted') as start:
            info=flow.hosted_info(None,archive=self.archive)
        self.assertIn('url',info);start.assert_not_called()
        (self.archive/'state.json').write_text(json.dumps(render.review_state.initial_state('Archived')))
        (self.archive/'journal.jsonl').write_text('')
        info=flow.hosted_info(None,archive=self.archive)
        fake=SimpleNamespace(parse_config=lambda _:None,show=lambda _:None,
                             serve_live=lambda record,config,**kw:kw['announce']('127.0.0.1:1234'))
        with mock.patch.dict(sys.modules,{'render_state':fake}),mock.patch.dict(os.environ,{},clear=True),mock.patch.object(flow,'start_hosted') as start,contextlib.redirect_stdout(io.StringIO()) as output:
            flow.view(SimpleNamespace(archive=self.archive,port=0))
        self.assertIn(info['url'],output.getvalue());start.assert_not_called()
    def test_missing_intent_retry_refuses_an_unfinished_archive_without_changing_identity(self):
        hosted.Publisher(self.archive,self.views['hosted'],'APP-1')
        (self.archive/'state.json').write_text(json.dumps(render.review_state.initial_state('Unfinished')))
        (self.archive/'journal.jsonl').write_text('');before=(self.archive/hosted.IDENTITY).read_bytes()
        with mock.patch.object(flow,'Config',return_value=self.c),mock.patch.object(flow,'repo',return_value=self.root):
            with self.assertRaisesRegex(flow.Stop,'no finished terminal cut'):
                flow.hosted_retry(SimpleNamespace(archive=self.archive,resume=False))
        self.assertEqual((self.archive/hosted.IDENTITY).read_bytes(),before)
        self.assertFalse((self.archive/hosted.TERMINAL).exists())
    def test_private_descriptor_transfers_token_without_environment_or_disk_copy(self):
        with mock.patch.dict(os.environ,{'FIXTURE_PUBLISH_TOKEN':TOKEN}):
            descriptor=flow.hosted_credentials_pipe(self.c)
            self.assertNotIn('FIXTURE_PUBLISH_TOKEN',os.environ)
            flow.HOSTED_TOKENS.clear()
            os.environ['RUN_DELIVERY_HOSTED_FD']=str(descriptor)
            flow.receive_hosted_credentials()
            self.assertNotIn('RUN_DELIVERY_HOSTED_FD',os.environ)
            self.assertEqual(flow.HOSTED_TOKENS[('FIXTURE_PUBLISH_TOKEN',CFG['hosted']['origin'],'repo_A')],TOKEN)
        self.assertEqual(list(self.state.iterdir()),[])
    def test_invalid_optional_config_preserves_core_copy_and_valid_hosted_identity(self):
        publisher=hosted.Publisher(self.state,self.views['hosted'],'APP-1')
        (self.state/'state.json').write_text(json.dumps(render.review_state.initial_state('Local remains usable')))
        (self.state/'journal.jsonl').write_text('');(self.state/'.hosted-unreferenced').write_text('staged body')
        self.views['hosted']['origin']='http://invalid.example.com'
        latest=flow.copy_latest(self.c)/'state'
        self.assertEqual(hosted.read_json(latest/hosted.IDENTITY)['publicId'],publisher.identity['publicId'])
        self.assertFalse((latest/'.hosted-unreferenced').exists());self.assertTrue((latest/'state.json').exists())
        self.assertEqual(flow.finalize_hosted(self.c,self.archive.parent,{'id':1},seconds=0)['state'],'final-publication-pending')
        self.c.raw={'lanes':{},'stories':{}};self.c.stories={};self.c.effective_ownership=lambda _:{}
        self.c.delivery_path=self.root/'APP-1.json'
        with mock.patch.object(flow,'archive_plan_review',return_value=False):
            archive,_=flow.archive_delivery(self.c)
        self.assertTrue((archive/'state/state.json').exists());self.assertFalse((archive/'state/.hosted-unreferenced').exists())
        self.assertEqual(hosted.read_json(archive/'state'/hosted.IDENTITY)['publicId'],publisher.identity['publicId'])
    def test_remote_shelf_token_is_absent_from_real_bootstrap_git_and_later_generic_children(self):
        root=self.root;real_git=shutil.which('git');wrapper=root/'bin';wrapper.mkdir();observed=root/'git-environment.jsonl'
        def git(*args):subprocess.run([real_git,*args],cwd=root,capture_output=True,check=True)
        git('init','-q','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
        (root/'.agents').mkdir()
        (root/'.agents/orchestration.json').write_text(json.dumps({'schemaVersion':'orchestration-binding/1','base':{'remote':'origin','ref':'main'}}))
        (root/'.agents/run-delivery.json').write_text(json.dumps({'schemaVersion':'delivery-flow/1','harness':'ignore'}))
        remote=copy.deepcopy(CFG);remote['hosted']['tokenEnv']='SHELF_PUBLISH_TOKEN'
        views=root/'.agents/delivery-views.json';views.write_text(json.dumps(remote))
        git('add','.agents');git('commit','-qm','Shared shelf');git('clone','-q','--bare',str(root),str(root/'origin.git'))
        git('remote','add','origin',str(root/'origin.git'));git('fetch','-q','origin');git('checkout','-qb','feature/lane')
        views.write_text(json.dumps(CFG))
        (wrapper/'git').write_text(f'#!{sys.executable}\nimport os,sys,json,re\nwith open({str(observed)!r},"a") as out:out.write(json.dumps([sys.argv[1:],"SHELF_PUBLISH_TOKEN" in os.environ,bool(re.fullmatch("[A-Za-z0-9_-]{{43,128}}",os.environ.get("LOGNAME","")))])+"\\n")\nos.execv({real_git!r},[{real_git!r},*sys.argv[1:]])\n')
        (wrapper/'git').chmod(0o755)
        with mock.patch.dict(os.environ,{'PATH':str(wrapper)+os.pathsep+os.environ['PATH'],'SHELF_PUBLISH_TOKEN':TOKEN,'FIXTURE_PUBLISH_TOKEN':TOKEN}):
            c=flow.Config(root,need_delivery=False,pick=False)
            self.assertEqual(c.shelf.ref,'origin/main')
            result=flow.run([sys.executable,'-c','import os;print("SHELF_PUBLISH_TOKEN" in os.environ)'],cwd=root)
            self.assertEqual(result.stdout.strip(),'False')
        git('checkout','-fq','main');remote['hosted']['tokenEnv']='LOGNAME';views.write_text(json.dumps(remote))
        git('add','.agents/delivery-views.json');git('commit','-qm','Baseline variable token');git('push','-q','origin','main')
        git('checkout','-fq','feature/lane');views.write_text(json.dumps(CFG))
        with mock.patch.dict(os.environ,{'PATH':str(wrapper)+os.pathsep+os.environ['PATH'],'LOGNAME':TOKEN,'FIXTURE_PUBLISH_TOKEN':TOKEN}):
            c=flow.Config(root,need_delivery=False,pick=False)
            self.assertEqual(c.shelf.json('.agents/delivery-views.json')['hosted']['tokenEnv'],'LOGNAME')
        rows=[json.loads(line) for line in observed.read_text().splitlines()]
        self.assertTrue(any(argv[:1]==['fetch'] for argv,_,_ in rows))
        self.assertFalse(any(present or baseline for _,present,baseline in rows))

class OwnedProcessTests(unittest.TestCase):
    run_quietly=True
    def setUp(self):
        publisher_fixtures.ReceiverTests.setUp(self)
        self.root=self.state.resolve();(self.root/'.git').mkdir()
        self.state=self.root/'state';self.state.mkdir();self.gen=self.root/'generation';self.gen.mkdir()
        self.archives=self.root/'archives';self.archive=self.archives/'closed'/'state';self.archive.mkdir(parents=True)
        self.config=views_common.validate_config(CFG)
        self.document={'state':render.review_state.initial_state('Independent watcher'),'journal':[]}
        self.write_record(self.state)
        (self.root/'views.json').write_text(json.dumps(CFG))
        (self.root/'binding.json').write_text(json.dumps({'state':{'directory':'state'}}))
        (self.root/'sitecustomize.py').write_text(
            'import http.client,os,sys,json\nReal=http.client.HTTPConnection\n'
            f'def fixture(host,port=None,timeout=None,context=None):\n assert host=="deliveries.example.com"\n return Real("127.0.0.1",{self.server.server_port},timeout=timeout)\n'
            'http.client.HTTPSConnection=fixture\n'
            f'with open({str(self.root/"environment.jsonl")!r},"a") as out:out.write(json.dumps([sys.argv,"FIXTURE_PUBLISH_TOKEN" in os.environ])+"\\n")\n')
        clock=self.root/'watcher-clock'
        with (self.root/'sitecustomize.py').open('a') as out:
            out.write('import time,pathlib\nreal_time=time.time\n'
                f'if "watch" in sys.argv:\n def watcher_time():\n  p=pathlib.Path({str(clock)!r})\n  return real_time()+(int(p.read_text()) if p.exists() else 0)\n time.time=watcher_time\n')
        self.clock=clock
        self.environment={**os.environ,'FIXTURE_PUBLISH_TOKEN':TOKEN,'PYTHONPATH':str(self.root)}
        OWNED.add(self.state)
        self.addCleanup(OWNED.discard,self.state);self.addCleanup(hosted.stop,self.state)
        self.before={}
        for number in (signal.SIGTERM,signal.SIGHUP):
            prior=signal.getsignal(number);self.before[number]=prior
            def interrupted(signum,frame,prior=prior):
                stop_owned()
                if callable(prior):return prior(signum,frame)
                raise KeyboardInterrupt()
            signal.signal(number,interrupted)
        self.addCleanup(lambda:[signal.signal(number,prior) for number,prior in self.before.items()])
    def write_record(self,directory):
        (directory/'state.json').write_text(json.dumps(self.document['state']))
        (directory/'journal.jsonl').write_text(''.join(json.dumps(entry)+'\n' for entry in self.document['journal']))
    def command(self,operation,*extra):
        argv=[sys.executable,'-B',str(ROOT/'skills/delivery-views/scripts/hosted.py'),operation,'--config','views.json',
              '--binding','binding.json','--key','APP-1','--generation',str(self.gen),'--archive-root',str(self.archives),*extra]
        done=subprocess.run(argv,cwd=self.root,env=self.environment,capture_output=True,text=True,timeout=8,start_new_session=True)
        self.assertEqual(done.returncode,0,done.stdout+done.stderr)
        return json.loads(done.stdout)
    def wait_for(self,predicate,seconds=8):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.05)
        self.fail('owned watcher did not reach the expected state')
    def wait_archive_published(self):
        self.wait_for(lambda:flow.hosted_info(None,archive=self.archive).get('state')=='final-published')
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK) and not hosted.locked(self.archive/hosted.LOCK))
        info=hosted.archive_status(self.archive)
        self.assertEqual(info['state'],'final-published')
        self.assertIsNotNone(hosted.read_json(self.archive/hosted.IDENTITY)['receipt'])
        return info
    def finished(self,*,notify=True):
        self.document['journal']=[{'id':1,'kind':'delivery.closed','at':'2026-11-15T00:00:00Z','subject':None,'lane':None,'summary':'Closed'}]
        self.document['state']['meta']['closing']={'started':{'entry':1,'at':'2026-11-15T00:00:00Z'},'finished':{'entry':1,'at':'2026-11-15T00:00:01Z'}}
        self.write_record(self.state);self.write_record(self.archive)
        if notify:return hosted.request_terminal(self.state,self.archive,self.archives,1,self.document)
    def test_two_starts_adopt_one_owner_direct_mutations_publish_and_supervisor_stop_leaves_it(self):
        first=self.command('start');owner=hosted.read_json(self.state/hosted.OWNER)
        second=self.command('start')
        self.assertEqual(first['url'],second['url']);self.assertEqual(hosted.read_json(self.state/hosted.OWNER)['pid'],owner['pid'])
        self.wait_for(lambda:any(op=='snapshots' for op,_,_ in self.observed))
        self.document['state']['meta']['headline']='Direct mutation';self.write_record(self.state)
        self.wait_for(lambda:any(body and b'Direct mutation' in body for op,_,body in self.observed if op=='snapshots'))
        supervisor=flow.Supervisor.__new__(flow.Supervisor);supervisor.children={}
        supervisor.stop_children()
        self.assertTrue(hosted.process_matches(owner));self.assertTrue(hosted.locked(self.state/hosted.LOCK))
        rows=[json.loads(line) for line in (self.root/'environment.jsonl').read_text().splitlines()]
        generic=[present for argv,present in rows if 'collect' in argv]
        self.assertTrue(generic);self.assertFalse(any(generic));self.assertFalse((self.state/'tracker-ledger.json').exists())
        self.gen=self.root/'generation-two';self.gen.mkdir();self.command('start')
        replacement=hosted.read_json(self.state/hosted.OWNER)
        self.assertNotEqual(replacement['pid'],owner['pid']);self.assertFalse(hosted.process_matches(owner))
    def test_ps_children_do_not_inherit_a_bearer_valued_home_variable(self):
        views=copy.deepcopy(CFG);views['hosted']['tokenEnv']='HOME'
        (self.root/'views.json').write_text(json.dumps(views))
        wrapper=self.root/'bin';wrapper.mkdir();observed=self.root/'ps-environment.jsonl';real_ps=shutil.which('ps')
        (wrapper/'ps').write_text(f'#!{sys.executable}\nimport os,sys,re,json\nwith open({str(observed)!r},"a") as out:out.write(json.dumps(bool(re.fullmatch("[A-Za-z0-9_-]{{43,128}}",os.environ.get("HOME",""))))+"\\n")\nos.execv({real_ps!r},[{real_ps!r},*sys.argv[1:]])\n')
        (wrapper/'ps').chmod(0o755)
        self.environment.update(HOME=TOKEN,PATH=str(wrapper)+os.pathsep+self.environment['PATH'])
        self.environment.pop('FIXTURE_PUBLISH_TOKEN',None)
        self.command('start');self.wait_for(lambda:any(op=='snapshots' for op,_,_ in self.observed))
        rows=[json.loads(line) for line in observed.read_text().splitlines()]
        self.assertTrue(rows);self.assertFalse(any(rows))
        self.assertTrue(any(auth=='Bearer '+TOKEN for _,auth,_ in self.observed))
    def test_terminal_ack_archives_finished_cut_and_owner_exits_without_cleanup(self):
        self.command('start');self.finished()
        self.wait_archive_published()
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK))
        self.assertEqual(hosted.read_json(self.archive/hosted.TERMINAL)['state'],'published')
        self.assertEqual(hosted.finished_entry(json.loads(next(body for op,_,body in reversed(self.observed) if op=='snapshots'))['document']),1)
        before=hosted.read_json(self.state/hosted.IDENTITY)['publicId']
        self.document['journal'].append({'id':2,'kind':'lane.assigned','at':'2026-11-15T00:01:00Z','subject':'APP-2','lane':'one','summary':'New work'})
        self.document['state']['meta']['headline']='Reopened delivery';self.write_record(self.state)
        self.command('start','--resume')
        self.wait_for(lambda:any(body and b'Reopened delivery' in body for op,_,body in self.observed if op=='snapshots'))
        self.assertEqual(hosted.read_json(self.state/hosted.IDENTITY)['publicId'],before)
        self.assertFalse((self.state/hosted.TERMINAL).exists());self.assertEqual(hosted.archive_status(self.archive)['state'],'final-published')
    def test_generation_flip_in_spawn_window_creates_no_obsolete_owner(self):
        target=self.gen;alias=self.root/'current';alias.symlink_to(target)
        other=self.root/'new-generation';other.mkdir();children=[]
        arguments=SimpleNamespace(config='views.json',binding='binding.json',key='APP-1',generation=str(alias),
            generation_target=str(target),archive_root=str(self.archives),resume=False)
        inputs=views_common.Inputs(self.root,self.config,{},self.state)
        popen=subprocess.Popen
        def switch(command,**kwargs):
            alias.unlink();alias.symlink_to(other)
            child=popen(command,**kwargs);children.append(child);return child
        with mock.patch.object(hosted.subprocess,'Popen',side_effect=switch):
            with self.assertRaisesRegex(hosted.PublishError,'owner_start_failed'):hosted.start(arguments,inputs)
        self.assertEqual(len(children),1);children[0].wait(timeout=5)
        self.assertFalse((self.state/hosted.IDENTITY).exists());self.assertFalse(hosted.locked(self.state/hosted.LOCK))
    def test_recreated_record_path_does_not_keep_the_previous_owner_alive(self):
        self.command('start');prior=self.root/'previous-record'
        self.state.rename(prior);self.state.mkdir();self.write_record(self.state)
        OWNED.add(prior);self.addCleanup(OWNED.discard,prior);self.addCleanup(hosted.stop,prior)
        self.wait_for(lambda:not hosted.locked(prior/hosted.LOCK))
        self.assertFalse((self.state/hosted.IDENTITY).exists())
    def test_control_contention_retains_finished_archive_and_later_retry_uses_same_identity(self):
        self.command('start');self.wait_for(lambda:hosted.read_json(self.state/hosted.IDENTITY)['receipt'] is not None)
        hosted.stop(self.state)
        identity=hosted.read_json(self.state/hosted.IDENTITY)
        self.finished(notify=False)
        hosted.mirror_hosted(self.state,self.archive)
        c=SimpleNamespace(root=self.root,main=self.root,state=self.state,release=ROOT,delivery='APP-1',
            flow_raw={'cleanup':{'archive':str(self.archives)}},shelf=SimpleNamespace(json=lambda _:CFG))
        ready=self.root/'control-held'
        child=subprocess.Popen([sys.executable,'-c',
            'import fcntl,pathlib,sys,time;f=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX);pathlib.Path(sys.argv[2]).touch();time.sleep(60)',
            str(self.state/hosted.CONTROL),str(ready)],start_new_session=True)
        def cleanup():
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)
        self.addCleanup(cleanup);self.wait_for(ready.exists)
        live=flow.hosted_info(c)
        self.assertEqual(live['state'],'final-publication-pending')
        self.assertTrue(live['url'].endswith(identity['publicId']))
        self.assertEqual(live['receipt'],identity['receipt'])
        self.assertIn('final-publication-pending '+live['url'],flow.hosted_line(c))
        before=(self.archive/hosted.IDENTITY).read_bytes();began=time.monotonic()
        with mock.patch.dict(os.environ,self.environment):
            result=flow.finalize_hosted(c,self.archive.parent,{'id':1},seconds=.2)
        self.assertLess(time.monotonic()-began,2);self.assertEqual(result['state'],'final-publication-pending')
        self.assertEqual((self.archive/hosted.IDENTITY).read_bytes(),before)
        self.assertEqual(hosted.archive_status(self.archive)['state'],'final-publication-pending')
        cleanup();OWNED.add(self.archive);self.addCleanup(OWNED.discard,self.archive);self.addCleanup(hosted.stop,self.archive)
        with mock.patch.object(flow,'Config',return_value=c),mock.patch.object(flow,'repo',return_value=self.root),mock.patch.dict(os.environ,self.environment),contextlib.redirect_stdout(io.StringIO()):
            flow.hosted_retry(SimpleNamespace(archive=self.archive,resume=False))
        self.wait_archive_published()
        accepted=hosted.read_json(self.archive/hosted.IDENTITY)
        self.assertEqual(accepted['publicId'],identity['publicId'])
        self.assertGreater(accepted['receipt']['sequence'],identity['receipt']['sequence'])
    def test_active_owner_control_contention_receives_terminal_deadline_and_allows_same_id_retry(self):
        self.command('start');self.wait_for(lambda:hosted.read_json(self.state/hosted.IDENTITY)['receipt'] is not None)
        identity=hosted.read_json(self.state/hosted.IDENTITY);owner=hosted.read_json(self.state/hosted.OWNER)
        self.api.code='unauthorized';self.finished(notify=False);hosted.mirror_hosted(self.state,self.archive)
        c=SimpleNamespace(root=self.root,main=self.root,state=self.state,release=ROOT,delivery='APP-1',
            flow_raw={'cleanup':{'archive':str(self.archives)}},shelf=SimpleNamespace(json=lambda _:CFG))
        ready=self.root/'active-control-held'
        child=subprocess.Popen([sys.executable,'-c',
            'import fcntl,pathlib,sys,time;f=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX);pathlib.Path(sys.argv[2]).touch();time.sleep(60)',
            str(self.state/hosted.CONTROL),str(ready)],start_new_session=True)
        def cleanup():
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)
        self.addCleanup(cleanup);self.wait_for(ready.exists)
        before=(self.archive/hosted.IDENTITY).read_bytes();began=time.monotonic()
        with mock.patch.dict(os.environ,self.environment):result=flow.finalize_hosted(c,self.archive.parent,{'id':1},seconds=.2)
        self.assertLess(time.monotonic()-began,2);self.assertEqual(result['state'],'final-publication-pending')
        self.assertTrue(hosted.process_matches(owner));self.assertEqual((self.archive/hosted.IDENTITY).read_bytes(),before)
        intent=hosted.read_terminal(self.state,self.archives)
        self.assertIsNotNone(intent,'the existing owner must receive intent before CONTROL is free')
        self.assertLessEqual(intent['retryUntil']-intent['requestedAt'],600000)
        again=hosted.request_terminal(self.state,self.archive,self.archives,1,self.document,timeout=0)
        self.assertEqual(again['requestedAt'],intent['requestedAt'])
        self.assertEqual(again['retryUntil'],intent['retryUntil'])
        self.wait_for(lambda:hosted.read_json(self.state/hosted.STATUS).get('code')=='unauthorized')
        cleanup();self.clock.write_text('700')
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK))
        self.assertEqual(hosted.archive_status(self.archive)['state'],'final-publication-pending')
        self.assertEqual(hosted.read_json(self.archive/hosted.IDENTITY)['publicId'],identity['publicId'])
        self.clock.write_text('0');self.api.code=None
        OWNED.add(self.archive);self.addCleanup(OWNED.discard,self.archive);self.addCleanup(hosted.stop,self.archive)
        with mock.patch.object(flow,'Config',return_value=c),mock.patch.object(flow,'repo',return_value=self.root),mock.patch.dict(os.environ,self.environment),contextlib.redirect_stdout(io.StringIO()):
            flow.hosted_retry(SimpleNamespace(archive=self.archive,resume=False))
        self.wait_archive_published()
        self.assertEqual(hosted.read_json(self.archive/hosted.IDENTITY)['publicId'],identity['publicId'])
    def test_expired_terminal_self_exit_and_separate_fixture_cleanup_proof(self):
        self.environment.pop('FIXTURE_PUBLISH_TOKEN');self.command('start')
        intent=self.finished();intent.update(requestedAt=1,retryUntil=600001);hosted.atomic(self.state/hosted.TERMINAL,intent)
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK))
        self.assertEqual(hosted.archive_status(self.archive)['state'],'final-publication-pending')
        self.environment['FIXTURE_PUBLISH_TOKEN']=TOKEN
        self.command('start','--resume')
        renewed=hosted.read_json(self.state/hosted.TERMINAL)
        self.assertGreater(renewed['retryUntil'],int(time.time()*1000))
        self.wait_archive_published()
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK))
        # Keep the source intact: this independent cleanup path must stop the
        # owner even though neither production disappearance guard fires.
        self.document['journal'].append({'id':2,'kind':'lane.assigned','at':'2026-11-15T00:01:00Z',
                                        'subject':'APP-2','lane':'one','summary':'New work'})
        self.write_record(self.state);self.command('start')
        self.assertTrue(hosted.locked(self.state/hosted.LOCK));stop_owned()
        self.assertFalse(hosted.locked(self.state/hosted.LOCK));self.assertTrue((self.state/'state.json').exists())
    def test_terminal_deadline_cancels_hanging_collection_and_upload_processes(self):
        custom=self.root/'sitecustomize.py';original=custom.read_text()
        marker=self.root/'hanging-pid'
        for operation in ('collect','snapshots'):
            with self.subTest(operation=operation):
                custom.write_text(original+
                    'import time\n'+
                    (f'if "collect" in sys.argv:\n open({str(marker)!r},"w").write(str(os.getpid()))\n time.sleep(60)\n'
                     if operation=='collect' else
                     'request=Real.request\n'+
                     f'def hanging(self,method,url,*args,**kw):\n if url.endswith("/snapshots"):\n  open({str(marker)!r},"w").write(str(os.getpid()))\n  time.sleep(60)\n return request(self,method,url,*args,**kw)\nReal.request=hanging\n'))
                self.command('start');self.finished()
                self.wait_for(marker.exists)
                pid=int(marker.read_text());intent=hosted.read_json(self.state/hosted.TERMINAL)
                until=int(time.time()*1000)+500
                intent.update(requestedAt=until-600000,retryUntil=until);hosted.atomic(self.state/hosted.TERMINAL,intent)
                self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK),seconds=5)
                self.assertIsNone(hosted.process_started(pid));self.assertEqual(hosted.archive_status(self.archive)['state'],'final-publication-pending')
                marker.unlink();(self.state/hosted.TERMINAL).unlink()
    def test_close_success_delay_unavailable_are_bounded_and_lost_ack_archive_retry_keeps_identity(self):
        c=SimpleNamespace(root=self.root,main=self.root,state=self.state,release=ROOT,delivery='APP-1',
            flow_raw={'cleanup':{'archive':str(self.archives)}},shelf=SimpleNamespace(json=lambda _:CFG))
        # The receiver delays actual snapshot acknowledgements, rather than
        # replacing the close function or its polling loop.
        class Delayed(FakeAPI):
            def __call__(self,method,operation,body=None,headers=None):
                if operation=='snapshots':time.sleep(.3)
                return super().__call__(method,operation,body,headers)
        self.api=Delayed()
        self.finished()
        began=time.monotonic()
        with mock.patch.dict(os.environ,self.environment):
            result=flow.finalize_hosted(c,self.archive.parent,{'id':1},seconds=2)
        self.assertLess(time.monotonic()-began,2.5);self.assertEqual(result['state'],'final-published')
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK))
        # A second close with no available credential still records its intent
        # within the caller's remaining budget; its observer continues separately.
        for path in self.state.glob('hosted-*'):
            if path.is_file():path.unlink()
        self.api=FakeAPI();self.api.code='unauthorized';self.finished()
        began=time.monotonic()
        with mock.patch.dict(os.environ,self.environment):
            result=flow.finalize_hosted(c,self.archive.parent,{'id':1},seconds=.5)
        self.assertLess(time.monotonic()-began,1);self.assertEqual(result['state'],'final-publication-pending')
        stop_owned()
        for path in self.state.glob('hosted-*'):
            if path.is_file():path.unlink()
        self.api=FakeAPI();self.mode='lost';self.command('start');self.finished()
        self.wait_for(lambda:bool((hosted.read_json(self.state/hosted.IDENTITY,optional=True) or {}).get('pending')))
        stop_owned();hosted.mirror_hosted(self.state,self.archive)
        identity=hosted.read_json(self.archive/hosted.IDENTITY);pending=identity['pending']
        self.assertIsNotNone(pending)
        before=(self.archive/(hosted.PENDING_PREFIX+pending['digest']+'.json')).read_bytes()
        OWNED.add(self.archive);self.addCleanup(OWNED.discard,self.archive);self.addCleanup(hosted.stop,self.archive)
        with mock.patch.object(flow,'Config',return_value=c),mock.patch.object(flow,'repo',return_value=self.root),mock.patch.dict(os.environ,self.environment):
            with contextlib.redirect_stdout(io.StringIO()):flow.hosted_retry(SimpleNamespace(archive=self.archive,resume=False))
        self.wait_archive_published()
        after=hosted.read_json(self.archive/hosted.IDENTITY)
        self.assertEqual(identity['publicId'],after['publicId']);self.assertEqual(after['receipt']['digest'],hashlib.sha256(before).hexdigest())
        self.assertEqual(sum(op=='snapshots' for op,_,_ in self.observed[-4:]),1)

class InstalledLifecycleTests(unittest.TestCase):
    """Exercise actual commands against the existing installed delivery fixture."""
    run_quietly=True
    @classmethod
    def setUpClass(cls):
        from tests import test_run_delivery as delivery
        cls.installed=delivery.installed_release()
    def setUp(self):
        from tests import test_run_delivery as delivery
        from tests import test_run_delivery_supervisor as supervisor
        publisher_fixtures.ReceiverTests.setUp(self)
        self.delivery=delivery;self.supervisor=supervisor
        self.fx=fx=delivery.Fixture(self.installed.generation,self.installed.receipt)
        self.addCleanup(fx.close);self.addCleanup(delivery.stop_supervisors,fx.tmp)
        self.path=supervisor.supervised(fx)
        self.state=fx.tmp/'record';OWNED.add(self.state)
        self.addCleanup(OWNED.discard,self.state);self.addCleanup(hosted.stop,self.state)
        before={number:signal.getsignal(number) for number in (signal.SIGTERM,signal.SIGHUP)}
        for number,prior in before.items():
            def interrupted(signum,frame,prior=prior):
                stop_owned()
                if callable(prior):return prior(signum,frame)
                raise KeyboardInterrupt()
            signal.signal(number,interrupted)
        self.addCleanup(lambda:[signal.signal(number,prior) for number,prior in before.items()])
        self.shim=fx.tmp/'hosted-shim';self.shim.mkdir()
        self.environment_log=fx.tmp/'child-environment.jsonl'
        self.base_shim=(
            'import http.client,os,sys,json\nReal=http.client.HTTPConnection\n'
            f'def fixture(host,port=None,timeout=None,context=None):\n assert host=="deliveries.example.com"\n return Real("127.0.0.1",{self.server.server_port},timeout=timeout)\n'
            'http.client.HTTPSConnection=fixture\n'
            f'with open({str(self.environment_log)!r},"a") as out:out.write(json.dumps([sys.argv,"FIXTURE_PUBLISH_TOKEN" in os.environ,"RUN_DELIVERY_HOSTED_FD" in os.environ])+"\\n")\n')
        (self.shim/'sitecustomize.py').write_text(self.base_shim)
        fx.env.update(FIXTURE_PUBLISH_TOKEN=TOKEN,PYTHONPATH=str(self.shim))
        (fx.main/'.agents/delivery-views.json').write_text(json.dumps(CFG))
        self.commit('Enable hosted fixture')
    def commit(self,message):
        self.delivery.commit(self.fx.main,message)
        self.delivery.sh('git','push','-q','origin','feature/FX-1',cwd=self.fx.main)
    def command(self,*argv,supervised=False):
        environment=self.supervisor.running(self.fx) if supervised else self.fx.env
        code,out=self.fx.flow(*argv,cwd=self.fx.main,env=environment)
        self.assertEqual(code,0,out)
        return out
    def wait_for(self,predicate,seconds=15):
        return self.supervisor.wait_for(predicate,'installed hosted lifecycle',seconds)
    def wait_archive_published(self,archive):
        self.wait_for(lambda:flow.hosted_info(None,archive=archive).get('state')=='final-published')
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK) and not hosted.locked(archive/hosted.LOCK))
        self.assertEqual(hosted.archive_status(archive)['state'],'final-published')
    def state_command(self,*argv):
        self.delivery.sh(sys.executable,'-B',self.fx.release/'skills/orchestrate-work/scripts/delivery_state.py',
                         '--dir',self.state,*argv,cwd=self.fx.main,env=self.fx.env)
    def isolate_supervisor_start(self,command):
        # Inject a parent-only start failure. The detached supervisor must still
        # receive the private credential and start its own hosted observer.
        (self.shim/'sitecustomize.py').write_text(self.base_shim+
            'def isolate(frame,event,arg):\n'
            f' if event=="call" and frame.f_code.co_name=={command!r} and "--resume" in sys.argv:\n'
            '  frame.f_globals["start_hosted"]=lambda *a,**kw:{"state":"failed"}\n  sys.settrace(None)\n return isolate\nsys.settrace(isolate)\n')
    def test_real_init_resume_supervisor_stop_and_manual_close(self):
        out=self.command('lead-init','--no-supervisor')
        self.assertIn('hosted:',out)
        self.wait_for(lambda:hosted.locked(self.state/hosted.LOCK))
        public_id=hosted.read_json(self.state/hosted.IDENTITY)['publicId']
        self.wait_for(lambda:any(op=='snapshots' for op,_,_ in self.observed))
        pointer=self.fx.main/'.agent-skills/current';old_target=pointer.resolve()
        replacement=self.fx.tmp/'new-generation';replacement.mkdir();pointer.unlink();pointer.symlink_to(replacement)
        try:self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK))
        finally:pointer.unlink();pointer.symlink_to(old_target)
        self.isolate_supervisor_start('lead_run')
        self.command('lead-run','--resume',supervised=True)
        self.wait_for(lambda:hosted.locked(self.state/hosted.LOCK))
        self.wait_for(lambda:hosted.read_json(self.state/hosted.STATUS).get('state')=='current')
        self.assertEqual(hosted.read_json(self.state/hosted.IDENTITY)['publicId'],public_id)
        stop=self.state.parent/'flow-log/FX-1.supervisor.stop';stop.write_text(json.dumps({'why':'held'}))
        lock=stop.with_name('FX-1.supervisor.lock')
        self.wait_for(lambda:not flow.read_lock(lock)[0])
        self.assertTrue(hosted.locked(self.state/hosted.LOCK))
        self.state_command('journal','--kind','delivery.closed','--summary','Manual finished close')
        (self.state/'.hosted-unreferenced').write_text('active atomic staging body')
        self.command('lead-close')
        closed=json.loads((self.state.parent/'flow-log/FX-1.closed.json').read_text())
        archive=Path(closed['archive'])/self.state.name
        self.wait_archive_published(archive)
        self.assertEqual(hosted.archive_status(archive)['state'],'final-published')
        self.assertEqual(hosted.read_json(archive/hosted.IDENTITY)['publicId'],public_id)
        self.assertFalse((archive/'.hosted-unreferenced').exists())
        rows=[json.loads(line) for line in self.environment_log.read_text().splitlines()]
        detached=[(present,fd) for argv,present,fd in rows if 'lead-run' in argv and '--resume' not in argv]
        self.assertIn((False,True),detached)
        generic=[present for argv,present,_ in rows if any(word in argv for word in ('collect','--state-config'))]
        self.assertTrue(generic);self.assertFalse(any(generic))
        self.assertTrue(any(auth=='Bearer '+TOKEN for _,auth,_ in self.observed))
    def test_real_automatic_close_freezes_finished_record_and_pending_normal_resume(self):
        data=json.loads(self.path.read_text());data.update(stories={},lanes={},finish=[])
        self.path.write_text(json.dumps(data));self.commit('An empty completed delivery')
        # A real collector hangs across close. The finalizer must create its
        # durable request and let local shutdown finish within its own budget.
        marker=self.fx.tmp/'blocked-collector'
        (self.shim/'sitecustomize.py').write_text(self.base_shim+
            f'if "collect" in sys.argv:\n import time\n open({str(marker)!r},"w").write(str(os.getpid()))\n time.sleep(60)\n'+
            'def budget(frame,event,arg):\n if event=="call" and frame.f_code.co_name=="finalize_hosted":\n  frame.f_globals["HOSTED_CLOSE_SECONDS"]=1\n  sys.settrace(None)\n return budget\nsys.settrace(budget)\n')
        self.command('lead-init',supervised=True)
        self.wait_for(lambda:(self.state/hosted.TERMINAL).exists())
        intent=hosted.read_json(self.state/hosted.TERMINAL)
        self.assertEqual(hosted.finished_entry(hosted.record_document(self.state)),intent['entry'])
        self.assertTrue((self.fx.tmp/'archive/FX-1'/intent['source']/hosted.TERMINAL).exists())
        self.supervisor.wait_for(lambda:not flow.read_lock(self.state.parent/'flow-log/FX-1.supervisor.lock')[0],
                                 'bounded automatic close',15)
        self.wait_for(marker.exists)
        intent.update(requestedAt=1,retryUntil=600001,state='final-publication-pending')
        hosted.atomic(self.state/hosted.TERMINAL,intent)
        self.wait_for(lambda:not hosted.locked(self.state/hosted.LOCK))
        self.assertIsNone(hosted.process_started(int(marker.read_text())))
        (self.shim/'sitecustomize.py').write_text(self.base_shim)
        self.command('lead-run','--resume',supervised=True)
        self.wait_for(lambda:hosted.read_json(self.state/hosted.TERMINAL)['state']=='published')
        archived=self.fx.tmp/'archive/FX-1'/intent['source']
        self.wait_archive_published(archived)
        self.assertEqual(hosted.archive_status(archived)['state'],'final-published')

if __name__=='__main__':unittest.main()
