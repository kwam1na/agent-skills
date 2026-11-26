"""Portable publisher boundaries using a local receiver, never Cloudflare proof."""
from __future__ import annotations
import copy
import contextlib
import signal
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import threading
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
from unittest import mock
from tests.delivery_views_fixtures import render, views_common
from tests.quiet_git import quieten

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/delivery-views/scripts/hosted.py'
spec = importlib.util.spec_from_file_location('hosted', SCRIPT)
hosted = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosted)
TOKEN = 'SYNTHETIC_PUBLISHER_CANARY_' + 'a' * 30
CFG = {'spec':'delivery-views-config/1', 'hosted': {'origin':'https://deliveries.example.com',
       'repositoryId':'repo_A','tokenEnv':'FIXTURE_PUBLISH_TOKEN'}}
VALID = json.loads((ROOT/'tests/fixtures/delivery-views/hosted/valid-dissent-markup.json').read_text())

class FakeAPI:
    def __init__(self): self.session_value=None; self.calls=[]; self.lose=False; self.code=None
    def __call__(self, method, operation, body=None, headers=None):
        self.calls.append((method,operation,body,headers))
        if self.code: raise hosted.PublishError(self.code, transient=False)
        if operation=='status':
            if self.session_value is None: raise hosted.PublishError('not_found')
            return copy.deepcopy(self.session_value)
        if operation=='session':
            request=json.loads(body)
            if self.session_value is None:
                self.session_value={'protocolVersion':hosted.PROTOCOL,'deliveryKey':request['deliveryKey'],
                    'ownerId':request['ownerId'],'epoch':1,'sequence':0,'leaseUntil':10**15,'receipt':None}
            elif request['mode']=='resume':
                self.session_value.update(epoch=self.session_value['epoch']+1,sequence=0,leaseUntil=10**15)
            answer=copy.deepcopy(self.session_value)
        elif operation=='snapshots':
            payload=json.loads(body);epoch=int(headers['X-Writer-Epoch']);seq=int(headers['X-Snapshot-Sequence'])
            receipt={'protocolVersion':hosted.PROTOCOL,'rendererVersion':hosted.RENDERER,'epoch':epoch,'sequence':seq,
                'digest':hashlib.sha256(body).hexdigest(),'sourceFingerprint':payload['sourceFingerprint'],
                'publishedAt':1000+seq,'sourceConfirmedAt':1000+seq}
            self.session_value.update(sequence=seq,receipt=receipt)
            answer=receipt
        elif operation=='confirm':
            answer={'protocolVersion':hosted.PROTOCOL,'sourceConfirmedAt':2000,
                'leaseUntil':10**15,'receipt':self.session_value['receipt']}
        if self.lose: self.lose=False; raise hosted.PublishError('transport',transient=True)
        return copy.deepcopy(answer)

class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=self.root/'state';self.state.mkdir()
        self.config=views_common.validate_config(CFG);self.api=FakeAPI()
        self.publisher=hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api,secret_values=[TOKEN])
    def test_initial_publish_exact_bytes_and_fresh_confirmation(self):
        self.publisher.step(lambda:copy.deepcopy(VALID))
        snapshot=next(call for call in self.api.calls if call[1]=='snapshots')
        self.assertEqual(snapshot[3]['X-Snapshot-Digest'],hashlib.sha256(snapshot[2]).hexdigest())
        first=copy.deepcopy(self.publisher.identity)
        self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertEqual(self.api.calls[-1][1],'confirm')
        self.assertEqual(self.publisher.identity['publicId'],first['publicId'])
        encoded=''.join(p.read_text() for p in self.state.glob('*.json'))
        self.assertNotIn(TOKEN,encoded)
    def test_lost_ack_restart_reconciles_without_duplicate_or_false_freshness(self):
        self.publisher.step(lambda:copy.deepcopy(VALID))
        changed=copy.deepcopy(VALID);changed['sourceFingerprint']='b'*64
        self.api.lose=True
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:changed)
        restarted=hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api,secret_values=[TOKEN])
        def failed():raise hosted.PublishError('source_changed',transient=True)
        before=len([c for c in self.api.calls if c[1] in ('snapshots','confirm')])
        with self.assertRaises(hosted.PublishError):restarted.step(failed)
        after=len([c for c in self.api.calls if c[1] in ('snapshots','confirm')])
        self.assertEqual(before,after)
        self.assertIsNone(restarted.identity['pending'])
        self.assertEqual(restarted.identity['receipt']['sourceFingerprint'],'b'*64)
    def test_expiry_requires_explicit_resume_and_fencing_never_reacquires(self):
        self.publisher.step(lambda:copy.deepcopy(VALID));self.api.session_value['leaseUntil']=1
        with self.assertRaisesRegex(hosted.PublishError,'resume_required'):self.publisher.step(lambda:copy.deepcopy(VALID))
        self.publisher.step(lambda:copy.deepcopy(VALID),resume=True)
        self.assertEqual(self.publisher.identity['epoch'],2)
        self.api.session_value['ownerId']='f'*64
        before=len([c for c in self.api.calls if c[1]=='session'])
        for resume in (False,True):
            with self.assertRaisesRegex(hosted.PublishError,'writer_fenced'):self.publisher.step(lambda:copy.deepcopy(VALID),resume=resume)
        self.assertEqual(before,len([c for c in self.api.calls if c[1]=='session']))
    def test_corrupt_mismatched_sidecars_and_secret_payloads_never_overwrite(self):
        self.publisher.step(lambda:copy.deepcopy(VALID));saved=(self.state/hosted.IDENTITY).read_bytes()
        with self.assertRaises(hosted.PublishError):hosted.Publisher(self.state,self.config['hosted'],'OTHER',self.api)
        self.assertEqual((self.state/hosted.IDENTITY).read_bytes(),saved)
        bad=copy.deepcopy(VALID);bad['view']['title']=TOKEN
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:bad)
        self.assertEqual((self.state/hosted.IDENTITY).read_bytes(),saved)
        (self.state/hosted.IDENTITY).write_text('{}')
        with self.assertRaisesRegex(hosted.PublishError,'identity_corrupt'):hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api)
        self.assertEqual((self.state/hosted.IDENTITY).read_text(),'{}')
        for raw in ('null','[]','true','0','""'):
            (self.state/hosted.IDENTITY).write_text(raw)
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(hosted.PublishError,'identity_corrupt'):
                    hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api)
                self.assertEqual((self.state/hosted.IDENTITY).read_text(),raw)
    def test_scheduler_bounded_debounce_backoff_and_one_inflight(self):
        schedule=hosted.Schedule(0)
        for at in range(11):schedule.changed(at)
        self.assertTrue(schedule.due(10))
        self.assertFalse(schedule.due(9))
        schedule.completed(10)
        self.assertFalse(schedule.due(39));self.assertTrue(schedule.due(40))
        delays=[hosted.retry_delay(n,lambda:0.5) for n in range(10)]
        self.assertEqual(delays[0],2);self.assertLessEqual(max(delays),60)
        self.assertGreaterEqual(hosted.retry_delay(0,lambda:0),2)
    def test_lost_acquisition_and_explicit_resume_acknowledgements_reconcile(self):
        self.api.lose=True
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:copy.deepcopy(VALID))
        public_id=self.publisher.identity['publicId']
        self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertEqual(self.publisher.identity['publicId'],public_id)
        self.api.session_value['leaseUntil']=1;self.api.lose=True
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:copy.deepcopy(VALID),resume=True)
        restarted=hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api)
        restarted.step(lambda:copy.deepcopy(VALID))
        self.assertEqual(restarted.identity['epoch'],2)
        self.assertEqual(len([c for c in self.api.calls if c[1]=='session']),2)
    def test_malformed_identity_receipts_and_pending_bytes_are_not_adopted(self):
        self.publisher.step(lambda:copy.deepcopy(VALID))
        saved=(self.state/hosted.IDENTITY).read_bytes()
        for field,value in [('sessionRequest','malformed'),('epoch',None),('unexpected',TOKEN),('receipt',{'digest':'a'*64})]:
            bad=json.loads(saved);bad[field]=value;hosted.atomic(self.state/hosted.IDENTITY,bad)
            before=(self.state/hosted.IDENTITY).read_bytes()
            with self.subTest(field=field):
                with self.assertRaises(hosted.PublishError):hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api)
                self.assertEqual((self.state/hosted.IDENTITY).read_bytes(),before)
        (self.state/hosted.IDENTITY).write_bytes(saved)
        bad=copy.deepcopy(self.api.session_value);bad['receipt']['sequence']=999
        self.api.session_value=bad
        with self.assertRaisesRegex(hosted.PublishError,'session_invalid'):self.publisher.step(lambda:copy.deepcopy(VALID))

    def test_interrupted_pending_replacement_recovers_old_pointer_and_identity(self):
        original_api=self.api
        def reject(method,operation,body=None,headers=None):
            if operation=='snapshots':raise hosted.PublishError('temporarily_unavailable',transient=True)
            return original_api(method,operation,body,headers)
        self.publisher.api=reject
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:copy.deepcopy(VALID))
        old=(self.state/hosted.IDENTITY).read_bytes();public_id=self.publisher.identity['publicId']
        changed=copy.deepcopy(VALID);changed['sourceFingerprint']='c'*64
        original_atomic=hosted.atomic
        def interrupted(path,value):
            if path.name==hosted.IDENTITY and value.get('pending',{}).get('digest')==hashlib.sha256(hosted.public.encode(changed)).hexdigest():
                raise OSError('fixture interruption between pending body and identity pointer')
            return original_atomic(path,value)
        with mock.patch.object(hosted,'atomic',side_effect=interrupted):
            with self.assertRaises(OSError):self.publisher.step(lambda:changed)
        self.assertEqual((self.state/hosted.IDENTITY).read_bytes(),old)
        restarted=hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api)
        restarted.step(lambda:changed)
        self.assertEqual(restarted.identity['publicId'],public_id)
        self.assertEqual(restarted.identity['receipt']['sourceFingerprint'],'c'*64)

    def test_otherwise_valid_pending_body_corruption_is_rejected_before_transport(self):
        self.api.lose=True
        # First ambiguity occurs during acquisition; finish acquisition, then lose
        # the snapshot acknowledgement to leave a real persisted pending body.
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:copy.deepcopy(VALID))
        self.api.lose=True
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:copy.deepcopy(VALID))
        identity=(self.state/hosted.IDENTITY).read_bytes()
        pending=self.publisher.pending_path(self.publisher.identity['pending']['digest'])
        hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api)
        payload=json.loads(pending.read_bytes());payload['view']['title']='Otherwise valid changed title'
        self.assertEqual(payload['sourceFingerprint'],VALID['sourceFingerprint']);hosted.public.validate(payload)
        pending.write_bytes(hosted.public.encode(payload));tampered=pending.read_bytes();calls=len(self.api.calls)
        with self.assertRaisesRegex(hosted.PublishError,'identity_corrupt'):
            hosted.Publisher(self.state,self.config['hosted'],'APP-1',self.api)
        self.assertEqual(len(self.api.calls),calls);self.assertEqual((self.state/hosted.IDENTITY).read_bytes(),identity)
        self.assertEqual(pending.read_bytes(),tampered)

    def test_disappearance_after_status_or_collection_prevents_later_mutations(self):
        live=[True];calls=[]
        def api(method,operation,body=None,headers=None):
            calls.append(operation)
            if operation=='status':live[0]=False;raise hosted.PublishError('not_found')
            return self.api(method,operation,body,headers)
        self.publisher.api=api;self.publisher.alive=lambda:live[0]
        with self.assertRaisesRegex(hosted.PublishError,'source_missing'):self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertEqual(calls,['status'])
        live[0]=True;self.publisher.api=self.api
        def disappear():live[0]=False;return copy.deepcopy(VALID)
        with self.assertRaisesRegex(hosted.PublishError,'source_missing'):self.publisher.step(disappear)
        self.assertFalse(any(call[1] in ('snapshots','confirm') for call in self.api.calls))

    def test_conflicting_surviving_request_reconciles_and_allocates_a_new_sequence(self):
        collided=[False]
        def race(method,operation,body=None,headers=None):
            if operation=='snapshots' and not collided[0]:
                collided[0]=True
                older=json.loads(body);older['sourceFingerprint']='e'*64
                older_bytes=hosted.public.encode(older)
                self.api(method,operation,older_bytes,{**headers,'X-Snapshot-Digest':hashlib.sha256(older_bytes).hexdigest()})
                raise hosted.PublishError('sequence_conflict')
            return self.api(method,operation,body,headers)
        self.publisher.api=race
        with self.assertRaisesRegex(hosted.PublishError,'sequence_reconcile') as error:self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertTrue(error.exception.transient)
        self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertEqual(self.publisher.identity['sequence'],2)
        self.assertEqual(self.publisher.identity['receipt']['sourceFingerprint'],VALID['sourceFingerprint'])
        self.assertEqual(len([call for call in self.api.calls if call[1]=='session']),1)

    def test_a_refused_cut_names_the_projection_code_in_the_retried_status(self):
        def refuse():raise hosted.public.ProjectionError('snapshot.credential_field')
        with self.assertRaisesRegex(hosted.PublishError,'^snapshot.credential_field$') as caught:self.publisher.step(refuse)
        self.assertTrue(caught.exception.transient)
        bad=copy.deepcopy(VALID);bad['document']['state']['meta']['token']='a credential'
        with self.assertRaisesRegex(hosted.PublishError,'^snapshot.credential_field$'):self.publisher.step(lambda:bad)
        # A code the projection does not define, and every other unavailable source, stays generic.
        for error in (hosted.public.ProjectionError('/Users/operator/private'),OSError('x')):
            def fail(error=error):raise error
            with self.subTest(error=error),self.assertRaisesRegex(hosted.PublishError,'^source_unavailable$'):
                self.publisher.step(fail)
        args=SimpleNamespace(config='views.json',binding='binding.json',state_dir=str(self.state),key='APP-1')
        inputs=views_common.Inputs(self.root,self.config,{},self.state)
        for answer,code in (({'refused':'snapshot.credential_field'},'^snapshot.credential_field$'),
                            ({'refused':'TOKEN-shaped words'},'^source_unavailable$'),({'refused':7},'^source_unavailable$')):
            with self.subTest(answer=answer),mock.patch.object(hosted.views,'run_json',return_value=answer), \
                 self.assertRaisesRegex(hosted.PublishError,code):hosted.child_collect(args,inputs)
        hosted.atomic(self.state/hosted.STATUS,{'spec':'hosted-delivery-status/1','state':'retrying','code':'snapshot.credential_field'})
        self.assertEqual(hosted.status(self.state,self.config['hosted'],'APP-1')['code'],'snapshot.credential_field')
        # The collect child answers a refusal with the code alone.
        with mock.patch.object(hosted,'load_inputs',return_value=SimpleNamespace(state_directory=self.state,config=self.config)), \
             mock.patch.object(hosted,'collect',side_effect=hosted.public.ProjectionError('snapshot.credential_field')),mock.patch('builtins.print') as printed:
            self.assertEqual(hosted.main(['collect','--config','views.json','--binding','binding.json',
                                          '--state-dir',str(self.state),'--key','APP-1']),0)
        self.assertEqual(json.loads(printed.call_args.args[0]),{'refused':'snapshot.credential_field'})
class ConfigTests(unittest.TestCase):
    def test_explicit_origins_and_environment_names_fail_closed(self):
        self.assertIsNone(views_common.validate_config({'spec':'delivery-views-config/1'})['hosted'])
        self.assertEqual(views_common.validate_config(CFG)['hosted'],CFG['hosted'])
        for origin in ['http://example.com','https://user:pass@example.com','https://example.com/path',
                       'https://example.com?x=1','https://example.com#x','https://example.com?','https://example.com#','https://example.com\\@evil.com',
                       'https://example.com\n','https://{host}','https://127.0.0.1:443']:
            bad=copy.deepcopy(CFG);bad['hosted']['origin']=origin
            with self.subTest(origin=origin):
                with self.assertRaises(views_common.ConfigError):views_common.validate_config(bad)
        for field,value in [('repositoryId','../other'),('tokenEnv','TOKEN=value'),('unknown',True)]:
            bad=copy.deepcopy(CFG);bad['hosted'][field]=value
            with self.assertRaises(views_common.ConfigError):views_common.validate_config(bad)


class ReceiverTests(unittest.TestCase):
    """Real loopback HTTP exercises exact transport bytes and redirect handling."""
    run_quietly=True
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name);self.api=FakeAPI();self.mode='normal';self.observed=[]
        fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.handle_api()
            def do_POST(self):self.handle_api()
            def handle_api(self):
                body=self.rfile.read(int(self.headers.get('Content-Length',0))) or None
                operation=self.path.rsplit('/',1)[-1];fixture.observed.append((operation,self.headers.get('Authorization'),body))
                if fixture.mode=='redirect':
                    self.send_response(302);self.send_header('Location',fixture.origin+'/leak');self.end_headers();return
                if fixture.mode=='oversize':answer={'padding':'x'*(hosted.MAX_RESPONSE+1)}
                else:
                    if operation in ('snapshots','confirm'):
                        if self.headers.get('X-Renderer-Version')!='delivery-line-map/3':
                            self.send_response(422);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"error":"unsupported_renderer"}');return
                        if operation=='snapshots' and self.headers.get('X-Snapshot-Digest')!=hashlib.sha256(body).hexdigest():
                            self.send_error(422);return
                    try:answer=fixture.api(self.command,operation,body,self.headers)
                    except hosted.PublishError as error:
                        self.send_response(401 if error.code=='unauthorized' else 404)
                        self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'error':error.code}).encode());return
                    if isinstance(answer,dict) and 'protocolVersion' in answer:
                        answer['protocolVersion']='hosted-delivery-api/1'
                        if 'rendererVersion' in answer:answer['rendererVersion']='delivery-line-map/3'
                        if answer.get('receipt'):
                            answer['receipt']['protocolVersion']='hosted-delivery-api/1';answer['receipt']['rendererVersion']='delivery-line-map/3'
                if fixture.mode=='lost' and operation=='snapshots':
                    fixture.mode='normal';self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(answer).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        config=views_common.validate_config(CFG)['hosted']
        self.publisher=hosted.Publisher(self.state,config,'APP-1',secret_values=[TOKEN])
        self.transport=hosted.Transport(self.origin,'repo_A',self.publisher.identity['publicId'],TOKEN,allow_loopback=True)
        self.publisher.api=self.transport
    def test_actual_lost_response_reconciles_and_confirmation_is_fresh_collection_only(self):
        self.mode='lost'
        with self.assertRaises(hosted.PublishError):self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertIsNotNone(self.publisher.identity['pending'])
        def changed():raise hosted.PublishError('source_changed',transient=True)
        with self.assertRaises(hosted.PublishError):self.publisher.step(changed)
        self.assertIsNone(self.publisher.identity['pending'])
        self.assertEqual([op for op,_,_ in self.observed].count('snapshots'),1)
        self.assertNotIn('confirm',[op for op,_,_ in self.observed])
        self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertEqual(self.observed[-1][0],'confirm')
        for _,auth,body in self.observed:
            self.assertEqual(auth,'Bearer '+TOKEN)
            self.assertNotIn(TOKEN,repr(body))
    def test_redirect_oversize_and_permanent_errors_never_echo_or_forward(self):
        self.mode='redirect'
        with self.assertRaisesRegex(hosted.PublishError,'redirect_refused'):self.transport('GET','status')
        self.assertEqual(len(self.observed),1)
        self.mode='oversize'
        with self.assertRaisesRegex(hosted.PublishError,'response_oversize'):self.transport('GET','status')
        self.mode='normal';self.api.code='unauthorized'
        with self.assertRaises(hosted.PublishError) as error:self.transport('GET','status')
        self.assertEqual(str(error.exception),'unauthorized');self.assertFalse(error.exception.transient)
        with self.assertRaises(hosted.PublishError):hosted.Transport(self.origin,'repo_A','a'*64,TOKEN)

    def test_actual_request_cli_child_reaches_receiver_with_literal_protocol(self):
        (self.state/'.git').mkdir();(self.state/'views.json').write_text(json.dumps(CFG))
        (self.state/'binding.json').write_text(json.dumps({'state':{'directory':'.'}}))
        # Substitute only the socket connection inside the real request child.
        # Production origin validation, dispatch, headers and receipt parsing all
        # execute unchanged; no HTTP production-config exception is introduced.
        (self.state/'sitecustomize.py').write_text(
            'import http.client\nReal=http.client.HTTPConnection\n'
            f'def fixture(host,port=None,timeout=None,context=None):\n assert host=="deliveries.example.com"\n return Real("127.0.0.1",{self.server.server_port},timeout=timeout)\n'
            'http.client.HTTPSConnection=fixture\n')
        args=SimpleNamespace(config='views.json',binding='binding.json',key='APP-1')
        inputs=views_common.Inputs(self.state,views_common.validate_config(CFG),{'state':{'directory':'.'}},self.state)
        self.publisher.api=hosted.child_transport(args,inputs)
        with mock.patch.dict(os.environ,{'FIXTURE_PUBLISH_TOKEN':TOKEN,'PYTHONPATH':str(self.state)}):
            result=self.publisher.step(lambda:copy.deepcopy(VALID))
        self.assertEqual(result['receipt']['protocolVersion'],'hosted-delivery-api/1')
        self.assertEqual(result['receipt']['rendererVersion'],'delivery-line-map/3')
        self.assertEqual(result['receipt']['digest'],hashlib.sha256(hosted.public.encode(VALID)).hexdigest())
        self.assertEqual([call[0] for call in self.observed],['status','session','snapshots'])
        self.assertEqual(hosted.CHILDREN,set())


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.state=self.root/'state';self.state.mkdir()
        self.config=views_common.validate_config(CFG)
        self.document=render.review_state.initial_state('Portable delivery')
        self.document['planned']=[{'subject':'APP-1','title':'Work','lane':None,'stage':'building'}]
        self.write_state();(self.state/'journal.jsonl').write_text('')
        self.inputs=views_common.Inputs(self.root,self.config,{'state':{'publishers':[{'argv':['should-never-run']}] }},self.state)
    def write_state(self):
        render.review_state._write_atomically(self.state/'state.json',json.dumps(self.document))
    def test_continuous_fixture_writes_are_valid_at_concurrent_read_boundaries(self):
        """Read while a writer is open and before each rename, without a timing race."""
        target=self.state/'state.json';observed=[];errors=[]
        original_write=Path.write_text;original_replace=os.replace
        def read_concurrently():
            def read():
                try:observed.append(hosted.record_document(self.state)['state']['meta']['headline'])
                except Exception as error:errors.append(error)
            thread=threading.Thread(target=read);thread.start();thread.join(2)
            self.assertFalse(thread.is_alive())
        def write(path,text,*args,**kwargs):
            if path==target:
                with path.open('w') as stream:
                    read_concurrently()
                    return stream.write(text)
            return original_write(path,text,*args,**kwargs)
        def replace(source,destination,*args,**kwargs):
            if Path(destination)==target:read_concurrently()
            return original_replace(source,destination,*args,**kwargs)
        with mock.patch.object(Path,'write_text',write),mock.patch.object(os,'replace',replace):
            for number in range(3):
                self.document['meta']['headline']=f'Concurrent update {number}';self.write_state()
                read_concurrently()
        self.assertEqual(errors,[])
        self.assertEqual(observed,['Portable delivery','Concurrent update 0','Concurrent update 0',
                                   'Concurrent update 1','Concurrent update 1','Concurrent update 2'])
        target.write_text('{')
        with self.assertRaisesRegex(hosted.PublishError,'source_invalid'):hosted.record_document(self.state)
    def test_full_source_cut_observes_optional_inputs_and_review_artifact_content(self):
        first=hosted.collect(self.inputs)
        self.document['meta']['headline']='State changed';self.write_state()
        state_change=hosted.collect(self.inputs);self.assertNotEqual(first['sourceFingerprint'],state_change['sourceFingerprint'])
        plan=json.loads((ROOT/'tests/fixtures/delivery-views/hosted/valid-plan-review.json').read_text())['plan']['plan']
        (self.state/'plan.json').write_text(json.dumps(plan))
        planned=hosted.collect(self.inputs);self.assertIn('plan',planned['plan'])
        self.assertNotEqual(state_change['sourceFingerprint'],planned['sourceFingerprint'])
        needs={'spec':'delivery-needs-you/1','running':False,'at':'2026-11-15T00:00:00Z','items':[]}
        (self.state/'needs-you.json').write_text(json.dumps(needs))
        needed=hosted.collect(self.inputs);self.assertEqual(needed['needs']['running'],False)
        self.assertNotEqual(planned['sourceFingerprint'],needed['sourceFingerprint'])
        event={'id':1,'at':'2026-11-15T00:00:00Z','kind':'review.recorded','lane':None,'subject':'APP-1',
               'data':{'digest':'a'*64,'candidateRef':'b'*40}}
        (self.state/'journal.jsonl').write_text(json.dumps(event)+'\n')
        missing=hosted.collect(self.inputs)
        folder=self.state/'review-artifacts';folder.mkdir();(folder/('a'*64+'.json')).write_text('unavailable artifact')
        artifact=hosted.collect(self.inputs)
        self.assertNotEqual(missing['sourceFingerprint'],artifact['sourceFingerprint'])
        self.assertEqual(artifact['reviews']['APP-1'],{'unavailable':True})
        # The files view's reads are part of the cut, as the local page embeds them, and a new read is a new cut.
        changes={'spec':'delivery-lane-changes/1','at':'2026-11-15T00:00:00Z','base':'b'*40,'lanes':{},'files':[]}
        (self.state/render.LANE_CHANGES_FILE).write_text(json.dumps(changes))
        filed=hosted.collect(self.inputs)
        self.assertEqual(filed['files'],render.read_lane_changes_block(self.state))
        self.assertEqual(filed['files']['at'],'2026-11-15T00:00:00Z')
        self.assertNotEqual(artifact['sourceFingerprint'],filed['sourceFingerprint'])
        (self.state/render.LANE_CHANGES_FILE).write_text(json.dumps({**changes,'at':'2026-11-15T00:01:00Z'}))
        refiled=hosted.collect(self.inputs)
        self.assertNotEqual(filed['sourceFingerprint'],refiled['sourceFingerprint'])
    def test_the_cut_carries_the_forges_checks_on_the_change_request_as_the_local_page_reads_them(self):
        self.document['meta']['integration']={'base':'integrate/app','target':'main','remote':'origin'};self.write_state()
        request='https://github.com/acme/app/pull/7'
        (self.state/'journal.jsonl').write_text(json.dumps({'id':1,'at':'2026-11-15T00:00:00Z','kind':'integration.requested',
            'lane':None,'subject':'APP','data':{'changeRequest':request,'head':'a'*40}})+'\n')
        checks={'pending':0,'failing':[{'name':'lint','onTarget':True}]}
        (self.state/'snapshots').mkdir()
        (self.state/render.FORGE_FILE).write_text(json.dumps({'items':[{'changeRequest':request,'checks':checks}]}))
        value=hosted.collect(self.inputs)
        self.assertEqual(value['integration'],{'checks':checks})
        self.assertEqual(value['integration'],render.read_forge_block(self.state,hosted.record_document(self.state)))
        # A new forge read is a new cut.
        (self.state/render.FORGE_FILE).write_text(json.dumps({'items':[{'changeRequest':request,'checks':{'pending':1,'failing':[]}}]}))
        again=hosted.collect(self.inputs)
        self.assertEqual(again['integration'],{'checks':{'pending':1,'failing':[]}})
        self.assertNotEqual(value['sourceFingerprint'],again['sourceFingerprint'])
    def test_changed_cut_symlink_and_different_run_response_are_refused(self):
        original=render.read_needs_block
        def mutate(directory):
            self.document['meta']['headline']='Changed during capture';self.write_state();return original(directory)
        with mock.patch.object(hosted.render,'read_needs_block',side_effect=mutate):
            with self.assertRaises(hosted.public.SourceChanged):hosted.collect(self.inputs)
        # A supervisor pass that rewrites the files view's reads during a cut changes the cut too.
        lane_changes=self.state/render.LANE_CHANGES_FILE
        lane_changes.write_text(json.dumps({'spec':'delivery-lane-changes/1','at':'2026-11-15T00:00:00Z','lanes':{}}))
        read_files=render.read_lane_changes_block
        def rewrite(directory):
            lane_changes.write_text(json.dumps({'spec':'delivery-lane-changes/1','at':'2026-11-15T00:02:00Z','lanes':{}}))
            return read_files(directory)
        with mock.patch.object(hosted.render,'read_lane_changes_block',side_effect=rewrite):
            with self.assertRaises(hosted.public.SourceChanged):hosted.collect(self.inputs)
        (self.state/'plan.json').symlink_to(self.root.parent/'outside')
        with self.assertRaisesRegex(hosted.PublishError,'source_path'):hosted.collect(self.inputs)
    def test_run_children_are_credential_scrubbed_and_all_selected_inputs_rechecked(self):
        self.document['planned'][0]['runId']='run-1';self.write_state()
        self.config['runs']={'argv':[sys.executable,'-c','unused'],'timeoutSeconds':3}
        calls=[]
        view={'spec':'run-view/1','sections':[]}
        def answer(argv,**kwargs):
            calls.append((argv,kwargs))
            return {'runs':[{'runId':'run-1'}]} if 'list' in argv else view
        with mock.patch.object(hosted.views,'run_json',side_effect=answer):value=hosted.collect(self.inputs)
        self.assertEqual(len(calls),4)
        self.assertTrue(all('FIXTURE_PUBLISH_TOKEN' in kwargs['scrub'] for _,kwargs in calls))
        self.assertIn('run-1',value['runs'])
        count=0
        def changing(argv,**kwargs):
            nonlocal count
            count+=1
            return {'runs':[{'runId':'run-1','bytes':count}]} if 'list' in argv else view
        with mock.patch.object(hosted.views,'run_json',side_effect=changing):
            with self.assertRaisesRegex(hosted.PublishError,'source_changed'):hosted.collect(self.inputs)
        version=['before']
        def stable_until_optional(argv,**kwargs):
            return {'runs':[{'runId':'run-1'}]} if 'list' in argv else {**view,'version':version[0]}
        def final_optional(directory):version[0]='after';return None
        with mock.patch.object(hosted.views,'run_json',side_effect=stable_until_optional), \
             mock.patch.object(hosted.render,'read_needs_block',side_effect=final_optional):
            with self.assertRaisesRegex(hosted.PublishError,'source_changed'):hosted.collect(self.inputs)

    def test_a_shared_records_cut_reads_no_seat(self):
        """The hosted page has no seat: collecting a shared record never asks which seat this checkout holds, and its cut
        names none, while the shared list's stamps and wordings go with it."""
        self.document['meta']['collab']={'key':'APP-9','peers':{},'seats':{'s-aaaaaaaaaaaa':{'label':'Ama','admitted':True,'approver':True},
                                                               's-bbbbbbbbbbbb':{'label':'Ben','admitted':True,'approver':True}}}
        self.write_state()
        said={'headline':'Ben marks the draft ready','why':'w','clears':'c','who':'Ben'}
        (self.state/'needs-you.json').write_text(json.dumps({'spec':'delivery-needs-you/1','running':False,'at':'2026-11-15T00:00:00Z',
            'summaryAll':'1 thing needs Ben.','items':[{'holding':False,'source':'acceptance','text':'t','clears':'c','kind':'drafts',
            'who':'operator','scope':'queue','seat':'s-bbbbbbbbbbbb','said':said,'saidAll':said,'saidRole':said}]}))
        with mock.patch.object(hosted.render.review_state,'local_seat',return_value='s-bbbbbbbbbbbb') as local:
            cut=hosted.collect(self.inputs)
        local.assert_not_called()
        self.assertNotIn('viewer',cut)
        self.assertEqual(cut['needs']['summaryAll'],'1 thing needs Ben.')
        self.assertEqual((cut['needs']['items'][0]['scope'],cut['needs']['items'][0]['seat']),('queue','s-bbbbbbbbbbbb'))

    def prepare_watch(self):
        (self.root/'.git').mkdir();(self.root/'views.json').write_text(json.dumps(CFG))
        (self.root/'binding.json').write_text(json.dumps({'state':{'directory':'state','publishers':[{'argv':['must-not-run']}]}}))
        gen=self.root/'generation';gen.mkdir()
        return SimpleNamespace(config='views.json',binding='binding.json',state_dir=str(self.state),key='APP-1',generation=str(gen),resume=False)

    def test_automatic_observer_publishes_state_plan_needs_and_artifact_without_tracker(self):
        arguments=self.prepare_watch();api=FakeAPI();clock=[0.0];stage=[0];published=[]
        event={'id':1,'at':'2026-11-15T00:00:00Z','kind':'review.recorded','lane':None,'subject':'APP-1',
               'data':{'digest':'a'*64,'candidateRef':'b'*40}}
        (self.state/'journal.jsonl').write_text(json.dumps(event)+'\n')
        plan=json.loads((ROOT/'tests/fixtures/delivery-views/hosted/valid-plan-review.json').read_text())['plan']['plan']
        def sleep(seconds):
            clock[0]+=seconds
            snapshots=[call for call in api.calls if call[1]=='snapshots']
            if len(snapshots)>stage[0]:
                published.append(json.loads(snapshots[-1][2])['sourceFingerprint']);stage[0]+=1
                if stage[0]==1:self.document['meta']['headline']='Automatic state';self.write_state()
                elif stage[0]==2:(self.state/'plan.json').write_text(json.dumps(plan))
                elif stage[0]==3:(self.state/'needs-you.json').write_text(json.dumps({'spec':'delivery-needs-you/1','running':False,'at':'2026-11-15T00:00:00Z','items':[]}))
                elif stage[0]==4:
                    (self.state/'review-artifacts').mkdir();(self.state/'review-artifacts'/('a'*64+'.json')).write_text('new artifact only')
                else:(self.state/'state.json').unlink()
            if clock[0]>30:self.fail('observer did not publish each source change')
        with mock.patch.object(hosted,'load_inputs',return_value=self.inputs),mock.patch.object(hosted,'Transport'), \
             mock.patch.object(hosted,'child_transport',return_value=api),mock.patch.object(hosted,'child_collect',side_effect=lambda a,i:hosted.collect(i)), \
             mock.patch.object(hosted.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(hosted.time,'sleep',side_effect=sleep), \
             mock.patch.object(hosted.signal,'signal'),mock.patch.dict(os.environ,{'FIXTURE_PUBLISH_TOKEN':TOKEN}):
            hosted.watch(arguments,self.inputs)
        self.assertEqual(len(published),5);self.assertEqual(len(set(published)),5)
        self.assertTrue(all(call[1] in ('status','session','snapshots','confirm') for call in api.calls))

    def test_continuous_changes_publish_within_maximum_deferral(self):
        arguments=self.prepare_watch();api=FakeAPI();clock=[0.0];published=[]
        def collect(a,i):published.append(clock[0]);return hosted.collect(i)
        def sleep(seconds):
            clock[0]+=seconds;self.document['meta']['headline']=f'Change {clock[0]}';self.write_state()
            if len(published)==2:(self.state/'state.json').unlink()
            if clock[0]>25:self.fail('continuous changes starved publication')
        with mock.patch.object(hosted,'load_inputs',return_value=self.inputs),mock.patch.object(hosted,'Transport'), \
             mock.patch.object(hosted,'child_transport',return_value=api),mock.patch.object(hosted,'child_collect',side_effect=collect), \
             mock.patch.object(hosted.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(hosted.time,'sleep',side_effect=sleep), \
             mock.patch.object(hosted.signal,'signal'),mock.patch.dict(os.environ,{'FIXTURE_PUBLISH_TOKEN':TOKEN}):
            hosted.watch(arguments,self.inputs)
        self.assertLessEqual(published[0],10.25);self.assertLessEqual(published[1]-published[0],10.25)

    def test_permanent_remote_error_halts_ordinary_network_retries(self):
        arguments=self.prepare_watch();api=FakeAPI();api.code='unauthorized';clock=[0.0]
        def sleep(seconds):
            clock[0]+=seconds
            if clock[0]>65:(self.state/'state.json').unlink()
        with mock.patch.object(hosted,'load_inputs',return_value=self.inputs),mock.patch.object(hosted,'Transport'), \
             mock.patch.object(hosted,'child_transport',return_value=api),mock.patch.object(hosted,'child_collect',side_effect=lambda a,i:hosted.collect(i)), \
             mock.patch.object(hosted.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(hosted.time,'sleep',side_effect=sleep), \
             mock.patch.object(hosted.signal,'signal'),mock.patch.dict(os.environ,{'FIXTURE_PUBLISH_TOKEN':TOKEN}):
            hosted.watch(arguments,self.inputs)
        self.assertEqual(len(api.calls),1)
        self.assertEqual(hosted.read_json(self.state/hosted.STATUS)['code'],'unauthorized')

    def test_real_collection_child_and_run_commands_receive_no_configured_credentials(self):
        arguments=self.prepare_watch();self.document['planned'][0]['runId']='run-1';self.write_state()
        raw=copy.deepcopy(CFG);raw['scrubEnv']=['OTHER_CREDENTIAL']
        raw['runs']={'argv':[sys.executable,'-c',"import os,json; assert 'FIXTURE_PUBLISH_TOKEN' not in os.environ; assert 'OTHER_CREDENTIAL' not in os.environ; print(json.dumps(dict(runs=[])))"],'timeoutSeconds':3}
        (self.root/'views.json').write_text(json.dumps(raw))
        (self.root/'sitecustomize.py').write_text("import os\nif 'FIXTURE_PUBLISH_TOKEN' in os.environ or 'OTHER_CREDENTIAL' in os.environ: os._exit(99)\n")
        inputs=views_common.Inputs(self.root,views_common.validate_config(raw),{'state':{'directory':'state'}},self.state)
        with mock.patch.dict(os.environ,{'FIXTURE_PUBLISH_TOKEN':TOKEN,'OTHER_CREDENTIAL':'SECOND_CANARY','PYTHONPATH':str(self.root)}):
            value=hosted.child_collect(arguments,inputs)
        self.assertEqual(value['runs']['run-1'],{'runId':'run-1','unavailable':True})
        self.assertNotIn(TOKEN,json.dumps(value));self.assertNotIn('SECOND_CANARY',json.dumps(value))
        self.assertEqual(hosted.CHILDREN,set())


class ProcessTests(unittest.TestCase):
    run_quietly=True
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();subprocess.run(['git','init','-q',str(self.root)],check=True);quieten(self.root)
        self.state=self.root/'state';self.state.mkdir();self.gen=self.root/'generation';self.gen.mkdir()
        (self.state/'state.json').write_text(json.dumps(render.review_state.initial_state('Process fixture')))
        (self.state/'journal.jsonl').write_text('')
        (self.root/'views.json').write_text(json.dumps(CFG));(self.root/'binding.json').write_text(json.dumps({'state':{'directory':'state'}}))
        self.arguments=['--config','views.json','--binding','binding.json','--key','APP-1','--generation',str(self.gen)]
    def launch(self):
        child=subprocess.Popen([sys.executable,'-B',str(SCRIPT),'watch',*self.arguments],cwd=self.root,
            env={k:v for k,v in os.environ.items() if k!='FIXTURE_PUBLISH_TOKEN'},stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        def cleanup():
            if child.poll() is None:child.terminate()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if (self.state/hosted.OWNER).exists() and (self.state/hosted.STATUS).exists() and hosted.read_json(self.state/hosted.STATUS).get('code')=='credential_invalid':return child
            if child.poll() is not None:self.fail(child.communicate())
            time.sleep(.02)
        self.fail('watch did not record its owned PID')
    def test_missing_record_and_generation_self_exit_without_fixture_cleanup(self):
        for removed in ('state.json','generation'):
            with self.subTest(removed=removed):
                child=self.launch()
                self.assertTrue(hosted.process_matches(hosted.read_json(self.state/hosted.OWNER)))
                if removed=='state.json':(self.state/'state.json').unlink()
                else:self.gen.rmdir()
                child.wait(timeout=5)
                self.assertEqual(child.returncode,0)
                self.assertFalse(hosted.locked(self.state/hosted.LOCK))
                (self.state/hosted.OWNER).unlink()
                if removed=='state.json':(self.state/'state.json').write_text(json.dumps(render.review_state.initial_state('Restored')))
                else:self.gen.mkdir()
    def test_stop_checks_process_identity_and_lock_before_signalling(self):
        child=self.launch();record=hosted.read_json(self.state/hosted.OWNER)
        for bad in ({**record,'started':'a different process'},{**record,'scriptDigest':'f'*64}):
            hosted.atomic(self.state/hosted.OWNER,bad)
            self.assertFalse(hosted.process_matches(bad))
            with mock.patch.object(hosted.os,'kill') as kill:
                with self.assertRaisesRegex(hosted.PublishError,'owner_identity_mismatch'):hosted.stop(self.state)
                kill.assert_not_called()
        hosted.atomic(self.state/hosted.OWNER,record)
        reaper=threading.Thread(target=child.wait,daemon=True);reaper.start()
        self.assertTrue(hosted.stop(self.state));reaper.join(1)
        with mock.patch.object(hosted.os,'kill') as kill:
            self.assertFalse(hosted.stop(self.state));kill.assert_not_called()

    def test_status_rejects_corrupt_sidecar_instead_of_echoing_it(self):
        hosted.atomic(self.state/hosted.STATUS,{'spec':'hosted-delivery-status/1','state':'failed','token':TOKEN})
        with self.assertRaisesRegex(hosted.PublishError,'status_corrupt'):hosted.status(self.state,CFG['hosted'],'APP-1')
        hosted.atomic(self.state/hosted.STATUS,{})
        with self.assertRaisesRegex(hosted.PublishError,'status_corrupt'):hosted.status(self.state,CFG['hosted'],'APP-1')
        for raw in ('null','[]','true','0','""'):
            (self.state/hosted.STATUS).write_text(raw)
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(hosted.PublishError,'sidecar_corrupt'):
                    hosted.status(self.state,CFG['hosted'],'APP-1')
                self.assertEqual((self.state/hosted.STATUS).read_text(),raw)

    def test_null_identity_in_an_otherwise_empty_record_is_not_an_absent_identity(self):
        (self.state/hosted.IDENTITY).write_text('null')
        calls=mock.Mock()
        with self.assertRaisesRegex(hosted.PublishError,'identity_corrupt'):
            hosted.Publisher(self.state,CFG['hosted'],'APP-1',calls)
        self.assertEqual((self.state/hosted.IDENTITY).read_text(),'null');calls.assert_not_called()

    def test_whole_upload_process_deadline_stops_only_its_recorded_child(self):
        self.assertEqual((hosted.CAPTURE_SECONDS,hosted.UPLOAD_SECONDS),(60,30))
        args=SimpleNamespace(config='views.json',binding='binding.json',key='APP-1')
        inputs=views_common.Inputs(self.root,views_common.validate_config(CFG),{},self.state)
        real_popen=subprocess.Popen;started=[]
        def sleeping(command,**kwargs):
            child=real_popen([sys.executable,'-c','import time;time.sleep(30)'],**kwargs);started.append(child);return child
        with mock.patch.object(hosted.subprocess,'Popen',side_effect=sleeping),mock.patch.object(hosted,'UPLOAD_SECONDS',.1):
            with self.assertRaisesRegex(hosted.PublishError,'transport_timeout'):
                hosted.child_transport(args,inputs)('GET','status')
        self.assertEqual(len(started),1);self.assertIsNotNone(started[0].poll());self.assertEqual(hosted.CHILDREN,set())

        args.state_dir=str(self.state)
        started.clear()
        with mock.patch.object(hosted.subprocess,'Popen',side_effect=sleeping),mock.patch.object(hosted,'CAPTURE_SECONDS',.1):
            with self.assertRaisesRegex(hosted.PublishError,'source_unavailable'):hosted.child_collect(args,inputs)
        self.assertEqual(len(started),1);self.assertIsNotNone(started[0].poll());self.assertEqual(hosted.CHILDREN,set())

    def test_cancellation_during_spawn_is_observed_before_normal_child_wait(self):
        args=SimpleNamespace(config='views.json',binding='binding.json',state_dir=str(self.state),key='APP-1')
        inputs=views_common.Inputs(self.root,views_common.validate_config(CFG),{},self.state)
        real_popen=subprocess.Popen;real_stop=hosted.views._stop
        for operation in ('upload','collection'):
            started=[];waiting=[False];stopped_before_wait=[]
            def spawn(command,**kwargs):
                child=real_popen([sys.executable,'-c','import time;time.sleep(30)'],**kwargs);started.append(child)
                original_wait=child.wait
                def wait(*a,**kw):waiting[0]=True;return original_wait(*a,**kw)
                child.wait=wait
                # Deterministically cancel before the caller can register this
                # actual process; no monitor or normal timeout can win the race.
                hosted.CANCELLED.set()
                return child
            def stop(process,**kwargs):
                stopped_before_wait.append(not waiting[0]);return real_stop(process,**kwargs)
            hosted.CANCELLED.clear()
            try:
                with self.subTest(operation=operation),mock.patch.object(hosted.subprocess,'Popen',side_effect=spawn), \
                     mock.patch.object(hosted.views,'_stop',side_effect=stop),mock.patch.object(hosted,'UPLOAD_SECONDS',.1), \
                     mock.patch.object(hosted,'CAPTURE_SECONDS',.1):
                    if operation=='upload':
                        with self.assertRaisesRegex(hosted.PublishError,'^source_missing$'):
                            hosted.child_transport(args,inputs)('GET','status')
                    else:
                        with self.assertRaisesRegex(hosted.PublishError,'^source_unavailable$'):
                            hosted.child_collect(args,inputs)
                    self.assertEqual(len(started),1);self.assertIsNotNone(started[0].poll())
                    self.assertEqual(hosted.CHILDREN,set());self.assertTrue(stopped_before_wait[0])
                    for stream in (started[0].stdin,started[0].stdout):
                        self.assertTrue(stream is None or stream.closed)
            finally:
                hosted.CANCELLED.clear()
                for child in started:
                    if child.poll() is None:real_stop(child)
                    child.wait(timeout=2)
                    for stream in (child.stdin,child.stdout):
                        if stream is not None:stream.close()

    def test_start_adopts_same_owner_and_old_retry_is_not_replayed_on_restart(self):
        hosted.atomic(self.state/hosted.RETRY,{'nonce':'a'*64,'resume':True})
        child=self.launch()
        identity=hosted.read_json(self.state/hosted.IDENTITY)
        self.assertIsNone(identity['sessionRequest'])
        self.assertEqual(hosted.read_json(self.state/hosted.STATUS)['code'],'credential_invalid')
        args=SimpleNamespace(config='views.json',binding='binding.json',key='APP-1',generation=str(self.gen),resume=False)
        inputs=views_common.Inputs(self.root,views_common.validate_config(CFG),{},self.state)
        result=hosted.start(args,inputs)
        self.assertEqual(hosted.read_json(self.state/hosted.OWNER)['pid'],child.pid)
        self.assertEqual(result['state'],'failed')

    def test_generation_switch_exits_and_normal_start_retains_public_identity(self):
        alias=self.root/'current';alias.symlink_to(self.gen,target_is_directory=True)
        self.arguments[-1]=str(alias);child=self.launch()
        original=hosted.read_json(self.state/hosted.IDENTITY)['publicId']
        replacement=self.root/'replacement';replacement.mkdir();alias.unlink();alias.symlink_to(replacement,target_is_directory=True)
        child.wait(timeout=5)
        command=[sys.executable,'-B',str(SCRIPT),'start',*self.arguments]
        done=subprocess.run(command,cwd=self.root,env={k:v for k,v in os.environ.items() if k!='FIXTURE_PUBLISH_TOKEN'},
                            capture_output=True,text=True,timeout=8,start_new_session=True)
        self.addCleanup(lambda:hosted.stop(self.state))
        self.assertEqual(done.returncode,0,done.stdout+done.stderr)
        self.assertEqual(hosted.read_json(self.state/hosted.IDENTITY)['publicId'],original)
        self.assertNotEqual(hosted.read_json(self.state/hosted.OWNER)['pid'],child.pid)

    def launch_active(self,kind):
        sentinel=self.root/'active-child.pid'
        if sentinel.exists():sentinel.unlink()
        if kind=='collection':
            state=render.review_state.initial_state('Active collector')
            state['planned']=[{'subject':'APP-1','title':'Active','lane':None,'stage':'building','runId':'run-1'}]
            (self.state/'state.json').write_text(json.dumps(state))
            run=self.root/'runs.py';run.write_text(f'import os,time\nfrom pathlib import Path\nPath({str(sentinel)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
            config=copy.deepcopy(CFG);config['runs']={'argv':[sys.executable,str(run)],'timeoutSeconds':30}
            (self.root/'views.json').write_text(json.dumps(config))
            connection='''
import http.client,io,json
class Response:
 def __init__(self,status,value):self.status=status;self.data=io.BytesIO(json.dumps(value).encode())
 def read1(self,size):return self.data.read(size)
 def getheader(self,name,default=''):return 'application/json'
class Connection:
 sock=None
 def __init__(self,*a,**kw):pass
 def request(self,method,path,body=None,headers=None):
  if path.endswith('/status'):self.response=Response(404,dict(error='not_found'))
  else:
   request=json.loads(body);self.response=Response(200,dict(protocolVersion='hosted-delivery-api/1',deliveryKey=request['deliveryKey'],ownerId=request['ownerId'],epoch=1,sequence=0,leaseUntil=9999999999999,receipt=None))
 def getresponse(self):return self.response
 def close(self):pass
http.client.HTTPSConnection=Connection
'''
        else:
            (self.root/'views.json').write_text(json.dumps(CFG))
            connection=f'''
import http.client,os,time
from pathlib import Path
class Connection:
 def __init__(self,*a,**kw):pass
 def request(self,*a,**kw):Path({str(sentinel)!r}).write_text(str(os.getpid()));time.sleep(30)
 def close(self):pass
http.client.HTTPSConnection=Connection
'''
        (self.root/'sitecustomize.py').write_text(connection)
        child=subprocess.Popen([sys.executable,'-B',str(SCRIPT),'watch',*self.arguments],cwd=self.root,
            env={**os.environ,'FIXTURE_PUBLISH_TOKEN':TOKEN,'PYTHONPATH':str(self.root)},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        recorded=[None,None]
        def cleanup():
            if child.poll() is None:child.terminate()
            try:child.communicate(timeout=2)
            except subprocess.TimeoutExpired:child.kill();child.communicate(timeout=2)
            pid,started=recorded
            if pid and hosted.process_started(pid)==started:
                with contextlib.suppress(ProcessLookupError):os.kill(pid,signal.SIGKILL)
        self.addCleanup(cleanup)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if sentinel.exists():
                pid=int(sentinel.read_text());recorded[:]=[pid,hosted.process_started(pid)]
                self.assertIsNone(child.poll());self.assertIsNotNone(recorded[1])
                self.assertTrue(hosted.locked(self.state/hosted.LOCK))
                return child,pid
            if child.poll() is not None:self.fail(child.communicate())
            time.sleep(.02)
        self.fail('fixture did not observe an active owned child')

    def test_active_child_is_reaped_on_stop_and_source_or_generation_loss(self):
        for mode,kind in [('source','request'),('generation','collection'),('stop','request')]:
            with self.subTest(mode=mode):
                child,pid=self.launch_active(kind)
                if mode=='source':(self.state/'state.json').unlink()
                elif mode=='generation':self.gen.rmdir()
                else:
                    reaper=threading.Thread(target=child.wait,daemon=True);reaper.start()
                    self.assertTrue(hosted.stop(self.state));reaper.join(1)
                child.wait(timeout=5)
                self.assertEqual(child.returncode,0)
                deadline=time.monotonic()+5
                while hosted.process_started(pid) is not None and time.monotonic()<deadline:time.sleep(.02)
                self.assertIsNone(hosted.process_started(pid));self.assertFalse(hosted.locked(self.state/hosted.LOCK))
                if mode=='source':(self.state/'state.json').write_text(json.dumps(render.review_state.initial_state('Restored')))
                if mode=='generation':self.gen.mkdir()
                # The fake service returns a fresh epoch per fixture; keep each
                # process scenario's private sidecars independent as production
                # identity recovery is covered separately by the protocol tests.
                for name in (hosted.IDENTITY,hosted.STATUS,hosted.OWNER):
                    with contextlib.suppress(FileNotFoundError):(self.state/name).unlink()

if __name__=='__main__':unittest.main()
