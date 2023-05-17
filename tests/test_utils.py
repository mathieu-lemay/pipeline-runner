import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from pipeline_runner.utils import PathTraversalError, escape_shell_string, safe_extract_tar


def test_escape_shell_string():
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
        safe_extract_tar(tar, tmp_path)

    actual_files = [f for f in tmp_path.glob("**/*") if f.is_file()]
    assert sorted([str(f.relative_to(tmp_path)) for f in actual_files]) == sorted(files)

    for f in actual_files:
        assert f.read_text() == data


def test_safe_extract_tar_raises_on_files_outside_of_dir(tmp_path: Path) -> None:
    tar_file_obj = BytesIO()
    with tarfile.open(fileobj=tar_file_obj, mode="w") as tar:
        tar.addfile(tarfile.TarInfo("../a"), BytesIO())

    tar_file_obj.seek(0)

    with tarfile.open(fileobj=tar_file_obj, mode="r|") as tar:
        with pytest.raises(PathTraversalError):
            safe_extract_tar(tar, tmp_path)
