# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Étape apt du job i75w-uf2 : délai (spécification, complément 68) et options d'apt (complément 71).

Le workflow est lu comme texte, sans PyYAML : le bloc de l'étape va de sa ligne « - name: »
jusqu'à l'étape suivante (ligne « - » à la même indentation) ou jusqu'à la fin du job.
"""

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_REL = Path(".github") / "workflows" / "dartspico-bridge-tests.yml"


def find_workflow(root):
    """Workflow à la racine du projet (dépôt DartsPico-Bridge), sinon au-dessus (My_GIT_Projects),
    complément 81. Aucun des deux : le chemin de la racine, absent, pour que les tests échouent
    (jamais de saut)."""
    for base in (root, root.parent):
        if (base / WORKFLOW_REL).is_file():
            return base / WORKFLOW_REL
    return root / WORKFLOW_REL


WORKFLOW = find_workflow(ROOT)
JOB = "i75w-uf2"
STEP = "Installer la chaîne ARM du système"
EXPECTED = "timeout-minutes: 15"
# Options -o exigées sur apt-get update et apt-get install (complément 71), clé -> valeur.
APT_OPTIONS = {
    "Acquire::Retries": "3",
    "Acquire::http::Timeout": "30",
    "Acquire::https::Timeout": "30",
}
UPDATE_FLAG = "--error-on=any"


def job_lines(text, job):
    """Lignes du job `job` (clé à deux espaces sous jobs:), sans sa ligne de titre."""
    lines = text.splitlines()
    head = f"  {job}:"
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == head)
    except StopIteration:
        raise AssertionError(f"job « {job} » introuvable dans le workflow") from None
    out = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 2:
            break  # job suivant, commentaire de premier niveau ou clé de tête
        out.append(line)
    return out


def step_block(text, job, name):
    """(indentation des clés de l'étape, lignes du bloc de l'étape `name` du job `job`)."""
    lines = job_lines(text, job)
    title = re.compile(r"^( *)- name: *(.*?) *$")
    for i, line in enumerate(lines):
        m = title.match(line)
        if m and m.group(2).strip("\"'") == name:
            dash = len(m.group(1))
            block = [line]
            for nxt in lines[i + 1:]:
                if nxt.startswith(" " * dash + "- "):
                    break
                block.append(nxt)
            return dash + 2, block
    raise AssertionError(f"étape « {name} » introuvable dans le job « {job} »")


def timeout_lines(indent, block):
    """Lignes « timeout-minutes: » à l'indentation exacte des clés de l'étape (sans espaces de fin)."""
    prefix = " " * indent + "timeout-minutes:"
    return [line[indent:].rstrip() for line in block[1:] if line.startswith(prefix)]


def apt_calls(block):
    """Commandes apt-get du bloc : liste de (sous-commande, [(clé, valeur) des -o], [autres options]).

    Les lignes terminées par « \\ » sont jointes à la suivante ; les commentaires sont ignorés.
    """
    logical, current = [], ""
    for line in block[1:]:
        stripped = line.strip()
        if not current and stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        logical.append(current + stripped)
        current = ""
    if current:
        logical.append(current)
    calls = []
    for text in logical:
        tokens = text.split()
        if "apt-get" not in tokens:
            continue
        rest = tokens[tokens.index("apt-get") + 1:]
        sub, opts, flags = None, [], []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "-o" and i + 1 < len(rest):
                key, _, value = rest[i + 1].partition("=")
                opts.append((key, value))
                i += 2
                continue
            if tok.startswith("-"):
                flags.append(tok)
            elif sub is None:
                sub = tok
            i += 1
        calls.append((sub, opts, flags))
    return calls


def apt_errors(calls):
    """Écarts avec le complément 71 (liste vide si tout est conforme)."""
    errors = []
    subs = [sub for sub, _, _ in calls]
    for wanted in ("update", "install"):
        if subs.count(wanted) != 1:
            errors.append(f"apt-get {wanted} : {subs.count(wanted)} commande(s) au lieu d'une")
    for sub, opts, flags in calls:
        if sub not in ("update", "install"):
            errors.append(f"commande apt-get inattendue : {sub}")
            continue
        for key, value in APT_OPTIONS.items():
            found = [v for k, v in opts if k == key]
            if found != [value]:
                errors.append(f"apt-get {sub} : -o {key}={value} attendu une fois (trouvé : {found})")
        if sub == "update" and flags.count(UPDATE_FLAG) != 1:
            errors.append(f"apt-get update : {UPDATE_FLAG} attendu une fois "
                          f"(trouvé {flags.count(UPDATE_FLAG)} fois)")
    return errors


class AptStepTimeoutTest(unittest.TestCase):
    def test_apt_step_has_15_minute_timeout(self):
        self.assertTrue(WORKFLOW.is_file(), f"workflow absent : {WORKFLOW}")
        indent, block = step_block(WORKFLOW.read_text(encoding="utf-8"), JOB, STEP)
        found = timeout_lines(indent, block)
        self.assertEqual(
            found, [EXPECTED],
            f"l'étape « {STEP} » du job « {JOB} » doit avoir exactement une ligne "
            f"« {EXPECTED} » à {indent} espaces (trouvé : {found})")

    def test_apt_commands_have_retries_and_network_timeout(self):
        _, block = step_block(WORKFLOW.read_text(encoding="utf-8"), JOB, STEP)
        errors = apt_errors(apt_calls(block))
        self.assertEqual(errors, [], f"étape « {STEP} » du job « {JOB} » (complément 71) : "
                         + " ; ".join(errors))


LICENSE_STEP = "Empreinte et informations du .uf2"
REPRO_STEP = "Vérifier que le .uf2 est reproductible (seconde compilation, autre date)"
# Licences jointes à l'artefact (complément 79, Q5) : source -> nom dans $OUT/licenses/.
LICENSES = {
    "$RUNNER_TEMP/pico-sdk/LICENSE.TXT": "pico-sdk-LICENSE.TXT",
    "$RUNNER_TEMP/pico-sdk/lib/tinyusb/LICENSE": "tinyusb-LICENSE",
    "LICENSES/pimoroni-MIT.txt": "pimoroni-MIT.txt",
}


def license_errors(block):
    """Écarts avec le complément 79, Q5 : pour chaque licence, un « [ -f source ] || { … exit 1; } »
    puis, plus bas, la copie vers "$OUT/licenses/<nom>" (liste vide si tout est conforme)."""
    lines = [line.strip() for line in block[1:] if not line.strip().startswith("#")]
    errors = []
    for src, name in LICENSES.items():
        check = re.compile(r'^\[ -f "' + re.escape(src) + r'" \] \|\| \{ .*\bexit 1; \}$')
        copy = 'cp "' + src + '" "$OUT/licenses/' + name + '"'
        checks = [i for i, line in enumerate(lines) if check.match(line)]
        copies = [i for i, line in enumerate(lines) if line == copy]
        if len(checks) != 1:
            errors.append(f"{src} : contrôle [ -f ] bloquant attendu une fois (trouvé {len(checks)})")
        if len(copies) != 1:
            errors.append(f"{src} : copie « {copy} » attendue une fois (trouvé {len(copies)})")
        if len(checks) == 1 and len(copies) == 1 and checks[0] > copies[0]:
            errors.append(f"{src} : contrôle [ -f ] après la copie")
    return errors


class ArtifactLicensesTest(unittest.TestCase):
    def test_fingerprint_step_copies_three_licenses_with_blocking_check(self):
        _, block = step_block(WORKFLOW.read_text(encoding="utf-8"), JOB, LICENSE_STEP)
        self.assertIn('mkdir -p "$OUT/licenses"', [line.strip() for line in block])
        errors = license_errors(block)
        self.assertEqual(errors, [], f"étape « {LICENSE_STEP} » (complément 79) : " + " ; ".join(errors))

    def test_sha256_and_reproducibility_cover_the_uf2_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        _, block = step_block(text, JOB, LICENSE_STEP)
        self.assertIn("(cd \"$OUT\" && sha256sum i75w_display.uf2 > i75w_display.uf2.sha256 "
                      "&& cat i75w_display.uf2.sha256)", [line.strip() for line in block])
        _, repro = step_block(text, JOB, REPRO_STEP)
        cmps = [line.strip() for line in repro if re.search(r"\bcmp\b", line)]
        self.assertEqual(cmps, ['if cmp "$RUNNER_TEMP/i75w-fw/i75w_display.uf2" '
                                '"$RUNNER_TEMP/i75w-fw2/i75w_display.uf2"; then'])
        self.assertFalse(any("licen" in line.lower() for line in repro))

    def test_parser_rejects_missing_check_or_copy(self):
        good = ["      - name: x", '        mkdir -p "$OUT/licenses"']
        for src, name in LICENSES.items():
            good += [f'        [ -f "{src}" ] || {{ echo "absent"; exit 1; }}',
                     f'        cp "{src}" "$OUT/licenses/{name}"']
        self.assertEqual(license_errors(good), [])
        self.assertEqual(len(license_errors([l for l in good if "tinyusb" not in l or "cp " in l])), 1)
        self.assertEqual(len(license_errors([l for l in good if "pimoroni" not in l or "[ -f" in l])), 1)
        no_exit = [l.replace("exit 1; ", "") for l in good]
        self.assertEqual(len(license_errors(no_exit)), 3)
        swapped = good[:2] + [good[3], good[2]] + good[4:]
        self.assertEqual(license_errors(swapped), [f"{list(LICENSES)[0]} : contrôle [ -f ] après la copie"])


class AptOptionsParserTest(unittest.TestCase):
    GOOD = (
        "      - name: Installer la chaîne ARM du système\n"
        "        # apt-get bloqué : commentaire ignoré\n"
        "        timeout-minutes: 15\n"
        "        run: |\n"
        "          sudo apt-get update -q --error-on=any -o Acquire::Retries=3 \\\n"
        "            -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30\n"
        "          sudo apt-get install -y -q --no-install-recommends \\\n"
        "            -o Acquire::Retries=3 -o Acquire::http::Timeout=30 \\\n"
        "            -o Acquire::https::Timeout=30 \\\n"
        "            gcc-arm-none-eabi libnewlib-arm-none-eabi\n"
        "          arm-none-eabi-gcc --version\n"
    )

    def errors(self, text):
        return apt_errors(apt_calls(text.splitlines()))

    def test_good_sample_accepted(self):
        calls = apt_calls(self.GOOD.splitlines())
        self.assertEqual([c[0] for c in calls], ["update", "install"])
        self.assertEqual(self.errors(self.GOOD), [])

    def test_missing_option_rejected(self):
        text = self.GOOD.replace("-o Acquire::Retries=3 \\\n            -o Acquire::http", "\\\n            -o Acquire::http")
        self.assertEqual(self.errors(text),
                         ["apt-get update : -o Acquire::Retries=3 attendu une fois (trouvé : [])"])

    def test_option_on_one_command_only_rejected(self):
        text = self.GOOD.replace("            -o Acquire::https::Timeout=30 \\\n", "")
        self.assertEqual(self.errors(text),
                         ["apt-get install : -o Acquire::https::Timeout=30 attendu une fois (trouvé : [])"])

    def test_other_value_or_duplicate_rejected(self):
        for bad in ("-o Acquire::Retries=5", "-o Acquire::Retries=3 -o Acquire::Retries=3"):
            text = self.GOOD.replace("update -q --error-on=any -o Acquire::Retries=3",
                                     "update -q --error-on=any " + bad)
            self.assertEqual(len(self.errors(text)), 1, bad)

    def test_error_on_any_required_on_update(self):
        text = self.GOOD.replace(" --error-on=any", "")
        self.assertEqual(self.errors(text), ["apt-get update : --error-on=any attendu une fois (trouvé 0 fois)"])

    def test_missing_or_extra_command_rejected(self):
        text = self.GOOD.replace("sudo apt-get update", "sudo true")
        self.assertIn("apt-get update : 0 commande(s) au lieu d'une", self.errors(text))
        text = self.GOOD + "          sudo apt-get upgrade -y\n"
        self.assertIn("commande apt-get inattendue : upgrade", self.errors(text))

    def test_comment_mentioning_apt_get_ignored(self):
        self.assertEqual(len(apt_calls(self.GOOD.splitlines())), 2)


class ParserTest(unittest.TestCase):
    SAMPLE = (
        "jobs:\n"
        "  other:\n"
        "    steps:\n"
        "      - name: Installer la chaîne ARM du système\n"
        "        timeout-minutes: 99\n"
        "  i75w-uf2:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7\n"
        "      - name: Installer la chaîne ARM du système\n"
        "        timeout-minutes: 15\n"
        "        run: |\n"
        "          timeout-minutes: 7\n"
        "      - uses: x/y@v1\n"
        "        timeout-minutes: 3\n"
        "  # commentaire du job suivant\n"
        "  next:\n"
        "    timeout-minutes: 1\n"
    )

    def test_block_limited_to_step_and_job(self):
        indent, block = step_block(self.SAMPLE, JOB, STEP)
        self.assertEqual(indent, 8)
        self.assertEqual(len(block), 4)
        self.assertEqual(timeout_lines(indent, block), [EXPECTED])

    def test_last_step_ends_with_job(self):
        text = self.SAMPLE.replace("      - uses: x/y@v1\n        timeout-minutes: 3\n", "")
        indent, block = step_block(text, JOB, STEP)
        self.assertEqual(timeout_lines(indent, block), [EXPECTED])
        self.assertNotIn("    timeout-minutes: 1", block)

    def test_wrong_indent_or_value_rejected(self):
        for bad in ("          timeout-minutes: 15\n", "        timeout-minutes: 20\n", ""):
            text = self.SAMPLE.replace("        timeout-minutes: 15\n", bad)
            indent, block = step_block(text, JOB, STEP)
            self.assertNotEqual(timeout_lines(indent, block), [EXPECTED], repr(bad))

    def test_duplicate_rejected(self):
        text = self.SAMPLE.replace("        timeout-minutes: 15\n", "        timeout-minutes: 15\n" * 2)
        indent, block = step_block(text, JOB, STEP)
        self.assertEqual(len(timeout_lines(indent, block)), 2)

    def test_missing_step_or_job_clear_message(self):
        with self.assertRaisesRegex(AssertionError, "étape « Inexistante » introuvable"):
            step_block(self.SAMPLE, JOB, "Inexistante")
        with self.assertRaisesRegex(AssertionError, "job « absent » introuvable"):
            step_block(self.SAMPLE, "absent", STEP)



class FindWorkflow(unittest.TestCase):
    """Ordre de recherche du workflow (complément 81, Q4)."""

    def make(self, base):
        path = base / WORKFLOW_REL
        path.parent.mkdir(parents=True)
        path.write_text("name: x\n", encoding="utf-8")
        return path

    def test_root_first_then_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "projet"
            root.mkdir()
            self.assertFalse(find_workflow(root).is_file())       # aucun : chemin absent
            self.assertEqual(find_workflow(root), root / WORKFLOW_REL)
            above = self.make(parent)
            self.assertEqual(find_workflow(root), above)
            inside = self.make(root)
            self.assertEqual(find_workflow(root), inside)

    def test_missing_workflow_fails_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = find_workflow(Path(tmp) / "a" / "b")
            with self.assertRaises(FileNotFoundError):
                missing.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
