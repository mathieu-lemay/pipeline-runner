from pipeline_runner import PipelineResult


def test_pipeline_result_ok_returns_true_if_exit_code_is_zero():
    build_number = 33
    pipeline_uuid = "uuid"
    res = PipelineResult(0, build_number, pipeline_uuid)

    assert res.ok


def test_pipeline_result_ok_returns_false_if_exit_code_is_not_zero():
    build_number = 33
    pipeline_uuid = "uuid"

    for i in range(1, 256):
        res = PipelineResult(1, build_number, pipeline_uuid)

        assert res.ok is False
