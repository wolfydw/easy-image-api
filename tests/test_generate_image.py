from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import zlib
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_image.py"
SPEC = importlib.util.spec_from_file_location("easy_image_api_generate", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
GENERATE_IMAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATE_IMAGE)

PNG_BYTES = b"\x89PNG\r\n\x1a\nmock-png"
JPEG_BYTES = b"\xff\xd8\xffmock-jpeg"


def _png_bytes(
    width: int = 1,
    height: int = 1,
    *,
    alpha: int = 0,
    color_type: int = 6,
) -> bytes:
    """Build a valid RGBA PNG without depending on an image library."""

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)

    if color_type == 6:
        pixel = bytes((0, 128, 255, alpha))
    elif color_type == 2:
        pixel = bytes((0, 128, 255))
    else:
        raise ValueError("Unsupported test PNG color type")
    scanlines = b"".join(b"\x00" + pixel * width for _ in range(height))
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0),
            ),
            chunk(b"IDAT", zlib.compress(scanlines)),
            chunk(b"IEND", b""),
        )
    )


VALID_PNG_BYTES = _png_bytes()


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _CliFixture:
    def __init__(self, testcase: unittest.TestCase, directory: Path) -> None:
        self.testcase = testcase
        self.directory = directory
        self.skill_dir = directory / "skill"
        self.config_path = self.skill_dir / "config.json"
        self.response = {
            "data": [{"b64_json": base64.b64encode(VALID_PNG_BYTES).decode("ascii")}]
        }

    def write_config(self, **overrides: object) -> Path:
        config: dict[str, object] = {
            "endpoint": "https://relay.example.com",
            "api_key": "test-api-key-that-must-not-leak",
            "model": "gpt-image-2.5",
            "size": "auto",
            "quality": "high",
            "output_format": "png",
        }
        config.update(overrides)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        return self.config_path

    def invoke(
        self,
        *arguments: str,
        urlopen: object | None = None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        patches = [
            mock.patch.object(sys, "argv", [str(SCRIPT), *arguments]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            mock.patch.object(GENERATE_IMAGE, "SKILL_DIR", self.skill_dir),
            mock.patch.object(
                urllib.request,
                "urlopen",
                side_effect=urlopen or AssertionError("Unexpected network request"),
            ),
        ]

        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            try:
                result = GENERATE_IMAGE.main()
            except SystemExit as exc:
                result = int(exc.code or 0)
        return result, stdout.getvalue(), stderr.getvalue()

    def successful_urlopen(self, captured: list[urllib.request.Request]):
        def open_request(
            request: urllib.request.Request, *, timeout: float
        ) -> _FakeHTTPResponse:
            self.testcase.assertGreater(timeout, 0)
            captured.append(request)
            return _FakeHTTPResponse(self.response)

        return open_request


class ConfigDiscoveryTests(unittest.TestCase):
    def test_loads_only_skill_directory_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            environment_config = directory / "environment.json"
            environment_config.write_text(
                json.dumps({"endpoint": "https://environment.example.com"}),
                encoding="utf-8",
            )
            config_path = fixture.write_config(
                endpoint="https://user.example.com"
            )
            with mock.patch.dict(os.environ, {"EASY_IMAGE_API_CONFIG": str(environment_config)}):
                code, stdout, stderr = fixture.invoke(
                    "--prompt",
                    "config discovery test",
                    "--out",
                    str(directory / "result.png"),
                    "--dry-run",
                )

            self.assertEqual(code, 0, stderr)
            details = json.loads(stdout)
            self.assertEqual(details["config"], str(config_path.resolve()))
            self.assertEqual(details["endpoint"], "https://user.example.com")

    def test_missing_config_reports_only_fixed_skill_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            code, stdout, stderr = fixture.invoke(
                "--prompt",
                "config discovery test",
                "--out",
                str(directory / "result.png"),
                "--dry-run",
            )

            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertIn(str(fixture.config_path.resolve()), stderr)

    def test_resolves_config_relative_to_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            skill_config = directory / "skill" / "config.json"
            skill_config.parent.mkdir(parents=True)
            skill_config.write_text(
                json.dumps({"endpoint": "https://skill.example.com"}),
                encoding="utf-8",
            )
            self.assertEqual(
                GENERATE_IMAGE._resolve_config_path(skill_config.parent),
                skill_config.resolve(),
            )

    def test_config_override_argument_is_not_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            code, stdout, stderr = fixture.invoke(
                "--config",
                str(directory / "other.json"),
                "--prompt",
                "config discovery test",
                "--out",
                str(directory / "result.png"),
                "--dry-run",
            )

            self.assertNotEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertIn("unrecognized arguments: --config", stderr)

class EndpointResolutionTests(unittest.TestCase):
    def test_resolves_base_and_complete_endpoints(self) -> None:
        cases = {
            "https://relay.example.com":
                "https://relay.example.com/v1/images/generations",
            "https://relay.example.com/":
                "https://relay.example.com/v1/images/generations",
            "https://relay.example.com/v1":
                "https://relay.example.com/v1/images/generations",
            "https://relay.example.com/v1/":
                "https://relay.example.com/v1/images/generations",
            "https://relay.example.com/images/generations":
                "https://relay.example.com/images/generations",
            "https://relay.example.com/v1/images/generations/":
                "https://relay.example.com/v1/images/generations",
            "https://relay.example.com/custom":
                "https://relay.example.com/custom/v1/images/generations",
            "https://relay.example.com/custom/v1":
                "https://relay.example.com/custom/v1/images/generations",
            "https://relay.example.com/custom/images/generations":
                "https://relay.example.com/custom/images/generations",
        }
        for endpoint, expected in cases.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    GENERATE_IMAGE._resolve_generation_url(endpoint), expected
                )

    def test_rejects_invalid_endpoint_components(self) -> None:
        for endpoint in (
            "relay.example.com",
            "https://relay.example.com?token=value",
            "https://relay.example.com#fragment",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(GENERATE_IMAGE.ImageGenError):
                    GENERATE_IMAGE._resolve_generation_url(endpoint)


class EditEndpointResolutionTests(unittest.TestCase):
    def test_edit_mode_resolves_base_and_generation_endpoints(self) -> None:
        cases = {
            "https://relay.example.com": "https://relay.example.com/v1/images/edits",
            "https://relay.example.com/v1": "https://relay.example.com/v1/images/edits",
            "https://relay.example.com/images/generations":
                "https://relay.example.com/images/edits",
            "https://relay.example.com/v1/images/generations/":
                "https://relay.example.com/v1/images/edits",
            "https://relay.example.com/custom":
                "https://relay.example.com/custom/v1/images/edits",
            "https://relay.example.com/custom/v1":
                "https://relay.example.com/custom/v1/images/edits",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            image_path = directory / "source.png"
            image_path.write_bytes(VALID_PNG_BYTES)
            fixture = _CliFixture(self, directory)
            for index, (endpoint, expected) in enumerate(cases.items()):
                with self.subTest(endpoint=endpoint):
                    fixture.write_config(endpoint=endpoint)
                    code, stdout, stderr = fixture.invoke(
                        "--prompt", "edit test",
                        "--image", str(image_path),
                        "--out", str(directory / f"result-{index}.png"),
                        "--dry-run",
                    )
                    self.assertEqual(code, 0, stderr)
                    self.assertEqual(json.loads(stdout)["request_url"], expected)

    def test_edit_endpoint_config_overrides_derived_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            image_path = directory / "source.png"
            image_path.write_bytes(VALID_PNG_BYTES)
            fixture = _CliFixture(self, directory)
            fixture.write_config(
                edit_endpoint="https://edits.example.com/custom/images/edits"
            )
            code, stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--out", str(directory / "result.png"),
                "--dry-run",
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(
                json.loads(stdout)["request_url"],
                "https://edits.example.com/custom/images/edits",
            )


class RequestEncodingTests(unittest.TestCase):
    def test_generation_mode_keeps_json_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            captured: list[urllib.request.Request] = []
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "generate test",
                "--out", str(directory / "result.png"),
                urlopen=fixture.successful_urlopen(captured),
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(len(captured), 1)
            request = captured[0]
            self.assertEqual(request.full_url, "https://relay.example.com/v1/images/generations")
            self.assertEqual(request.headers["Content-type"], "application/json")
            self.assertEqual(json.loads(request.data or b"{}"), {
                "model": "gpt-image-2.5",
                "prompt": "generate test",
                "n": 1,
                "size": "auto",
                "quality": "high",
                "output_format": "png",
            })

    def test_edit_mode_sends_images_and_mask_as_multipart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            first_image = directory / "first.png"
            second_image = directory / "second.png"
            mask_path = directory / "mask.png"
            first_bytes = _png_bytes(1, 1)
            second_bytes = _png_bytes(2, 1)
            mask_bytes = _png_bytes(1, 1)
            first_image.write_bytes(first_bytes)
            second_image.write_bytes(second_bytes)
            mask_path.write_bytes(mask_bytes)
            captured: list[urllib.request.Request] = []

            code, _stdout, stderr = fixture.invoke(
                "--prompt", "replace only the masked region",
                "--image", str(first_image),
                "--image", str(second_image),
                "--mask", str(mask_path),
                "--out", str(directory / "result.png"),
                urlopen=fixture.successful_urlopen(captured),
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(len(captured), 1)
            request = captured[0]
            self.assertEqual(request.full_url, "https://relay.example.com/v1/images/edits")
            content_type = request.headers["Content-type"]
            self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
            body = request.data or b""
            self.assertEqual(body.count(b'name="image[]"'), 2)
            self.assertIn(b'name="mask"', body)
            self.assertIn(b'filename="first.png"', body)
            self.assertIn(b'filename="second.png"', body)
            self.assertIn(b'filename="mask.png"', body)
            self.assertLess(body.index(first_bytes), body.index(second_bytes))
            for name, value in (
                (b"model", b"gpt-image-2.5"),
                (b"prompt", b"replace only the masked region"),
                (b"n", b"1"),
                (b"size", b"auto"),
                (b"quality", b"high"),
                (b"output_format", b"png"),
            ):
                self.assertIn(b'name="' + name + b'"', body)
                self.assertIn(b"\r\n\r\n" + value + b"\r\n", body)

    def test_edit_image_field_can_be_configured_without_brackets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config(edit_image_field="image")
            image_path = directory / "source.png"
            image_path.write_bytes(VALID_PNG_BYTES)
            captured: list[urllib.request.Request] = []
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--out", str(directory / "result.png"),
                urlopen=fixture.successful_urlopen(captured),
            )
            self.assertEqual(code, 0, stderr)
            body = captured[0].data or b""
            self.assertIn(b'name="image"', body)
            self.assertNotIn(b'name="image[]"', body)


class EditCliValidationTests(unittest.TestCase):
    def test_mask_requires_an_input_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            mask_path = directory / "mask.png"
            mask_path.write_bytes(VALID_PNG_BYTES)
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--mask", str(mask_path),
                "--out", str(directory / "result.png"),
                "--dry-run",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("--mask", stderr)
            self.assertIn("--image", stderr)

    def test_force_cannot_overwrite_an_input_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            image_path = directory / "source.png"
            image_path.write_bytes(VALID_PNG_BYTES)
            original = image_path.read_bytes()
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--out", str(image_path),
                "--force",
                "--dry-run",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("input", stderr.lower())
            self.assertEqual(image_path.read_bytes(), original)

    def test_force_cannot_overwrite_the_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            image_path = directory / "source.png"
            mask_path = directory / "mask.png"
            image_path.write_bytes(VALID_PNG_BYTES)
            mask_path.write_bytes(VALID_PNG_BYTES)
            original = mask_path.read_bytes()
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--mask", str(mask_path),
                "--out", str(mask_path),
                "--force",
                "--dry-run",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("input", stderr.lower())
            self.assertEqual(mask_path.read_bytes(), original)

    def test_dry_run_lists_inputs_without_secrets_or_image_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            image_path = directory / "source.png"
            image_path.write_bytes(VALID_PNG_BYTES)
            code, stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--out", str(directory / "result.png"),
                "--dry-run",
            )
            self.assertEqual(code, 0, stderr)
            details = json.loads(stdout)
            self.assertEqual(details["request_url"], "https://relay.example.com/v1/images/edits")
            self.assertIn(str(image_path.resolve()), stdout)
            self.assertNotIn("test-api-key-that-must-not-leak", stdout)
            self.assertNotIn(base64.b64encode(VALID_PNG_BYTES).decode("ascii"), stdout)

    def test_rejects_mask_without_transparency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            image_path = directory / "source.png"
            mask_path = directory / "mask.png"
            image_path.write_bytes(_png_bytes(alpha=255))
            mask_path.write_bytes(_png_bytes(alpha=255))
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--mask", str(mask_path),
                "--out", str(directory / "result.png"),
                "--dry-run",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("no transparent", stderr.lower())

    def test_rejects_mask_without_alpha_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            image_path = directory / "source.png"
            mask_path = directory / "mask.png"
            image_path.write_bytes(_png_bytes())
            mask_path.write_bytes(_png_bytes(color_type=2))
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--mask", str(mask_path),
                "--out", str(directory / "result.png"),
                "--dry-run",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("alpha channel", stderr.lower())

    def test_rejects_mask_with_different_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            fixture = _CliFixture(self, directory)
            fixture.write_config()
            image_path = directory / "source.png"
            mask_path = directory / "mask.png"
            image_path.write_bytes(_png_bytes(2, 2))
            mask_path.write_bytes(_png_bytes(1, 2))
            code, _stdout, stderr = fixture.invoke(
                "--prompt", "edit test",
                "--image", str(image_path),
                "--mask", str(mask_path),
                "--out", str(directory / "result.png"),
                "--dry-run",
            )
            self.assertNotEqual(code, 0)
            self.assertIn("dimensions", stderr.lower())


class OutputPathTests(unittest.TestCase):
    def test_default_output_uses_skill_named_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            with mock.patch.object(Path, "cwd", return_value=directory):
                output = GENERATE_IMAGE._default_output("png")

            self.assertEqual(
                output.parent,
                directory / "outputs" / "easy-image-api",
            )
            self.assertEqual(output.suffix, ".png")

    def test_normalizes_extension_to_requested_format(self) -> None:
        cases = (
            ("cat", "png", "cat.png"),
            ("cat.jpg", "png", "cat.png"),
            ("cat.png", "jpeg", "cat.jpg"),
            ("cat.JPG", "jpeg", "cat.JPG"),
            ("cat.jpeg", "jpeg", "cat.jpeg"),
            ("cat.png", "webp", "cat.webp"),
        )
        for path, output_format, expected in cases:
            with self.subTest(path=path, output_format=output_format):
                self.assertEqual(
                    GENERATE_IMAGE._normalize_output(path, output_format),
                    Path(expected),
                )

    def test_adds_variant_suffixes(self) -> None:
        self.assertEqual(
            GENERATE_IMAGE._output_paths(Path("cat.png"), 1),
            [Path("cat.png")],
        )
        self.assertEqual(
            GENERATE_IMAGE._output_paths(Path("cat.png"), 3),
            [Path("cat-1.png"), Path("cat-2.png"), Path("cat-3.png")],
        )


class FormatValidationTests(unittest.TestCase):
    def _run_with_mock(
        self, directory: Path, images: list[bytes], *, count: int = 1
    ) -> subprocess.CompletedProcess[str]:
        skill_dir = directory / "skill"
        script_path = skill_dir / "scripts" / "generate_image.py"
        config_path = skill_dir / "config.json"
        response_path = directory / "response.json"
        output_path = directory / "result.png"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SCRIPT, script_path)
        config_path.write_text(
            json.dumps(
                {
                    "endpoint": "https://relay.example.com",
                    "model": "gpt-image-2.5",
                    "size": "auto",
                    "quality": "high",
                    "output_format": "png",
                }
            ),
            encoding="utf-8",
        )
        response_path.write_text(
            json.dumps(
                {
                    "data": [
                        {"b64_json": base64.b64encode(image).decode("ascii")}
                        for image in images
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--prompt",
                "test image",
                "--mock-response",
                str(response_path),
                "--out",
                str(output_path),
                "--n",
                str(count),
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "HOME": str(directory / "home")},
        )

    def test_writes_image_when_response_format_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            result = self._run_with_mock(directory, [PNG_BYTES])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((directory / "result.png").read_bytes(), PNG_BYTES)

    def test_rejects_mismatched_response_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            result = self._run_with_mock(directory, [JPEG_BYTES])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No files were written", result.stderr)
            self.assertFalse((directory / "result.png").exists())

    def test_batch_validation_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            result = self._run_with_mock(
                directory, [PNG_BYTES, JPEG_BYTES], count=2
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "result-1.png").exists())
            self.assertFalse((directory / "result-2.png").exists())


if __name__ == "__main__":
    unittest.main()
