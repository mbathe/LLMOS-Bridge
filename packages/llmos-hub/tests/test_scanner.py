"""Tests for HubSourceScanner — standalone security scanner (Phase 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmos_hub.scanner import HubSourceScanner, ScanVerdict


@pytest.fixture()
def scanner() -> HubSourceScanner:
    return HubSourceScanner()


def _write_module(tmp_path: Path, filename: str, code: str) -> Path:
    """Write a Python file in a module directory and return the module root."""
    mod_dir = tmp_path / "test_module"
    mod_dir.mkdir(exist_ok=True)
    (mod_dir / filename).write_text(code, encoding="utf-8")
    return mod_dir


class TestHubSourceScanner:
    def test_clean_module_allows(self, tmp_path, scanner):
        """A clean module with no suspicious patterns gets ALLOW verdict."""
        mod_dir = _write_module(tmp_path, "module.py", """\
class MyModule:
    def _action_hello(self):
        return "hello world"
""")
        result = scanner.scan_directory(mod_dir)
        assert result.verdict == ScanVerdict.ALLOW
        assert result.score == 100.0
        assert result.findings == []
        assert result.files_scanned == 1

    def test_eval_detected(self, tmp_path, scanner):
        """Use of eval() should produce a finding."""
        mod_dir = _write_module(tmp_path, "module.py", """\
class MyModule:
    def _action_run(self, params):
        return eval(params["code"])
""")
        result = scanner.scan_directory(mod_dir)
        assert len(result.findings) >= 1
        eval_findings = [f for f in result.findings if f.rule_id == "sc_eval"]
        assert len(eval_findings) == 1
        assert eval_findings[0].severity == 8.0
        assert eval_findings[0].line_number == 3
        # Score should be < 100 but not reject-level for a single eval.
        assert result.score < 100.0
        assert result.verdict == ScanVerdict.WARN

    def test_subprocess_shell_detected(self, tmp_path, scanner):
        """subprocess with shell=True should be flagged."""
        mod_dir = _write_module(tmp_path, "module.py", """\
import subprocess

class Mod:
    def _action_run(self, params):
        subprocess.run(params["cmd"], shell=True)
""")
        result = scanner.scan_directory(mod_dir)
        shell_findings = [f for f in result.findings if f.rule_id == "sc_subprocess_shell"]
        assert len(shell_findings) == 1
        assert shell_findings[0].severity == 9.0
        assert result.verdict in (ScanVerdict.WARN, ScanVerdict.REJECT)

    def test_base64_exec_combo_rejects(self, tmp_path, scanner):
        """Enough high-severity patterns push score below reject threshold (30)."""
        mod_dir = _write_module(tmp_path, "module.py", """\
import base64
import os
import subprocess
import ctypes
import marshal
import pickle

class Evil:
    def backdoor(self):
        exec(base64.b64decode("cHJpbnQoJ2hhY2tlZCcp"))
        os.system("rm -rf /")
        os.popen("id")
        subprocess.run("ls", shell=True)
        eval("1+1")
        exec("print('hi')")
        __import__("shutil")
        ctypes.cdll.LoadLibrary("libc.so")
        marshal.loads(b"data")
        pickle.loads(b"data")
""")
        result = scanner.scan_directory(mod_dir)
        # Multiple high-severity findings should push score below reject threshold.
        assert result.verdict == ScanVerdict.REJECT
        assert result.score < 30.0
        # Should have many findings.
        assert len(result.findings) >= 8

    def test_credential_detected(self, tmp_path, scanner):
        """Hardcoded credentials should be flagged."""
        mod_dir = _write_module(tmp_path, "module.py", """\
class Config:
    AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
    GH_TOKEN = "ghp_1234567890abcdefghij1234567890ab"
""")
        result = scanner.scan_directory(mod_dir)
        cred_findings = [f for f in result.findings if f.category == "credential_exposure"]
        assert len(cred_findings) >= 1
        assert result.verdict == ScanVerdict.WARN

    def test_diminishing_deduction(self, tmp_path, scanner):
        """Repeated occurrences of the same rule have diminishing score impact."""
        # 5 eval() calls: deductions = 8 + 4 + 2.67 + 2 + 1.6 = 18.27 -> score ~81.7
        code_lines = "\n".join(f"    eval('line{i}')" for i in range(5))
        mod_dir = _write_module(tmp_path, "module.py", f"""\
class Mod:
    def run(self):
{code_lines}
""")
        result = scanner.scan_directory(mod_dir)
        eval_findings = [f for f in result.findings if f.rule_id == "sc_eval"]
        assert len(eval_findings) == 5
        # Score should be significantly above 0 due to diminishing returns.
        # First deduction 8, second 4, third 2.67, fourth 2, fifth 1.6 = ~18.27
        assert result.score > 70.0
        assert result.verdict == ScanVerdict.WARN

    def test_comments_are_skipped(self, tmp_path, scanner):
        """Lines that are comments should not trigger findings."""
        mod_dir = _write_module(tmp_path, "module.py", """\
# eval("this is a comment")
# subprocess.run("ls", shell=True)
class Clean:
    pass
""")
        result = scanner.scan_directory(mod_dir)
        assert result.verdict == ScanVerdict.ALLOW
        assert result.findings == []
