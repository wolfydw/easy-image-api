from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_skill.py"
SPEC = importlib.util.spec_from_file_location("easy_image_api_install", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def _write_source(directory: Path, marker: str = "first") -> Path:
    source = directory / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "SKILL.md").write_text(f"skill {marker}\n", encoding="utf-8")
    (source / "scripts" / "generate_image.py").write_text(
        f"# generator {marker}\n", encoding="utf-8"
    )
    (source / "scripts" / "ignored.pyc").write_bytes(b"ignored")
    (source / "assets" / "asset.txt").write_text(marker, encoding="utf-8")
    (source / "config.json").write_text(
        '{"endpoint":"placeholder","api_key":"placeholder"}\n',
        encoding="utf-8",
    )
    return source


class InstallTests(unittest.TestCase):
    def test_fresh_install_writes_fixed_endpoint_and_escaped_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root)
            destination = root / "codex" / "skills" / "easy-image-api"
            api_key = '密钥-$&-"quotes"-and-\\slashes\nnext-line'

            result = INSTALLER.install(source, destination, lambda _prompt: api_key)

            self.assertEqual(result, "installed")
            config = json.loads((destination / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["endpoint"], "https://cf.ydw.cool")
            self.assertEqual(config["api_key"], api_key)
            self.assertEqual(config["model"], "gpt-image-2.5")
            self.assertEqual(
                (destination / "scripts" / "generate_image.py").read_text(
                    encoding="utf-8"
                ),
                "# generator first\n",
            )
            self.assertFalse((destination / "scripts" / "ignored.pyc").exists())
            if os.name == "posix":
                mode = stat.S_IMODE((destination / "config.json").stat().st_mode)
                self.assertEqual(mode, 0o600)

    def test_upgrade_preserves_config_without_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root, marker="updated")
            destination = root / "destination"
            (destination / "scripts").mkdir(parents=True)
            config_bytes = b"existing config bytes that must remain untouched\n"
            (destination / "config.json").write_bytes(config_bytes)
            (destination / "SKILL.md").write_text("old\n", encoding="utf-8")
            config_stat = (destination / "config.json").stat()

            def unexpected_prompt(_prompt: str) -> str:
                self.fail("upgrade must not request an API key")

            result = INSTALLER.install(source, destination, unexpected_prompt)

            self.assertEqual(result, "upgraded")
            self.assertEqual((destination / "config.json").read_bytes(), config_bytes)
            updated_config_stat = (destination / "config.json").stat()
            self.assertEqual(updated_config_stat.st_ino, config_stat.st_ino)
            self.assertEqual(updated_config_stat.st_mtime_ns, config_stat.st_mtime_ns)
            self.assertEqual(
                (destination / "SKILL.md").read_text(encoding="utf-8"),
                "skill updated\n",
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unsupported")
    def test_upgrade_preserves_broken_config_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root, marker="updated")
            destination = root / "destination"
            destination.mkdir()
            config_path = destination / "config.json"
            config_path.symlink_to(destination / "missing-config-target")

            def unexpected_prompt(_prompt: str) -> str:
                self.fail("an existing config entry must suppress the API key prompt")

            result = INSTALLER.install(source, destination, unexpected_prompt)

            self.assertEqual(result, "upgraded")
            self.assertTrue(config_path.is_symlink())
            self.assertFalse(config_path.exists())

    def test_failed_upgrade_rolls_back_runtime_and_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root, marker="updated")
            destination = root / "destination"
            (destination / "scripts").mkdir(parents=True)
            config_bytes = b"do not touch this config"
            (destination / "config.json").write_bytes(config_bytes)
            (destination / "SKILL.md").write_text("old skill\n", encoding="utf-8")
            (destination / "scripts" / "generate_image.py").write_text(
                "# old generator\n", encoding="utf-8"
            )
            original_replace = INSTALLER.os.replace
            failed = False

            def flaky_replace(source_path: object, destination_path: object) -> None:
                nonlocal failed
                source_text = os.fspath(source_path).replace("\\", "/")
                if not failed and "/new/scripts/generate_image.py" in source_text:
                    failed = True
                    raise OSError("simulated replacement failure")
                original_replace(source_path, destination_path)

            with mock.patch.object(INSTALLER.os, "replace", side_effect=flaky_replace):
                with self.assertRaises(OSError):
                    INSTALLER.install(source, destination)

            self.assertEqual((destination / "config.json").read_bytes(), config_bytes)
            self.assertEqual(
                (destination / "SKILL.md").read_text(encoding="utf-8"),
                "old skill\n",
            )
            self.assertEqual(
                (destination / "scripts" / "generate_image.py").read_text(
                    encoding="utf-8"
                ),
                "# old generator\n",
            )
            self.assertFalse((destination / "assets" / "asset.txt").exists())

    def test_incomplete_install_prompts_and_creates_only_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root)
            destination = root / "destination"
            destination.mkdir()

            result = INSTALLER.install(source, destination, lambda _prompt: "new-key")

            self.assertEqual(result, "installed")
            config = json.loads((destination / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["api_key"], "new-key")

    def test_config_created_during_install_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root)
            destination = root / "destination"
            destination.mkdir()
            config_path = destination / "config.json"
            concurrent_config = b"config created by another process"
            update_runtime = INSTALLER._update_runtime_transactionally

            def update_then_create_config(
                managed_files: list[tuple[Path, Path]], install_destination: Path
            ) -> None:
                update_runtime(managed_files, install_destination)
                config_path.write_bytes(concurrent_config)

            with mock.patch.object(
                INSTALLER,
                "_update_runtime_transactionally",
                side_effect=update_then_create_config,
            ):
                result = INSTALLER.install(
                    source, destination, lambda _prompt: "discarded-key"
                )

            self.assertEqual(result, "upgraded")
            self.assertEqual(config_path.read_bytes(), concurrent_config)

    def test_cancelled_key_prompt_leaves_no_partial_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root)
            destination = root / "destination"

            def cancelled_prompt(_prompt: str) -> str:
                raise EOFError

            with self.assertRaises(INSTALLER.InstallError):
                INSTALLER.install(source, destination, cancelled_prompt)

            self.assertFalse(destination.exists())

    def test_invalid_source_does_not_prompt_or_create_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "invalid-source"
            source.mkdir()
            (source / "SKILL.md").write_text("incomplete", encoding="utf-8")
            destination = root / "destination"

            def unexpected_prompt(_prompt: str) -> str:
                self.fail("invalid source must be rejected before prompting")

            with self.assertRaises(INSTALLER.InstallError):
                INSTALLER.install(source, destination, unexpected_prompt)
            self.assertFalse(destination.exists())

    def test_default_destination_respects_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ, {"CODEX_HOME": temporary_directory}, clear=False
            ):
                self.assertEqual(
                    INSTALLER._default_destination(),
                    Path(temporary_directory) / "skills" / "easy-image-api",
                )

    def test_installation_lock_rejects_a_concurrent_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "easy-image-api"
            with INSTALLER._installation_lock(destination):
                with self.assertRaises(INSTALLER.InstallError):
                    with INSTALLER._installation_lock(destination):
                        self.fail("a second installer must not acquire the lock")

    def test_rejects_parent_traversal_in_archive(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("repository-main/SKILL.md", "skill")
            archive.writestr("repository-main/../outside.txt", "unsafe")

        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(INSTALLER.InstallError):
                INSTALLER._extract_archive(payload.getvalue(), Path(temporary_directory))

    def test_rejects_windows_style_traversal_in_archive(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("repository-main/SKILL.md", "skill")
            archive.writestr("repository-main\\..\\outside.txt", "unsafe")

        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(INSTALLER.InstallError):
                INSTALLER._extract_archive(payload.getvalue(), Path(temporary_directory))

    def test_rejects_case_insensitive_duplicate_archive_paths(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("repository-main/SKILL.md", "first")
            archive.writestr("repository-main/skill.md", "second")

        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(INSTALLER.InstallError):
                INSTALLER._extract_archive(payload.getvalue(), Path(temporary_directory))

    def test_cli_upgrade_works_without_interactive_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root)
            destination = root / "destination"
            destination.mkdir()
            (destination / "config.json").write_text("preserve me", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source",
                    str(source),
                    "--destination",
                    str(destination),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("已原样保留 config.json", completed.stdout)
            self.assertEqual(
                (destination / "config.json").read_text(encoding="utf-8"),
                "preserve me",
            )


if __name__ == "__main__":
    unittest.main()
