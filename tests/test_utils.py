from pipeline_runner.utils import escape_shell_string


def test_escape_shell_string():
    assert escape_shell_string(r"echo \n") == r"echo \x5cn"
    assert escape_shell_string('echo ""') == r"echo \x22\x22"
    assert escape_shell_string("echo ''") == r"echo \x27\x27"
    assert escape_shell_string("echo $ENVVAR") == r"echo \x24ENVVAR"
    assert escape_shell_string("echo ${ENVVAR}") == r"echo \x24\x7bENVVAR\x7d"
    assert escape_shell_string("awk '(NR % 5 == 0)'") == r"awk \x27(NR \x25 5 == 0)\x27"
    assert (
        escape_shell_string(r"cat /proc/$$/environ | xargs -0 -n1 echo | tr '\n' ','")
        == r"cat /proc/\x24\x24/environ | xargs -0 -n1 echo | tr \x27\x5cn\x27 \x27,\x27"  # noqa: W503 (contradicts PEP8 and will be updated soon)
    )
