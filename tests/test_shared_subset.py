# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Le code de shared/ reste dans le sous-ensemble commun à CPython et MicroPython (§ 10).

Analyse syntaxique seulement : ce test ne remplace pas une exécution sous
MicroPython. Dans la CI, seul pico_proto.py passe par mpy-cross, à travers sa
copie firmware/pico_target/proto.py (job mpy-cross-pico, complément 60).
"""

import ast
import unittest
from pathlib import Path

SHARED = Path(__file__).resolve().parent.parent / "shared"

ALLOWED_IMPORTS = {"binascii", "struct", "micropython"}
FORBIDDEN_ATTRS = {"hex", "fromhex", "to_bytes", "from_bytes"}
FORBIDDEN_CALLS = {"print", "int", "open", "input"}


def shared_files():
    files = sorted(SHARED.glob("*.py"))
    assert files, "shared/ est vide"
    return files


class SharedSubset(unittest.TestCase):
    def check(self, path):
        return self.check_source(path.name, path.read_text(encoding="utf-8"))

    def check_source(self, name, source):
        tree = ast.parse(source, name)
        problems = []

        def bad(node, what):
            problems.append("%s:%d : %s" % (name, node.lineno, what))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                        bad(node, "import %s" % a.name)
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                    bad(node, "from %s import" % node.module)
            elif isinstance(node, ast.JoinedStr):
                bad(node, "f-string")
            elif isinstance(node, ast.AnnAssign):
                bad(node, "annotation de variable")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns is not None:
                    bad(node, "annotation de retour")
                args = node.args
                for a in args.posonlyargs + args.args + args.kwonlyargs:
                    if a.annotation is not None:
                        bad(node, "annotation de l'argument %s" % a.arg)
            elif isinstance(node, ast.Match):
                bad(node, "match")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
                bad(node, "méthode .%s()" % node.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    bad(node, "appel de %s()" % node.func.id)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (isinstance(item, ast.Assign) and
                            any(isinstance(t, ast.Name) and t.id == "__slots__" for t in item.targets)):
                        bad(item, "__slots__")
        return problems

    def test_shared_files(self):
        for path in shared_files():
            with self.subTest(file=path.name):
                self.assertEqual(self.check(path), [])

    def test_detector_catches_violations(self):
        found = self.check_source(
            "exemple.py",
            "import re\nx: int = 1\ndef f(a: int) -> int:\n    print(f'{a}')\n"
            "    return int('1') + b''.hex()\n")
        kinds = " ".join(found)
        for word in ("import re", "annotation de variable", "annotation de retour",
                     "argument a", "f-string", "print()", "int()", ".hex()"):
            self.assertIn(word, kinds)


if __name__ == "__main__":
    unittest.main()
