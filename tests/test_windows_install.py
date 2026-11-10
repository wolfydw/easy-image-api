from __future__ import annotations

import base64
import codecs
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
RESULT_PREFIX = "EASY_IMAGE_API_TEST_RESULT="

# Requests and prompts are replaced unless a test explicitly uses localhost.
# CODEX_HOME and temporary-directory variables stay inside the test directory.
HARNESS_SETUP = r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$global:easyImageTestDownloads = @()
$global:easyImageTestInstallerRequests = @()
$global:easyImageTestPrompts = @()
$global:easyImageTestExecutionCount = 0
$global:easyImageTestObservedScriptRoot = "<not executed>"
$previousTls = [Net.ServicePointManager]::SecurityProtocol
function Invoke-WebRequest {
    [CmdletBinding()]
    param(
        [switch]$UseBasicParsing,
        [string]$Uri,
        [hashtable]$Headers,
        [int]$TimeoutSec,
        [string]$OutFile
    )
    $global:easyImageTestDownloads += @{
        uri = $Uri
        timeout = $TimeoutSec
        headers = $Headers
        path = $OutFile
        tls12 = ([Net.ServicePointManager]::SecurityProtocol -band [Net.SecurityProtocolType]::Tls12) -ne 0
    }
    [IO.File]::WriteAllText($OutFile, "partial download")
    if ($global:easyImageTestDownloads.Count -le [int]$env:EASY_IMAGE_TEST_FAIL_COUNT) {
        throw "simulated HTTPS connection closed"
    }
    if ($env:EASY_IMAGE_TEST_INVALID_FIRST -eq "1" -and $global:easyImageTestDownloads.Count -eq 1) {
        return
    }
    Copy-Item -LiteralPath $env:EASY_IMAGE_TEST_PAYLOAD -Destination $OutFile -Force
}
function Invoke-RestMethod {
    [CmdletBinding()]
    param(
        [switch]$UseBasicParsing,
        [string]$Uri,
        [hashtable]$Headers,
        [string]$Method,
        [int]$TimeoutSec
    )
    if ($Uri -match '/install\.ps1(?:\?|$)') {
        $global:easyImageTestInstallerRequests += @{
            uri = $Uri
            timeout = $TimeoutSec
            headers = $Headers
            errorAction = [string]$PSBoundParameters["ErrorAction"]
            tls12 = ([Net.ServicePointManager]::SecurityProtocol -band [Net.SecurityProtocolType]::Tls12) -ne 0
        }
        if ($env:EASY_IMAGE_TEST_SCRIPT_DOWNLOAD_FAIL -eq "1") {
            Write-Error "simulated HTTPS connection closed"
            return
        }
        return [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($env:EASY_IMAGE_TEST_INSTALLER_PAYLOAD))
    }
    return @{ data = @(@{ id = "gpt-image-2" }, @{ id = "fixture-other" }) }
}
function Read-Host {
    param([string]$Prompt)
    $global:easyImageTestPrompts += $Prompt
    if ($global:easyImageTestPrompts.Count -eq 1) { return "isolated-test-key" }
    return ""
}
if ($env:EASY_IMAGE_TEST_REAL_HTTP -eq "1") {
    Remove-Item Function:Invoke-RestMethod
}
$ErrorActionPreference = "Continue"
$ProgressPreference = "Continue"
$previousErrorPreference = $ErrorActionPreference
$previousProgressPreference = $ProgressPreference
$succeeded = $false
$failure = ""
try {
    if ($env:EASY_IMAGE_TEST_COMMAND_TARGET -eq "1") {
        Invoke-Expression ([IO.File]::ReadAllText($env:EASY_IMAGE_TEST_TARGET, [Text.Encoding]::UTF8))
    }
    else {
        & $env:EASY_IMAGE_TEST_TARGET
    }
    $succeeded = $true
}
catch {
    $failure = $_.Exception.Message
}
$result = [ordered]@{
    succeeded = $succeeded
    error = $failure
    downloads = @($global:easyImageTestDownloads)
    installerRequests = @($global:easyImageTestInstallerRequests)
    prompts = @($global:easyImageTestPrompts)
    executionCount = $global:easyImageTestExecutionCount
    observedScriptRoot = $global:easyImageTestObservedScriptRoot
    removed = @($global:easyImageTestDownloads | ForEach-Object { -not (Test-Path -LiteralPath $_.path) })
    tlsRestored = [Net.ServicePointManager]::SecurityProtocol -eq $previousTls
    errorPreferenceRestored = $ErrorActionPreference -eq $previousErrorPreference
    progressPreferenceRestored = $ProgressPreference -eq $previousProgressPreference
}
Write-Output ("EASY_IMAGE_API_TEST_RESULT=" + ($result | ConvertTo-Json -Depth 8 -Compress))
'''


def _run_harness(
    directory: Path, target: Path, payload: Path, *, command_mode: bool = False, **options: str
) -> dict:
    harness = directory / "harness.ps1"
    harness.write_text(HARNESS_SETUP, encoding="utf-8-sig")
    temporary = directory / "temporary files"
    temporary.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "CODEX_HOME": str(directory / "isolated codex"),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
            "EASY_IMAGE_TEST_TARGET": str(target),
            "EASY_IMAGE_TEST_PAYLOAD": str(payload),
            "EASY_IMAGE_TEST_INSTALLER_PAYLOAD": str(payload),
            "EASY_IMAGE_TEST_FAIL_COUNT": "0",
            "EASY_IMAGE_TEST_INVALID_FIRST": "0",
            "EASY_IMAGE_TEST_SCRIPT_DOWNLOAD_FAIL": "0",
            "EASY_IMAGE_TEST_INSTALLER_THROW": "0",
            "EASY_IMAGE_TEST_REAL_HTTP": "0",
            "EASY_IMAGE_TEST_COMMAND_TARGET": "1" if command_mode else "0",
            "EASY_IMAGE_TEST_INSTALLER_MARKER": str(directory / "installer marker.txt"),
        }
    )
    environment.update(options)
    command = [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if command_mode:
        encoded = base64.b64encode(HARNESS_SETUP.encode("utf-16le")).decode("ascii")
        command += ["-EncodedCommand", encoded]
    else:
        command += ["-File", str(harness)]
    completed = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    result_lines = [line for line in completed.stdout.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(result_lines) != 1:
        raise AssertionError(completed.stdout + completed.stderr)
    result = json.loads(result_lines[0][len(RESULT_PREFIX):])
    result["stdout"] = completed.stdout
    result["stderr"] = completed.stderr
    return result


def _bootstrap_fixture(directory: Path, block: int = 0) -> tuple[Path, Path]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```powershell\r?\n(.*?)```", readme, re.DOTALL)
    if len(blocks) <= block:
        raise AssertionError("README has no PowerShell installation command")
    bootstrap = directory / "readme command.ps1"
    bootstrap.write_text(blocks[block], encoding="utf-8-sig")
    payload = directory / "downloaded installer.ps1"
    payload.write_text(
        '$global:easyImageTestExecutionCount++\n'
        '[IO.File]::WriteAllText($env:EASY_IMAGE_TEST_INSTALLER_MARKER, "安装脚本已执行", [Text.Encoding]::UTF8)\n'
        'if ($env:EASY_IMAGE_TEST_INSTALLER_THROW -eq "1") { throw "simulated installer failure" }\n',
        encoding="utf-8-sig",
    )
    return bootstrap, payload


def _source_fixture(directory: Path) -> Path:
    source = directory / "本地安装 source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text("fixture skill\n", encoding="utf-8")
    for name in ("generate_image.py", "run_generate_image.sh", "run_generate_image.ps1"):
        (source / "scripts" / name).write_text("fixture runtime\n", encoding="utf-8")
    return source


def _archive_fixture(directory: Path) -> Path:
    source = _source_fixture(directory)
    archive_path = directory / "fixture.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.write(path, "repository-main/" + path.relative_to(source).as_posix())
    return archive_path


class PowerShellEncodingTests(unittest.TestCase):
    def test_powershell_files_use_utf8_bom_for_windows_powershell(self) -> None:
        for relative in ("install.ps1", "scripts/run_generate_image.ps1"):
            with self.subTest(path=relative):
                content = (ROOT / relative).read_bytes()
                self.assertTrue(content.startswith(codecs.BOM_UTF8))
                content.decode("utf-8-sig")


@unittest.skipUnless(POWERSHELL, "PowerShell is required for installation behavior tests")
class WindowsBootstrapTests(unittest.TestCase):
    def assert_request_and_preferences(self, result: dict) -> None:
        self.assertEqual(len(result["installerRequests"]), 1)
        request = result["installerRequests"][0]
        self.assertTrue(request["tls12"], result)
        self.assertTrue(0 < request["timeout"] <= 60, result)
        self.assertEqual(request["errorAction"], "Stop")
        self.assertTrue(result["errorPreferenceRestored"], result)
        self.assertTrue(result["progressPreferenceRestored"], result)

    def test_readme_command_downloads_and_executes_unicode_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, payload = _bootstrap_fixture(root)
            result = _run_harness(root, target, payload)
            self.assertTrue(result["succeeded"], result)
            self.assertEqual(result["executionCount"], 1)
            self.assertEqual((root / "installer marker.txt").read_text(encoding="utf-8-sig"), "安装脚本已执行")
            self.assert_request_and_preferences(result)

    def test_readme_api_command_downloads_raw_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, payload = _bootstrap_fixture(root, block=1)
            result = _run_harness(root, target, payload)
            self.assertTrue(result["succeeded"], result)
            self.assertEqual(result["executionCount"], 1)
            request = result["installerRequests"][0]
            self.assertIn("api.github.com", request["uri"])
            self.assertEqual(request["headers"]["Accept"], "application/vnd.github.raw+json")
            self.assert_request_and_preferences(result)

    def test_readme_command_stops_when_script_download_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, payload = _bootstrap_fixture(root)
            result = _run_harness(root, target, payload, EASY_IMAGE_TEST_SCRIPT_DOWNLOAD_FAIL="1")
            self.assertFalse(result["succeeded"], result)
            self.assertEqual(result["executionCount"], 0)
            self.assertEqual(result["downloads"], [])
            self.assertFalse((root / "installer marker.txt").exists())
            self.assertIn("simulated HTTPS connection closed", result["error"])
            self.assert_request_and_preferences(result)

    def test_readme_command_propagates_installer_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, payload = _bootstrap_fixture(root)
            result = _run_harness(root, target, payload, EASY_IMAGE_TEST_INSTALLER_THROW="1")
            self.assertFalse(result["succeeded"], result)
            self.assertEqual(result["executionCount"], 1)
            self.assertIn("simulated installer failure", result["error"])
            self.assert_request_and_preferences(result)

    def test_http_download_decodes_utf8_bom_before_iex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, payload = _bootstrap_fixture(root)
            content = payload.read_bytes()
            requests = []

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append(self.path)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

                def log_message(self, *_args):
                    pass

            with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
                endpoint = "http://127.0.0.1:{}/install.ps1".format(server.server_port)
                command, replacements = re.subn(
                    r"https://raw\.githubusercontent\.com/\S+/install\.ps1",
                    endpoint,
                    target.read_text(encoding="utf-8-sig"),
                )
                self.assertEqual(replacements, 1)
                target.write_text(command, encoding="utf-8-sig")
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    result = _run_harness(root, target, payload, EASY_IMAGE_TEST_REAL_HTTP="1")
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

            self.assertTrue(result["succeeded"], result)
            self.assertEqual(requests, ["/install.ps1"])
            self.assertEqual(result["executionCount"], 1)
            self.assertEqual((root / "installer marker.txt").read_text(encoding="utf-8-sig"), "安装脚本已执行")


@unittest.skipUnless(POWERSHELL, "PowerShell is required for installation behavior tests")
class WindowsInstallerTests(unittest.TestCase):
    def assert_installed(self, root: Path, result: dict, *, restore_tls: bool = True) -> None:
        self.assertTrue(result["succeeded"], result)
        destination = root / "isolated codex" / "skills" / "easy-image-api"
        self.assertEqual((destination / "SKILL.md").read_text(encoding="utf-8"), "fixture skill\n")
        self.assertTrue((destination / "scripts" / "generate_image.py").is_file())
        config = json.loads((destination / "config.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(config["api_key"], "isolated-test-key")
        self.assertEqual(config["model"], "gpt-image-2")
        self.assertNotIn("isolated-test-key", result["stdout"] + result["stderr"])
        if restore_tls:
            self.assertTrue(result["tlsRestored"], result)

    def test_readme_iex_installs_when_script_root_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, installer_payload = _bootstrap_fixture(root)
            installer_payload.write_text(
                '$global:easyImageTestObservedScriptRoot = $PSScriptRoot\n'
                + (ROOT / "install.ps1").read_text(encoding="utf-8-sig"),
                encoding="utf-8-sig",
            )
            archive = _archive_fixture(root)
            result = _run_harness(
                root,
                target,
                archive,
                command_mode=True,
                EASY_IMAGE_TEST_INSTALLER_PAYLOAD=str(installer_payload),
            )
            self.assert_installed(root, result, restore_tls=False)
            self.assertEqual(result["observedScriptRoot"], "")
            self.assertEqual(len(result["installerRequests"]), 1)
            self.assertEqual(len(result["downloads"]), 1)
            self.assertTrue(all(result["removed"]), result)

    def test_local_install_uses_extracted_source_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _source_fixture(root)
            target = source / "install.ps1"
            shutil.copyfile(ROOT / "install.ps1", target)
            result = _run_harness(root, target, root / "unused.zip", EASY_IMAGE_TEST_FAIL_COUNT="100")
            self.assert_installed(root, result)
            self.assertEqual(result["downloads"], [])

    def test_archive_download_falls_back_and_cleans_partial_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = _archive_fixture(root)
            target = root / "detached installer.ps1"
            shutil.copyfile(ROOT / "install.ps1", target)
            result = _run_harness(root, target, payload, EASY_IMAGE_TEST_FAIL_COUNT="1")
            self.assert_installed(root, result)
            self.assertEqual(len(result["downloads"]), 2)
            self.assertTrue(all(request["tls12"] for request in result["downloads"]))
            self.assertTrue(all(0 < request["timeout"] <= 120 for request in result["downloads"]))
            self.assertTrue(all(result["removed"]), result)

    def test_invalid_archive_tries_next_download_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = _archive_fixture(root)
            target = root / "detached installer.ps1"
            shutil.copyfile(ROOT / "install.ps1", target)
            result = _run_harness(root, target, payload, EASY_IMAGE_TEST_INVALID_FIRST="1")
            self.assert_installed(root, result)
            self.assertEqual(len(result["downloads"]), 2)
            self.assertTrue(all(result["removed"]), result)

    def test_failed_download_does_not_prompt_or_create_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "detached installer.ps1"
            shutil.copyfile(ROOT / "install.ps1", target)
            result = _run_harness(root, target, root / "unused.zip", EASY_IMAGE_TEST_FAIL_COUNT="2")
            self.assertFalse(result["succeeded"], result)
            self.assertEqual(len(result["downloads"]), 2)
            self.assertEqual(result["prompts"], [])
            self.assertFalse((root / "isolated codex").exists())
            self.assertTrue(all(result["removed"]), result)
            self.assertTrue(result["tlsRestored"], result)
            self.assertIn("simulated HTTPS connection closed", result["error"])


if __name__ == "__main__":
    unittest.main()
