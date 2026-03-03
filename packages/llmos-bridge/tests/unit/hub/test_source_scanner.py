"""Tests for hub.source_scanner — SourceCodeScanner."""

from __future__ import annotations

import asyncio
import time

import pytest

from llmos_bridge.hub.source_scanner import (
    SourceCodeScanner,
    SourceCodeRule,
    SourceScanFinding,
    SourceScanResult,
)
from llmos_bridge.security.scanners.base import ScanVerdict


@pytest.fixture()
def scanner():
    return SourceCodeScanner()


def _write_module(tmp_path, filename: str, content: str):
    """Write a .py file inside tmp_path and return the path."""
    f = tmp_path / filename
    f.write_text(content, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Rule category tests
# ---------------------------------------------------------------------------


class TestDangerousBuiltins:
    @pytest.mark.asyncio
    async def test_eval_detected(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'result = eval(user_input)\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_eval" for f in result.findings)

    @pytest.mark.asyncio
    async def test_exec_detected(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'exec(code_string)\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_exec" for f in result.findings)

    @pytest.mark.asyncio
    async def test_compile_detected(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'c = compile(src, "<string>", "exec")\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_compile" for f in result.findings)

    @pytest.mark.asyncio
    async def test_dunder_import_detected(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'mod = __import__(name)\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_dunder_import" for f in result.findings)

    @pytest.mark.asyncio
    async def test_globals_detected(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'g = globals()\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_globals_access" for f in result.findings)

    @pytest.mark.asyncio
    async def test_setattr_on_sys(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'setattr(sys, "path", [])\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_setattr_dynamic" for f in result.findings)


class TestSubprocessShell:
    @pytest.mark.asyncio
    async def test_subprocess_shell_true(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'subprocess.call("ls", shell=True)\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_subprocess_shell" for f in result.findings)

    @pytest.mark.asyncio
    async def test_os_system(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'os.system("rm -rf /")\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_os_system" for f in result.findings)

    @pytest.mark.asyncio
    async def test_os_popen(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'os.popen("cat /etc/passwd")\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_os_popen" for f in result.findings)


class TestNetworkAccess:
    @pytest.mark.asyncio
    async def test_socket_creation(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 's = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_socket_create" for f in result.findings)

    @pytest.mark.asyncio
    async def test_requests_get(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'r = requests.get("http://example.com")\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_requests_call" for f in result.findings)

    @pytest.mark.asyncio
    async def test_httpx_client(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'c = httpx.AsyncClient()\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_httpx_call" for f in result.findings)


class TestObfuscation:
    @pytest.mark.asyncio
    async def test_b64_exec_combo(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'exec(base64.b64decode(payload))\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_b64_exec" for f in result.findings)

    @pytest.mark.asyncio
    async def test_marshal_loads(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'code = marshal.loads(data)\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_marshal_loads" for f in result.findings)

    @pytest.mark.asyncio
    async def test_pickle_loads(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'obj = pickle.loads(data)\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_pickle_loads" for f in result.findings)


class TestCredentialExposure:
    @pytest.mark.asyncio
    async def test_hardcoded_aws_key(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_hardcoded_aws" for f in result.findings)

    @pytest.mark.asyncio
    async def test_hardcoded_openai_token(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'key = "sk-abcdefghijklmnopqrstuvwxyz12345678"\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_hardcoded_token" for f in result.findings)

    @pytest.mark.asyncio
    async def test_hardcoded_github_token(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'token = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234"\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_hardcoded_token" for f in result.findings)


class TestCodeInjection:
    @pytest.mark.asyncio
    async def test_ctypes_cdll(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'lib = ctypes.CDLL("libevil.so")\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_ctypes_cdll" for f in result.findings)

    @pytest.mark.asyncio
    async def test_sys_modules_inject(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'sys.modules["os"] = fake_os\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_sys_modules_inject" for f in result.findings)


class TestPermissionAbuse:
    @pytest.mark.asyncio
    async def test_unrestricted_profile(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'permission_profile = "unrestricted"\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_unrestricted_profile" for f in result.findings)

    @pytest.mark.asyncio
    async def test_disable_decorators(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'enable_decorators = False\n')
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "sc_disable_decorators" for f in result.findings)


# ---------------------------------------------------------------------------
# Clean module tests
# ---------------------------------------------------------------------------


class TestCleanModule:
    @pytest.mark.asyncio
    async def test_clean_module_passes(self, scanner, tmp_path):
        _write_module(tmp_path, "__init__.py", "")
        _write_module(tmp_path, "module.py", """
from typing import Any

class MyModule:
    def __init__(self):
        self.name = "clean_module"

    def process(self, data: dict) -> dict:
        return {"result": data.get("input", "")}
""")
        result = await scanner.scan_directory(tmp_path)
        assert result.verdict == ScanVerdict.ALLOW
        assert result.score == 100.0
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_empty_directory(self, scanner, tmp_path):
        result = await scanner.scan_directory(tmp_path)
        assert result.verdict == ScanVerdict.ALLOW
        assert result.score == 100.0
        assert result.files_scanned == 0

    @pytest.mark.asyncio
    async def test_comments_and_strings_in_code(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", """
# This comment mentions eval() but should not trigger
name = "eval is a function"
""")
        result = await scanner.scan_directory(tmp_path)
        # The comment line is skipped, but the string line may or may not match
        # depending on regex. The key is comments are skipped.
        comment_findings = [f for f in result.findings if f.line_number == 2]
        assert len(comment_findings) == 0  # Comment line skipped


class TestScoringAndVerdict:
    @pytest.mark.asyncio
    async def test_single_low_severity_warning(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'r = requests.get("http://example.com")\n')
        result = await scanner.scan_directory(tmp_path)
        # requests.get severity = 0.3, penalty = 3.0, score = 97
        assert result.score > 90
        assert result.verdict == ScanVerdict.ALLOW

    @pytest.mark.asyncio
    async def test_multiple_high_severity_rejects(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", """
exec(base64.b64decode(payload))
os.system("rm -rf /")
eval(user_input)
subprocess.call("cmd", shell=True)
marshal.loads(data)
pickle.loads(data)
ctypes.CDLL("libevil.so")
os.popen("whoami")
""")
        result = await scanner.scan_directory(tmp_path)
        assert result.verdict == ScanVerdict.REJECT
        assert result.score < 30
        assert len(result.findings) >= 7

    @pytest.mark.asyncio
    async def test_diminishing_returns_same_rule(self, scanner, tmp_path):
        # Multiple eval() calls should have diminishing penalty
        _write_module(tmp_path, "mod.py", """
a = eval(x)
b = eval(y)
c = eval(z)
""")
        result = await scanner.scan_directory(tmp_path)
        # First eval: 0.8 * 1.0 * 10 = 8, second: 0.8 * 0.5 * 10 = 4, third: 4
        # Total penalty = 16, score = 84
        assert 70 < result.score < 95

    @pytest.mark.asyncio
    async def test_verdict_thresholds_configurable(self, tmp_path):
        scanner = SourceCodeScanner(reject_threshold=50.0, warn_threshold=90.0)
        _write_module(tmp_path, "mod.py", 'r = requests.get("http://example.com")\n')
        result = await scanner.scan_directory(tmp_path)
        # score ~97, warn_threshold=90 → still ALLOW
        assert result.verdict == ScanVerdict.ALLOW


class TestFileHandling:
    @pytest.mark.asyncio
    async def test_skips_pycache(self, scanner, tmp_path):
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "cached.py").write_text('eval("bad")\n')
        _write_module(tmp_path, "mod.py", "x = 1\n")
        result = await scanner.scan_directory(tmp_path)
        assert not any("__pycache__" in f.file_path for f in result.findings)

    @pytest.mark.asyncio
    async def test_skips_test_directories(self, scanner, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_mod.py").write_text('eval("test ok")\n')
        _write_module(tmp_path, "mod.py", "x = 1\n")
        result = await scanner.scan_directory(tmp_path)
        assert not any("tests" in f.file_path for f in result.findings)

    @pytest.mark.asyncio
    async def test_non_py_files_ignored(self, scanner, tmp_path):
        (tmp_path / "readme.md").write_text("eval() is dangerous\n")
        (tmp_path / "config.json").write_text('{"eval": true}\n')
        _write_module(tmp_path, "mod.py", "x = 1\n")
        result = await scanner.scan_directory(tmp_path)
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_finding_has_correct_line_number(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", """import os
# comment
x = 1
os.system("whoami")
""")
        result = await scanner.scan_directory(tmp_path)
        os_findings = [f for f in result.findings if f.rule_id == "sc_os_system"]
        assert len(os_findings) == 1
        assert os_findings[0].line_number == 4

    @pytest.mark.asyncio
    async def test_relative_file_path_in_finding(self, scanner, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        _write_module(sub, "inner.py", 'eval("x")\n')
        # Scan from tmp_path (not subdir)
        # subdir is not a test dir, so its files should be scanned
        result = await scanner.scan_directory(tmp_path)
        if result.findings:
            assert result.findings[0].file_path.startswith("subdir/")


class TestPerformance:
    @pytest.mark.asyncio
    async def test_scan_under_100ms(self, scanner, tmp_path):
        # Create 20 files with ~25 lines each
        for i in range(20):
            lines = [f"x_{i}_{j} = {j}" for j in range(25)]
            _write_module(tmp_path, f"mod_{i}.py", "\n".join(lines))
        result = await scanner.scan_directory(tmp_path)
        assert result.scan_duration_ms < 100.0
        assert result.files_scanned == 20


class TestCustomRules:
    @pytest.mark.asyncio
    async def test_extra_rules(self, tmp_path):
        import re
        custom = SourceCodeRule(
            id="custom_test",
            category="custom",
            pattern=re.compile(r"\bforbidden_func\b"),
            severity=0.9,
            description="Custom forbidden function",
        )
        scanner = SourceCodeScanner(extra_rules=[custom])
        _write_module(tmp_path, "mod.py", "forbidden_func()\n")
        result = await scanner.scan_directory(tmp_path)
        assert any(f.rule_id == "custom_test" for f in result.findings)

    @pytest.mark.asyncio
    async def test_disabled_rules(self, tmp_path):
        scanner = SourceCodeScanner(disabled_rule_ids={"sc_eval"})
        _write_module(tmp_path, "mod.py", 'eval("x")\n')
        result = await scanner.scan_directory(tmp_path)
        assert not any(f.rule_id == "sc_eval" for f in result.findings)


class TestSerialization:
    def test_finding_to_dict(self):
        f = SourceScanFinding(
            rule_id="sc_eval",
            category="dangerous_builtins",
            severity=0.8,
            file_path="mod.py",
            line_number=1,
            line_content='eval("x")',
            description="Use of eval()",
        )
        d = f.to_dict()
        assert d["rule_id"] == "sc_eval"
        assert d["line_number"] == 1

    @pytest.mark.asyncio
    async def test_result_to_dict(self, scanner, tmp_path):
        _write_module(tmp_path, "mod.py", 'eval("x")\n')
        result = await scanner.scan_directory(tmp_path)
        d = result.to_dict()
        assert "verdict" in d
        assert "score" in d
        assert "findings" in d
        assert isinstance(d["findings"], list)
