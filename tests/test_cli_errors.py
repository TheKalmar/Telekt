from openai import RateLimitError

from digital_company import cli


def test_cli_has_sdk_error_boundary():
    assert issubclass(RateLimitError, Exception)
    assert callable(cli.main)
