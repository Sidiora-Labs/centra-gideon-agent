"""Provider exception classification stays reusable across workflow entry points."""

from gideon.automation.workflows.failure_taxonomy import classify_exception
from gideon.automation.workflows.models import FailureClass


class InternalServerException(Exception):
    pass


def test_provider_server_exception_class_name_is_transient_and_retryable():
    failure = classify_exception(InternalServerException("opaque provider failure"))

    assert failure.failure_class is FailureClass.TRANSIENT
    assert failure.retryable


def test_provider_server_exception_text_is_transient_and_retryable():
    failure = classify_exception(
        RuntimeError("provider returned internal server error")
    )

    assert failure.failure_class is FailureClass.TRANSIENT
    assert failure.retryable
