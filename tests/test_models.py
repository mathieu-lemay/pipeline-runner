from pipeline_runner import PipelineResult


def test_pipeline_result_ok_returns_true_if_exit_code_is_zero():
    res = PipelineResult(0)

    assert res.ok


def test_pipeline_result_ok_returns_false_if_exit_code_is_not_zero():
    for i in range(1, 256):
        res = PipelineResult(i)

        assert res.ok is False
