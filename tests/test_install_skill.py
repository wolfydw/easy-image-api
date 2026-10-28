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
from contextlib import redirect_stderr, redirect_stdout


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
    (source / "scripts" / "run_generate_image.sh").write_text(
        "#!/usr/bin/env sh\n", encoding="utf-8"
    )
    (source / "scripts" / "run_generate_image.ps1").write_text(
        "# launcher\n", encoding="utf-8"
    )
    (source / "scripts" / "ignored.pyc").write_bytes(b"ignored")
    (source / "assets" / "asset.txt").write_text(marker, encoding="utf-8")
    (source / "config.json").write_text(
        '{"endpoint":"placeholder","api_key":"placeholder"}\n',
        encoding="utf-8",
    )
    return source


class InstallTests(unittest.TestCase):
    @staticmethod
    def _model_options():
        return (lambda _api_key, _endpoint: []), (lambda _prompt: "")

    def test_default_api_key_prompt_uses_visible_input(self) -> None:
        with mock.patch("builtins.input", return_value="visible-key") as prompt:
            api_key = INSTALLER._prompt_api_key()

        self.assertEqual(api_key, "visible-key")
        prompt.assert_called_once_with("请输入生图 API Key：")

    def test_fetch_models_uses_models_endpoint_and_bearer_header(self) -> None:
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size=-1):
                return b'{"data":[{"id":"gpt-image-2"},{"id":"other"}]}'

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

        models = INSTALLER._fetch_models(
            "secret-key", "https://relay.example.com/v1/images/generations", opener
        )

        self.assertEqual(models, ["gpt-image-2", "other"])
        self.assertEqual(captured["request"].full_url, "https://relay.example.com/v1/models")
        self.assertEqual(captured["request"].get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(captured["timeout"], INSTALLER.MODEL_REQUEST_TIMEOUT)

    def test_select_model_displays_models_and_returns_choice(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            selected = INSTALLER._select_model(
                "secret-key",
                model_reader=lambda _prompt: "2",
                model_fetcher=lambda _key, _endpoint: ["first", "second"],
            )

        self.assertEqual(selected, "second")
        self.assertIn("1. first", output.getvalue())
        self.assertIn("2. second", output.getvalue())

    def test_select_model_uses_default_model_when_entered(self) -> None:
        selected = INSTALLER._select_model(
            "secret-key",
            model_reader=lambda _prompt: "",
            model_fetcher=lambda _key, _endpoint: ["other", "gpt-image-2"],
        )

        self.assertEqual(selected, "gpt-image-2")

    def test_select_model_uses_current_model_when_upgrading(self) -> None:
        selected = INSTALLER._select_model(
            "secret-key",
            default_model="current-model",
            model_reader=lambda _prompt: "",
            model_fetcher=lambda _key, _endpoint: ["other-model"],
        )

        self.assertEqual(selected, "current-model")

    def test_failed_model_fetch_allows_manual_model(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            selected = INSTALLER._select_model(
                "secret-key",
                model_reader=lambda _prompt: "manual-model",
                model_fetcher=lambda _key, _endpoint: (_ for _ in ()).throw(
                    INSTALLER.InstallError("service unavailable")
                ),
            )

        self.assertEqual(selected, "manual-model")
        self.assertIn("手动输入模型名", errors.getvalue())
        self.assertNotIn("secret-key", errors.getvalue())

    def test_fresh_install_writes_fixed_endpoint_and_escaped_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root)
            destination = root / "codex" / "skills" / "easy-image-api"
            api_key = '密钥-$&-"quotes"-and-\\slashes\nnext-line'

            model_fetcher, model_reader = self._model_options()
            result = INSTALLER.install(
                source,
                destination,
                lambda _prompt: api_key,
                model_reader,
                model_fetcher,
            )

            self.assertEqual(result, "installed")
            config = json.loads((destination / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["endpoint"], "https://cf.ydw.cool")
            self.assertEqual(config["api_key"], api_key)
            self.assertEqual(config["model"], "gpt-image-2")
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

    def test_fresh_install_writes_selected_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root)
            destination = root / "destination"
            result = INSTALLER.install(
                source,
                destination,
                lambda _prompt: "key",
                lambda _prompt: "1",
                lambda _key, _endpoint: ["selected-model"],
            )

            self.assertEqual(result, "installed")
            config = json.loads((destination / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["model"], "selected-model")

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

    def test_upgrade_selects_model_with_current_model_as_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _write_source(root, marker="updated")
            destination = root / "destination"
            destination.mkdir()
            config_path = destination / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "endpoint": "https://relay.example.com",
                        "api_key": "existing-key",
                        "model": "current-model",
                        "size": "1024x1024",
                        "quality": "high",
                        "output_format": "webp",
                        "custom": {"keep": True},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            config_stat = config_path.stat()

            def fetch_existing_models(key: str, endpoint: str) -> list[str]:
                self.assertEqual(key, "existing-key")
                self.assertEqual(endpoint, "https://relay.example.com")
                return ["current-model", "new-model"]

            result = INSTALLER.install(
                source,
                destination,
                lambda _prompt: self.fail("upgrade must not request an API key"),
                lambda _prompt: "2",
                fetch_existing_models,
            )

            self.assertEqual(result, "upgraded")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["model"], "new-model")
            self.assertEqual(config["api_key"], "existing-key")
            self.assertEqual(config["size"], "1024x1024")
            self.assertEqual(config["custom"], {"keep": True})
            self.assertNotEqual(config_path.stat().st_mtime_ns, config_stat.st_mtime_ns)

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

            model_fetcher, model_reader = self._model_options()
            result = INSTALLER.install(
                source,
                destination,
                lambda _prompt: "new-key",
                model_reader,
                model_fetcher,
            )

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
                    source,
                    destination,
                    lambda _prompt: "discarded-key",
                    lambda _prompt: "",
                    lambda _api_key, _endpoint: [],
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
            self.assertIn("保留其他配置字段", completed.stdout)
            self.assertEqual(
                (destination / "config.json").read_text(encoding="utf-8"),
                "preserve me",
            )


if __name__ == "__main__":
    unittest.main()
