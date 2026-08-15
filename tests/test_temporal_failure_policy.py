from agents import ModelBehaviorError
from openai import APIConnectionError

from digital_company.temporal_worker import is_non_retryable_activity_error


def test_exhausted_structured_output_error_is_not_retried_by_temporal():
    assert is_non_retryable_activity_error(ModelBehaviorError("invalid JSON"))


def test_validation_errors_are_not_retried_by_temporal():
    assert is_non_retryable_activity_error(ValueError("invalid proposal"))


def test_transport_errors_remain_retryable_by_temporal():
    assert not is_non_retryable_activity_error(APIConnectionError(request=None))
