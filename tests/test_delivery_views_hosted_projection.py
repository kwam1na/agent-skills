"""Public projection proofs; shared JSON fixtures also bind the hosted validator."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from tests.delivery_views_fixtures import render, config, document_naming, block

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/delivery-views/hosted'
_spec = importlib.util.spec_from_file_location('public_projection', render.__file__.replace('render.py', 'public_projection.py'))
public = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(public)


def source():
    doc = {'state': {'planned': [], 'queue': [{'subject': 'APP-1', 'title': '</script><b>Work</b>', 'lane': 'one',
                           'stage': 'queued', 'head': 'a' * 40, 'runId': 'run-1'}]}}
    doc['state']['meta'] = {'headline': 'Delivery', 'updatedAt': '2026-11-15T00:00:00Z'}
    doc['journal'] = [{'id': 1, 'at': '2026-11-15T00:00:00Z', 'kind': 'review.round', 'lane': 'one',
                       'subject': 'APP-1', 'data': {'round': 1, 'startedAt': '2026-11-15T00:00:00Z',
                       'finishedAt': '2026-11-15T00:00:00Z',
                       'verdicts': {'lens.security': 'changes'}, 'findings': {'P0': 0, 'P1': 1, 'P2': 0, 'P3': 0}}}]
    runs = {'run-1': {'runId': 'run-1', 'roundDetail': [{'round': 1, 'candidateRef': 'a' * 40,
             'at': '2026-11-15T00:00:00Z', 'outcome': 'changes', 'detailsUnavailable': True,
             'verdicts': {'lens.security': 'changes'}, 'findings': {'P0': 0, 'P1': 1, 'P2': 0, 'P3': 0},
             'raised': [{'id': 'f1', 'severity': 'P1', 'lens': 'lens.security', 'summary': '<img onerror=x>',
                         'state': 'unresolved'}], 'briefs': {'lens.security': {'brief': {'text': 'SECRET'}}}}]},
             'unrelated': {'runId': 'unrelated', 'status': 'SECRET'}}
    return doc, runs


class ProjectionTests(unittest.TestCase):
    def project(self, **kw):
        doc, runs = source()
        return public.project(doc, runs, config(), source_fingerprint='1' * 64, **kw)

    def test_candidate_dissent_markers_and_inert_markup_survive(self):
        value = self.project()
        public.validate(value)
        self.assertEqual(value['document']['state']['queue'][0]['head'], 'a' * 40)
        row = value['runs']['run-1']['roundDetail'][0]
        self.assertEqual((row['candidateRef'], row['verdicts']['lens.security'], row['detailsUnavailable']),
                         ('a' * 40, 'changes', True))
        self.assertEqual(row['raised'][0]['summary'], '<img onerror=x>')
        self.assertEqual(value['document']['state']['queue'][0]['title'], '</script><b>Work</b>')
        self.assertNotIn('</script>', render.embed(value))
        self.assertNotIn('SECRET', json.dumps(value))
        self.assertNotIn('unrelated', value['runs'])

    def test_settled_nochange_evidence_omits_local_prose_and_preserves_settlement(self):
        doc, _ = source()
        local = ['Verified /Users/operator/private/proof.json', 'Verified docs/private-proof.md',
                 'Verified --output=/tmp/private-proof.json', r'Verified C:\work\proof.json',
                 'Verified .agent-skills/current/proof.json', 'Verified ../private-proof.json']
        suffix = ' Verified /Users/operator/private/long-proof.json'
        local.append(('Evidence. ' * 400)[:3985 - len(suffix)] + suffix)
        self.assertEqual(len(local[-1]), 3985)
        benign = ['Verified all acceptance criteria without a code change.',
                  'Verified https://example.com/public_docs/proof']
        for evidence in local + benign:
            with self.subTest(evidence=evidence[:80]):
                row = {'subject': 'APP-2', 'title': 'No change needed', 'lane': None, 'stage': 'settled',
                       'noChange': {'evidence': evidence, 'baseHead': 'b' * 40,
                                    'handedAt': '2026-11-15T00:00:00Z', 'settledAt': '2026-11-15T00:01:00Z'}}
                doc['state']['planned'] = [row]
                original = copy.deepcopy(doc)
                value = public.project(doc, {}, config(), source_fingerprint='1' * 64)
                expected = copy.deepcopy(row)
                if evidence in local: expected['noChange']['evidence'] = public.OMITTED_LOCAL_REFERENCE
                self.assertEqual(value['document']['state']['planned'], [expected])
                self.assertEqual(doc, original)
                Draft202012Validator(public.SCHEMA).validate(value); public.validate(value)
                raw = copy.deepcopy(value); raw['document']['state']['planned'][0]['noChange']['evidence'] = evidence
                if evidence in local:
                    with self.assertRaisesRegex(public.ProjectionError, '^snapshot.local_path$'): public.validate(raw)
                else: public.validate(raw)
        doc['state']['planned'][0]['noChange']['evidence'] = 'KNOWN-CANARY beside /tmp/private-proof.json'
        original = copy.deepcopy(doc)
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.configured_secret$'):
            public.project(doc, {}, config(), source_fingerprint='1' * 64, secret_values=['KNOWN-CANARY'])
        self.assertEqual(doc, original)

    def test_local_render_payload_is_the_same_shared_construction(self):
        doc, runs = source()
        data = render.line_map_data(doc, runs, config(), theme_key='theme', history='history.html')
        page = render.render_line_map(doc, runs, config(), theme_key='theme', history='history.html')
        for key, element in [('document', 'delivery-state'), ('runs', 'delivery-runs'),
                             ('reviews', 'delivery-reviews'), ('view', 'delivery-view'), ('links', 'delivery-links')]:
            self.assertEqual(data[key], block(page, element))
        self.assertEqual(self.project()['view']['history'], None)

    def test_private_fields_paths_unknowns_and_configured_canaries(self):
        doc, runs = source()
        doc['state']['queue'][0].update(argv=['TOKEN'], cwd='/Users/private', checks=[{'id': 'lint', 'state': 'failed', 'log': '/tmp/log'}])
        value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        encoded = json.dumps(value)
        for canary in ('TOKEN', '/Users/private', '/tmp/log'):
            self.assertNotIn(canary, encoded)
        for edit in [lambda v: v.update(token='x'), lambda v: v['runs']['run-1'].update(argv=[]),
                     lambda v: v['document']['state']['meta'].update(cwd='x')]:
            bad = copy.deepcopy(value); edit(bad)
            with self.assertRaises(public.ProjectionError): public.validate(bad)
        with self.assertRaises(public.ProjectionError): self.project(secret_values=['Work'])
        doc['state']['queue'][0]['title'] = 'Open /Users/operator/private/file'
        safe=public.project(doc,runs,config(),source_fingerprint='1'*64)
        self.assertEqual(safe['document']['state']['queue'][0]['title'],public.OMITTED_LOCAL_REFERENCE)
        unsafe=copy.deepcopy(safe);unsafe['document']['state']['queue'][0]['title']='Open /Users/operator/private/file'
        with self.assertRaisesRegex(public.ProjectionError,'snapshot.local_path'):public.validate(unsafe)

    def test_workflow_prose_with_local_references_is_explicitly_omitted_before_shared_validation(self):
        base=ROOT/'tests/fixtures/delivery-views/line-map/plan-review'
        doc=json.loads((base/'document.json').read_text())
        plan=render.plan_block(json.loads((base/'plan.json').read_text()))
        doc['state']['finish']=[{'id':'suite','text':'Suite passes inside tests/suite-budget.json','met':False}]
        prose=['Review at docs/plans/approved.md','Source /Users/operator/work/app','Archive /tmp/delivery/state',
               'Read file:///private/run.json','Use C:\\work\\repo','Inspect ~/.config/tool']
        doc['journal']=[{'id':i+1,'at':'2026-11-15T00:00:00Z','kind':'decision.recorded','lane':None,'subject':None,
                         'data':{'text':text,'why':text}} for i,text in enumerate(prose)]
        finish={'id':'suite','label':'Local suite','text':'Checks under scripts/run-tests.py',
                'at':'2026-11-15T00:00:00Z','covers':{'criteria':[],'proofs':[]}}
        plan['plan']['finish']=[finish]
        plan['plan']['approved']['snapshot']['finish']=[{**finish,'text':'Reviewed source skills/run-delivery/SKILL.md'}]
        value=public.project(doc,{},config(),source_fingerprint='1'*64,plan=plan)
        self.assertEqual(value['document']['state']['finish'][0]['text'],public.OMITTED_LOCAL_REFERENCE)
        # An entry publishes its identity and kind only: none of its words reach the page, local or not.
        self.assertEqual([set(row) for row in value['document']['journal']],[{'id','at','kind','lane','subject'}]*len(prose))
        self.assertEqual(value['plan']['plan']['finish'][0]['text'],public.OMITTED_LOCAL_REFERENCE)
        self.assertEqual(value['plan']['plan']['approved']['snapshot']['finish'][0]['text'],public.OMITTED_LOCAL_REFERENCE)
        self.assertEqual(value['document']['state']['finish'][0]['id'],'suite')
        self.assertEqual(len(value['document']['journal']),len(prose))
        Draft202012Validator(public.SCHEMA).validate(value);public.validate(value)
        expected=json.loads((FIXTURES/'valid-omitted-workflow-prose.json').read_text())
        self.assertEqual(value,expected)
        plan['plan']['approved']['review']='https://example.com/public_docs/review'
        linked=public.project(doc,{},config(),source_fingerprint='1'*64,plan=plan)
        self.assertEqual(linked['plan']['plan']['approved']['review'],'https://example.com/public_docs/review')
        plan['plan']['approved']['review']='docs/private-review.md'
        private=public.project(doc,{},config(),source_fingerprint='1'*64,plan=plan)
        self.assertNotIn('review',private['plan']['plan']['approved'])
        doc['state']['finish'][0]['text']='KNOWN-CANARY in /tmp/run'
        with self.assertRaisesRegex(public.ProjectionError,'snapshot.configured_secret'):
            public.project(doc,{},config(),source_fingerprint='1'*64,plan=plan,secret_values=['KNOWN-CANARY'])

    def test_an_entry_publishes_its_identity_and_kind_and_a_round_only_its_named_facts(self):
        doc, runs = source()
        round_facts = copy.deepcopy(doc['journal'][0]['data'])
        doc['journal'][0].update(because=[1], answers=[1], occurredAt='2026-11-15T00:00:00Z')
        doc['journal'][0]['data'].update(refused=2, restarts=1, note='A note in /tmp/x',
                                         findings={**round_facts['findings'], 'P4': 3})
        doc['journal'].append({'id': 2, 'at': '2026-11-15T00:01:00Z', 'kind': 'decision.recorded', 'lane': None,
                               'subject': 'APP-1', 'because': [1], 'answers': [1],
                               'data': {'text': 'Carry it', 'why': 'It holds.'}})
        # A round whose facts don't fit the round's shape publishes as any other entry: its identity alone.
        doc['journal'].append({'id': 3, 'at': '2026-11-15T00:02:00Z', 'kind': 'review.round', 'lane': 'one',
                               'subject': 'APP-1', 'data': {**round_facts, 'verdicts': {'lens.security': 'unavailable'}}})
        value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        self.assertEqual(value['document']['journal'], [
            {'id': 1, 'at': '2026-11-15T00:00:00Z', 'kind': 'review.round', 'lane': 'one', 'subject': 'APP-1',
             'occurredAt': '2026-11-15T00:00:00Z', 'data': round_facts},
            {'id': 2, 'at': '2026-11-15T00:01:00Z', 'kind': 'decision.recorded', 'lane': None, 'subject': 'APP-1'},
            {'id': 3, 'at': '2026-11-15T00:02:00Z', 'kind': 'review.round', 'lane': 'one', 'subject': 'APP-1'}])
        self.assertNotIn('story', value['document'])
        # What an entry may publish is named in the schema, so adding a name is a deliberate edit of this test.
        branches = public.SCHEMA['properties']['document']['properties']['journal']['items']['anyOf']
        members = {'id', 'at', 'kind', 'lane', 'subject', 'occurredAt'}
        self.assertEqual([set(branch['properties']) for branch in branches], [members | {'data'}, members])
        self.assertEqual(set(public.JOURNAL_MEMBERS), members)
        # An occurrence is a time, or null for one unknown, and nothing else.
        for branch in branches:
            occurred = branch['properties']['occurredAt']
            self.assertEqual([option.get('type') for option in occurred['anyOf']], ['string', 'null'])
        for when, valid in (('2026-11-15T00:00:00Z', True), (None, True), ('yesterday', False)):
            with self.subTest(occurredAt=when):
                timed = copy.deepcopy(value)
                timed['document']['journal'][1]['occurredAt'] = when
                if valid:
                    public.validate(timed)
                else:
                    with self.assertRaises(public.ProjectionError):
                        public.validate(timed)
        self.assertEqual(set(branches[0]['properties']['data']['properties']),
                         {'round', 'startedAt', 'finishedAt', 'verdicts', 'findings'})
        self.assertEqual(set(branches[0]['properties']['data']['properties']['findings']['properties']), {'P0', 'P1', 'P2', 'P3'})
        # A kind the registry gains later, with prose facts, publishes none of them; a kind it doesn't list is refused,
        # wherever it stands in the journal.
        words = public.render.narrator.journal_words
        registry = json.loads(Path(words.REGISTRY_FILE).read_text(encoding="utf-8"))
        registry['kinds']['lease.extended'] = {**registry['kinds']['lease.granted'], 'data': {'why': {'type': 'prose'}}}
        planted = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'journal-kinds.json'
        planted.write_text(json.dumps(registry), encoding='utf-8')
        doc['journal'].insert(1, {'id': 4, 'at': '2026-11-15T00:02:00Z', 'kind': 'lease.extended', 'lane': 'one',
                                  'subject': None, 'data': {'why': 'A secret reason in /tmp/x'}})
        with self.assertRaisesRegex(public.ProjectionError, 'snapshot.unregistered_kind'):
            public.project(doc, runs, config(), source_fingerprint='1' * 64)
        with mock.patch.object(words, "REGISTRY_FILE", planted):
            value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        self.assertEqual(value['document']['journal'][1],
                         {'id': 4, 'at': '2026-11-15T00:02:00Z', 'kind': 'lease.extended', 'lane': 'one', 'subject': None})
        public.validate(value)
        # A snapshot of the older shape is refused, not half-read.
        older = {**copy.deepcopy(value), 'schemaVersion': 'hosted-delivery-snapshot/1'}
        with self.assertRaises(public.ProjectionError):
            public.validate(older)

    def test_the_integration_step_publishes_its_kinds_by_identity_and_no_head_or_drift(self):
        doc, runs = source()
        doc['state']['meta']['integration'] = {'base': 'integrate/app', 'target': 'main', 'remote': 'origin'}
        head, target = 'c' * 40, 'd' * 40
        facts = {'integration.merged': {'target': 'main', 'targetHead': target, 'head': head, 'conflicts': ['a.py']},
                 'integration.suite': {'head': head, 'result': 'over-budget', 'seconds': 12, 'budgetSeconds': 10},
                 'integration.requested': {'changeRequest': 'https://forge.test/pull/7', 'head': head},
                 'integration.landed': {'target': 'main', 'head': target}}
        for number, (kind, data) in enumerate(facts.items(), start=2):
            doc['journal'].append({'id': number, 'at': '2026-11-15T00:01:00Z', 'kind': kind, 'lane': None,
                                   'subject': 'APP', 'data': data})
        round_facts = copy.deepcopy(doc['journal'][0]['data'])
        doc['journal'].append({'id': 6, 'at': '2026-11-15T00:02:00Z', 'kind': 'review.round', 'lane': None,
                               'subject': 'APP', 'data': {**round_facts, 'head': head}})
        # Even where the renderer can count the drift, the published cut carries none.
        with mock.patch.object(public.render, 'integration_drift', return_value={'moved': 3, 'behind': 1}):
            local = public.render.line_map_data(doc, {}, config(), theme_key='local', history=None)
            value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        self.assertEqual(local['integration'], {'drift': {'moved': 3, 'behind': 1}})
        self.assertNotIn('integration', value)
        public.validate(value)
        self.assertEqual(value['document']['journal'][1:], [
            *({'id': number, 'at': '2026-11-15T00:01:00Z', 'kind': kind, 'lane': None, 'subject': 'APP'}
              for number, kind in enumerate(facts, start=2)),
            {'id': 6, 'at': '2026-11-15T00:02:00Z', 'kind': 'review.round', 'lane': None, 'subject': 'APP',
             'data': round_facts}])
        published = json.dumps(value)
        for private in (head, target, 'forge.test', 'drift'):
            with self.subTest(private=private):
                self.assertNotIn(private, published)

    def test_the_receiver_does_not_police_kinds_and_the_projection_does(self):
        # On purpose: the service accepts any kind by its name's shape, so a kind the registry gains needs no service
        # deploy. Only the projection publishes registered kinds.
        value = json.loads((FIXTURES / 'valid-unregistered-kind.json').read_text())
        self.assertEqual(value['document']['journal'][-1]['kind'], 'lease.extended')
        public.validate(value)
        doc, runs = source()
        doc['journal'].append({**value['document']['journal'][-1], 'data': {}})
        with self.assertRaisesRegex(public.ProjectionError, 'snapshot.unregistered_kind'):
            public.project(doc, runs, config(), source_fingerprint='1' * 64)

    def test_no_link_comes_from_an_entrys_facts(self):
        doc, runs = source()
        doc['journal'].append({'id': 2, 'at': '2026-11-15T00:01:00Z', 'kind': 'candidate.handed', 'lane': 'one',
                               'subject': 'APP-1', 'data': {'head': 'c' * 40, 'checks': [],
                                                            'changeRequest': 'https://github.com/example/app/pull/99'}})
        # A commit a fact's words name cut short is no link either: the words are not published.
        doc['journal'].append({'id': 3, 'at': '2026-11-15T00:02:00Z', 'kind': 'decision.recorded', 'lane': None,
                               'subject': 'APP-1', 'data': {'text': 'Reverted 1a2b3c4', 'why': 'It broke the build.'}})
        cfg = config(forges=[{'name': 'GitHub', 'repository': 'example/app',
                             'pullUrl': 'https://github.com/{repository}/pull/{number}',
                             'commitUrl': 'https://github.com/{repository}/commit/{sha}'}])
        with mock.patch.object(public.render, 'with_story', side_effect=AssertionError('the projection told a story')):
            value = public.project(doc, runs, cfg, source_fingerprint='1' * 64)
        # The commit a run's round reviewed is still linked, from the published run.
        self.assertIn('a' * 40, value['links']['commits'])
        self.assertNotIn('c' * 40, value['links']['commits'])
        self.assertNotIn('1a2b3c4', value['links']['commits'])
        self.assertNotIn('https://github.com/example/app/pull/99', value['links']['changes'])
        self.assertNotIn('c' * 40, json.dumps(value))
        self.assertNotIn('1a2b3c4', json.dumps(value))
        # The page offers the same links locally, from the facts its opened beats show.
        local = render.line_map_data(doc, runs, cfg, theme_key='local', history=None)
        self.assertIn('c' * 40, local['links']['commits'])
        self.assertIn('1a2b3c4', local['links']['commits'])
        self.assertIn('https://github.com/example/app/pull/99', local['links']['changes'])

    def test_unsafe_links_are_not_published(self):
        doc, runs = source()
        doc['state']['queue'][0]['changeRequest'] = 'javascript:alert(1)'
        value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        self.assertEqual(value['links']['changes'], {})
        bad = copy.deepcopy(value); bad['links']['issues']['APP-1'] = 'https://user:pass@example.com/a'
        with self.assertRaises(public.ProjectionError): public.validate(bad)
        doc['state']['queue'][0]['changeRequest'] = 'https://github.com/example/app/pull/12'
        cfg = config(forges=[{'name': 'GitHub', 'repository': 'example/app',
                             'pullUrl': 'https://github.com/{repository}/pull/{number}',
                             'commitUrl': 'https://github.com/{repository}/commit/{sha}'}])
        linked = public.project(doc, runs, cfg, source_fingerprint='1' * 64)
        self.assertEqual(linked['links']['changes']['https://github.com/example/app/pull/12']['label'], '#12')

    def test_a_waiting_lane_projects_with_its_state_and_without_the_tools_own_members(self):
        doc, _ = source()
        doc['state']['lanes'] = [{'id': 'one', 'title': 'Lane one', 'focus': 'waiting for APP-1', 'state': 'waiting',
                                  'waitingOn': 'APP-1', 'since': '2026-11-15T00:00:00Z',
                                  'waitingFrom': {'state': 'working', 'at': '2026-11-15T00:00:00Z'},
                                  'restedFrom': {'state': 'waiting', 'at': '2026-11-15T00:00:00Z'}}]
        value = public.project(doc, {}, config(), source_fingerprint='1' * 64)
        [lane] = value['document']['state']['lanes']
        self.assertEqual((lane['state'], lane['waitingOn'], lane['restedFrom']['state']), ('waiting', 'APP-1', 'waiting'))
        self.assertNotIn('waitingFrom', lane)
        public.validate(value)

    def test_the_lead_listener_item_projects_as_it_is_written(self):
        doc, _ = source()
        item = {'holding': False, 'source': 'lead-wait.json in the record',
                'text': "Lane 1 and lane 2 can't reach the lead. A hand-off or question from them will wait unseen "
                        "until the lead is watching again.", 'clears': 'the lead is watching again'}
        needs = {'running': True, 'at': '2026-11-15T00:00:00Z', 'staleAfter': '2026-11-15T00:02:00Z', 'items': [item]}
        value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
        self.assertEqual(value['needs']['items'], [item])
        public.validate(value)

    def test_a_needs_you_row_projects_with_its_facts_and_words_and_the_list_with_its_own(self):
        doc, _ = source()
        item = {'holding': True, 'source': "lane-1's state", 'text': 'lane-1 is blocked: which API',
                'clears': "the lane's state changing", 'kind': 'laneBlocked', 'who': 'lead', 'lane': 'lane-1',
                'subject': 'APP-2', 'state': 'building', 'since': '2026-11-15T00:00:00Z',
                'blocker': {'subject': 'APP-1', 'lane': 'lane-2', 'state': 'stopped'},
                'said': {'headline': 'Lane 1 is blocked on APP-2', 'why': 'which API',
                         'clears': "Clears when Lane 1's state changes.", 'who': 'The lead'}}
        needs = {'running': True, 'at': '2026-11-15T00:00:00Z', 'staleAfter': '2026-11-15T00:02:00Z', 'items': [item],
                 'summary': "1 thing needs the lead, and it's holding up the finish.",
                 'words': {'heading': 'Waiting on someone', 'holding': 'Holding up the finish', 'age': 'for {age}'}}
        value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
        self.assertEqual((value['needs']['items'], value['needs']['words'], value['needs']['summary']),
                         ([item], needs['words'], needs['summary']))
        public.validate(value)

    def test_a_need_naming_a_command_omits_option_paths_without_dropping_the_need(self):
        doc, _ = source()
        prefix = ("The lead's wake: lane-2 (working). Start one in the "
                  "background: python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py "
                  "--delivery APP-1 lead-wait --after 187 --timeout 1800.0 --poll 15.0 ")
        local_options = [
            '--output=/Users/operator/work/.orchestration/flow-log/APP-1.wait.json',
            '--output=/tmp/wait.json', '--output="/Users/operator/work/wait.json"',
            "--output='/tmp/wait.json'", '--output /tmp/wait.json',
            r'--output=C:\work\wait.json', '--output=C:/work/wait.json',
            r'--output=\\server\share\wait.json', '--output=~/work/wait.json',
            '--output="~/work/wait.json"', '--output=./wait.json', '--output=../wait.json',
            r'--output=.\wait.json', r'--output=..\wait.json',
            '--output=.orchestration/wait.json', '--output=docs/wait.json',
            r'--output=docs\wait.json', '--output=file:///tmp/wait.json',
            '--output=.agent-skills/current/wait.json',
            'python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py',
            'Read `/Users/operator/private/wait.json`', 'Inspect `docs/private-plan.md`',
            '--output=`/Users/operator/private/wait.json`', '`--output=/Users/operator/private/wait.json`',
            'Read “/Users/operator/private/wait.json”',
            'Read ‘.agent-skills/current/run.py’', 'Output:/Users/operator/private/wait.json',
            '[/Users/operator/private/wait.json]', '[docs/private-plan.md]',
            r'Output:[C:\work\wait.json]', 'Read “~/work/wait.json”', 'Read `../wait.json`',
            '{docs/private-plan.md}', '<./wait.json>', 'Output;/tmp/wait.json', 'Output,/tmp/wait.json',
            'Read **/Users/operator/private/wait.json**', 'Inspect **docs/private-plan.md**',
            'Read _/Users/operator/private/wait.json_', 'Read ~~/Users/operator/private/wait.json~~',
        ]
        benign_options = ['--output=https://example.com/public/wait.json', '--timeout=1800.0',
                          '--output=summary.json', '--mode=public', 'Review https://example.com/docs/wait.json',
                          '`https://example.com/docs/wait.json`', '“https://example.com/docs/wait.json”',
                          '[https://example.com/docs/wait.json]', '‘https://example.com/docs/wait.json’',
                          '<https://example.com/docs/wait.json>', 'Output:summary.json', '[ready]', '`--mode=public`']
        # Exercise the actual private executable separately: it must not mask
        # failures in option detection or turn harmless control rows into omissions.
        actual_warning = prefix + '--timeout=1800.0'
        prefix = prefix.replace('.agent-skills/current/skills/run-delivery/scripts/run_delivery.py', 'run-delivery')
        for option in local_options + benign_options:
            with self.subTest(option=option):
                item = {'holding': False, 'source': 'lead-wait.json in the record', 'text': prefix + option,
                        'clears': 'the lead is watching again'}
                needs = {'running': True, 'at': '2026-11-15T00:00:00Z',
                         'staleAfter': '2026-11-15T00:02:00Z', 'items': [item]}
                value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
                projected = value['needs']['items'][0]
                self.assertEqual(projected, {**item, 'text': public.OMITTED_LOCAL_REFERENCE}
                                 if option in local_options else item)
                self.assertEqual(needs['items'][0], item)  # private warning is retained
                Draft202012Validator(public.SCHEMA).validate(value)
                raw = copy.deepcopy(value); raw['needs']['items'][0]['text'] = item['text']
                if option in local_options:
                    with self.assertRaisesRegex(public.ProjectionError, '^snapshot.local_path$'):
                        public.validate(raw)
                else:
                    public.validate(raw)
        needs['items'][0]['text'] = actual_warning
        value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
        self.assertEqual(value['needs']['items'][0]['text'], public.OMITTED_LOCAL_REFERENCE)
        raw = copy.deepcopy(value); raw['needs']['items'][0]['text'] = actual_warning
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.local_path$'):
            public.validate(raw)

    def test_local_structural_references_fail_closed_instead_of_becoming_prose_markers(self):
        for key in ('candidateRef','runId'):
            doc,runs=source()
            if key=='candidateRef':runs['run-1']['roundDetail'][0][key]='docs/candidates/reviewed-tree'
            else:
                doc['state']['queue'][0]['runId']='docs/runs/current'
                runs={'docs/runs/current':{**runs['run-1'],'runId':'docs/runs/current'}}
            with self.subTest(key=key),self.assertRaises(public.ProjectionError):
                public.project(doc,runs,config(),source_fingerprint='1'*64)

    def test_punctuation_wrappers_preserve_privacy_and_public_url_components(self):
        punctuation = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~“”‘’"
        local = ['/Users/operator/private/wait.json', 'docs/private-plan.md', r'C:\work\wait.json',
                 '.agent-skills/current/run.py', '~/work/wait.json', './wait.json', '../wait.json']
        benign = ['https://public_docs.example.com/part_docs/file', 'https://example.com/public_docs/file',
                  'HTTPS://example.com/public_docs/file', 'https://public-docs.example.com/.docs/file',
                  '--mode=public', 'ready']
        doc, _ = source()
        for prefix in punctuation:
            for text in local + benign:
                with self.subTest(prefix=prefix, text=text):
                    # These separators create actual UNC/absolute paths even
                    # around a nominal control; they are not benign wrappers.
                    private = text in local or prefix == '\\' or (prefix == '/' and text == 'ready')
                    original = f'Read {prefix}{prefix}{text}{prefix}{prefix}'
                    needs = {'running': True, 'at': '2026-11-15T00:00:00Z', 'staleAfter': '2026-11-15T00:02:00Z',
                             'items': [{'holding': False, 'source': 'lead-wait.json in the record', 'text': original,
                                        'clears': 'a running lead-wait'}]}
                    value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
                    self.assertEqual(value['needs']['items'][0]['text'],
                                     public.OMITTED_LOCAL_REFERENCE if private else original)
                    self.assertEqual(needs['items'][0]['text'], original)
                    raw = copy.deepcopy(value); raw['needs']['items'][0]['text'] = original
                    if private:
                        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.local_path$'): public.validate(raw)
                    else: public.validate(raw)

    def test_url_exception_does_not_hide_malformed_urls_adjacent_paths_or_secrets(self):
        doc, _ = source()
        local = ['https:///Users/operator/private/wait.json', 'https://user:pass@example.com/docs/private.md',
                 'Read "https://example.com/public_docs/file"/Users/operator/private/wait.json',
                 'Read `https://example.com/public_docs/file`docs/private-plan.md']
        for text in local:
            with self.subTest(text=text):
                needs = {'running': True, 'at': '2026-11-15T00:00:00Z', 'staleAfter': '2026-11-15T00:02:00Z',
                         'items': [{'holding': False, 'source': 'listener', 'text': text, 'clears': 'listener restored'}]}
                value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
                self.assertEqual(value['needs']['items'][0]['text'], public.OMITTED_LOCAL_REFERENCE)
                value['needs']['items'][0]['text'] = text
                with self.assertRaisesRegex(public.ProjectionError, '^snapshot.local_path$'): public.validate(value)
        needs['items'][0]['text'] = 'https://example.com/public_docs/KNOWN-CANARY'
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.configured_secret$'):
            public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs, secret_values=['KNOWN-CANARY'])
        value['needs']['items'][0]['text'] = needs['items'][0]['text']
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.configured_secret$'):
            public.validate(value, secret_values=['KNOWN-CANARY'])

    def test_url_wrapper_types_distinguish_public_paths_from_local_suffixes(self):
        doc, _ = source()
        pairs = {'(': ')', '[': ']', '{': '}'}
        for opening, balanced_closing in pairs.items():
            for closing in (')', ']', '}'):
                with self.subTest(opening=opening, closing=closing):
                    text = f'Read [public](https://example.com/{opening}nested{closing}/Users/operator/private/wait.json)'
                    balanced = closing == balanced_closing
                    needs = {'running': True, 'at': '2026-11-15T00:00:00Z', 'staleAfter': '2026-11-15T00:02:00Z',
                             'items': [{'holding': False, 'source': 'listener', 'text': text, 'clears': 'listener restored'}]}
                    value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
                    self.assertEqual(value['needs']['items'][0]['text'], text if balanced else public.OMITTED_LOCAL_REFERENCE)
                    self.assertEqual(needs['items'][0]['text'], text)
                    value['needs']['items'][0]['text'] = text
                    if balanced:
                        public.validate(value)
                    else:
                        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.local_path$'): public.validate(value)

    def test_completed_url_wrappers_leave_adjoining_local_prose_visible(self):
        doc, _ = source()
        private = ['[public](https://example.com/public_docs/file)/Users/operator/private/wait.json',
                   '[public](https://example.com/public_docs/file)docs/private-plan.md',
                   '(https://example.com/public_docs/file)/Users/operator/private/wait.json',
                   '[public](https://example.com/public_docs/file)[docs/private-plan.md]',
                   '[public](https://example.com/public_docs/file){docs/private-plan.md}',
                   '[public](https://example.com/public_docs/file)**/Users/operator/private/wait.json**',
                   '[public](https://example.com/(nested/(docs/file)))/Users/operator/private/wait.json',
                   '[public](https://example.com/public_docs/file)_docs/private-plan.md_',
                   '[public](https://example.com/public_docs/file)~~docs/private-plan.md~~']
        benign = ['[public](https://example.com/public_docs/file)', '(https://example.com/public_docs/file)',
                  'https://example.com/(docs/file)', 'https://example.com/(outer/(docs/file))',
                  '[public](https://example.com/(outer/(docs/file)))', 'https://example.com/docs/file;more',
                  'https://example.com/docs/file,more', '(https://example.com/docs/file)(https://example.com/docs/next)',
                  '[public](https://example.com/public_docs/file)ready', 'https://[::1]/docs/file']
        for text in private + benign:
            with self.subTest(text=text):
                needs = {'running': True, 'at': '2026-11-15T00:00:00Z', 'staleAfter': '2026-11-15T00:02:00Z',
                         'items': [{'holding': False, 'source': 'listener', 'text': text, 'clears': 'listener restored'}]}
                value = public.project(doc, {}, config(), source_fingerprint='1' * 64, needs=needs)
                self.assertEqual(value['needs']['items'][0]['text'], public.OMITTED_LOCAL_REFERENCE if text in private else text)
                self.assertEqual(needs['items'][0]['text'], text)
                value['needs']['items'][0]['text'] = text
                if text in private:
                    with self.assertRaisesRegex(public.ProjectionError, '^snapshot.local_path$'): public.validate(value)
                else: public.validate(value)

    def test_byte_bound_and_credential_shaped_map_fields_are_refused(self):
        value = self.project()
        self.assertEqual(public.MAX_BYTES, 16 * 1024 * 1024)
        # Observe the exact inclusive byte boundary without allocating a large
        # repeated JSON body or changing the contractual limit under test.
        class SizedBytes:
            def __init__(self, length): self.length = length
            def __len__(self): return self.length
        with mock.patch.object(public, 'encode', return_value=SizedBytes(16 * 1024 * 1024)):
            public.validate(value)
        with mock.patch.object(public, 'encode', return_value=SizedBytes(16 * 1024 * 1024 + 1)):
            with self.assertRaisesRegex(public.ProjectionError, '^snapshot.byte_bound$'): public.validate(value)
        value['view']['lenses']['token'] = {'name': 'Credential-shaped field', 'icon': None}
        with self.assertRaises(public.ProjectionError): public.validate(value)

    def test_per_lens_evidence_markers_survive_without_raw_text(self):
        doc, runs = source()
        runs['run-1']['roundDetail'][0]['briefs'] = {
            'lens.missing': {'brief': {'unavailable': True}, 'result': None},
            'lens.clipped': {'brief': {'text': 'RAW BRIEF CANARY', 'shortened': True},
                             'result': {'text': 'RAW RESULT CANARY', 'shortened': False}}}
        value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        row = value['runs']['run-1']['roundDetail'][0]
        self.assertEqual(row['briefs'], {
            'lens.missing': {'brief': {'unavailable': True}, 'result': None},
            'lens.clipped': {'brief': {'unavailable': True, 'shortened': True},
                             'result': {'unavailable': True, 'shortened': False}}})
        self.assertTrue(row['clipped']['texts'])
        self.assertNotIn('RAW BRIEF CANARY', json.dumps(value))
        self.assertNotIn('RAW RESULT CANARY', json.dumps(value))

    def test_populated_progress_chapters_survive(self):
        doc, runs = source()
        state = doc['state']
        state.update(lanes=[{'id': 'one', 'title': 'Lane one', 'focus': 'Checking', 'state': 'working',
                             'waitingOn': 'lead', 'since': '2026-11-15T00:00:00Z'}],
                     finish=[{'id': 'all', 'text': 'Every criterion', 'met': False}],
                     leases=[{'id': 'browser', 'holder': 'one', 'since': '2026-11-15T00:00:00Z'}],
                     followups=[{'id': 'later', 'title': 'Preserve follow-up', 'reference': 'APP-2'}],
                     tickets={'APP-1': {'title': 'Public title', 'summary': 'Progress details'}})
        state['meta']['checking'] = {'now': None, 'next': ['APP-1']}
        doc.update(stats={'APP-1': {'rounds': 1, 'findings': {'P0': 0, 'P1': 1, 'P2': 0, 'P3': 0}}},
                   estimates={'APP-1': {'position': 1, 'expectedStart': None}}, focus={'one': 'Checking'})
        value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        for key in ('lanes', 'finish', 'leases', 'followups', 'tickets'):
            self.assertEqual(value['document']['state'][key], state[key])
        for key in ('stats', 'estimates', 'focus', 'journal'):
            self.assertEqual(value['document'][key], doc[key])
        self.assertEqual(value['document']['state']['meta']['checking'], state['meta']['checking'])

    def test_native_review_truth_and_unavailability_survive_without_raw_outcomes(self):
        doc = json.loads((ROOT / 'tests/fixtures/delivery-views/round-card-deferred/document.json').read_text())
        expected = render.line_map_data(doc, {}, config(), theme_key='hosted-delivery', history=None)['reviews']
        value = public.project(doc, {}, config(), source_fingerprint='1' * 64)
        self.assertEqual(value['reviews'], expected)
        self.assertTrue(value['reviews']['WORK-1']['roundDetail'])
        self.assertNotIn('reviewEvidence', value['document'])
        self.assertNotIn('outcomes', json.dumps(value))
        # The commit a native review reviewed is linked, from the published review.
        cfg = config(forges=[{'name': 'GitHub', 'repository': 'example/app',
                             'pullUrl': 'https://github.com/{repository}/pull/{number}',
                             'commitUrl': 'https://github.com/{repository}/commit/{sha}'}])
        reviewed = {row['candidateRef'] for row in value['reviews']['WORK-1']['roundDetail']}
        self.assertTrue(reviewed)
        self.assertLessEqual(reviewed, set(public.project(doc, {}, cfg, source_fingerprint='1' * 64)['links']['commits']))
        doc['reviewEvidence']['WORK-1'] = {'unavailable': True}
        unavailable = public.project(doc, {}, config(), source_fingerprint='1' * 64)
        self.assertEqual(unavailable['reviews']['WORK-1'], {'unavailable': True})

    def test_clipped_plan_and_needs_keep_the_existing_bounded_meaning(self):
        base = ROOT / 'tests/fixtures/delivery-views/line-map/plan-review'
        doc = json.loads((base / 'document.json').read_text())
        plan = render.plan_block(json.loads((base / 'plan.json').read_text()), share=1)
        needs = {'running': True, 'at': '2026-11-15T00:00:00Z', 'staleAfter': '2026-11-15T00:02:00Z',
                 'items': [{'holding': True, 'source': 'review', 'text': 'Fix dissent', 'clears': 'A new round'}], 'more': 2}
        value = public.project(doc, {}, config(), source_fingerprint='1' * 64, plan=plan, needs=needs)
        self.assertEqual((value['plan'], value['needs']), (plan, needs))
        self.assertTrue(value['plan']['clipped']['overShare'])
        self.assertTrue(value['plan']['plan']['planReview']['rounds'])

    def test_secrets_in_plan_review_and_needs_are_rejected_recursively(self):
        base = ROOT / 'tests/fixtures/delivery-views/line-map/plan-review'
        doc = json.loads((base / 'document.json').read_text())
        clean_plan = render.plan_block(json.loads((base / 'plan.json').read_text()))
        clean_needs = {'running': True, 'at': '2026-11-15T00:00:00Z',
                       'items': [{'holding': True, 'source': 'review', 'text': 'Fix dissent', 'clears': 'A new round'}]}
        public.project(doc, {}, config(), source_fingerprint='1' * 64, plan=clean_plan, needs=clean_needs,
                       secret_values=['KNOWN-CREDENTIAL-CANARY'])
        for placement in ('plan', 'review', 'needs'):
            plan, needs = copy.deepcopy(clean_plan), copy.deepcopy(clean_needs)
            if placement == 'plan': plan['plan']['outcome']['text'] = 'KNOWN-CREDENTIAL-CANARY'
            elif placement == 'review':
                plan['plan']['planReview']['rounds'][0]['raised'][0]['summary'] = 'KNOWN-CREDENTIAL-CANARY'
            else: needs['items'][0]['text'] = 'KNOWN-CREDENTIAL-CANARY'
            with self.subTest(placement=placement):
                with self.assertRaisesRegex(public.ProjectionError, '^snapshot.configured_secret$'):
                    public.project(doc, {}, config(), source_fingerprint='1' * 64, plan=plan, needs=needs,
                                   secret_values=['KNOWN-CREDENTIAL-CANARY'])

    def test_source_cut_rejects_mutation_missing_to_present_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / 'state.json'; path.write_text('old')
            def mutate(): path.write_text('new'); return self.project()
            with self.assertRaises(public.SourceChanged): public.capture(root, ['state.json'], mutate)
            def appear(): (root / 'plan.json').write_text('{}'); return self.project()
            with self.assertRaises(public.SourceChanged): public.capture(root, ['state.json', 'plan.json'], appear)
            (root / 'escape').symlink_to(root.parent)
            with self.assertRaises(public.ProjectionError): public.capture(root, ['escape/private'], self.project)
            value = public.capture(root, ['state.json', 'plan.json'], self.project)
            self.assertRegex(value['sourceFingerprint'], '^[a-f0-9]{64}$')
            same = public.capture(root, ['state.json', 'plan.json'], self.project)
            self.assertEqual(value['sourceFingerprint'], same['sourceFingerprint'])
            path.write_text('different source')
            changed = public.capture(root, ['state.json', 'plan.json'], self.project)
            self.assertNotEqual(changed['sourceFingerprint'], value['sourceFingerprint'])
            path.unlink(); path.write_text('different source')
            replaced = public.capture(root, ['state.json', 'plan.json'], self.project)
            self.assertNotEqual(replaced['sourceFingerprint'], changed['sourceFingerprint'])
            def added_review(): (root / 'round.json').write_text('{}'); return self.project()
            with self.assertRaises(public.SourceChanged):
                public.capture(root, lambda: [p.name for p in root.glob('*.json')], added_review)
            with mock.patch.object(public.time, 'monotonic', side_effect=[0, 0, 61]):
                with self.assertRaises(public.SourceChanged): public.capture(root, ['state.json'], self.project)

    def test_shared_fixtures_have_meaningful_acceptance_and_rejection(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        self.assertTrue(any(row['valid'] for row in manifest['cases']))
        self.assertTrue(any(not row['valid'] for row in manifest['cases']))
        for row in manifest['cases']:
            with self.subTest(row=row['file']):
                value = json.loads((FIXTURES / row['file']).read_text())
                if row['valid']:
                    public.validate(value)
                    Draft202012Validator(public.SCHEMA).validate(value)
                    self.assertTrue(value['document']['state']['queue'] or value.get('plan'))
                else:
                    with self.assertRaises(public.ProjectionError): public.validate(value)
                    if row['layer'] == 'schema':
                        self.assertTrue(list(Draft202012Validator(public.SCHEMA).iter_errors(value)))
