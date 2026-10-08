from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path
from unittest import TestCase, mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_skill.py"
SPEC = importlib.util.spec_from_file_location("easy_image_api_install_download", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class _Response:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return self._payload


class DownloadTests(TestCase):
    def test_direct_download_uses_bounded_timeout(self) -> None:
        with mock.patch.object(
            INSTALLER.urllib.request,
            "urlopen",
            return_value=_Response(b"archive"),
        ) as urlopen:
            self.assertEqual(INSTALLER._download_archive(), b"archive")

        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, INSTALLER.REPOSITORY_ARCHIVE_URL)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 15)

    def test_github_network_failure_falls_back_to_ghfast_once(self) -> None:
        with mock.patch.object(
            INSTALLER.urllib.request,
            "urlopen",
            side_effect=[OSError("network unreachable"), _Response(b"archive")],
        ) as urlopen, mock.patch.object(INSTALLER.sys, "stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(INSTALLER._download_archive(), b"archive")
            self.assertIn("加速源 ghfast.top", stderr.getvalue())

        self.assertEqual(urlopen.call_count, 2)
        first_request = urlopen.call_args_list[0].args[0]
        second_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(first_request.full_url, INSTALLER.REPOSITORY_ARCHIVE_URL)
        self.assertEqual(
            second_request.full_url,
            f"{INSTALLER.GHFAST_PREFIX}{INSTALLER.REPOSITORY_ARCHIVE_URL}",
        )

    def test_timeout_and_http_error_each_allow_fallback(self) -> None:
        errors = [
            TimeoutError("timed out"),
            urllib.error.HTTPError(
                INSTALLER.REPOSITORY_ARCHIVE_URL, 403, "forbidden", {}, io.BytesIO()
            ),
            urllib.error.URLError("DNS unavailable"),
        ]
        for error in errors:
            if isinstance(error, urllib.error.HTTPError):
                self.addCleanup(error.close)
            with self.subTest(error=type(error).__name__), mock.patch.object(
                INSTALLER.urllib.request,
                "urlopen",
                side_effect=[error, _Response(b"archive")],
            ) as urlopen, mock.patch.object(INSTALLER.sys, "stderr", new_callable=io.StringIO):
                self.assertEqual(INSTALLER._download_archive(), b"archive")
                self.assertEqual(urlopen.call_count, 2)

    def test_oversized_archive_does_not_trigger_fallback(self) -> None:
        responses = [
            _Response(b"", {"Content-Length": "6"}),
            _Response(b"123456"),
        ]
        for response in responses:
            with self.subTest(headers=response.headers), mock.patch.object(
                INSTALLER, "MAX_ARCHIVE_BYTES", 5
            ), mock.patch.object(
                INSTALLER.urllib.request, "urlopen", return_value=response
            ) as urlopen:
                with self.assertRaisesRegex(INSTALLER.InstallError, "安装包过大"):
                    INSTALLER._download_archive()
                urlopen.assert_called_once()

    def test_custom_url_failure_does_not_use_proxy(self) -> None:
        custom_url = "https://example.invalid/archive.zip"
        with mock.patch.object(
            INSTALLER.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ) as urlopen:
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER._download_archive(custom_url)

        urlopen.assert_called_once()
        self.assertIn("无法下载 skill", str(raised.exception))

    def test_failed_direct_and_accelerated_downloads_report_both_errors(self) -> None:
        direct_error = urllib.error.HTTPError(
            INSTALLER.REPOSITORY_ARCHIVE_URL, 503, "unavailable", {}, io.BytesIO()
        )
        self.addCleanup(direct_error.close)
        with mock.patch.object(
            INSTALLER.urllib.request,
            "urlopen",
            side_effect=[direct_error, TimeoutError("timed out")],
        ) as urlopen, mock.patch.object(INSTALLER.sys, "stderr", new_callable=io.StringIO):
            with self.assertRaises(INSTALLER.InstallError) as raised:
                INSTALLER._download_archive()

        message = str(raised.exception)
        self.assertIn("GitHub 直连失败", message)
        self.assertIn("加速源也失败", message)
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    import unittest

    unittest.main()
