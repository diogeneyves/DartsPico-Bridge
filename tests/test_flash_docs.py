# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Appels à `i75w.sh flash` dans la documentation.

`tools/bench_i75w/i75w.sh` (ligne 154) : « usage : i75w.sh flash [--micropython] <fichier.uf2>
<sha256 relevé> » ; le second argument est la **valeur** de l'empreinte, comparée telle quelle
à celle du fichier (`check_sha`, ligne 128), jamais le nom d'un fichier `.sha256`. Chaque appel
écrit dans un bloc de code des documents ci-dessous passe donc une valeur : la forme
"$(cut -d ' ' -f 1 <fichier>.sha256)", 64 chiffres hexadécimaux, ou un marqueur `<…>`.
"""

import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS = ["docs/INSTALLATION.md", "docs/PROCEDURE.md", "tools/bench_i75w/PROCEDURE.md",
        "tools/README.md"]
SCRIPT = os.path.join(ROOT, "tools", "bench_i75w", "i75w.sh")

CALL = re.compile(r"i75w\.sh flash((?: +\S+)*)")
CUT_FORM = re.compile(r"""^"\$\(cut -d ' ' -f 1 \S+\.sha256\)"$""")
HEX_FORM = re.compile(r"^[0-9a-fA-F]{64}$")
MARKER_FORM = re.compile(r"^<[^<>]+>$")


def flash_calls(text):
    """(numéro de ligne, dernier argument) de chaque appel à `i75w.sh flash` dans un bloc de code."""
    calls, fenced = [], False
    for no, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            continue
        m = CALL.search(line)
        if m:
            # Marqueur à espace (`<SHA-256 relevé>`) ou forme "$(cut …)" : un seul argument.
            rest = m.group(1).strip()
            last = re.search(r'("\$\([^)]*\)"|<[^<>]+>|\S+)$', rest)
            calls.append((no, last.group(1) if last else ""))
    return calls


def is_value(arg):
    return bool(CUT_FORM.match(arg) or HEX_FORM.match(arg) or MARKER_FORM.match(arg))


class TestFlashDocs(unittest.TestCase):
    def test_usage_of_script(self):
        # La syntaxe citée ci-dessus est bien celle du script.
        with open(SCRIPT, encoding="utf-8") as f:
            self.assertIn('usage : i75w.sh flash [--micropython] <fichier.uf2> <sha256 relevé>', f.read())

    def test_calls_pass_a_value(self):
        seen = 0
        for rel in DOCS:
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                for no, arg in flash_calls(f.read()):
                    seen += 1
                    with self.subTest(fichier=rel, ligne=no):
                        self.assertTrue(is_value(arg), "%s:%d : empreinte attendue, reçu %r" % (rel, no, arg))
        self.assertGreaterEqual(seen, 4)


class TestHelpers(unittest.TestCase):
    def test_witness(self):
        text = ("i75w.sh flash a.uf2 a.uf2.sha256\n```bash\n"
                "X=1 tools/bench_i75w/i75w.sh flash a.uf2 a.uf2.sha256\n"
                "./i75w.sh flash a.uf2 \"$(cut -d ' ' -f 1 a.uf2.sha256)\"\n"
                "./i75w.sh flash --micropython b.uf2 " + "ab" * 32 + "\n"
                "./i75w.sh flash b.uf2 <SHA-256 relevé>\n```\n")
        calls = flash_calls(text)
        self.assertEqual([no for no, _ in calls], [3, 4, 5, 6])
        self.assertEqual([is_value(a) for _, a in calls], [False, True, True, True])
        self.assertFalse(is_value("ab" * 31))
        self.assertFalse(is_value('"$(cat a.uf2.sha256)"'))


if __name__ == "__main__":
    unittest.main()
