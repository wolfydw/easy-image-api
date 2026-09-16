#!/usr/bin/env python3
"""Install or upgrade easy-image-api without exposing the user's API key."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Iterator, Optional


SKILL_NAME = "easy-image-api"
REPOSITORY_ARCHIVE_URL = (
    "https://github.com/wolfydw/easy-image-api/archive/refs/heads/main.zip"
)
ARCHIVE_USER_AGENT = "easy-image-api-installer/1.0"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2_000
REQUIRED_FILES = ("SKILL.md", "scripts/generate_image.py")
MANAGED_ROOTS = ("SKILL.md", "agents", "assets", "references", "scripts")
IGNORED_NAMES = {"__pycache__", ".DS_Store"}

DEFAULT_CONFIG = {
    "endpoint": "https://cf.ydw.cool",
    "api_key": "",
    "model": "gpt-image-2.5",
    "size": "auto",
    "quality": "high",
    "output_format": "png",
}


class InstallError(RuntimeError):
    pass


def _default_destination() -> Path:
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    codex_home = (
        Path(configured_home).expanduser()
        if configured_home
        else Path.home() / ".codex"
    )
    return codex_home / "skills" / SKILL_NAME


def _download_archive(url: str = REPOSITORY_ARCHIVE_URL) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": ARCHIVE_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_ARCHIVE_BYTES:
                        raise InstallError("下载的安装包过大，已停止安装。")
                except ValueError:
                    pass
            archive = response.read(MAX_ARCHIVE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise InstallError(f"无法下载 skill：{exc}") from exc

    if len(archive) > MAX_ARCHIVE_BYTES:
        raise InstallError("下载的安装包过大，已停止安装。")
    return archive


def _validated_archive_members(
    archive: zipfile.ZipFile,
) -> tuple[str, list[zipfile.ZipInfo]]:
    members = archive.infolist()
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise InstallError("安装包为空或包含过多文件。")

    roots: set[str] = set()
    normalized_paths: set[str] = set()
    extracted_size = 0
    for member in members:
        if "\\" in member.filename:
            raise InstallError("安装包包含不安全的文件路径。")
        path = PurePosixPath(member.filename)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
        ):
            raise InstallError("安装包包含不安全的文件路径。")
        normalized = path.as_posix().rstrip("/").casefold()
        if normalized in normalized_paths:
            raise InstallError("安装包包含重复的文件路径。")
        normalized_paths.add(normalized)
        roots.add(path.parts[0])
        extracted_size += member.file_size
        if (
            member.file_size > MAX_MEMBER_BYTES
            or extracted_size > MAX_EXTRACTED_BYTES
        ):
            raise InstallError("安装包解压后的内容过大，已停止安装。")

        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise InstallError("安装包包含符号链接，已停止安装。")
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise InstallError("安装包包含不支持的特殊文件。")

    if len(roots) != 1:
        raise InstallError("安装包目录结构无效。")
    return next(iter(roots)), members


def _extract_archive(archive_bytes: bytes, output_directory: Path) -> Path:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            root_name, members = _validated_archive_members(archive)
            for member in members:
                relative = PurePosixPath(member.filename)
                target = output_directory.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallError(f"无法解压 skill 安装包：{exc}") from exc

    return output_directory / root_name


def _validate_source(source: Path) -> Path:
    resolved = source.expanduser().resolve()
    if not resolved.is_dir():
        raise InstallError(f"找不到 skill 源目录：{resolved}")
    for relative in REQUIRED_FILES:
        candidate = resolved / relative
        if not candidate.is_file() or candidate.is_symlink():
            raise InstallError(f"skill 安装包缺少必要文件：{relative}")
    return resolved


def _iter_managed_files(source: Path) -> Iterable[tuple[Path, Path]]:
    for root_name in MANAGED_ROOTS:
        root = source / root_name
        if not root.exists():
            continue
        if root.is_symlink():
            raise InstallError(f"skill 安装包包含不允许的符号链接：{root_name}")
        if root.is_file():
            yield root, Path(root_name)
            continue
        if not root.is_dir():
            raise InstallError(f"skill 安装包包含不支持的文件类型：{root_name}")
        for candidate in sorted(root.rglob("*")):
            relative = candidate.relative_to(source)
            if any(part in IGNORED_NAMES for part in relative.parts):
                continue
            if candidate.is_symlink():
                raise InstallError(f"skill 安装包包含不允许的符号链接：{relative}")
            if candidate.is_file() and candidate.suffix != ".pyc":
                yield candidate, relative


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_runtime(
    managed_files: Iterable[tuple[Path, Path]], destination: Path
) -> None:
    for source_file, relative in managed_files:
        _copy_file(source_file, destination / relative)


def _path_entry_exists(path: Path) -> bool:
    return os.path.lexists(str(path))


@contextlib.contextmanager
def _installation_lock(destination: Path) -> Iterator[None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / f".{destination.name}.install.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallError("另一个 easy-image-api 安装进程正在运行。") from exc
        yield
    finally:
        os.close(descriptor)


def _validate_update_targets(
    managed_files: Iterable[tuple[Path, Path]], destination: Path
) -> None:
    checked_parents: set[Path] = set()
    for _source_file, relative in managed_files:
        target = destination / relative
        if _path_entry_exists(target) and target.is_dir():
            raise InstallError(f"无法用文件替换已有目录：{target}")
        for parent in target.parents:
            if parent == destination:
                break
            if parent in checked_parents:
                continue
            checked_parents.add(parent)
            if _path_entry_exists(parent) and not parent.is_dir():
                raise InstallError(f"安装目标包含冲突路径：{parent}")


def _update_runtime_transactionally(
    managed_files: list[tuple[Path, Path]], destination: Path
) -> None:
    _validate_update_targets(managed_files, destination)
    transaction = Path(
        tempfile.mkdtemp(prefix=f".{SKILL_NAME}-update-", dir=str(destination.parent))
    )
    staged = transaction / "new"
    rollback = transaction / "rollback"
    changes: list[tuple[Path, Path, bool]] = []
    clean_transaction = True
    try:
        _copy_runtime(managed_files, staged)
        for _source_file, relative in managed_files:
            new_file = staged / relative
            target = destination / relative
            backup = rollback / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            had_existing = _path_entry_exists(target)
            if had_existing:
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            changes.append((target, backup, had_existing))
            os.replace(new_file, target)
    except BaseException as original_error:
        rollback_errors: list[OSError] = []
        for target, backup, had_existing in reversed(changes):
            try:
                if _path_entry_exists(target):
                    target.unlink()
                if had_existing and _path_entry_exists(backup):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, target)
            except OSError as exc:
                rollback_errors.append(exc)
        if rollback_errors:
            clean_transaction = False
            raise InstallError(
                f"升级失败且未能完整回滚；恢复文件保留在 {rollback}"
            ) from original_error
        raise
    finally:
        if clean_transaction:
            shutil.rmtree(transaction, ignore_errors=True)


def _prompt_api_key(reader: Optional[Callable[[str], str]] = None) -> str:
    prompt = reader or getpass.getpass
    while True:
        try:
            api_key = prompt("请输入生图 API Key（输入时不会显示）：")
        except (EOFError, KeyboardInterrupt) as exc:
            raise InstallError("未收到 API Key，安装已取消。") from exc
        if api_key.strip():
            return api_key
        print("API Key 不能为空，请重新输入。", file=sys.stderr)


def _write_config(destination: Path, api_key: str) -> None:
    config = dict(DEFAULT_CONFIG)
    config["api_key"] = api_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2)
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _create_config_exclusively(destination: Path, api_key: str) -> bool:
    config = dict(DEFAULT_CONFIG)
    config["api_key"] = api_key
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return False

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2)
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise
    return True


def install(
    source: Path,
    destination: Path,
    key_reader: Optional[Callable[[str], str]] = None,
) -> str:
    source = _validate_source(source)
    managed_files = list(_iter_managed_files(source))
    destination = destination.expanduser().resolve()

    if destination.exists() and not destination.is_dir():
        raise InstallError(f"安装目标不是目录：{destination}")

    with _installation_lock(destination):
        return _install_locked(managed_files, destination, key_reader)


def _install_locked(
    managed_files: list[tuple[Path, Path]],
    destination: Path,
    key_reader: Optional[Callable[[str], str]],
) -> str:
    config_path = destination / "config.json"
    has_existing_config = _path_entry_exists(config_path)
    api_key = None if has_existing_config else _prompt_api_key(key_reader)

    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=str(destination.parent))
        )
        try:
            _copy_runtime(managed_files, staging)
            if api_key is None:
                raise InstallError("首次安装缺少 API Key。")
            _write_config(staging / "config.json", api_key)
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return "installed"

    _update_runtime_transactionally(managed_files, destination)
    if api_key is not None:
        if _create_config_exclusively(config_path, api_key):
            return "installed"
        return "upgraded"
    return "upgraded"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安装或升级 easy-image-api skill。")
    parser.add_argument(
        "--source",
        type=Path,
        help="从本地仓库安装，用于开发和测试；默认下载 GitHub main 分支。",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=_default_destination(),
        help="skill 安装目录；默认使用 CODEX_HOME/skills/easy-image-api。",
    )
    return parser


def main() -> int:
    if sys.version_info < (3, 9):
        print("错误：安装和使用此 skill 需要 Python 3.9 或更高版本。", file=sys.stderr)
        return 1

    args = _build_parser().parse_args()
    try:
        if args.source:
            result = install(args.source, args.destination)
        else:
            with tempfile.TemporaryDirectory(prefix=f"{SKILL_NAME}-download-") as temporary:
                source = _extract_archive(_download_archive(), Path(temporary))
                result = install(source, args.destination)
    except InstallError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"错误：安装失败：{exc}", file=sys.stderr)
        return 1

    if result == "upgraded":
        print("检测到已有配置，已原样保留 config.json。")
        print(f"easy-image-api 已升级：{args.destination.expanduser().resolve()}")
    else:
        print(f"easy-image-api 已安装：{args.destination.expanduser().resolve()}")
    print("请在 Codex 的下一个任务中使用 $easy-image-api。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
