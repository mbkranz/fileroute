"""Client scopes release owned sessions without taking over injected resources."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from fileroute.clients.googledrive import GoogleDriveClient
from fileroute.clients.sharepoint import SharepointClient
from fileroute.clients.s3 import S3Client


@pytest.fixture(params=[SharepointClient, GoogleDriveClient])
def client_factory(request):
    def build(**kwargs):
        if request.param is SharepointClient:
            return SharepointClient(access_token="token", **kwargs)
        return GoogleDriveClient(
            credentials=SimpleNamespace(valid=True, token="token"), **kwargs
        )

    return build


@pytest.mark.parametrize("fail", [False, True])
def test_context_closes_owned_session(client_factory, monkeypatch, fail):
    session = Mock(spec=requests.Session)
    monkeypatch.setattr(requests, "Session", lambda: session)
    client = client_factory()
    expected = (
        pytest.raises(ValueError, match="operation failed") if fail else nullcontext()
    )
    with expected:
        with client as entered:
            assert entered is client
            session.close.assert_not_called()
            if fail:
                raise ValueError("operation failed")
    session.close.assert_called_once_with()


def test_injected_falsey_session_remains_caller_owned(client_factory, monkeypatch):
    class Session(requests.Session):
        def __bool__(self):
            return False

    with Session() as session:
        close = Mock()
        monkeypatch.setattr(session, "close", close)
        with client_factory(session=session) as client:
            assert client.session is session
        client.close()
        close.assert_not_called()
    close.assert_called_once_with()


@pytest.mark.parametrize("client_class", [SharepointClient, GoogleDriveClient])
def test_invalid_constructor_does_not_allocate_session(client_class, monkeypatch):
    monkeypatch.setattr(
        requests, "Session", lambda: pytest.fail("session allocated before validation")
    )
    with pytest.raises(ValueError):
        client_class()


def test_existing_provider_inherits_nonabstract_context_support():
    client = S3Client(client=object())
    with client as entered:
        assert entered is client
    client.close()
