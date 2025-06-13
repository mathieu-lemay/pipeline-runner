import base64
import hashlib
import logging
import os
import sys
from collections.abc import Iterator
from logging import Logger
from tarfile import TarFile
from typing import IO

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from platformdirs import user_cache_dir, user_data_dir
from slugify import slugify

from . import APP_NAME
from .errors import NegativeIntegerError

logger = logging.getLogger(__name__)

ONE_KB = 1024


def get_output_logger(output_directory: str, name: str) -> Logger:
    formatter = logging.Formatter("%(message)s")

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.terminator = ""

    file_handler = logging.FileHandler(os.path.join(output_directory, f"{name}.txt"))
    file_handler.setFormatter(formatter)
    file_handler.terminator = ""

    output_logger = logging.getLogger(f"pipeline_runner_output.{name}")
    output_logger.handlers.append(stream_handler)
    output_logger.handlers.append(file_handler)
    output_logger.setLevel("DEBUG")

    return output_logger


def get_cache_directory() -> str:
    return user_cache_dir(appname=APP_NAME)


def get_data_directory() -> str:
    return user_data_dir(appname=APP_NAME)


def get_project_cache_directory(project_path_slug: str) -> str:
    return ensure_directory(os.path.join(get_cache_directory(), project_path_slug))


def get_project_data_directory(project_path_slug: str) -> str:
    return ensure_directory(os.path.join(get_data_directory(), project_path_slug))


def ensure_directory(path: str) -> str:
    if not os.path.exists(path):
        os.makedirs(path)

    return path


def stringify(value: str | list[str], sep: str = " ") -> str:
    if isinstance(value, list):
        value = sep.join(value)

    return value


def escape_shell_string(value: str) -> str:
    for c in "\\$%{}\"'":
        value = value.replace(c, rf"\x{ord(c):02x}")

    return value


def get_human_readable_size(value: int) -> str:
    if value < 0:
        raise NegativeIntegerError

    num: float = value
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if num < ONE_KB:
            return f"{num:3.1f}{unit}"

        num /= ONE_KB

    return f"{num:.1f}TiB"


def wrap_in_shell(command: str | list[str], *, stop_on_error: bool = True) -> list[str]:
    command = stringify(command)

    wrapped = ["sh"]
    if stop_on_error:
        wrapped.append("-e")

    wrapped += ["-c", command]

    return wrapped


def hashify_path(path: str) -> str:
    slug = slugify(os.path.basename(path))

    h = hashlib.sha256(path.encode()).digest()
    suffix = base64.urlsafe_b64encode(h).decode()[:8]

    return f"{slug}-{suffix}"


def generate_rsa_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return private_key.decode()


class PathTraversalError(Exception):
    pass


def safe_extract_tar(tar: TarFile, path: str = ".", *, numeric_owner: bool = False) -> None:
    def _is_within_directory(directory: str, target: str) -> bool:
        abs_directory = os.path.abspath(directory)
        abs_target = os.path.abspath(target)

        prefix = os.path.commonprefix([abs_directory, abs_target])

        return prefix == abs_directory

    for member in tar:
        member_path = os.path.join(path, member.name)
        if not _is_within_directory(path, member_path):
            raise PathTraversalError

        if sys.version_info >= (3, 12):
            tar.extract(member, path, numeric_owner=numeric_owner, filter="data")
        else:
            tar.extract(member, path, numeric_owner=numeric_owner)


class FileStreamer(IO[bytes]):
    def __init__(self, it: Iterator[bytes]) -> None:
        self._it = it
        self._chunk = b""
        self._has_more_data = True

    def _grow_chunk(self) -> None:
        self._chunk = self._chunk + next(self._it)

    def read(self, n: int = 512) -> bytes:
        if not self._has_more_data:
            return b""

        try:
            while len(self._chunk) < n:
                self._grow_chunk()
            rv = self._chunk[:n]
            self._chunk = self._chunk[n:]
        except StopIteration:
            rv = self._chunk
            self._has_more_data = False

        return rv
