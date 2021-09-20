import base64
import hashlib
import logging
import os
import sys
from typing import List, Union

from appdirs import user_cache_dir, user_data_dir
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from slugify import slugify

from . import APP_NAME

logger = logging.getLogger(__name__)


def get_output_logger(output_directory: str, name: str) -> logging.Logger:
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


def get_project_cache_directory(project_path_slug):
    return ensure_directory(os.path.join(get_cache_directory(), project_path_slug))


def get_project_data_directory(project_path_slug):
    return ensure_directory(os.path.join(get_data_directory(), project_path_slug))


def ensure_directory(path) -> str:
    if not os.path.exists(path):
        os.makedirs(path)

    return path


def stringify(value: Union[str, List[str]], sep: str = " "):
    if isinstance(value, list):
        value = sep.join(value)

    return value


def escape_shell_string(value: str) -> str:
    for c in "\\$%{}\"'":
        value = value.replace(c, fr"\x{ord(c):02x}")

    return value


def get_human_readable_size(num):
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB"]:

        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"

        num /= 1024.0

    return f"{num:.1f}{unit}"


def wrap_in_shell(command: Union[str, List[str]], stop_on_error=True):
    command = stringify(command)

    wrapped = ["sh"]
    if stop_on_error:
        wrapped.append("-e")

    wrapped += ["-c", command]

    return wrapped


def hashify_path(path):
    slug = slugify(os.path.basename(path))

    h = hashlib.sha256(path.encode()).digest()
    h = base64.urlsafe_b64encode(h).decode()[:8]

    return f"{slug}-{h}"


def generate_ssh_rsa_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return private_key.decode()


class FileStreamer:
    def __init__(self, it):
        self._it = it
        self._chunk = b""
        self._has_more_data = True

    def _grow_chunk(self):
        self._chunk = self._chunk + next(self._it)

    def read(self, n):
        if not self._has_more_data:
            return None

        try:
            while len(self._chunk) < n:
                self._grow_chunk()
            rv = self._chunk[:n]
            self._chunk = self._chunk[n:]
            return rv
        except StopIteration:
            rv = self._chunk
            self._has_more_data = False
            return rv
