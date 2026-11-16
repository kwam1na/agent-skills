#!/usr/bin/env python3
"""Owned, data-only delivery publisher. Standard library; no tracker publisher calls.

start/status/retry/stop are the lifecycle seam; collect and watch are private
bounded child operations. Local sidecars never hold credentials. Only the
transport process receives the configured bearer. Expired sessions need explicit
retry --resume; an owner fenced by another owner never reacquires automatically.
"""
from __future__ import annotations
import argparse
import base64
import contextlib
import copy
import fcntl
import hashlib
import http.client
import json
import os
import random
import re
import secrets
import shlex
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import public_projection as public
import render
import views_common as views

PROTOCOL = 'hosted-delivery-api/1'
RENDERER = 'delivery-line-map/1'
IDENTITY = 'hosted-identity.json'
STATUS = 'hosted-status.json'
OWNER = 'hosted-owner.json'
LOCK = 'hosted-owner.lock'
CONTROL = 'hosted-control.lock'
PENDING_PREFIX = 'hosted-pending-'
RETRY = 'hosted-retry.json'
HEX = re.compile(r'^[a-f0-9]{64}$')
KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
CAPTURE_SECONDS, UPLOAD_SECONDS = 60, 30
DEBOUNCE, MAX_DEFERRAL, CONFIRM_SECONDS = 2, 10, 30
MAX_RESPONSE = 64 * 1024
CHILDREN = set()
CANCELLED = threading.Event()
PS_ENV = {key:value for key,value in os.environ.items() if key in ('PATH','HOME')} | {'LC_ALL':'C','TZ':'UTC'}
ERRORS = frozenset(('invalid_identifier invalid_sequence invalid_envelope invalid_json invalid_projection unauthorized '
 'not_found method_not_allowed lease_held resume_required writer_fenced lease_expired sequence_conflict '
 'sequence_superseded confirmation_conflict body_too_large unsupported_content_type unsupported_encoding '
 'unsupported_renderer digest_mismatch temporarily_unavailable').split())
SAFE_ERRORS = ERRORS | frozenset(('sidecar_unsafe sidecar_unreadable sidecar_oversize sidecar_missing sidecar_corrupt '
 'owner_lock_unsafe owner_busy origin_invalid credential_invalid identity_corrupt operation_invalid redirect_refused '
 'transport_timeout response_oversize response_invalid remote_error transport receipt_invalid session_invalid '
 'delivery_key_invalid identity_missing identity_mismatch session_regressed receipt_regressed receipt_conflict '
 'source_unavailable source_path capture_deadline source_invalid source_changed owner_identity_mismatch '
 'owner_stop_timeout status_corrupt source_missing retry_corrupt owner_start_failed owner_start_timeout '
 'not_configured request_oversize configuration_or_source_invalid confirmation_invalid receipt_mismatch configuration_changed sequence_reconcile').split())

class PublishError(RuntimeError):
    def __init__(self, code, *, transient=False):
        self.code, self.transient = code, transient
        super().__init__(code)


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 2**53-1


def regular(path: Path, limit=MAX_RESPONSE):
    """A bounded fixed sidecar, with no symlink following."""
    if path.is_symlink(): raise PublishError('sidecar_unsafe')
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
            data = stream.read(limit+1)
    except FileNotFoundError:
        return None
    except OSError:
        raise PublishError('sidecar_unreadable') from None
    if len(data)>limit: raise PublishError('sidecar_oversize')
    return data


def read_json(path, *, optional=False):
    raw=regular(path)
    if raw is None:
        if optional:return None
        raise PublishError('sidecar_missing')
    try:
        value=json.loads(raw)
        if not isinstance(value,dict):raise PublishError('sidecar_corrupt')
        return value
    except (ValueError, UnicodeError, RecursionError):raise PublishError('sidecar_corrupt') from None


def atomic(path, value):
    data = value if isinstance(value, bytes) else public.encode(value)
    if path.is_symlink():raise PublishError('sidecar_unsafe')
    descriptor, name = tempfile.mkstemp(prefix='.hosted-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name, path)
        folder=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(folder)
        finally:os.close(folder)
    finally:
        with contextlib.suppress(FileNotFoundError):os.unlink(name)


@contextlib.contextmanager
def lock(path, *, blocking=True):
    try:descriptor=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    except OSError:raise PublishError('owner_lock_unsafe') from None
    try:
        try:fcntl.flock(descriptor,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:raise PublishError('owner_busy') from None
        yield
    finally:os.close(descriptor)


def locked(path):
    try:
        with lock(path,blocking=False):return False
    except PublishError as error:
        if error.code=='owner_busy':return True
        raise


class Transport:
    def __init__(self, origin, repository, public_id, token, *, allow_loopback=False):
        # The HTTP exception is injectable only in fixture code, never config or CLI.
        parts=urlsplit(origin)
        if not isinstance(repository,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',repository):raise PublishError('origin_invalid')
        if allow_loopback and parts.scheme=='http' and parts.hostname=='127.0.0.1' and parts.port:
            if parts.path or parts.query or parts.fragment or parts.username or parts.password:
                raise PublishError('origin_invalid')
        else:
            try:views.validate_config({'spec':views.CONFIG_SPEC,'hosted':{'origin':origin,'repositoryId':repository,'tokenEnv':'TOKEN'}})
            except views.ConfigError:raise PublishError('origin_invalid') from None
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',token or ''):raise PublishError('credential_invalid')
        if not HEX.fullmatch(public_id):raise PublishError('identity_corrupt')
        self.parts,self.repository,self.public_id,self.token=parts,repository,public_id,token
    def __call__(self,method,operation,body=None,headers=None):
        if operation not in ('session','status','snapshots','confirm'):raise PublishError('operation_invalid')
        started=time.monotonic()
        connection = (http.client.HTTPSConnection(self.parts.hostname,self.parts.port or 443,
                      timeout=UPLOAD_SECONDS,context=ssl.create_default_context()) if self.parts.scheme=='https'
                      else http.client.HTTPConnection(self.parts.hostname,self.parts.port,timeout=UPLOAD_SECONDS))
        request_headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'}
        if body is not None:request_headers['Content-Type']='application/json'
        request_headers.update(headers or {})
        try:
            path=f'/api/v1/repos/{self.repository}/deliveries/{self.public_id}/{operation}'
            connection.request(method,path,body=body,headers=request_headers)
            response=connection.getresponse()
            if 300<=response.status<400:raise PublishError('redirect_refused')
            data=bytearray()
            while True:
                remaining=UPLOAD_SECONDS-(time.monotonic()-started)
                if remaining<=0:raise PublishError('transport_timeout',transient=True)
                if connection.sock:connection.sock.settimeout(remaining)
                chunk=response.read1(min(4096,MAX_RESPONSE+1-len(data)))
                if not chunk:break
                data.extend(chunk)
                if len(data)>MAX_RESPONSE:raise PublishError('response_oversize')
            try:answer=json.loads(data)
            except (ValueError,UnicodeError,RecursionError):raise PublishError('response_invalid',transient=response.status>=500) from None
            if response.status!=200:
                code=answer.get('error') if isinstance(answer,dict) else None
                if code not in ERRORS:code='remote_error'
                raise PublishError(code,transient=response.status in (429,500,502,503,504))
            if response.getheader('Content-Type','').split(';')[0]!='application/json':raise PublishError('response_invalid')
            return answer
        except (OSError,http.client.HTTPException,TimeoutError):
            raise PublishError('transport',transient=True) from None
        finally:connection.close()


def receipt(value):
    fields={'protocolVersion','rendererVersion','epoch','sequence','digest','sourceFingerprint','publishedAt','sourceConfirmedAt'}
    if (not isinstance(value,dict) or set(value)!=fields or value['protocolVersion']!=PROTOCOL
        or value['rendererVersion']!=RENDERER or not integer(value['epoch'],1) or not integer(value['sequence'],1)
        or not all(isinstance(value[k],str) and HEX.fullmatch(value[k]) for k in ('digest','sourceFingerprint'))
        or not all(integer(value[k]) for k in ('publishedAt','sourceConfirmedAt'))):raise PublishError('receipt_invalid')
    return copy.deepcopy(value)


def session(value):
    fields={'protocolVersion','deliveryKey','ownerId','epoch','sequence','leaseUntil','receipt'}
    if (not isinstance(value,dict) or set(value)!=fields or value['protocolVersion']!=PROTOCOL
        or not isinstance(value['deliveryKey'],str) or not KEY.fullmatch(value['deliveryKey'])
        or not isinstance(value['ownerId'],str) or not HEX.fullmatch(value['ownerId'])
        or not integer(value['epoch'],1) or not integer(value['sequence']) or not integer(value['leaseUntil'])):
        raise PublishError('session_invalid')
    if value['receipt'] is not None:
        entry=receipt(value['receipt'])
        if entry['epoch']>value['epoch'] or entry['epoch']==value['epoch'] and entry['sequence']>value['sequence']:
            raise PublishError('session_invalid')
        if value['sequence']>0 and (entry['epoch'],entry['sequence'])!=(value['epoch'],value['sequence']):raise PublishError('session_invalid')
    elif value['sequence']>0:raise PublishError('session_invalid')
    return value


class Publisher:
    """One local owner. Caller holds LOCK for every mutation and network step."""
    def __init__(self,state,config,key,api=None,*,secret_values=(),alive=lambda:True):
        self.state,self.config,self.key,self.api,self.secrets=state,config,key,api,tuple(secret_values)
        self.alive=alive
        if not isinstance(key,str) or not KEY.fullmatch(key):raise PublishError('delivery_key_invalid')
        try:self.identity=read_json(state/IDENTITY,optional=True)
        except PublishError:raise PublishError('identity_corrupt') from None
        if self.identity is None:
            # A status/pending without its identity is not permission to create another public link.
            if any((state/name).exists() for name in (STATUS,OWNER)) or next(state.glob(PENDING_PREFIX+'*.json'),None):raise PublishError('identity_missing')
            self.identity={'spec':'hosted-delivery-identity/1','origin':config['origin'],'repositoryId':config['repositoryId'],
                'deliveryKey':key,'publicId':secrets.token_hex(32),'ownerId':secrets.token_hex(32),
                'epoch':None,'sequence':0,'receipt':None,'pending':None,'sessionRequest':None}
            self.save()
        self.check_identity()
    def check_identity(self):
        d=self.identity
        fields={'spec','origin','repositoryId','deliveryKey','publicId','ownerId','epoch','sequence','receipt','pending','sessionRequest'}
        if (not isinstance(d,dict) or set(d)!=fields or d['spec']!='hosted-delivery-identity/1'
            or not all(isinstance(d[k],str) and HEX.fullmatch(d[k]) for k in ('publicId','ownerId'))
            or d['epoch'] is not None and not integer(d['epoch'],1) or not integer(d['sequence'])):
            raise PublishError('identity_corrupt')
        if (d['origin'],d['repositoryId'],d['deliveryKey'])!=(self.config['origin'],self.config['repositoryId'],self.key):
            raise PublishError('identity_mismatch')
        if d['epoch'] is None and (d['sequence']!=0 or d['receipt'] is not None or d['pending'] is not None):raise PublishError('identity_corrupt')
        try:
            if d['receipt'] is not None:
                r=receipt(d['receipt'])
                if r['epoch']>d['epoch'] or r['epoch']==d['epoch'] and r['sequence']>d['sequence']:raise ValueError()
            if d['pending'] is not None:
                p=d['pending']
                if set(p)!={'epoch','sequence','digest','sourceFingerprint'} or not integer(p['epoch'],1) or not integer(p['sequence'],1):raise ValueError()
                if any(not isinstance(p[k],str) or not HEX.fullmatch(p[k]) for k in ('digest','sourceFingerprint')):raise ValueError()
                data=regular(self.pending_path(p['digest']),public.MAX_BYTES)
                if data is None or hashlib.sha256(data).hexdigest()!=p['digest']:raise ValueError()
                payload=json.loads(data);public.validate(payload,secret_values=self.secrets)
                if payload['sourceFingerprint']!=p['sourceFingerprint']:raise ValueError()
            request=d['sessionRequest']
            if request is not None:
                if not isinstance(request,dict):raise ValueError()
                expected={'mode','deliveryKey','ownerId'} | ({'epoch'} if request.get('mode')=='resume' else set())
                if (set(request)!=expected or request['mode'] not in ('acquire','resume')
                    or request['ownerId']!=d['ownerId'] or request['deliveryKey']!=self.key
                    or request['mode']=='resume' and request['epoch']!=d['epoch']):raise ValueError()
        except (TypeError,KeyError,ValueError,public.ProjectionError,PublishError):raise PublishError('identity_corrupt') from None
    def save(self):self.guard();atomic(self.state/IDENTITY,self.identity)
    def pending_path(self,digest):return self.state/(PENDING_PREFIX+digest+'.json')
    def guard(self):
        if not self.alive():raise PublishError('source_missing')
    def request(self,*args):
        self.guard()
        try:answer=self.api(*args)
        except PublishError:
            self.guard();raise
        self.guard();return answer
    def commit_request(self,*args):
        try:return self.request(*args)
        except PublishError as error:
            if error.code in ('sequence_conflict','sequence_superseded','confirmation_conflict'):
                # A surviving old request may have won an ambiguous race. Read
                # the committed pointer before allocating the next sequence;
                # never retry a conflicting body at an already-used sequence.
                self.reconcile()
                raise PublishError('sequence_reconcile',transient=True) from None
            if error.code in ('writer_fenced','lease_expired'):
                self.reconcile()  # no implicit resume or acquisition at a known epoch
            raise
    def adopt(self,remote):
        remote=session(remote);d=self.identity
        if remote['ownerId']!=d['ownerId'] or remote['deliveryKey']!=self.key:raise PublishError('writer_fenced')
        request=d['sessionRequest']
        allowed=(remote['epoch']==d['epoch'] or request is not None and
                 (request['mode']=='acquire' and remote['epoch']==1 or request['mode']=='resume' and remote['epoch']==request['epoch']+1))
        if not allowed:raise PublishError('writer_fenced')
        if remote['epoch']==d['epoch'] and remote['sequence']<d['sequence']:raise PublishError('session_regressed')
        d['epoch'],d['sequence']=remote['epoch'],remote['sequence']
        if remote['receipt'] is not None:
            new=receipt(remote['receipt']);old=d['receipt']
            if old is not None and (new['epoch'],new['sequence'])<(old['epoch'],old['sequence']):raise PublishError('receipt_regressed')
            if old is not None and (new['epoch'],new['sequence'])==(old['epoch'],old['sequence']) and new!=old:raise PublishError('receipt_conflict')
            d['receipt']=new
            p=d['pending']
            if p and all(new[k]==p[k] for k in ('epoch','sequence','digest','sourceFingerprint')):d['pending']=None
        d['sessionRequest']=None
        self.save()
        return remote
    def reconcile(self,*,resume=False):
        d=self.identity
        try:remote=self.request('GET','status')
        except PublishError as error:
            if error.code!='not_found' or d['epoch'] is not None:raise
            request={'mode':'acquire','deliveryKey':self.key,'ownerId':d['ownerId']}
            d['sessionRequest']=request;self.save()
            remote=self.request('POST','session',public.encode(request))
        remote=self.adopt(remote)
        if remote['leaseUntil']<=int(time.time()*1000):
            if not resume:raise PublishError('resume_required')
            request={'mode':'resume','deliveryKey':self.key,'ownerId':d['ownerId'],'epoch':d['epoch']}
            d['sessionRequest']=request;self.save()
            remote=self.adopt(self.request('POST','session',public.encode(request)))
        return remote
    def step(self,collect,*,resume=False):
        self.check_identity()
        self.reconcile(resume=resume)
        # Reconciliation alone never confirms a source. Even a durable pending
        # body is re-collected before any snapshot/confirmation request.
        self.guard()
        try:value=collect();public.validate(value,secret_values=self.secrets)
        except (public.ProjectionError,views.ConfigError,views.RunUnavailable,OSError,ValueError):
            raise PublishError('source_unavailable',transient=True) from None
        self.guard()
        data=public.encode(value);digest=hashlib.sha256(data).hexdigest();d=self.identity
        previous=d['receipt']
        headers={'X-Writer-Epoch':str(d['epoch']),'X-Renderer-Version':RENDERER}
        if previous and previous['epoch']==d['epoch'] and previous['digest']==digest:
            headers.update({'X-Snapshot-Sequence':str(previous['sequence']),'X-Snapshot-Digest':digest})
            answer=self.commit_request('POST','confirm',public.encode({'sourceFingerprint':value['sourceFingerprint']}),headers)
            if (not isinstance(answer,dict) or set(answer)!={'protocolVersion','sourceConfirmedAt','leaseUntil','receipt'}
                or answer['protocolVersion']!=PROTOCOL or not integer(answer['sourceConfirmedAt']) or not integer(answer['leaseUntil'])
                or receipt(answer['receipt'])!=previous):raise PublishError('confirmation_invalid')
            return {'state':'current','sourceConfirmedAt':answer['sourceConfirmedAt'],'receipt':previous}
        p=d['pending']
        if not p or p['epoch']!=d['epoch'] or p['digest']!=digest or p['sequence']<=d['sequence']:
            p={'epoch':d['epoch'],'sequence':d['sequence']+1,'digest':digest,'sourceFingerprint':value['sourceFingerprint']}
            path=self.pending_path(digest)
            existing=regular(path,public.MAX_BYTES)
            if existing is not None and existing!=data:raise PublishError('identity_corrupt')
            if existing is None:atomic(path,data)
            d['pending']=p;self.save()
        atomic(self.state/STATUS,{'spec':'hosted-delivery-status/1','state':'pending'})
        headers.update({'X-Snapshot-Sequence':str(p['sequence']),'X-Snapshot-Digest':digest})
        accepted=receipt(self.commit_request('POST','snapshots',data,headers))
        if not all(accepted[k]==p[k] for k in p):raise PublishError('receipt_mismatch')
        d['sequence']=accepted['sequence'];d['receipt']=accepted;d['pending']=None;self.save()
        with contextlib.suppress(FileNotFoundError):self.pending_path(digest).unlink()
        return {'state':'current','sourceConfirmedAt':accepted['sourceConfirmedAt'],'receipt':accepted}


class Schedule:
    def __init__(self,now):self.first=None;self.last=None;self.next=now
    def changed(self,now):
        if self.first is None:self.first=now
        self.last=now
    def due(self,now):
        if self.first is not None:return now>=min(self.last+DEBOUNCE,self.first+MAX_DEFERRAL)
        return now>=self.next
    def completed(self,now):self.first=self.last=None;self.next=now+CONFIRM_SECONDS


def retry_delay(attempt,random_value=random.random):
    return max(2,min(60,2**min(attempt+1,6)*(0.75+0.5*random_value())))


def selected_files(directory):
    """Only canonical files and referenced native review artifacts, including absent optional inputs."""
    names=['state.json','journal.jsonl','plan.json',render.NEEDS_FILE]
    if any((directory/name).is_symlink() or not (directory/name).resolve().is_relative_to(directory)
           for name in [*names,'review-artifacts']):raise PublishError('source_path')
    state=render.review_state.read_state(directory)
    journal=render.review_state.read_journal(directory)
    subjects={row['subject'] for name in ('queue','planned') for row in state.get(name,[])}
    refs={entry['subject']:entry['reviewArtifact']['digest'] for entry in journal if entry['kind']=='review.evidence' and entry['subject'] in subjects}
    for digest in refs.values():
        if not HEX.fullmatch(digest):raise PublishError('source_invalid')
        names.append('review-artifacts/'+digest+'.json')
    return names


def load_inputs(arguments):
    # Discover the checkout without starting git (which must not inherit the
    # uploader's credential before its configured environment name is known).
    here=Path.cwd().resolve()
    root=next((path for path in (here,*here.parents) if (path/'.git').exists()),None)
    if root is None:raise PublishError('source_path')
    def source(given):
        if not given:raise PublishError('configuration_or_source_invalid')
        path=root/given
        if not path.resolve().is_relative_to(root):raise PublishError('source_path')
        data=regular(path,1024*1024)
        if data is None:raise PublishError('configuration_or_source_invalid')
        return json.loads(data)
    config=views.validate_config(source(arguments.config));binding=source(arguments.binding)
    directory=(binding.get('state') or {}).get('directory')
    if not isinstance(directory,str):raise PublishError('source_path')
    state=(root/(arguments.state_dir or directory)).resolve()
    if not state.is_relative_to(root):raise PublishError('source_path')
    return views.Inputs(root,config,binding,state)


def collect(inputs,arguments=None):
    directory,root,config=inputs.state_directory,inputs.root,inputs.config
    if not directory.is_relative_to(root):raise PublishError('source_path')
    deadline=time.monotonic()+CAPTURE_SECONDS-5
    trace={}
    argv=views.runs_argv(config,root)
    def run(*args):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise PublishError('capture_deadline',transient=True)
        scrub=set(config['scrubEnv'])|({config['hosted']['tokenEnv']} if config['hosted'] else set())
        answer=views.run_json([*argv,*args],timeout=min(config['runs']['timeoutSeconds'],remaining),
                              scrub=list(scrub),cwd=root,new_session=False)
        trace[args]=answer
        return answer
    def cut():
        nonlocal config,argv
        if arguments is not None:
            fresh=load_inputs(arguments)
            if fresh.state_directory!=directory:raise PublishError('source_changed',transient=True)
            config=fresh.config;argv=views.runs_argv(config,root)
        doc=render.review_state.show(directory)
        runs={}
        named=render.named_runs(doc)
        if named and argv:
            listing=run('runs','list','--json')
            if not isinstance(listing,dict) or not isinstance(listing.get('runs'),list):raise PublishError('source_invalid',transient=True)
            rows={row['runId']:row for row in listing['runs'] if isinstance(row,dict) and isinstance(row.get('runId'),str)}
            for run_id in named:
                if run_id not in rows:runs[run_id]={'runId':run_id,'unavailable':True}
                else:
                    summary=render._read_run(run,run_id,rows[run_id],config['findings']['reportSpecs'])
                    if summary is None:raise PublishError('source_invalid',transient=True)
                    runs[run_id]=summary
        value=public.project(doc,runs,config,source_fingerprint='0'*64,
              plan=render.read_plan_block(directory,doc),needs=render.read_needs_block(directory))
        # Close the complete collection interval, including optional inputs and
        # payload construction, rather than verifying APIs before those reads.
        before=copy.deepcopy(trace)
        for args,answer in before.items():
            if public.encode(run(*args))!=public.encode(answer):raise PublishError('source_changed',transient=True)
        return value
    def inventory():
        names=[str((directory/name).relative_to(root)) for name in selected_files(directory)]
        if arguments is not None:
            for given in (arguments.config,arguments.binding):
                path=root/given
                if path.is_symlink() or not path.resolve().is_relative_to(root):raise PublishError('source_path')
                names.append(str(path.relative_to(root)))
        return names
    value=public.capture(root,inventory,cut)
    value['sourceFingerprint']=hashlib.sha256(public.encode([value['sourceFingerprint'],
        [[list(args),answer] for args,answer in trace.items()]])).hexdigest()
    return value


def child_collect(arguments,inputs):
    if CANCELLED.is_set():raise PublishError('source_missing')
    argv=[sys.executable,'-B',str(Path(__file__).resolve()),'collect','--config',arguments.config,
          '--binding',arguments.binding,'--state-dir',str(inputs.state_directory),'--key',arguments.key]
    scrub=set(inputs.config['scrubEnv'])|{inputs.config['hosted']['tokenEnv']}
    try:return views.run_json(argv,timeout=CAPTURE_SECONDS,scrub=list(scrub),cwd=inputs.root,limit=public.MAX_BYTES,process_registry=CHILDREN,cancelled=CANCELLED)
    except views.RunUnavailable:raise PublishError('source_unavailable',transient=True) from None


def child_transport(arguments,inputs):
    """Bound DNS, connect, upload and response parsing by one process deadline."""
    def request(method,operation,body=None,headers=None):
        if CANCELLED.is_set():raise PublishError('source_missing')
        command=[sys.executable,'-B',str(Path(__file__).resolve()),'request','--config',arguments.config,
                 '--binding',arguments.binding,'--state-dir',str(inputs.state_directory),'--key',arguments.key]
        environment={k:v for k,v in os.environ.items() if k not in inputs.config['scrubEnv'] or k==inputs.config['hosted']['tokenEnv']}
        envelope={'method':method,'operation':operation,'body':base64.b64encode(body).decode() if body is not None else None,'headers':headers}
        process=subprocess.Popen(command,cwd=inputs.root,env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL,start_new_session=True)
        CHILDREN.add(process)
        try:
            if CANCELLED.is_set():raise PublishError('source_missing')
            try:output,_=process.communicate(public.encode(envelope),timeout=UPLOAD_SECONDS)
            except subprocess.TimeoutExpired:
                views._stop(process);process.communicate(timeout=2)
                raise PublishError('transport_timeout',transient=True) from None
            if process.returncode or len(output)>MAX_RESPONSE:raise PublishError('transport',transient=True)
            try:answer=json.loads(output)
            except (ValueError,UnicodeError):raise PublishError('response_invalid') from None
            if set(answer)=={'ok'}:return answer['ok']
            if (set(answer)!={'error','transient'} or answer['error'] not in SAFE_ERRORS
                or type(answer['transient']) is not bool):raise PublishError('response_invalid')
            raise PublishError(answer['error'],transient=answer['transient'])
        finally:
            views._stop(process)
            with contextlib.suppress(subprocess.TimeoutExpired):process.wait(2)
            for stream in (process.stdin,process.stdout):
                if stream is not None:
                    with contextlib.suppress(OSError):stream.close()
            CHILDREN.discard(process)
    return request


def process_started(pid):
    try:
        done=subprocess.run(['ps','-o','lstart=','-p',str(pid)],capture_output=True,text=True,timeout=2,
                            env=PS_ENV)
        return done.stdout.strip() or None
    except (OSError,subprocess.TimeoutExpired):return None


def process_matches(record):
    if not isinstance(record,dict) or set(record)!={'pid','started','scriptDigest','generation'}:return False
    pid=record['pid']
    if type(pid) is not int or pid<=1 or not isinstance(record['started'],str):return False
    try:
        if os.getpgid(pid)!=pid or process_started(pid)!=record['started']:return False
        done=subprocess.run(['ps','-o','args=','-p',str(pid)],capture_output=True,text=True,timeout=2,env=PS_ENV)
        command=shlex.split(done.stdout)
        if len(command)<4 or command[1]!='-B' or Path(command[2]).name!='hosted.py' or command[3]!='watch':return False
        path=Path(command[2])
        return (path.is_file() and path.stat().st_size<1024*1024
                and hashlib.sha256(path.read_bytes()).hexdigest()==record['scriptDigest']
                and isinstance(record['generation'],str) and HEX.fullmatch(record['generation']) is not None)
    except (OSError,subprocess.TimeoutExpired):return False


def stop(directory):
    record=read_json(directory/OWNER,optional=True)
    if not locked(directory/LOCK):return False
    if not process_matches(record):raise PublishError('owner_identity_mismatch')
    os.kill(record['pid'],signal.SIGTERM)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if not locked(directory/LOCK) and process_started(record['pid']) is None:return True
        time.sleep(.05)
    raise PublishError('owner_stop_timeout')


def generation(arguments):
    return Path(arguments.generation or Path(__file__).resolve().parents[3]).absolute()


def status(directory,config,key):
    value=read_json(directory/STATUS,optional=True)
    if value is None:value={'spec':'hosted-delivery-status/1','state':'disabled'}
    if (not isinstance(value,dict) or not set(value)<={'spec','state','code','sourceConfirmedAt','receipt'}
        or value.get('spec')!='hosted-delivery-status/1' or value.get('state') not in ('pending','current','retrying','failed','stopped','disabled')
        or 'code' in value and (not isinstance(value['code'],str) or value['code'] not in SAFE_ERRORS)
        or 'sourceConfirmedAt' in value and not integer(value['sourceConfirmedAt'])):raise PublishError('status_corrupt')
    if value['state']=='current' and not {'receipt','sourceConfirmedAt'}<=set(value):raise PublishError('status_corrupt')
    if value['state'] in ('failed','retrying') and 'code' not in value:raise PublishError('status_corrupt')
    if 'receipt' in value:receipt(value['receipt'])
    identity=read_json(directory/IDENTITY,optional=True)
    if identity is not None:
        Publisher(directory,config,key).check_identity()
        value={**value,'url':config['origin']+'/d/'+identity['publicId']}
    return value


@contextlib.contextmanager
def monitor_liveness(alive,stop):
    """Observe source loss even while the main thread waits on a recorded child."""
    done=threading.Event()
    CANCELLED.clear()
    def observe():
        while not done.wait(.1):
            if not alive():
                stop(None,None)
                return
    thread=threading.Thread(target=observe,daemon=True)
    thread.start()
    try:yield
    finally:
        done.set();thread.join(1);CANCELLED.clear()


def watch(arguments,inputs):
    directory=inputs.state_directory;config=inputs.config
    generation_path=generation(arguments);resolved=generation_path.resolve()
    script_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    prior_retry=read_json(directory/RETRY,optional=True)
    retry_seen=prior_retry.get('nonce') if isinstance(prior_retry,dict) else None
    halted=False;attempt=0;retry_at=0;resume=arguments.resume
    schedule=Schedule(time.monotonic());previous=None;running=True
    def alive():
        return ((directory/'state.json').is_file() and (directory/'journal.jsonl').is_file()
                and generation_path.exists() and generation_path.resolve()==resolved and Path(__file__).is_file())
    def signal_stop(signum,frame):
        nonlocal running
        running=False
        CANCELLED.set()
        for process in list(CHILDREN):
            if process.poll() is None:views._stop(process)
    signal.signal(signal.SIGTERM,signal_stop);signal.signal(signal.SIGINT,signal_stop)
    with lock(directory/LOCK,blocking=False):
        if not alive():raise PublishError('source_missing')
        token=os.environ.get(config['hosted']['tokenEnv'],'')
        secret_values=[value for name in set(config['scrubEnv'])|{config['hosted']['tokenEnv']}
                       if (value:=os.environ.get(name))]
        publisher=Publisher(directory,config['hosted'],arguments.key,secret_values=secret_values,alive=lambda:running and alive())
        status(directory,config['hosted'],arguments.key)
        if not (directory/STATUS).exists():atomic(directory/STATUS,{'spec':'hosted-delivery-status/1','state':'pending'})
        atomic(directory/OWNER,{'pid':os.getpid(),'started':process_started(os.getpid()),'scriptDigest':script_digest,
                               'generation':hashlib.sha256(str(resolved).encode()).hexdigest()})
        try:
            Transport(config['hosted']['origin'],config['hosted']['repositoryId'],publisher.identity['publicId'],token)
            publisher.api=child_transport(arguments,inputs)
        except PublishError as error:
            halted=True;atomic(directory/STATUS,{'spec':'hosted-delivery-status/1','state':'failed','code':error.code})
        with monitor_liveness(alive,signal_stop):
            while running and alive():
                now=time.monotonic()
                request=read_json(directory/RETRY,optional=True)
                if request is not None:
                    if not isinstance(request,dict) or set(request)!={'nonce','resume'} or not isinstance(request['nonce'],str) or not HEX.fullmatch(request['nonce']) or type(request['resume']) is not bool:raise PublishError('retry_corrupt')
                    if request['nonce']!=retry_seen:
                        retry_seen=request['nonce'];resume=request['resume'];retry_at=0;schedule.next=now
                        try:
                            Transport(config['hosted']['origin'],config['hosted']['repositoryId'],publisher.identity['publicId'],token)
                            publisher.api=child_transport(arguments,inputs);halted=False
                        except PublishError as error:
                            halted=True;atomic(directory/STATUS,{'spec':'hosted-delivery-status/1','state':'failed','code':error.code})
                try:
                    fresh=load_inputs(arguments)
                    if fresh.config['hosted']!=config['hosted'] or fresh.state_directory!=directory:
                        raise PublishError('configuration_changed')
                    publisher.secrets=tuple(value for name in set(fresh.config['scrubEnv'])|{config['hosted']['tokenEnv']}
                                            if (value:=os.environ.get(name)))
                    inventory=selected_files(directory)
                    signature=[(name,(directory/name).stat().st_mtime_ns,(directory/name).stat().st_size)
                               if (directory/name).exists() else (name,None) for name in inventory]
                    if signature!=previous:schedule.changed(now);previous=signature
                    if not halted and now>=retry_at and schedule.due(now):
                        result=publisher.step(lambda:child_collect(arguments,fresh),resume=resume)
                        resume=False;attempt=0;schedule.completed(time.monotonic())
                        if alive():atomic(directory/STATUS,{'spec':'hosted-delivery-status/1',**result})
                except (PublishError,public.ProjectionError,views.ConfigError,render.review_state.StateError,OSError) as error:
                    code=error.code if isinstance(error,PublishError) else 'source_unavailable'
                    transient=error.transient if isinstance(error,PublishError) else True
                    if alive():atomic(directory/STATUS,{'spec':'hosted-delivery-status/1','state':'retrying' if transient else 'failed','code':code})
                    if transient:retry_at=time.monotonic()+retry_delay(attempt);attempt+=1
                    else:halted=True
                # Fixed short sleeps keep record/generation disappearance independent
                # of a long retry interval and fixture cleanup.
                time.sleep(.25)
        if directory.exists() and alive():atomic(directory/STATUS,{'spec':'hosted-delivery-status/1','state':'stopped'})


def start(arguments,inputs):
    directory=inputs.state_directory
    with lock(directory/CONTROL):
        owner=read_json(directory/OWNER,optional=True)
        current_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        current_generation=hashlib.sha256(str(generation(arguments).resolve()).encode()).hexdigest()
        if locked(directory/LOCK):
            if not process_matches(owner):raise PublishError('owner_identity_mismatch')
            if owner['scriptDigest']==current_digest and owner['generation']==current_generation:return status(directory,inputs.config['hosted'],arguments.key)
            stop(directory)
        command=[sys.executable,'-B',str(Path(__file__).resolve()),'watch','--config',arguments.config,'--binding',arguments.binding,
                 '--state-dir',str(directory),'--key',arguments.key,'--generation',str(generation(arguments))]
        if arguments.resume:command.append('--resume')
        environment={k:v for k,v in os.environ.items() if k not in inputs.config['scrubEnv'] or k==inputs.config['hosted']['tokenEnv']}
        child=subprocess.Popen(command,cwd=inputs.root,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,start_new_session=True)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if child.poll() is not None:raise PublishError('owner_start_failed')
            record=read_json(directory/OWNER,optional=True)
            if record and record.get('pid')==child.pid and locked(directory/LOCK):return status(directory,inputs.config['hosted'],arguments.key)
            time.sleep(.05)
        # Only this freshly started child is eligible for cleanup.
        child.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):child.wait(2)
        raise PublishError('owner_start_timeout')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['start','status','retry','stop','watch','collect','request'])
    views.add_input_arguments(parser)
    parser.add_argument('--key',required=True)
    parser.add_argument('--generation')
    parser.add_argument('--resume',action='store_true')
    arguments=parser.parse_args(argv)
    try:
        inputs=load_inputs(arguments)
        directory=inputs.state_directory
        if not directory.is_relative_to(inputs.root) or not directory.is_dir():raise PublishError('source_path')
        if inputs.config['hosted'] is None:raise PublishError('not_configured')
        if arguments.operation=='collect':answer=collect(inputs,arguments)
        elif arguments.operation=='request':
            try:
                raw=sys.stdin.buffer.read(24*1024*1024+1)
                if len(raw)>24*1024*1024:raise PublishError('request_oversize')
                envelope=json.loads(raw)
                publisher=Publisher(directory,inputs.config['hosted'],arguments.key)
                transport=Transport(inputs.config['hosted']['origin'],inputs.config['hosted']['repositoryId'],
                                    publisher.identity['publicId'],os.environ.get(inputs.config['hosted']['tokenEnv'],''))
                body=base64.b64decode(envelope['body'],validate=True) if envelope['body'] is not None else None
                answer={'ok':transport(envelope['method'],envelope['operation'],body,envelope['headers'])}
            except PublishError as error:answer={'error':error.code,'transient':error.transient}
        elif arguments.operation=='watch':watch(arguments,inputs);return 0
        elif arguments.operation=='start':answer=start(arguments,inputs)
        elif arguments.operation=='status':answer=status(directory,inputs.config['hosted'],arguments.key)
        elif arguments.operation=='stop':
            with lock(directory/CONTROL):stopped=stop(directory)
            answer={'state':'stopped' if stopped else 'not_running'}
        else:
            with lock(directory/CONTROL):atomic(directory/RETRY,{'nonce':secrets.token_hex(32),'resume':arguments.resume})
            answer=start(arguments,inputs)
        print(public.encode(answer).decode());return 0
    except (PublishError,views.ConfigError,public.ProjectionError,render.review_state.StateError,OSError,ValueError,TypeError,KeyError):
        error=sys.exc_info()[1]
        code=error.code if isinstance(error,PublishError) else 'configuration_or_source_invalid'
        print(json.dumps({'error':code}));return 1

if __name__=='__main__':raise SystemExit(main())
