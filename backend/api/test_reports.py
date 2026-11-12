"""The server's report-template rules, run against the same cases as the browser's (frontend/src/report/template-cases.json)."""
import json
import math
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from api.reports import DEFAULT_TITLE, MAX_BLOCKS, MAX_FILTER_VALUES, validate_template

CASES = Path(settings.BASE_DIR).parent / 'frontend' / 'src' / 'report' / 'template-cases.json'


class SharedCases(SimpleTestCase):
    def test_every_shared_case(self):
        if not CASES.is_file():
            self.skipTest('frontend/src/report/template-cases.json is not deployed with this copy')
        cases = json.loads(CASES.read_text(encoding='utf-8'))['cases']
        self.assertGreater(len(cases), 25)
        for case in cases:
            with self.subTest(case['name']):
                if case.get('error'):
                    with self.assertRaises(ValueError):
                        validate_template(case['input'])
                    continue
                result = validate_template(case['input'])
                if 'expect' in case:
                    self.assertEqual(result, case['expect'])
                for key, value in case.get('expect_field', {}).items():
                    self.assertEqual(result[key], value, key)
                for key, value in case.get('expect_block_filter', {}).items():
                    self.assertEqual(result['blocks'][0]['filters'][key], value, key)

    def test_a_normalised_template_is_unchanged_by_validating_it_again(self):
        if not CASES.is_file():
            self.skipTest('template-cases.json is not deployed with this copy')
        for case in json.loads(CASES.read_text(encoding='utf-8'))['cases']:
            if 'expect' in case:
                self.assertEqual(validate_template(case['expect']), case['expect'], case['name'])


class ServerRules(SimpleTestCase):
    """What the shared cases cannot say as plain JSON, and where Python differs from JavaScript."""

    def make(self, **extra):
        return {'version': 2, 'blocks': [{'type': 'backups'}], **extra}

    def test_titles_and_filter_lengths(self):
        self.assertEqual(len(validate_template(self.make(title='x' * 130))['title']), 120)
        with self.assertRaises(ValueError):
            validate_template({'version': 2, 'blocks': [{'type': 'backups', 'title': 'x' * 121}]})
        with self.assertRaises(ValueError):
            validate_template({'version': 2, 'blocks': [{'type': 'tickets', 'filters': {'customer': 'x' * 201}}]})
        validate_template({'version': 2, 'blocks': [{'type': 'tickets', 'filters': {'customer': 'x' * 200}}]})

    def test_the_number_of_blocks_and_weights(self):
        self.assertEqual(len(validate_template({'version': 2, 'blocks': [{'type': 'backups'}] * MAX_BLOCKS})['blocks']), MAX_BLOCKS)
        with self.assertRaises(ValueError):
            validate_template({'version': 2, 'blocks': [{'type': 'backups'}] * (MAX_BLOCKS + 1)})
        validate_template(self.make(weights={f'k{i}': 1 for i in range(500)}))
        with self.assertRaises(ValueError):
            validate_template(self.make(weights={f'k{i}': 1 for i in range(501)}))

    def test_json_oddities_do_not_get_past_python_the_way_they_could(self):
        for bad in ({'version': True}, {'version': 2.5}, {'version': '2'}):
            with self.assertRaises(ValueError, msg=str(bad)):
                validate_template({**bad, 'blocks': [{'type': 'backups'}]})
        for weight in (True, False, math.nan, math.inf, -math.inf, '1', None, [1]):
            with self.assertRaises(ValueError, msg=repr(weight)):
                validate_template(self.make(weights={'third-party': weight}))
        with self.assertRaises(ValueError):
            validate_template(self.make(weights={'a\n': 1}))            # "$" would have let a trailing newline through
        with self.assertRaises(ValueError):
            validate_template({'version': 2, 'blocks': [{'type': 'tickets'}], 'period': {'from': '2026-10-01\n'}})

    def test_a_whole_number_written_as_a_float_is_accepted_for_max_rows(self):
        self.assertEqual(validate_template(self.make(maxRows=50.0))['maxRows'], 50)    # JSON numbers have no int/float difference in JavaScript
        self.assertEqual(validate_template(self.make(maxRows=50.5))['maxRows'], 200)
        self.assertEqual(validate_template(self.make(maxRows=True))['maxRows'], 200)

    def test_unknown_fields_are_dropped_and_the_input_is_not_changed(self):
        raw = {'version': 2, 'surprise': 1, 'blocks': [{'type': 'backups', 'colour': 'red', 'filters': {'customer': 'Acme'}}]}
        before = json.dumps(raw)
        result = validate_template(raw)
        self.assertNotIn('surprise', result)
        self.assertNotIn('colour', result['blocks'][0])
        self.assertEqual(json.dumps(raw), before)
        result['blocks'][0]['filters']['customer'] = 'changed'
        self.assertEqual(raw['blocks'][0]['filters']['customer'], 'Acme')

    def test_the_default_title(self):
        self.assertEqual(validate_template(self.make())['title'], DEFAULT_TITLE)


class SeveralValuesPerFilter(SimpleTestCase):
    def make(self, customer, kind='tickets'):
        return validate_template({'version': 2, 'blocks': [{'type': kind, 'filters': {'customer': customer}}]})['blocks'][0]['filters']['customer']

    def test_twenty_values_are_allowed_and_twenty_one_are_not(self):
        self.assertEqual(len(self.make([f'Customer {i}' for i in range(MAX_FILTER_VALUES)])), 20)
        with self.assertRaises(ValueError):
            self.make([f'Customer {i}' for i in range(MAX_FILTER_VALUES + 1)])

    def test_the_length_of_the_list_is_what_is_limited_not_what_is_left_after_cleaning(self):
        with self.assertRaises(ValueError):
            self.make(['Acme'] * 21)

    def test_values_over_200_characters_are_refused(self):
        with self.assertRaises(ValueError):
            self.make(['x' * 201])
        self.assertEqual(len(self.make(['x' * 200, 'y'])), 2)

    def test_a_single_value_filter_is_unchanged(self):
        stored = validate_template({'version': 2, 'blocks': [{'type': 'tickets', 'filters': {'customer': 'Acme'}}]})
        self.assertEqual(stored['blocks'][0]['filters']['customer'], 'Acme')
        self.assertEqual(validate_template(stored), stored)

    def test_the_cleaned_list_is_a_new_object_and_the_input_is_not_changed(self):
        raw = {'version': 2, 'blocks': [{'type': 'tickets', 'filters': {'customer': ['Acme', 'acme', 'Zed']}}]}
        before = json.dumps(raw)
        result = validate_template(raw)
        self.assertEqual(result['blocks'][0]['filters']['customer'], ['Acme', 'Zed'])
        self.assertEqual(json.dumps(raw), before)

    def test_json_values_that_are_not_text_inside_the_list_are_refused(self):
        for item in (5, None, True, ['a'], {'a': 1}):
            with self.assertRaises(ValueError, msg=repr(item)):
                self.make(['Acme', item])


class DefaultPeriodDuration(SimpleTestCase):
    def make(self, period):
        return validate_template({'version': 2, 'period': period, 'blocks': [{'type': 'backups'}]})['period']

    def test_each_unit_allows_its_maximum_and_not_one_more(self):
        for unit, longest in (('days', 3660), ('weeks', 520), ('months', 120)):
            self.assertEqual(self.make({'last': {'amount': longest, 'unit': unit}})['last'], {'amount': longest, 'unit': unit})
            with self.assertRaises(ValueError, msg=f'{longest + 1} {unit}'):
                self.make({'last': {'amount': longest + 1, 'unit': unit}})

    def test_a_whole_number_written_as_a_float_is_accepted_and_stored_as_an_integer(self):
        stored = self.make({'last': {'amount': 3.0, 'unit': 'months'}})['last']
        self.assertEqual(stored, {'amount': 3, 'unit': 'months'})
        self.assertIsInstance(stored['amount'], int)

    def test_json_oddities_in_a_duration_are_refused(self):
        for amount in (True, False, math.nan, math.inf, '3', None, [3], 0, -1, 1.5):
            with self.assertRaises(ValueError, msg=repr(amount)):
                self.make({'last': {'amount': amount, 'unit': 'days'}})
        for unit in (None, 5, ['days'], 'Days', 'years', ''):
            with self.assertRaises(ValueError, msg=repr(unit)):
                self.make({'last': {'amount': 3, 'unit': unit}})

    def test_a_duration_and_dates_together_are_refused_but_blank_dates_are_fine(self):
        with self.assertRaises(ValueError):
            self.make({'to': '2026-10-31', 'last': {'amount': 3, 'unit': 'days'}})
        self.assertEqual(self.make({'from': '', 'to': '', 'last': {'amount': 3, 'unit': 'days'}}), {'from': '', 'to': '', 'last': {'amount': 3, 'unit': 'days'}})

    def test_no_duration_means_no_field_and_a_stored_duration_survives_validating_again(self):
        self.assertEqual(self.make({'from': '2026-10-01'}), {'from': '2026-10-01', 'to': ''})
        stored = validate_template({'version': 2, 'period': {'last': {'amount': 6, 'unit': 'months'}}, 'blocks': [{'type': 'backups'}]})
        self.assertEqual(validate_template(stored), stored)
