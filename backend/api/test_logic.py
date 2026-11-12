"""Pure-logic tests (no database): scoring, weights and client matching."""
import ast
from pathlib import Path

from django.test import SimpleTestCase

from config.env import database_from_url, without_search_path

from api.clients import (Resolver, build_client_options, client_discrepancies, group_customers, normalise, parse_ids, tokenise)
from api.scoring import category_key, clean_weights, compute_scores, effective_weights, explain, report_weights, weighted_average


def types(security=(80.0, 60.0), third=(75.0, None)):
    return [
        {'key': 'windows-security', 'label': 'S', 'categories': [{'category_id': 'g1', 'compliance': security[0], 'previous': security[1]}]},
        {'key': 'windows-patching', 'label': 'W', 'categories': []},
        {'key': 'third-party', 'label': 'T', 'categories': [{'category_id': None, 'compliance': third[0], 'previous': third[1]}]},
    ]


class Scoring(SimpleTestCase):
    def test_weighted_average_skips_missing_values_and_zero_weights(self):
        items = [(10, 1), (20, 3), (None, 5), (90, 0)]
        self.assertAlmostEqual(weighted_average(items, lambda i: i[1], lambda i: i[0]), 17.5)
        self.assertIsNone(weighted_average([], lambda i: 1, lambda i: i))

    def test_a_category_with_many_patches_does_not_swamp_the_type_score(self):
        # Compliance is per category, so two categories count equally whatever their patch counts.
        two = [{'key': 't', 'label': 'T', 'categories': [{'category_id': 'a', 'compliance': 100.0, 'previous': None},
                                                          {'category_id': 'b', 'compliance': 80.0, 'previous': None}]}]
        self.assertEqual(compute_scores(two, {})[1]['current'], 90.0)

    def test_category_and_type_weights(self):
        _, overall = compute_scores(types(), {'windows-security': 3})
        self.assertEqual(overall['current'], 78.75)
        rows, overall = compute_scores(types(), {category_key('third-party', None): 0})
        self.assertIsNone(rows[3]['current'])
        self.assertEqual(overall['current'], 80.0)

    def test_nothing_to_score_is_none_not_zero(self):
        rows, overall = compute_scores(types(security=(None, None), third=(None, None)), {})
        self.assertEqual(overall, {'current': None, 'previous': None})

    def test_user_weights_override_shared_ones(self):
        self.assertEqual(effective_weights({'a': 1, 'b': 2}, {'b': 5}), {'a': 1, 'b': 5})

    def test_weight_validation(self):
        self.assertEqual(clean_weights({'third-party': 2, 'windows-security:g1': 0})[0], {'third-party': 2.0, 'windows-security:g1': 0.0})
        for bad in ({'Third Party': 1}, {'third-party': -1}, {'third-party': '2'}, {'third-party': True}, {'third-party': 1001}, [1]):
            self.assertIsNotNone(clean_weights(bad)[1], bad)

    def test_category_keys_handle_a_missing_category_id(self):
        self.assertEqual(category_key('third-party', None), 'third-party:')
        self.assertEqual(category_key('windows-security', 'g1'), 'windows-security:g1')


class ClientMatching(SimpleTestCase):
    def test_names_compare_without_case_punctuation_or_spacing(self):
        self.assertEqual(normalise('IP Capital:  BITCO'), normalise('ip capital bitco'))
        self.assertEqual(normalise(None), '')
        self.assertNotEqual(normalise('IP Capital'), normalise('IP Capital: BITCO'))  # a parent is not its child

    def test_id_mapping_wins_over_a_name(self):
        r = Resolver({('autotask', '101'): 1}, {'acme': 2})
        self.assertEqual(r.client_id('autotask', 101, 'Acme'), 1)

    def test_name_or_alias_is_only_a_fallback(self):
        r = Resolver({}, {'acme': 2, 'acme logistics': 2})
        self.assertEqual(r.client_id('autotask', 999, 'ACME Logistics'), 2)
        self.assertIsNone(r.client_id('autotask', 999, 'Someone Else'))
        self.assertIsNone(r.client_id('autotask', None, None))

    def test_the_same_id_in_two_sources_is_not_confused(self):
        r = Resolver({('autotask', '5'): 1, ('ncentral', '5'): 2})
        self.assertEqual((r.client_id('autotask', 5), r.client_id('ncentral', 5)), (1, 2))


class ClientOptions(SimpleTestCase):
    def rows(self, *items):
        return [{'org_unit_id': o, 'customer_name': n, 'devices': d} for o, n, d in items]

    def test_linked_org_units_merge_and_unlinked_ones_stand_alone(self):
        options = build_client_options(self.rows((10, 'A', 5), (20, 'A2', 3), (30, 'B', 4)), {'10': (1, 'Acme'), '20': (1, 'Acme')})
        self.assertEqual([(o['key'], o['label'], o['devices']) for o in options], [('client:1', 'Acme', 8), ('org:30', 'B', 4)])

    def test_options_are_sorted_by_name_ignoring_case(self):
        options = build_client_options(self.rows((1, 'zed', 1), (2, 'Alpha', 1), (3, 'beta', 1)), {})
        self.assertEqual([o['label'] for o in options], ['Alpha', 'beta', 'zed'])

    def test_a_renamed_customer_uses_the_name_most_devices_carry_and_a_missing_name_falls_back(self):
        options = build_client_options(self.rows((10, 'Old Name', 1), (10, 'New Name', 7), (11, None, 2)), {})
        self.assertEqual({o['key']: (o['label'], o['devices']) for o in options}, {'org:10': ('New Name', 8), 'org:11': ('Org unit 11', 2)})

    def test_no_devices_means_no_options(self):
        self.assertEqual(build_client_options([], {}), [])


ALL = ['autotask', 'ncentral', 'sentinelone', 'cove']


def customers(**by_source):
    return {s: [{'external_id': i, 'customer_name': n} for i, n in rows] for s, rows in by_source.items()}


def propose(rows, mapped=(), known=None, client_sources=None, available=ALL):
    return group_customers(rows, set(mapped), known or {}, client_sources or {}, available)


class Grouping(SimpleTestCase):
    def test_same_words_in_the_same_order_group_whatever_the_punctuation_or_case(self):
        self.assertEqual(tokenise('IP Capital: BITCO'), ('ip', 'capital', 'bitco'))
        self.assertEqual(tokenise('IP Capital (BitCo)'), tokenise('IP Capital: BITCO'))
        found = propose(customers(autotask=[(101, 'IP Capital: BITCO')], ncentral=[(7, 'IP Capital (BitCo)')]))
        self.assertEqual(len(found), 1)
        group = found[0]
        self.assertEqual([(m['source'], m['external_id']) for m in group['members']], [('autotask', '101'), ('ncentral', '7')])
        self.assertTrue(group['inferred'])
        self.assertIn('(inference)', group['message'])
        self.assertIn('IP Capital (BitCo)', group['message'])

    def test_a_single_different_word_stops_the_grouping(self):
        found = propose(customers(autotask=[(1, 'IP Capital: BITCO')], ncentral=[(2, 'IP Capital: BITCO Holdings')], cove=[(3, 'IP Capital')]))
        self.assertEqual(len(found), 3)
        self.assertTrue(all(not p['inferred'] and p['message'] is None for p in found))   # nothing was inferred for a lone customer

    def test_word_order_matters(self):
        self.assertEqual(len(propose(customers(autotask=[(1, 'IP Capital')], ncentral=[(2, 'Capital IP')]))), 2)

    def test_accents_and_case_are_ignored(self):
        self.assertEqual(len(propose(customers(autotask=[(1, 'Café Noir')], cove=[(2, 'CAFÉ NOIR')]))), 1)

    def test_names_without_words_are_never_grouped(self):
        found = propose(customers(autotask=[(1, '---')], ncentral=[(2, '---')], cove=[(3, None)]))
        self.assertEqual(len(found), 3)
        self.assertEqual(sorted(p['name'] for p in found), ['---', '---', 'Cove 3'])   # a customer with no name still gets a label

    def test_customers_already_linked_are_left_out(self):
        rows = customers(autotask=[(1, 'Acme'), (2, 'Blue Crane')])
        self.assertEqual([p['members'][0]['external_id'] for p in propose(rows, mapped=[('autotask', '1')])], ['2'])

    def test_a_name_matching_an_existing_client_or_alias_is_offered_for_that_client(self):
        known = {('acme',): (5, 'Acme'), ('acme', 'logistics'): (5, 'Acme')}
        found = propose(customers(cove=[(9, 'ACME Logistics')]), known=known, client_sources={5: {'autotask'}})
        self.assertEqual((found[0]['existing_client_id'], found[0]['name']), (5, 'Acme'))
        self.assertIn('existing client "Acme" (inference)', found[0]['message'])
        self.assertEqual(found[0]['found_in'], ['autotask', 'cove'])   # the client's existing links count too

    def test_two_customers_from_one_source_with_the_same_name_are_warned_about(self):
        found = propose(customers(autotask=[(1, 'Acme'), (2, 'ACME')]))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['warnings'], ['More than one Autotask customer has this name; they may be different companies.'])

    def test_a_client_missing_from_some_sources_is_reported_as_a_discrepancy(self):
        found = propose(customers(autotask=[(1, 'Acme')], cove=[(2, 'Acme')]))
        self.assertEqual(found[0]['missing_from'], ['ncentral', 'sentinelone'])
        self.assertEqual(found[0]['discrepancy'], 'Found in Autotask, Cove. Not found in N-central, SentinelOne.')

    def test_a_source_that_could_not_be_read_is_not_reported_as_missing(self):
        found = propose(customers(autotask=[(1, 'Acme')]), available=['autotask', 'ncentral'])
        self.assertEqual(found[0]['missing_from'], ['ncentral'])

    def test_nothing_is_reported_when_every_source_has_the_customer(self):
        rows = customers(**{s: [(1, 'Acme')] for s in ALL})
        self.assertIsNone(propose(rows)[0]['discrepancy'])

    def test_the_suggested_name_comes_from_the_first_source_in_priority_order(self):
        found = propose(customers(cove=[(2, 'IP Capital (BitCo)')], autotask=[(1, 'IP Capital: BITCO')]))
        self.assertEqual(found[0]['name'], 'IP Capital: BITCO')


class Discrepancies(SimpleTestCase):
    def make_client(self, links, **extra):
        return {'id': 1, 'name': 'Acme', 'is_active': True, 'aliases': [], 'sources': [{'source': s, 'external_id': i} for s, i in links], **extra}

    def test_missing_sources_and_stale_links_are_reported(self):
        rows = customers(autotask=[(101, 'Acme')], ncentral=[(10, 'Acme')])
        result = client_discrepancies([self.make_client([('autotask', '101'), ('ncentral', '99')])], rows, ['autotask', 'ncentral', 'cove'])[0]
        self.assertEqual(result['missing_from'], ['cove'])
        self.assertEqual(result['stale'], [{'source': 'ncentral', 'external_id': '99'}])
        self.assertEqual(result['discrepancies'], ['No Cove customer is linked to this client.',
                                                   'N-central 99 is linked but no longer exists in N-central.'])

    def test_a_fully_linked_client_has_no_discrepancies(self):
        rows = customers(autotask=[(101, 'Acme')])
        self.assertEqual(client_discrepancies([self.make_client([('autotask', '101')])], rows, ['autotask'])[0]['discrepancies'], [])

    def test_inactive_clients_are_not_expected_everywhere(self):
        result = client_discrepancies([self.make_client([], is_active=False)], customers(), ['autotask', 'cove'])[0]
        self.assertEqual(result['discrepancies'], [])

    def test_an_unreadable_source_cannot_make_a_link_look_stale(self):
        result = client_discrepancies([self.make_client([('cove', '4')])], customers(), ['autotask'])[0]
        self.assertEqual(result['stale'], [])


class TestDatabaseSetup(SimpleTestCase):
    """`manage.py test` creates a fresh database, so a search_path pointing at a schema that only exists in the real one must go."""

    def config(self, query):
        return database_from_url(f'postgres://u:p@h:5432/db?{query}', Path('/x'))

    def test_the_search_path_option_is_removed(self):
        stripped = without_search_path(self.config('options=-c%20search_path%3Dreport_manager'))
        self.assertEqual(stripped['OPTIONS'], {})
        self.assertEqual((stripped['NAME'], stripped['USER'], stripped['HOST']), ('db', 'u', 'h'))

    def test_other_options_and_other_flags_are_kept(self):
        stripped = without_search_path(self.config('sslmode=require&options=-c%20search_path%3Dx%20-c%20statement_timeout%3D5000'))
        self.assertEqual(stripped['OPTIONS'], {'sslmode': 'require', 'options': '-c statement_timeout=5000'})

    def test_a_config_without_it_is_unchanged_and_the_original_is_not_modified(self):
        original = self.config('sslmode=require')
        self.assertEqual(without_search_path(original), original)
        with_path = self.config('options=-c%20search_path%3Dx')
        without_search_path(with_path)
        self.assertIn('options', with_path['OPTIONS'])

    def test_sqlite_needs_no_options(self):
        self.assertEqual(without_search_path(database_from_url('sqlite:///db.sqlite3', Path('/x')))['OPTIONS'], {})

    def test_an_in_memory_sqlite_url_stays_in_memory(self):
        self.assertEqual(database_from_url('sqlite:///:memory:', Path('/x'))['NAME'], ':memory:')


class WeightRules(SimpleTestCase):
    def test_a_key_with_a_trailing_newline_is_refused(self):
        self.assertIsNotNone(clean_weights({'third-party\n': 1})[1])
        self.assertIsNone(clean_weights({'third-party': 1, 'windows-security:g1': 0})[1])

    def test_a_report_takes_the_shared_defaults_not_the_viewers_weights(self):
        from unittest.mock import patch
        with patch('api.scoring.shared_weights', lambda: {'a': 2, 'b': 3}):
            self.assertEqual(report_weights({'b': 5, 'c': 1}), {'a': 2, 'b': 5, 'c': 1})


class TestFileHygiene(SimpleTestCase):
    """Mistakes that only show up when the Django database tests run (the pure tests are run without Django, so they cannot see them)."""

    FILES = sorted(Path(__file__).parent.glob('test*.py'))

    def classes(self, path):
        return {n.name: n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n, ast.ClassDef)}

    def test_no_test_class_defines_something_called_client(self):
        # Django's test cases already have `self.client`; a helper with that name replaces it and breaks calls like self.client.get(...)
        clash = [f'{p.name}:{name}.{m.name}' for p in self.FILES for name, node in self.classes(p).items() for m in node.body
                 if isinstance(m, ast.FunctionDef) and m.name in ('client', 'async_client')]
        self.assertEqual(clash, [])

    def test_no_test_class_replaces_a_method_the_test_framework_itself_calls(self):
        # `run`, `debug`, `id`, `shortDescription`, `countTestCases` and the rest are called by unittest on every test: a helper with one of
        # those names (a command helper called `run`, say) breaks every test in its class, and only shows when the database tests run
        import unittest
        reserved = {name for name in dir(unittest.TestCase) if not name.startswith('_') and not name.startswith(('assert', 'fail'))} | {'client', 'async_client', 'setUp', 'tearDown'}
        clash = [f'{p.name}:{name}.{m.name}' for p in self.FILES for name, node in self.classes(p).items() for m in node.body
                 if isinstance(m, ast.FunctionDef) and m.name in reserved - {'setUp', 'tearDown', 'setUpClass', 'tearDownClass', 'setUpTestData'}]
        self.assertEqual(clash, [])

    def test_a_test_only_calls_helpers_its_class_has(self):
        """If a helper such as post() is defined on one class, another class that calls self.post() must define or inherit it."""
        problems = []
        for path in self.FILES:
            classes = self.classes(path)
            own = {name: {m.name for m in node.body if isinstance(m, ast.FunctionDef)} for name, node in classes.items()}
            helpers = {m for names in own.values() for m in names if not m.startswith(('test_', 'setUp', 'tearDown'))}

            def available(name, seen=()):
                names = set(own.get(name, set()))
                for base in classes[name].bases if name in classes else []:
                    base_name = base.id if isinstance(base, ast.Name) else None
                    if base_name in classes and base_name not in seen:
                        names |= available(base_name, seen + (name,))
                return names

            for name, node in classes.items():
                have = available(name)
                for method in (m for m in node.body if isinstance(m, ast.FunctionDef)):
                    for call in ast.walk(method):
                        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                                and call.func.value.id == 'self' and call.func.attr in helpers and call.func.attr not in have):
                            problems.append(f'{path.name}: {name}.{method.name} calls self.{call.func.attr}(), which {name} does not have')
        self.assertEqual(problems, [])


class ParseIds(SimpleTestCase):
    def test_one_or_several_numbers(self):
        self.assertEqual(parse_ids('5'), [5])
        self.assertEqual(parse_ids('1,2,3'), [1, 2, 3])
        self.assertEqual(parse_ids(5), [5])                                     # a number already
        self.assertEqual(parse_ids('007,8'), [7, 8])

    def test_nothing_given_is_no_numbers(self):
        self.assertEqual(parse_ids(None), [])
        self.assertEqual(parse_ids(''), [])

    def test_repeats_are_dropped_and_the_order_is_kept(self):
        self.assertEqual(parse_ids('3,1,3,2,1'), [3, 1, 2])

    def test_anything_else_is_refused(self):
        for bad in ('a', '1,', ',1', '1,,2', '1;2', '-1', '1 2', ' 1', '1, 2', '1.5', '1' * 13, '0x1', '1,a', "1'; DROP TABLE x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_ids(bad)

    def test_the_limit_is_fifty_by_default(self):
        self.assertEqual(len(parse_ids(','.join(str(i) for i in range(50)))), 50)
        with self.assertRaises(ValueError):
            parse_ids(','.join(str(i) for i in range(51)))
        with self.assertRaises(ValueError):
            parse_ids('1,2,3', limit=2)
        self.assertEqual(len(parse_ids(','.join(['7'] * 80))), 1)            # repeats do not count towards the limit


def cat(category_id, name, compliance):
    return {'category_id': category_id, 'category': name, 'compliance': compliance, 'previous': compliance}


def worked_types():
    return [{'key': 'windows-security', 'label': 'Windows Security Patching', 'categories': [cat('a', 'Cumulative Updates', 96.0), cat('b', 'Dot Net', 88.0)]},
            {'key': 'windows-patching', 'label': 'Windows Patching', 'categories': [cat('c', 'Drivers', 50.0), cat('d', 'Empty', None)]},
            {'key': 'third-party', 'label': 'Third Party Patching', 'categories': [cat('e', 'Browsers', 90.0), cat('f', 'Office', 70.0)]}]


WORKED_WEIGHTS = {'windows-security:a': 2, 'third-party:e': 0, 'third-party': 2}


class ExplainScores(SimpleTestCase):
    """The explanation of how the weights make the score. Its figures are checked by hand below, and against the real score on generated inputs."""

    def test_the_working_for_a_type_with_different_weights_is_done_by_hand(self):
        security = explain(worked_types(), WORKED_WEIGHTS)['types'][0]
        self.assertEqual([(c['category'], c['compliance'], c['weight'], c['counts'], c['share']) for c in security['categories']],
                         [('Cumulative Updates', 96.0, 2, True, 66.7), ('Dot Net', 88.0, 1, True, 33.3)])          # 2 of the 3 weights, and 1 of the 3
        self.assertEqual((security['weighted_total'], security['weight_total'], security['score']), (280.0, 3.0, 93.33))      # (96 x 2 + 88 x 1) / (2 + 1)

    def test_a_category_with_no_patches_is_left_out_and_the_rest_share_the_weight(self):
        patching = explain(worked_types(), WORKED_WEIGHTS)['types'][1]
        self.assertEqual([(c['category'], c['counts'], c['reason'], c['share']) for c in patching['categories']], [('Drivers', True, None, 100.0), ('Empty', False, 'it lists no patches', None)])
        self.assertEqual(patching['score'], 50.0)

    def test_a_category_with_weight_zero_is_left_out_and_says_why(self):
        third = explain(worked_types(), WORKED_WEIGHTS)['types'][2]
        self.assertEqual([(c['category'], c['counts'], c['reason']) for c in third['categories']], [('Browsers', False, 'its weight is 0'), ('Office', True, None)])
        self.assertEqual(third['score'], 70.0)                                                                              # only Office counts

    def test_zero_weight_wins_over_no_patches_as_the_reason(self):
        types = [{'key': 'third-party', 'label': 'T', 'categories': [cat('x', 'Nothing', None)]}]
        self.assertEqual(explain(types, {'third-party:x': 0})['types'][0]['categories'][0]['reason'], 'its weight is 0')

    def test_the_overall_score_is_the_weighted_average_of_the_type_scores_unrounded(self):
        result = explain(worked_types(), WORKED_WEIGHTS)
        self.assertEqual([(t['key'], t['score'], t['weight'], t['counts'], t['share']) for t in result['types']],
                         [('windows-security', 93.33, 1, True, 25.0), ('windows-patching', 50.0, 1, True, 25.0), ('third-party', 70.0, 2, True, 50.0)])
        self.assertEqual(result['overall'], {'score': 70.83, 'weighted_total': 283.3333, 'weight_total': 4.0})                 # (93.333 x 1 + 50 x 1 + 70 x 2) / (1 + 1 + 2)

    def test_a_type_with_weight_zero_or_nothing_to_score_is_left_out_of_the_overall_score(self):
        result = explain(worked_types(), {**WORKED_WEIGHTS, 'windows-patching': 0})
        self.assertEqual([(t['key'], t['counts'], t['reason']) for t in result['types']], [('windows-security', True, None), ('windows-patching', False, 'its weight is 0'), ('third-party', True, None)])
        self.assertEqual(result['overall']['score'], 77.78)                                                                    # (93.333 x 1 + 70 x 2) / 3
        types = worked_types()
        types[1]['categories'] = [cat('d', 'Empty', None)]
        self.assertEqual(explain(types, {})['types'][1]['reason'], 'none of its categories counts')

    def test_everything_left_out_gives_no_score_not_zero(self):
        result = explain(worked_types(), {'windows-security': 0, 'windows-patching': 0, 'third-party': 0})
        self.assertIsNone(result['overall']['score'])
        self.assertEqual(result['overall']['weight_total'], 0.0)
        self.assertTrue(all(t['share'] is None for t in result['types']))
        self.assertIsNone(explain([], {})['overall']['score'])

    def test_what_has_no_weight_set_counts_as_one(self):
        result = explain(worked_types(), {})
        self.assertTrue(all(c['weight'] == 1 for t in result['types'] for c in t['categories']))
        self.assertTrue(all(t['weight'] == 1 for t in result['types']))

    def test_the_shares_of_what_counts_add_up_to_a_hundred(self):
        result = explain(worked_types(), WORKED_WEIGHTS)
        for t in result['types']:
            self.assertAlmostEqual(sum(c['share'] or 0 for c in t['categories']), 100.0, delta=0.15)
        self.assertAlmostEqual(sum(t['share'] or 0 for t in result['types']), 100.0, delta=0.15)

    def test_the_input_is_not_changed(self):
        import copy
        types, weights = worked_types(), dict(WORKED_WEIGHTS)
        before = copy.deepcopy((types, weights))
        explain(types, weights)
        self.assertEqual((types, weights), before)

    def test_the_working_always_adds_up_to_the_real_score(self):
        """The property that makes the explanation trustworthy: on many generated inputs (missing values, zero weights, unset weights, empty
        categories) the type scores and the overall score in the explanation are exactly those compute_scores produces."""
        import random
        generator = random.Random(2026)
        for _ in range(400):
            types = []
            for key, label in (('windows-security', 'S'), ('windows-patching', 'P'), ('third-party', 'T')):
                n = generator.randint(0, 4)
                types.append({'key': key, 'label': label, 'categories': [cat(f'c{i}', f'Cat {i}', generator.choice([None, round(generator.uniform(0, 100), 2)])) for i in range(n)]})
            weights = {}
            for t in types:
                if generator.random() < 0.4:
                    weights[t['key']] = generator.choice([0, 0.5, 1, 2, 3, 10])
                for c in t['categories']:
                    if generator.random() < 0.5:
                        weights[category_key(t['key'], c['category_id'])] = generator.choice([0, 0.5, 1, 2, 5, 100])
            rows, overall = compute_scores(types, weights)
            result = explain(types, weights)
            self.assertEqual(result['overall']['score'], overall['current'], (types, weights))
            self.assertEqual([t['score'] for t in result['types']], [r['current'] for r in rows if not r.get('group')], (types, weights))
            for t in result['types']:
                if t['weight_total']:
                    self.assertAlmostEqual(t['weighted_total'] / t['weight_total'], t['score'], delta=0.01)
