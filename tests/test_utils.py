import tarfile
from io import BytesIO
from pathlib import Path
from typing import Union

import pytest

from pipeline_runner.utils import (
    PathTraversalError,
    ensure_directory,
    escape_shell_string,
    get_human_readable_size,
    safe_extract_tar,
    stringify,
)


def test_ensure_directory(tmp_path: Path) -> None:
    target = tmp_path / "foo" / "bar"

    assert not target.exists()

    res = ensure_directory(target.as_posix())

    assert res == target.as_posix()
    assert target.exists()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("foo", "foo"),
        ("foo bar", "foo bar"),
        (["foo"], "foo"),
        (["foo", "bar"], "foo bar"),
        (["foo", "bar", "baz"], "foo bar baz"),
    ],
)
def test_stringify(value: Union[str, list[str]], expected: str) -> None:
    assert stringify(value) == expected


@pytest.mark.parametrize(
    ("sep", "expected"),
    [
        ("", "foobar"),
        (" ", "foo bar"),
        (", ", "foo, bar"),
        ("->", "foo->bar"),
    ],
)
def test_stringify_uses_separator(sep: str, expected: str) -> None:
    assert stringify(["foo", "bar"], sep=sep) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0.0B"),
        (2**10, "1.0KiB"),
        (2**20, "1.0MiB"),
        (2**30, "1.0GiB"),
        (2**40, "1.0TiB"),
        (2**50, "1024.0TiB"),
        (2**10 - 1, "1023.0B"),
        (2**20 - 2**10, "1023.0KiB"),
        (2**30 - 2**20, "1023.0MiB"),
        (2**40 - 2**30, "1023.0GiB"),
        (2**50 - 2**40, "1023.0TiB"),
        (1536, "1.5KiB"),
        (1540, "1.5KiB"),
    ],
)
def test_get_human_readable_size(value: int, expected: str) -> None:
    assert get_human_readable_size(value) == expected


def test_get_human_readable_size_raises_for_negative() -> None:
    with pytest.raises(ValueError, match="size must be positive"):
        get_human_readable_size(-1)


def test_escape_shell_string() -> None:
    assert escape_shell_string(r"echo \n") == r"echo \x5cn"
    assert escape_shell_string('echo ""') == r"echo \x22\x22"
    assert escape_shell_string("echo ''") == r"echo \x27\x27"
    assert escape_shell_string("echo $ENVVAR") == r"echo \x24ENVVAR"
    assert escape_shell_string("echo ${ENVVAR}") == r"echo \x24\x7bENVVAR\x7d"
    assert escape_shell_string("awk '(NR % 5 == 0)'") == r"awk \x27(NR \x25 5 == 0)\x27"
    assert (
        escape_shell_string(r"cat /proc/$$/environ | xargs -0 -n1 echo | tr '\n' ','")
        == r"cat /proc/\x24\x24/environ | xargs -0 -n1 echo | tr \x27\x5cn\x27 \x27,\x27"
    )


def test_safe_extract_tar(tmp_path: Path) -> None:
    data = "some-data"
    bindata = data.encode()

    files = ["a", "b", "c/d"]

    tar_file_obj = BytesIO()
    with tarfile.open(fileobj=tar_file_obj, mode="w") as tar:
        for f in files:
            ti = tarfile.TarInfo(f)
            ti.size = len(data)
            tar.addfile(ti, BytesIO(bindata))

    tar_file_obj.seek(0)

    with tarfile.open(fileobj=tar_file_obj, mode="r:") as tar:
        safe_extract_tar(tar, str(tmp_path))

    actual_files = [f for f in tmp_path.glob("**/*") if f.is_file()]
    assert sorted([str(f.relative_to(tmp_path)) for f in actual_files]) == sorted(files)

    for p in actual_files:
        assert p.read_text() == data


def test_safe_extract_tar_raises_on_files_outside_of_dir(tmp_path: Path) -> None:
    tar_file_obj = BytesIO()
    with tarfile.open(fileobj=tar_file_obj, mode="w") as tar:
        tar.addfile(tarfile.TarInfo("../a"), BytesIO())

    tar_file_obj.seek(0)

    with tarfile.open(fileobj=tar_file_obj, mode="r|") as tar:
        with pytest.raises(PathTraversalError):
            safe_extract_tar(tar, str(tmp_path))
