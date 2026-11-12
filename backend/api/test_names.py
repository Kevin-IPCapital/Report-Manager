"""A name used but never defined is a NameError that only shows when that line runs, and the database-backed tests are the only thing that run the
views, so a typo in a rarely used view could wait a long time to be found. This reads every backend module with Python's own scope analysis
(`symtable`) and reports any name that is used but is not a local, an import, something defined in the module, or a built-in. No database needed."""
import builtins
import symtable
from pathlib import Path

from django.test import SimpleTestCase

BACKEND = Path(__file__).resolve().parent.parent
MODULE_NAMES = {'__file__', '__name__', '__doc__', '__package__', '__spec__', '__loader__', '__builtins__'}


def undefined_names(source, filename='<module>'):
    """[(where, name)] for every name the code uses that nothing defines."""
    top = symtable.symtable(source, filename, 'exec')
    defined = {s.get_name() for s in top.get_symbols() if s.is_assigned() or s.is_imported() or s.is_namespace() or s.is_parameter()}
    known = defined | set(dir(builtins)) | MODULE_NAMES
    found = []

    def walk(table):
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if not symbol.is_referenced() or name in known:
                continue
            if table is top and (symbol.is_assigned() or symbol.is_imported()):
                continue
            if symbol.is_global() or table is top:                    # looked up in the module, and the module has no such name
                found.append((table.get_name(), name))
        for child in table.get_children():
            walk(child)

    walk(top)
    return sorted(set(found))


class TheChecker(SimpleTestCase):
    """The checker itself, shown to find what it should and to leave alone what it should."""

    def test_a_misspelt_name_inside_a_function_is_found(self):
        self.assertEqual(undefined_names('def f(x):\n    return xx + 1\n'), [('f', 'xx')])

    def test_a_missing_import_is_found(self):
        self.assertEqual(undefined_names('def f():\n    return json.dumps({})\n'), [('f', 'json')])

    def test_names_that_are_defined_somewhere_in_scope_are_not_flagged(self):
        source = ('import os\nfrom a import b as c\nVALUE = 1\n\ndef g(): pass\n\nclass K:\n    attr = 2\n    def m(self, n):\n        total = n + VALUE\n'
                  '        return [i for i in range(total) if c(os.sep, g())] + [self.attr]\n\ndef outer():\n    seen = []\n    def inner():\n        seen.append(len(seen))\n    return inner\n'
                  'try:\n    pass\nexcept ValueError as exc:\n    print(exc)\nwith open("f") as handle:\n    handle.read()\n')
        self.assertEqual(undefined_names(source), [])

    def test_a_name_used_before_its_function_is_defined_is_fine_because_it_is_looked_up_when_called(self):
        self.assertEqual(undefined_names('def a():\n    return b()\n\ndef b():\n    return 1\n'), [])

    def test_built_ins_and_module_dunders_are_known(self):
        self.assertEqual(undefined_names('def f():\n    return len(str(__file__)), isinstance(1, int), __name__\n'), [])


class TheBackend(SimpleTestCase):
    def test_no_backend_module_uses_a_name_that_nothing_defines(self):
        modules = [p for p in sorted((BACKEND / 'api').rglob('*.py')) if 'migrations' not in p.parts and '__pycache__' not in p.parts]
        modules += sorted((BACKEND / 'config').glob('*.py'))
        self.assertGreater(len(modules), 40)
        problems = [f'{path.relative_to(BACKEND)}: {where} uses {name}' for path in modules for where, name in undefined_names(path.read_text(encoding='utf-8'), str(path))]
        self.assertEqual(problems, [])
