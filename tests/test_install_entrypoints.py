"""Run the real shell entrypoints against controlled downloads, without network."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_URL = "https://github.com/wolfydw/easy-image-api/archive/refs/heads/main.zip"
SCRIPT_URL = "https://raw.githubusercontent.com/wolfydw/easy-image-api/main/install.sh"
PROXY = "https://ghfast.top/"


@unittest.skipUnless(shutil.which("bash") and shutil.which("unzip"), "requires bash and unzip")
class ShellDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "requests.log"
        self.fixture = self.root / "fixture.zip"
        with zipfile.ZipFile(self.fixture, "w") as archive:
            for name in ("SKILL.md", "scripts/generate_image.py", "scripts/run_generate_image.sh", "scripts/run_generate_image.ps1"):
                archive.writestr("easy-image-api-main/" + name, "updated runtime\n")
        self.destination = self.root / "codex" / "skills" / "easy-image-api"
        self.destination.mkdir(parents=True)
        self.config = self.destination / "config.json"
        self.config.write_bytes(b"untouched config\n")
        self.config_stat = self.config.stat()
        (self.destination / "SKILL.md").write_text("old skill\n")
        # The failed direct request deliberately leaves executable partial content.
        # A bootstrap must never execute it or concatenate it with the retry.
        fake_curl = self.bin / "curl"
        fake_curl.write_text("#!" + sys.executable + "\n" + '''
import os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("https://"))
with open(os.environ["DOWNLOAD_LOG"], "a") as log:
    log.write(url + "\\n")
flag = "--output" if "--output" in args else "-o"
output = pathlib.Path(args[args.index(flag) + 1])
mode = os.environ["DOWNLOAD_MODE"]
is_proxy = url.startswith("https://ghfast.top/")
if mode == "both_fail" or (mode == "fallback" and not is_proxy):
    output.write_text('echo unsafe > "' + os.environ["PARTIAL_MARKER"] + '"\\n')
    sys.exit(28)
if mode == "html" and not is_proxy:
    output.write_text("<html>network login</html>")
else:
    shutil.copyfile(os.environ["DOWNLOAD_FIXTURE"], output)
''')
        fake_curl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CODEX_HOME=str(self.root / "codex"), DOWNLOAD_LOG=str(self.log),
                        DOWNLOAD_FIXTURE=str(self.fixture), PARTIAL_MARKER=str(self.root / "unsafe"),
                        TMPDIR=str(self.root))

    def run_script(self, mode, bootstrap=False):
        self.env["DOWNLOAD_MODE"] = mode
        if bootstrap:
            snippet = next(block for block in re.findall(r"```bash\n(.*?)```", (ROOT / "README.md").read_text(), re.S) if SCRIPT_URL in block)
            command = ["bash", "-c", snippet]
            fixture = self.root / "bootstrap-fixture.sh"
            fixture.write_text('echo complete > "' + str(self.root / "executed") + '"\n')
            self.env["DOWNLOAD_FIXTURE"] = str(fixture)
        else:
            command = ["bash", str(ROOT / "install.sh")]
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertFalse((self.root / "unsafe").exists(), "partial script was executed")
        self.assertEqual(self.config.read_bytes(), b"untouched config\n")
        self.assertEqual(self.config.stat().st_ino, self.config_stat.st_ino)
        self.assertEqual(self.config.stat().st_mtime_ns, self.config_stat.st_mtime_ns)
        self.assertFalse(list(self.root.glob("easy-image-api-installer.*")))
        return result, self.log.read_text().splitlines()

    def test_direct_success_does_not_contact_proxy(self):
        result, urls = self.run_script("direct")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(urls, [ARCHIVE_URL])
        self.assertEqual((self.destination / "SKILL.md").read_text(), "updated runtime\n")

    def test_network_failure_retries_proxy_and_preserves_config(self):
        result, urls = self.run_script("fallback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(urls, [ARCHIVE_URL, PROXY + ARCHIVE_URL])
        self.assertIn("ghfast", result.stderr)
        self.assertEqual((self.destination / "SKILL.md").read_text(), "updated runtime\n")

    def test_non_zip_response_retries_proxy(self):
        result, urls = self.run_script("html")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(urls, [ARCHIVE_URL, PROXY + ARCHIVE_URL])

    def test_both_fail_leave_installation_untouched(self):
        result, urls = self.run_script("both_fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(urls, [ARCHIVE_URL, PROXY + ARCHIVE_URL])
        self.assertEqual((self.destination / "SKILL.md").read_text(), "old skill\n")

    def test_bootstrap_direct_success(self):
        result, urls = self.run_script("direct", bootstrap=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(urls, [SCRIPT_URL])
        self.assertTrue((self.root / "executed").exists())

    def test_bootstrap_does_not_execute_partial_download(self):
        result, urls = self.run_script("fallback", bootstrap=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(urls, [SCRIPT_URL, PROXY + SCRIPT_URL])
        self.assertTrue((self.root / "executed").exists())

    def test_bootstrap_both_fail_executes_nothing(self):
        result, urls = self.run_script("both_fail", bootstrap=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(urls, [SCRIPT_URL, PROXY + SCRIPT_URL])
        self.assertFalse((self.root / "executed").exists())


if __name__ == "__main__":
    unittest.main()
