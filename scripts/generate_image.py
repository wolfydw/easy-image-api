#!/usr/bin/env python3
"""Generate or edit images through a configured OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import socket
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union


SKILL_NAME = "easy-image-api"
SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "high"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_EDIT_IMAGE_FIELD = "image[]"
MAX_INPUT_IMAGES = 16
MAX_MASK_EDIT_FILE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_INPUT_PAYLOAD_BYTES = 512 * 1024 * 1024
USER_AGENT = "easy-image-api/2.0"

MIME_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


class ImageGenError(RuntimeError):
    pass


def _die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _resolve_config_path(skill_dir: Optional[Path] = None) -> Path:
    config_path = (SKILL_DIR if skill_dir is None else skill_dir) / "config.json"
    if config_path.is_file():
        return config_path.resolve()

    raise ImageGenError(
        f"No configuration file found at {config_path.resolve()}."
    )


def _load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ImageGenError(f"Could not read configuration file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ImageGenError(
            f"Configuration file {path} is not valid JSON: line {exc.lineno}, column {exc.colno}."
        ) from exc
    if not isinstance(value, dict):
        raise ImageGenError("The configuration root must be a JSON object.")
    return value


def _config_text(config: dict[str, Any], key: str, default: str = "") -> str:
    value = config.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ImageGenError(f"Configuration value '{key}' must be a string.")
    return value.strip()


def _parse_endpoint(value: str) -> urllib.parse.ParseResult:
    endpoint = value.strip()
    if not endpoint:
        raise ImageGenError(
            "The image endpoint is not configured. Set 'endpoint' in config.json."
        )
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageGenError("The image endpoint must be a complete http(s) URL.")
    if parsed.params or parsed.query or parsed.fragment:
        raise ImageGenError(
            "The image endpoint must not contain URL parameters, a query, or a fragment."
        )
    return parsed


def _resolve_operation_url(value: str, operation: str) -> str:
    if operation not in {"generations", "edits"}:
        raise ValueError(f"Unsupported image operation: {operation}")

    parsed = _parse_endpoint(value)
    path = parsed.path.rstrip("/")
    if path.endswith(("/images/generations", "/images/edits")):
        request_path = f"{path.rsplit('/', 1)[0]}/{operation}"
    elif path.endswith("/v1"):
        request_path = f"{path}/images/{operation}"
    else:
        request_path = f"{path}/v1/images/{operation}"

    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, request_path, "", "", "")
    )


def _resolve_generation_url(value: str) -> str:
    return _resolve_operation_url(value, "generations")


def _resolve_edit_url(value: str) -> str:
    return _resolve_operation_url(value, "edits")


def _resolve_complete_url(value: str) -> str:
    parsed = _parse_endpoint(value)
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        prompt = args.prompt
    else:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ImageGenError(f"Could not read prompt file: {exc}") from exc
    prompt = prompt.strip()
    if not prompt:
        raise ImageGenError("The prompt must not be empty.")
    return prompt


def _response_json(raw: bytes) -> dict[str, Any]:
    try:
        result = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImageGenError("The image endpoint did not return valid JSON.") from exc
    if not isinstance(result, dict):
        raise ImageGenError("The image endpoint returned a non-object JSON response.")
    return result


def _send_request(request: urllib.request.Request, timeout: float) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read(2048).decode("utf-8", errors="replace").strip()
        detail = f": {body}" if body else ""
        raise ImageGenError(f"Image endpoint returned HTTP {exc.code}{detail}") from exc
    except urllib.error.URLError as exc:
        raise ImageGenError(f"Could not reach the image endpoint: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ImageGenError("The image request timed out.") from exc
    return _response_json(raw)


def _request_json(
    endpoint: str, payload: dict[str, Any], api_key: str, timeout: float
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    return _send_request(request, timeout)


def _quote_multipart_value(value: str) -> str:
    return (
        value.replace("\\", "_")
        .replace('"', "_")
        .replace("\r", "_")
        .replace("\n", "_")
    )


def _encode_multipart(
    fields: dict[str, str], files: list[dict[str, Any]], boundary: str
) -> bytes:
    body = bytearray()

    def append_line(value: Union[str, bytes] = b"") -> None:
        body.extend(value.encode("utf-8") if isinstance(value, str) else value)
        body.extend(b"\r\n")

    for name, value in fields.items():
        append_line(f"--{boundary}")
        append_line(
            f'Content-Disposition: form-data; name="{_quote_multipart_value(name)}"'
        )
        append_line()
        append_line(value)

    for file in files:
        append_line(f"--{boundary}")
        append_line(
            "Content-Disposition: form-data; "
            f'name="{_quote_multipart_value(file["field"])}"; '
            f'filename="{_quote_multipart_value(file["filename"])}"'
        )
        append_line(f'Content-Type: {file["mime"]}')
        append_line()
        append_line(file["data"])

    append_line(f"--{boundary}--")
    return bytes(body)


def _request_multipart(
    endpoint: str,
    fields: dict[str, str],
    files: list[dict[str, Any]],
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    boundary = f"easy-image-api-{uuid.uuid4().hex}"
    data = _encode_multipart(fields, files, boundary)
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    return _send_request(request, timeout)


def _response_items(response: dict[str, Any]) -> list[Any]:
    for key in ("data", "images"):
        value = response.get(key)
        if isinstance(value, list) and value:
            return value
        if isinstance(value, (str, dict)):
            return [value]

    output = response.get("output")
    if isinstance(output, list):
        items: list[Any] = []
        for entry in output:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "image_generation_call" and entry.get("result"):
                items.append(entry["result"])
        if items:
            return items

    for key in ("b64_json", "base64", "image_base64", "url", "image_url", "result"):
        if response.get(key):
            return [response]

    error = response.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("code") or "unknown API error"
        raise ImageGenError(f"Image endpoint error: {message}")
    if isinstance(error, str):
        raise ImageGenError(f"Image endpoint error: {error}")

    keys = ", ".join(sorted(str(key) for key in response.keys())) or "<none>"
    raise ImageGenError(f"No image data found. Top-level response keys: {keys}")


def _decode_base64(value: str) -> bytes:
    encoded = value.strip()
    if encoded.startswith("data:"):
        if "," not in encoded:
            raise ImageGenError("Malformed image data URL.")
        encoded = encoded.split(",", 1)[1]
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageGenError("The endpoint returned invalid base64 image data.") from exc


def _download_image(url: str, timeout: float) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageGenError("The endpoint returned an invalid image URL.")
    request = urllib.request.Request(
        url,
        headers={"Accept": "image/*", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ImageGenError(f"Image download returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ImageGenError(f"Could not download the generated image: {exc.reason}") from exc


def _image_bytes(item: Any, timeout: float) -> bytes:
    if isinstance(item, str):
        if item.startswith(("http://", "https://")):
            return _download_image(item, timeout)
        return _decode_base64(item)
    if not isinstance(item, dict):
        raise ImageGenError("The endpoint returned an unsupported image item.")

    for key in ("b64_json", "base64", "image_base64", "result"):
        value = item.get(key)
        if isinstance(value, str) and value:
            if value.startswith(("http://", "https://")):
                return _download_image(value, timeout)
            return _decode_base64(value)
    for key in ("url", "image_url"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return _download_image(value, timeout)
    raise ImageGenError("The endpoint returned an image item without base64 data or a URL.")


def _image_format(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if _image_format(data) != "png":
        raise ImageGenError("The mask and its target image must be PNG files.")
    chunks: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise ImageGenError("The PNG input is truncated or malformed.")
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ImageGenError("The PNG input contains a chunk with an invalid checksum.")
        chunks.append((chunk_type, chunk_data))
        offset = chunk_end
        if chunk_type == b"IEND":
            break
    if not chunks or chunks[0][0] != b"IHDR":
        raise ImageGenError("The PNG input is missing a valid IHDR chunk.")
    if chunks[-1][0] != b"IEND":
        raise ImageGenError("The PNG input is missing an IEND chunk.")
    return chunks


def _png_info(data: bytes) -> tuple[int, int, int, int, int]:
    ihdr = _png_chunks(data)[0][1]
    if len(ihdr) != 13:
        raise ImageGenError("The PNG input has an invalid IHDR chunk.")
    width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if width <= 0 or height <= 0:
        raise ImageGenError("The PNG input has invalid dimensions.")
    return width, height, bit_depth, color_type, interlace


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _mask_has_edit_area(data: bytes) -> bool:
    chunks = _png_chunks(data)
    ihdr = chunks[0][1]
    width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if color_type not in {4, 6} or bit_depth not in {8, 16}:
        raise ImageGenError(
            "The mask must be an 8-bit or 16-bit PNG with an alpha channel."
        )
    if interlace != 0:
        raise ImageGenError(
            "Interlaced PNG masks are not supported; save the mask without interlacing."
        )

    channels = 2 if color_type == 4 else 4
    bytes_per_sample = bit_depth // 8
    bytes_per_pixel = channels * bytes_per_sample
    row_size = width * bytes_per_pixel
    try:
        raw = zlib.decompress(
            b"".join(value for kind, value in chunks if kind == b"IDAT")
        )
    except zlib.error as exc:
        raise ImageGenError("The PNG mask contains invalid compressed image data.") from exc
    expected_size = height * (row_size + 1)
    if len(raw) != expected_size:
        raise ImageGenError("The PNG mask has an unsupported or malformed pixel layout.")

    previous = bytearray(row_size)
    offset = 0
    alpha_offset = (channels - 1) * bytes_per_sample
    opaque_alpha = b"\xff" * bytes_per_sample
    for _ in range(height):
        filter_type = raw[offset]
        encoded = raw[offset + 1 : offset + 1 + row_size]
        offset += row_size + 1
        decoded = bytearray(row_size)
        for index, value in enumerate(encoded):
            left = decoded[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                raise ImageGenError("The PNG mask uses an invalid row filter.")
            decoded[index] = (value + predictor) & 0xFF

        for pixel in range(width):
            start = pixel * bytes_per_pixel + alpha_offset
            if bytes(decoded[start : start + bytes_per_sample]) != opaque_alpha:
                return True
        previous = decoded
    return False


def _load_input_file(value: str, label: str) -> dict[str, Any]:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise ImageGenError(f"{label} does not exist: {path}")
    if not path.is_file():
        raise ImageGenError(f"{label} is not a file: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ImageGenError(f"Could not read {label.lower()} {path}: {exc}") from exc
    if not data:
        raise ImageGenError(f"{label} is empty: {path}")
    image_format = _image_format(data)
    if image_format is None:
        raise ImageGenError(f"{label} must be a PNG, JPEG, or WebP image: {path}")
    return {
        "path": path,
        "data": data,
        "format": image_format,
        "mime": MIME_TYPES[image_format],
        "bytes": len(data),
    }


def _prepare_edit_inputs(
    image_values: list[str], mask_value: Optional[str], image_field: str
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    if not re.fullmatch(r"[A-Za-z0-9_.\[\]-]+", image_field):
        raise ImageGenError("The edit image field contains unsupported characters.")
    if len(image_values) > MAX_INPUT_IMAGES:
        raise ImageGenError(f"At most {MAX_INPUT_IMAGES} input images are supported.")

    images = [
        _load_input_file(value, f"Input image {index}")
        for index, value in enumerate(image_values, start=1)
    ]
    mask = _load_input_file(mask_value, "Mask") if mask_value else None
    if mask:
        if mask["format"] != "png":
            raise ImageGenError("The mask must be a PNG image with transparency.")
        if images[0]["format"] != "png":
            raise ImageGenError("The first input image must be PNG when a mask is used.")
        if images[0]["bytes"] > MAX_MASK_EDIT_FILE_BYTES:
            raise ImageGenError("The mask target image exceeds the 50 MiB limit.")
        if mask["bytes"] > MAX_MASK_EDIT_FILE_BYTES:
            raise ImageGenError("The mask exceeds the 50 MiB limit.")
        if _png_info(images[0]["data"])[:2] != _png_info(mask["data"])[:2]:
            raise ImageGenError("The mask dimensions must match the first input image.")
        if not _mask_has_edit_area(mask["data"]):
            raise ImageGenError("The mask has no transparent or semi-transparent edit area.")

    total_size = sum(image["bytes"] for image in images) + (mask["bytes"] if mask else 0)
    if total_size > MAX_IMAGE_INPUT_PAYLOAD_BYTES:
        raise ImageGenError("The combined image input exceeds the 512 MiB limit.")
    return images, mask


def _multipart_files(
    images: list[dict[str, Any]], mask: Optional[dict[str, Any]], image_field: str
) -> list[dict[str, Any]]:
    files = [
        {
            "field": image_field,
            "filename": image["path"].name,
            "mime": image["mime"],
            "data": image["data"],
        }
        for image in images
    ]
    if mask:
        files.append(
            {
                "field": "mask",
                "filename": mask["path"].name,
                "mime": "image/png",
                "data": mask["data"],
            }
        )
    return files


def _input_metadata(file: dict[str, Any], field: str) -> dict[str, Any]:
    return {
        "field": field,
        "path": str(file["path"]),
        "format": file["format"],
        "mime": file["mime"],
        "bytes": file["bytes"],
    }


def _output_paths(base: Path, count: int) -> list[Path]:
    if count == 1:
        return [base]
    return [
        base.with_name(f"{base.stem}-{index}{base.suffix}")
        for index in range(1, count + 1)
    ]


def _default_output(output_format: str, prefix: str = "generated") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (
        Path.cwd()
        / "outputs"
        / SKILL_NAME
        / f"{prefix}-{timestamp}.{output_format}"
    )


def _normalize_output(
    path: Optional[str], output_format: str, prefix: str = "generated"
) -> Path:
    output = Path(path).expanduser() if path else _default_output(output_format, prefix)
    expected = ".jpg" if output_format == "jpeg" else f".{output_format}"
    allowed = {".jpg", ".jpeg"} if output_format == "jpeg" else {expected}
    if output.suffix.lower() not in allowed:
        output = output.with_suffix(expected)
    return output


def _check_output_paths(
    paths: list[Path], inputs: list[dict[str, Any]], force: bool
) -> None:
    input_paths = {file["path"] for file in inputs}
    for path in paths:
        resolved = path.resolve()
        if resolved in input_paths:
            raise ImageGenError(
                f"Output must not overwrite an input image or mask: {resolved}"
            )
        if resolved.exists() and not force:
            raise ImageGenError(f"Output already exists: {resolved}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or edit images with a configured OpenAI-compatible endpoint."
    )
    prompts = parser.add_mutually_exclusive_group(required=True)
    prompts.add_argument("--prompt", help="Image prompt text.")
    prompts.add_argument("--prompt-file", help="UTF-8 file containing the image prompt.")
    parser.add_argument(
        "--endpoint",
        help="Base endpoint override; the operation path is appended automatically.",
    )
    parser.add_argument(
        "--edit-endpoint",
        help="Complete image-edit endpoint override, such as /v1/images/edits.",
    )
    parser.add_argument(
        "--edit-image-field",
        help=f"Multipart field for input images (default: {DEFAULT_EDIT_IMAGE_FIELD}).",
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help=f"Input image for editing; repeat for up to {MAX_INPUT_IMAGES} images.",
    )
    parser.add_argument("--mask", help="Transparent PNG mask for the first input image.")
    parser.add_argument("--model", help="Image model override.")
    parser.add_argument("--size", help="Requested image size override.")
    parser.add_argument(
        "--quality", choices=("low", "medium", "high", "auto"), help="Quality override."
    )
    parser.add_argument(
        "--output-format", choices=("png", "jpeg", "webp"), help="Format override."
    )
    parser.add_argument("--n", type=int, default=1, help="Number of prompt variants (1-10).")
    parser.add_argument("--out", help="Output file path.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Timeout in seconds.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--dry-run", action="store_true", help="Print request metadata only.")
    parser.add_argument("--mock-response", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        config_path = _resolve_config_path()
        config = _load_config(config_path)
        if not 1 <= args.n <= 10:
            raise ImageGenError("--n must be between 1 and 10.")
        if args.timeout <= 0:
            raise ImageGenError("--timeout must be greater than zero.")
        if args.mask and not args.image:
            raise ImageGenError("--mask requires at least one --image.")

        prompt = _read_prompt(args)
        configured_endpoint = args.endpoint or _config_text(config, "endpoint")
        mode = "edit" if args.image else "generate"
        if mode == "edit":
            configured_edit_endpoint = (
                args.edit_endpoint or _config_text(config, "edit_endpoint")
            )
            request_url = (
                _resolve_complete_url(configured_edit_endpoint)
                if configured_edit_endpoint
                else _resolve_edit_url(configured_endpoint)
            )
        else:
            request_url = _resolve_generation_url(configured_endpoint)

        model = (args.model or _config_text(config, "model", DEFAULT_MODEL)).strip()
        size = (args.size or _config_text(config, "size", DEFAULT_SIZE)).strip()
        quality = (args.quality or _config_text(config, "quality", DEFAULT_QUALITY)).strip()
        output_format = (
            args.output_format
            or _config_text(config, "output_format", DEFAULT_OUTPUT_FORMAT)
        ).strip().lower()
        image_field = (
            args.edit_image_field
            or _config_text(config, "edit_image_field", DEFAULT_EDIT_IMAGE_FIELD)
        ).strip()

        if not model:
            raise ImageGenError("The image model must not be empty.")
        if size != "auto" and not re.fullmatch(r"\d+x\d+", size):
            raise ImageGenError("The size must be 'auto' or WIDTHxHEIGHT.")
        if quality not in {"low", "medium", "high", "auto"}:
            raise ImageGenError("The quality must be low, medium, high, or auto.")
        if output_format not in {"png", "jpeg", "webp"}:
            raise ImageGenError("The output format must be png, jpeg, or webp.")

        payload = {
            "model": model,
            "prompt": prompt,
            "n": args.n,
            "size": size,
            "quality": quality,
            "output_format": output_format,
        }
        fields = {key: str(value) for key, value in payload.items()}
        images: list[dict[str, Any]] = []
        mask: Optional[dict[str, Any]] = None
        if mode == "edit":
            images, mask = _prepare_edit_inputs(args.image, args.mask, image_field)

        output = _normalize_output(
            args.out, output_format, "edited" if mode == "edit" else "generated"
        )
        paths = _output_paths(output, args.n)
        input_files = images + ([mask] if mask else [])
        _check_output_paths(paths, input_files, args.force)

        if args.dry_run:
            dry_run: dict[str, Any] = {
                "config": str(config_path),
                "mode": mode,
                "endpoint": configured_endpoint,
                "request_url": request_url,
                "payload": payload if mode == "generate" else fields,
                "outputs": [str(path.resolve()) for path in paths],
            }
            if mode == "edit":
                dry_run["images"] = [
                    _input_metadata(image, image_field) for image in images
                ]
                dry_run["mask"] = _input_metadata(mask, "mask") if mask else None
            print(json.dumps(dry_run, ensure_ascii=False, indent=2))
            return 0

        if args.mock_response:
            try:
                response = json.loads(
                    Path(args.mock_response).read_text(encoding="utf-8-sig")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ImageGenError(f"Could not load mock response: {exc}") from exc
            if not isinstance(response, dict):
                raise ImageGenError("Mock response must be a JSON object.")
        else:
            api_key = _config_text(config, "api_key")
            if not api_key:
                raise ImageGenError(
                    f"The API key is not configured. Set 'api_key' in {config_path}."
                )
            if mode == "edit":
                response = _request_multipart(
                    request_url,
                    fields,
                    _multipart_files(images, mask, image_field),
                    api_key,
                    args.timeout,
                )
            else:
                response = _request_json(request_url, payload, api_key, args.timeout)

        items = _response_items(response)
        if len(items) < args.n:
            raise ImageGenError(
                f"The endpoint returned {len(items)} image(s), but {args.n} were requested."
            )
        decoded_images: list[bytes] = []
        for item in items[: args.n]:
            image = _image_bytes(item, args.timeout)
            actual_format = _image_format(image)
            if actual_format is None:
                raise ImageGenError(
                    "The decoded response is not a recognized PNG, JPEG, or WebP image."
                )
            if actual_format != output_format:
                raise ImageGenError(
                    f"The endpoint returned {actual_format}, but {output_format} "
                    "was requested. No files were written."
                )
            decoded_images.append(image)

        for image, path in zip(decoded_images, paths):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
            print(f"Wrote {path.resolve()}")
        print(
            f"Operation: {mode}; model: {model}; size: {size}; "
            f"quality: {quality}; format: {output_format}"
        )
        return 0
    except ImageGenError as exc:
        _die(str(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
