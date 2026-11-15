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
                       'subject': 'APP-1', 'summary': 'Dissent', 'review': {'round': 1,
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
        for canary in ('TOKEN', '/Users/private', '/tmp/log', 'briefs'):
            self.assertNotIn(canary, encoded)
        for edit in [lambda v: v.update(token='x'), lambda v: v['runs']['run-1'].update(argv=[]),
                     lambda v: v['document']['state']['meta'].update(cwd='x')]:
            bad = copy.deepcopy(value); edit(bad)
            with self.assertRaises(public.ProjectionError): public.validate(bad)
        with self.assertRaises(public.ProjectionError): self.project(secret_values=['Work'])
        doc['state']['queue'][0]['title'] = 'Open /Users/operator/private/file'
        with self.assertRaises(public.ProjectionError): public.project(doc, runs, config(), source_fingerprint='1' * 64)

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

    def test_byte_bound_and_credential_shaped_map_fields_are_refused(self):
        value = self.project()
        with mock.patch.object(public, 'MAX_BYTES', 100):
            with self.assertRaises(public.ProjectionError): public.validate(value)
        value['view']['lenses']['token'] = {'name': 'Credential-shaped field', 'icon': None}
        with self.assertRaises(public.ProjectionError): public.validate(value)

    def test_native_review_truth_and_unavailability_survive_without_raw_outcomes(self):
        doc = json.loads((ROOT / 'tests/fixtures/delivery-views/round-card-deferred/document.json').read_text())
        expected = render.line_map_data(doc, {}, config(), theme_key='hosted-delivery', history=None)['reviews']
        value = public.project(doc, {}, config(), source_fingerprint='1' * 64)
        self.assertEqual(value['reviews'], expected)
        self.assertTrue(value['reviews']['WORK-1']['roundDetail'])
        self.assertNotIn('reviewEvidence', value['document'])
        self.assertNotIn('outcomes', json.dumps(value))
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
