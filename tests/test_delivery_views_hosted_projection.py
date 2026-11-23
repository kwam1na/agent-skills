"""Hosted snapshot proofs; shared JSON fixtures also bind the hosted validator.

The operator ruled on 2026-11-20 that the hosted page shows what the local live view shows, from the same record, with
no privacy redactions: the one rule kept is that no credential or configured secret value is ever published. These
tests hold the snapshot to the local page's own cut and to that one rule, and pin the withdrawn rule's absence.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from tests.delivery_views_fixtures import render, config, block

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/delivery-views/hosted'
_spec = importlib.util.spec_from_file_location('public_projection', render.__file__.replace('render.py', 'public_projection.py'))
public = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(public)
_hosted_spec = importlib.util.spec_from_file_location('hosted_for_projection', render.__file__.replace('render.py', 'hosted.py'))
hosted = importlib.util.module_from_spec(_hosted_spec)
_hosted_spec.loader.exec_module(hosted)

FORGE = config(forges=[{'name': 'GitHub', 'repository': 'example/app',
                        'pullUrl': 'https://github.com/{repository}/pull/{number}',
                        'commitUrl': 'https://github.com/{repository}/commit/{sha}'}])
SIBLING = 'https://deliveries.example.com/d/' + 'e' * 64
MERGE_NONCE = '0123456789abcdef' * 2


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
                         'state': 'unresolved'}], 'briefs': {'lens.security': {'brief': {'text': 'The brief'}}}}]},
             'unrelated': {'runId': 'unrelated', 'status': 'not named by the record'}}
    return doc, runs


def full_record():
    """A record on an integration base with the facts, prose, paths, evidence and blocks the local page draws."""
    base = ROOT / 'tests/fixtures/delivery-views/line-map/integration'
    doc = json.loads((base / 'document.json').read_text(encoding='utf-8'))
    last = doc['journal'][-1]
    doc['journal'].append({'id': last['id'] + 1, 'at': last['at'], 'kind': 'decision.recorded', 'lane': None,
                           'subject': None, 'because': [last['id']],
                           'data': {'text': 'Keep the suite under tests/suite-budget.json',
                                    'why': 'Measured with --output=/Users/operator/work/.orchestration/wait.json'}})
    # A lane's authorized base merge, as the state tool journals it, with the nonce its merge commit carries.
    doc['journal'].append({'id': last['id'] + 2, 'at': last['at'], 'kind': 'base.merge.authorized', 'lane': 'one',
                           'subject': doc['state']['queue'][0]['subject'],
                           'data': {'reviewed': 'a' * 40, 'baseCommits': ['b' * 40], 'token': MERGE_NONCE}})
    doc['state']['finish'] = [{'id': 'suite', 'text': 'Suite passes under scripts/run-tests.py', 'met': False}]
    doc['state']['siblings'] = [{'name': 'web', 'link': SIBLING, 'addedAt': last['at']}]
    plan = render.plan_block(json.loads((ROOT / 'tests/fixtures/delivery-views/line-map/plan-review/plan.json')
                                        .read_text(encoding='utf-8')))
    needs = {'running': True, 'at': last['at'], 'staleAfter': '2099-01-01T00:00:00Z',
             'items': [{'holding': True, 'source': 'lead-wait.json in the record', 'clears': 'the lead is watching',
                        'text': 'Start it: python3 -B .agent-skills/current/skills/run-delivery/scripts/run_delivery.py '
                                'lead-wait --output=/tmp/wait.json'}]}
    files = {'at': last['at'], 'lanes': {}, 'files': {}, 'more': 0}
    runs = {'run-1': {'runId': 'run-1', 'roundDetail': [{'round': 1, 'candidateRef': 'a' * 40,
                      'at': last['at'], 'outcome': 'changes', 'verdicts': {'lens.security': 'changes'},
                      'findings': {'P0': 0, 'P1': 1, 'P2': 0, 'P3': 0},
                      'raised': [{'id': 'f1', 'severity': 'P1', 'lens': 'lens.security',
                                  'summary': 'Pin it in tests/test_a.py', 'state': 'unresolved'}],
                      'briefs': {'lens.security': {'brief': {'text': 'Read docs/plans/approved.md first'},
                                                   'result': {'text': 'Raised one finding'}}}}]}}
    for item in doc['state']['queue'][:1]:
        item['runId'] = 'run-1'
    return doc, runs, plan, needs, files


def project_full(**kw):
    doc, runs, plan, needs, files = full_record()
    with mock.patch.object(public.render, 'integration_drift', return_value={'moved': 3, 'behind': 1}):
        return public.project(doc, runs, FORGE, source_fingerprint='1' * 64, plan=plan, needs=needs, files=files, **kw)


class ProjectionTests(unittest.TestCase):
    def project(self, **kw):
        doc, runs = source()
        return public.project(doc, runs, config(), source_fingerprint='1' * 64, **kw)

    def test_the_snapshot_is_the_local_live_views_own_cut(self):
        doc, runs, plan, needs, files = full_record()
        with mock.patch.object(public.render, 'integration_drift', return_value={'moved': 3, 'behind': 1}):
            local = render.line_map_data(doc, runs, FORGE, theme_key='hosted-delivery', history=None,
                                         plan=plan, needs=needs, files=files)
            value = public.project(doc, runs, FORGE, source_fingerprint='1' * 64, plan=plan, needs=needs, files=files)
        # Every block the local page embeds is published as the local page has it. The differences are the sibling's
        # hosted link, a read capability, and a base merge's nonce, which the local page takes for a commit.
        expected = copy.deepcopy(local)
        for entry in expected['document']['journal']:
            if entry['kind'] == 'base.merge.authorized':
                entry['data'].pop('token')
                expected['document']['story'] = value['document']['story']
        expected['links']['commits'].pop(MERGE_NONCE)
        for row in expected['document']['state']['siblings']:
            row.pop('link')
        for row in expected['document'].get('across', {}).get('groups', []):
            row.pop('link', None)
        self.assertEqual({key: value[key] for key in expected}, expected)
        self.assertEqual(set(value) - set(expected), {'schemaVersion', 'sourceFingerprint'})
        self.assertEqual(value['schemaVersion'], 'hosted-delivery-snapshot/3')
        self.assertEqual(value['view']['history'], None)
        Draft202012Validator(public.SCHEMA).validate(value)
        public.validate(value)
        # The local page's own blocks are the same construction.
        page = render.render_line_map(doc, runs, FORGE, theme_key='hosted-delivery', history=None, plan=plan,
                                      needs=needs, files=files)
        for key, element in [('document', 'delivery-state'), ('runs', 'delivery-runs'), ('reviews', 'delivery-reviews'),
                             ('links', 'delivery-links'), ('needs', 'delivery-needs'), ('files', 'delivery-files')]:
            self.assertEqual(local[key], block(page, element))

    def test_the_full_record_publishes_its_story_facts_paths_evidence_and_integration(self):
        value = project_full()
        self.assertEqual(value, json.loads((FIXTURES / 'valid-full-record.json').read_text(encoding='utf-8')))
        document, encoded = value['document'], json.dumps(value)
        self.assertIsInstance(document.get('story'), dict)
        self.assertEqual(document['state']['meta']['integration'],
                         {'base': 'integrate/app', 'target': 'main', 'remote': 'origin'})
        self.assertEqual(value['integration'], {'drift': {'moved': 3, 'behind': 1}})
        decided = document['journal'][-2]
        self.assertEqual((decided['kind'], decided['because'], decided['data']['why']),
                         ('decision.recorded', [decided['because'][0]],
                          'Measured with --output=/Users/operator/work/.orchestration/wait.json'))
        merged = next(entry for entry in document['journal'] if entry['kind'] == 'integration.merged')
        self.assertIn('targetHead', merged['data'])
        for text in ('tests/suite-budget.json', 'scripts/run-tests.py', '/tmp/wait.json', 'docs/plans/approved.md',
                     'Raised one finding', '.agent-skills/current/skills/run-delivery'):
            with self.subTest(text=text):
                self.assertIn(text, encoded)
        self.assertEqual(value['files'], full_record()[4])
        self.assertEqual(value['runs']['run-1']['roundDetail'][0]['briefs']['lens.security']['brief'],
                         {'text': 'Read docs/plans/approved.md first'})
        self.assertNotIn(SIBLING, encoded)
        self.assertEqual(document['state']['siblings'], [{'name': 'web', 'addedAt': document['journal'][-1]['at']}])

    def test_a_base_merge_authorization_publishes_without_its_nonce(self):
        doc, runs, plan, needs, files = full_record()
        value = public.project(doc, runs, FORGE, source_fingerprint='1' * 64, plan=plan, needs=needs, files=files)
        [authorized] = [entry for entry in value['document']['journal'] if entry['kind'] == 'base.merge.authorized']
        self.assertEqual(authorized['data'], {'reviewed': 'a' * 40, 'baseCommits': ['b' * 40]})
        self.assertNotIn(MERGE_NONCE, public.encode(value).decode())
        self.assertEqual([entry for entry in doc['journal'] if entry['kind'] == 'base.merge.authorized'][0]['data']['token'],
                         MERGE_NONCE)
        # Any other credential-named fact is still refused.
        doc['journal'][-2]['data']['token'] = 'a credential'
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.credential_field$'):
            public.project(doc, runs, FORGE, source_fingerprint='1' * 64)

    def test_an_integration_step_moves_as_its_facts_are_journalled(self):
        doc, runs, plan, needs, files = full_record()
        kinds = [entry['kind'] for entry in doc['journal']]
        cut = kinds.index('integration.merged')
        earlier = {**doc, 'journal': doc['journal'][:cut]}
        before = public.project(earlier, runs, FORGE, source_fingerprint='1' * 64)
        after = public.project(doc, runs, FORGE, source_fingerprint='1' * 64)
        self.assertEqual(before['document']['state']['meta']['integration']['base'], 'integrate/app')
        self.assertNotIn('integration.merged', [entry['kind'] for entry in before['document']['journal']])
        self.assertEqual([entry['kind'] for entry in after['document']['journal']][:len(kinds)], kinds)
        local = render.line_map_data(doc, runs, FORGE, theme_key='hosted-delivery', history=None)
        self.assertEqual(after['document']['story'], local['document']['story'])
        self.assertNotEqual(before['document']['story'], after['document']['story'])

    def test_links_come_from_what_the_local_page_shows_entry_facts_included(self):
        doc, runs = source()
        doc['journal'].append({'id': 2, 'at': '2026-11-15T00:01:00Z', 'kind': 'candidate.handed', 'lane': 'one',
                               'subject': 'APP-1', 'data': {'head': 'c' * 40, 'checks': [],
                                                            'changeRequest': 'https://github.com/example/app/pull/99'}})
        value = public.project(doc, runs, FORGE, source_fingerprint='1' * 64)
        local = render.line_map_data(doc, runs, FORGE, theme_key='hosted-delivery', history=None)
        self.assertEqual(value['links'], local['links'])
        self.assertIn('c' * 40, value['links']['commits'])
        self.assertIn('https://github.com/example/app/pull/99', value['links']['changes'])

    def test_markup_stays_data_and_only_named_runs_are_selected(self):
        value = self.project()
        row = value['runs']['run-1']['roundDetail'][0]
        self.assertEqual(row['raised'][0]['summary'], '<img onerror=x>')
        self.assertEqual(value['document']['state']['queue'][0]['title'], '</script><b>Work</b>')
        self.assertNotIn('</script>', render.embed(value))
        # The local page draws only the runs its stops name; so does the hosted one.
        self.assertNotIn('unrelated', value['runs'])

    def test_a_configured_secret_anywhere_refuses_the_cut_with_its_code(self):
        canary = 'KNOWN-CREDENTIAL-CANARY'
        placements = {
            'journal fact': lambda doc, runs, plan, needs, files: doc['journal'][-1]['data'].update(why=f'used {canary}'),
            'state prose': lambda doc, runs, plan, needs, files: doc['state']['finish'][0].update(text=canary),
            'run brief': lambda doc, runs, plan, needs, files: runs['run-1']['roundDetail'][0]['briefs']['lens.security']
            ['brief'].update(text=canary),
            'plan': lambda doc, runs, plan, needs, files: plan['plan']['outcome'].update(summary=canary),
            'needs': lambda doc, runs, plan, needs, files: needs['items'][0].update(text=canary),
            'files': lambda doc, runs, plan, needs, files: files.update(note=canary),
            'a key': lambda doc, runs, plan, needs, files: doc['state']['meta'].update({canary: True}),
        }
        clean = project_full(secret_values=[canary])
        self.assertNotIn(canary, public.encode(clean).decode())
        for name, plant in placements.items():
            with self.subTest(placement=name):
                doc, runs, plan, needs, files = full_record()
                plant(doc, runs, plan, needs, files)
                with self.assertRaisesRegex(public.ProjectionError, '^snapshot.configured_secret$') as caught:
                    public.project(doc, runs, FORGE, source_fingerprint='1' * 64, plan=plan, needs=needs,
                                   files=files, secret_values=[canary])
                self.assertNotIn(canary, str(caught.exception))
        raw = copy.deepcopy(clean)
        raw['document']['journal'][-1]['data']['why'] = canary
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.configured_secret$'):
            public.validate(raw, secret_values=[canary])
        self.assertIn('snapshot.configured_secret', hosted.PROJECTION_ERRORS)

    def test_a_credential_named_field_anywhere_refuses_the_cut_with_its_code(self):
        for key in ('password', 'token', 'secret', 'apiKey', 'api_key', 'API-KEY', 'credential', 'Authorization', 'cookie'):
            for place in ('journal fact', 'state', 'run', 'needs'):
                with self.subTest(key=key, place=place):
                    doc, runs, plan, needs, files = full_record()
                    holder = {'journal fact': doc['journal'][-2]['data'], 'state': doc['state']['meta'],
                              'run': runs['run-1']['roundDetail'][0], 'needs': needs['items'][0]}[place]
                    holder[key] = 'the value'
                    with self.assertRaisesRegex(public.ProjectionError, '^snapshot.credential_field$'):
                        public.project(doc, runs, FORGE, source_fingerprint='1' * 64, plan=plan, needs=needs,
                                       files=files)
        # A key that merely contains one of the words is an ordinary field.
        doc, runs, plan, needs, files = full_record()
        doc['state']['meta']['tokenBudget'] = 3
        public.project(doc, runs, FORGE, source_fingerprint='1' * 64)
        self.assertIn('snapshot.credential_field', hosted.PROJECTION_ERRORS)

    def test_a_siblings_hosted_link_is_left_out_wherever_the_record_holds_it(self):
        doc, runs, plan, needs, files = full_record()
        last = doc['journal'][-1]
        doc['journal'].append({'id': last['id'] + 1, 'at': last['at'], 'kind': 'sibling.added', 'lane': None,
                               'subject': None, 'data': {'sibling': 'web', 'link': SIBLING}})
        doc['journal'].append({'id': last['id'] + 2, 'at': last['at'], 'kind': 'decision.recorded', 'lane': None,
                               'subject': None, 'data': {'text': f'Follow web at {SIBLING}', 'why': 'It rejoins first.'}})
        doc['state']['meta']['seen'] = {SIBLING: 'read'}
        doc['across'] = {'groups': [{'name': 'web', 'link': SIBLING, 'fresh': True}], 'held': {}}
        original = copy.deepcopy(doc)
        value = public.project(doc, runs, FORGE, source_fingerprint='1' * 64)
        self.assertNotIn('e' * 64, json.dumps(value))
        self.assertEqual(value['document']['across']['groups'][0]['name'], 'web')
        self.assertEqual(value['document']['journal'][-2]['data'], {'sibling': 'web'})
        self.assertEqual(value['document']['journal'][-1]['data']['text'], 'Follow web at web')
        self.assertEqual(value['document']['state']['meta']['seen'], {'web': 'read'})
        self.assertEqual(doc, original)

    def test_byte_text_and_list_bounds_stay(self):
        value = self.project()
        self.assertEqual(public.MAX_BYTES, 16 * 1024 * 1024)

        class SizedBytes:
            def __init__(self, length): self.length = length
            def __len__(self): return self.length
        with mock.patch.object(public, 'encode', return_value=SizedBytes(16 * 1024 * 1024)):
            public.validate(value)
        with mock.patch.object(public, 'encode', return_value=SizedBytes(16 * 1024 * 1024 + 1)):
            with self.assertRaisesRegex(public.ProjectionError, '^snapshot.byte_bound$'):
                public.validate(value)
        for edit, code in [
            (lambda v: v['document']['journal'][0]['data'].update(note='x' * 65537), 'snapshot.text_bound'),
            (lambda v: v['document']['state'].update(followups=[{}] * 10001), 'snapshot.list_bound'),
            (lambda v: v['document']['state']['meta'].update({'k' * 2049: 1}), 'snapshot.text_bound'),
        ]:
            with self.subTest(code=code):
                bad = copy.deepcopy(value)
                edit(bad)
                with self.assertRaisesRegex(public.ProjectionError, f'^{re.escape(code)}$'):
                    public.validate(bad)
        at_bound = copy.deepcopy(value)
        at_bound['document']['journal'][0]['data']['note'] = 'x' * 65536
        public.validate(at_bound)

    def test_unsafe_links_are_not_published(self):
        doc, runs = source()
        doc['state']['queue'][0]['changeRequest'] = 'javascript:alert(1)'
        value = public.project(doc, runs, config(), source_fingerprint='1' * 64)
        self.assertEqual(value['links']['changes'], {})
        bad = copy.deepcopy(value)
        bad['links']['issues']['APP-1'] = 'https://user:pass@example.com/a'
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.unsafe_link$'):
            public.validate(bad)

    def test_other_snapshot_versions_and_unknown_top_level_fields_are_refused(self):
        value = self.project()
        for version in ('hosted-delivery-snapshot/1', 'hosted-delivery-snapshot/2', 'hosted-delivery-snapshot/4'):
            with self.subTest(version=version), self.assertRaisesRegex(public.ProjectionError, '^snapshot.const$'):
                public.validate({**copy.deepcopy(value), 'schemaVersion': version})
        with self.assertRaisesRegex(public.ProjectionError, '^snapshot.unknown_field$'):
            public.validate({**copy.deepcopy(value), 'operator': 'someone@example.com'})
        self.assertEqual(hosted.RENDERER, 'delivery-line-map/3')

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
        listed = {row['file'] for row in manifest['cases']}
        self.assertEqual(listed, {path.name for path in FIXTURES.glob('*.json')} - {'manifest.json'})
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


# The withdrawn rule, by meaning: each way the corpus said that the hosted page leaves something of the record out for
# privacy. Absent from the projection's code, the publisher, the viewer, and every promise about what a hosted link shows.
WITHDRAWN = [
    r'local[- ]references?', r'local[- ]paths?', r'local[- ]prose', r'omission marker', r'omitted visibly',
    r'Local reference omitted', r'not published', r'unpublished', r'publishes no (facts|story)',
    r'carries no (story|facts)', r'none of its facts', r'privacy', r'redact', r'allowlist', r'workstation',
    r'hostedCut', r'option[- ]path', r'leaves? out the steps', r'unrelated[_ ]evidence', r'unregistered[_ ]kind',
    r'execution details', r'no other facts', r'and no story\b', r'identity and kind', r'no link a fact names',
]
WITHDRAWN_FILES = [
    'skills/delivery-views/scripts/public_projection.py', 'skills/delivery-views/references/hosted-snapshot.schema.json',
    'hosted/delivery-viewer/scripts/build-assets.mjs', 'hosted/delivery-viewer/src/protocol.ts',
    'hosted/delivery-viewer/src/viewer.ts', 'docs/hosted-deliveries.md',
]


def hosted_sections():
    """The delivery-views skill page's hosted publishing section and the catalog's line for the skill."""
    page = (ROOT / 'skills/delivery-views/SKILL.md').read_text(encoding='utf-8')
    start = page.index('Every send comes from a fresh coherent cut')
    end = page.index('Pending bytes use immutable')
    entry = next(row for row in json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))['skills']
                 if row['id'] == 'delivery-views')
    texts = {'SKILL.md hosted section': page[start:end], 'catalog degradedBehavior': entry['degradedBehavior']}
    for name in ('delivery-views', 'run-delivery', 'linear', 'jira'):
        profile = json.loads((ROOT / f'profiles/{name}.json').read_text(encoding='utf-8'))
        texts[f'profiles/{name}.json description'] = profile['description']
    schemas = json.loads((ROOT / 'skills/orchestrate-work/references/schemas.json').read_text(encoding='utf-8'))
    texts['delivery-state siblings description'] = schemas['delivery-state/1']['properties']['siblings']['description']
    return texts


class WithdrawnRuleTests(unittest.TestCase):
    def test_the_withdrawn_privacy_rule_is_absent_by_every_synonym(self):
        texts = {name: (ROOT / name).read_text(encoding='utf-8') for name in WITHDRAWN_FILES}
        texts.update(hosted_sections())
        for name, text in texts.items():
            flat = ' '.join(text.split())
            for pattern in WITHDRAWN:
                with self.subTest(file=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, flat, re.I))
        for gone in ('OMITTED_LOCAL_REFERENCE', 'LOCAL_PATH', 'JOURNAL_MEMBERS', 'PROSE_FIELDS', '_has_local_path',
                     '_omit_local_prose', '_registered_kinds', '_project'):
            with self.subTest(attribute=gone):
                self.assertFalse(hasattr(public, gone))
        for code in ('snapshot.local_path', 'snapshot.unregistered_kind', 'snapshot.unrelated_evidence'):
            with self.subTest(code=code):
                self.assertNotIn(code, hosted.PROJECTION_ERRORS)

    def test_the_kept_rule_is_said_where_a_hosted_link_is_described(self):
        texts = {name: (ROOT / name).read_text(encoding='utf-8') for name in ('docs/hosted-deliveries.md',)}
        texts.update({name: text for name, text in hosted_sections().items() if name != 'delivery-state siblings description'})
        for name, text in texts.items():
            flat = ' '.join(text.split())
            with self.subTest(file=name):
                self.assertRegex(flat, r'what the local live view shows')
                self.assertRegex(flat, r'(configured secret|credential)')

    def test_any_conforming_service_is_said_to_be_a_valid_origin(self):
        page = ' '.join((ROOT / 'skills/delivery-views/SKILL.md').read_text(encoding='utf-8').split())
        self.assertIn('Publishing is disabled without this member. Any service that conforms to `hosted-delivery-api/1`'
                      ' is a valid origin: the Cloudflare Worker, the portable container built from the release\'s'
                      ' service bundle, or your own implementation that passes the conformance suite.', page)
        sentence = ('Any service that conforms to hosted-delivery-api/1 is a valid hosted origin: the Cloudflare Worker,'
                    ' the portable container built from the release\'s verified service bundle, or one\'s own'
                    ' implementation that passes the conformance suite.')
        texts = {name: text for name, text in hosted_sections().items()
                 if name == 'catalog degradedBehavior' or name.startswith('profiles/')}
        self.assertEqual(len(texts), 5)
        for name, text in texts.items():
            with self.subTest(file=name):
                self.assertIn(sentence, text)
