import io
import json
import ssl
import threading
from urllib.error import HTTPError, URLError

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from music_sorter.connections import ConnectionError, ConnectionResult, Model, NoRedirect, list_models
from music_sorter.settings import Settings
from music_sorter.ui.settings_dialog import SettingsDialog


class Response(io.BytesIO):
    def read1(self, size=-1):
        return self.read(size)


class Transport:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return Response(page if isinstance(page, bytes) else json.dumps(page).encode())


def test_openai_model_lookup_only_uses_authenticated_get():
    transport = Transport({"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "z-model"}]})
    result = list_models("openai", "offline-test-key", opener=transport)
    assert [model.id for model in result.models] == ["a-model", "z-model"]
    request, timeout = transport.requests[0]
    assert request.full_url == "https://api.openai.com/v1/models"
    assert request.get_method() == "GET" and request.data is None
    assert dict(request.header_items())["Authorization"] == "Bearer offline-test-key"
    assert 0 < timeout <= 10


def test_claude_pagination_and_workspace_are_explicit():
    transport = Transport({"data": [{"id": "model-a", "display_name": "A"}], "has_more": True, "last_id": "model-a"},
                          {"data": [{"id": "model-b"}], "has_more": False})
    result = list_models("anthropic", "offline-test-key", "wrkspc_test", opener=transport)
    assert len(result.models) == 2
    request, _ = transport.requests[0]
    headers = dict(request.header_items())
    assert headers["X-api-key"] == "offline-test-key"
    assert headers["Anthropic-version"] == "2023-06-01"
    assert headers["Anthropic-workspace-id"] == "wrkspc_test"
    assert transport.requests[1][0].full_url.endswith("limit=1000&after_id=model-a")
    assert all(request.get_method() == "GET" and request.data is None for request, _ in transport.requests)


@pytest.mark.parametrize("status, message", [(400, "워크스페이스"), (401, "인증 실패"), (403, "접근 권한"),
                                           (404, "조회 대상"), (429, "요청 제한"), (503, "서비스 오류"), (302, "리디렉션")])
def test_http_errors_never_expose_response_or_retry(status, message):
    secret = "offline-test-key"
    error = HTTPError("https://api.anthropic.com/v1/models", status, secret, {}, Response(secret.encode()))
    transport = Transport(error)
    with pytest.raises(ConnectionError) as caught:
        list_models("anthropic", secret, opener=transport)
    assert message in str(caught.value) and secret not in str(caught.value)
    assert len(transport.requests) == 1
    assert error.fp.closed


@pytest.mark.parametrize("error, message", [(TimeoutError("offline-test-key"), "시간 초과"),
                                           (URLError("offline-test-key"), "네트워크"),
                                           (URLError(ssl.SSLError("offline-test-key")), "TLS")])
def test_network_errors_are_sanitized(error, message):
    with pytest.raises(ConnectionError, match=message) as caught:
        list_models("openai", "offline-test-key", opener=Transport(error))
    assert "offline-test-key" not in str(caught.value)


@pytest.mark.parametrize("page", [b"not json", [], {"data": None}, {"data": [{}]},
                                  {"data": [{"id": "offline-test-key"}]}, {"data": [{"id": "bad\nmodel"}]},
                                  {"data": [{"id": "model"}], "has_more": True, "last_id": ""}])
def test_malformed_responses_fail_closed(page):
    with pytest.raises(ConnectionError, match="응답 형식"):
        list_models("anthropic", "offline-test-key", opener=Transport(page))


def test_page_loop_and_response_size_are_bounded():
    page = {"data": [{"id": "model"}], "has_more": True, "last_id": "model"}
    transport = Transport(page, page)
    with pytest.raises(ConnectionError, match="응답 형식"):
        list_models("anthropic", "offline-test-key", opener=transport)
    assert len(transport.requests) == 2
    with pytest.raises(ConnectionError, match="크기 초과"):
        list_models("openai", "offline-test-key", opener=Transport(b" " * 2_000_001))
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com") is None


def test_invalid_header_never_sends_a_request():
    transport = Transport()
    with pytest.raises(ConnectionError, match="입력 형식"):
        list_models("openai", "invalid\nkey", opener=transport)
    with pytest.raises(ConnectionError, match="키 미등록"):
        list_models("openai", "", opener=transport)
    assert not transport.requests


def test_total_deadline_and_max_pages(monkeypatch):
    ticks = iter([0, 0, 0, 31])
    monkeypatch.setattr("music_sorter.connections.time.monotonic", lambda: next(ticks))
    with pytest.raises(ConnectionError, match="시간 초과"):
        list_models("openai", "offline-test-key", opener=Transport({"data": []}))
    monkeypatch.setattr("music_sorter.connections.time.monotonic", lambda: 0)
    pages = [{"data": [{"id": f"model-{index}"}], "has_more": True, "last_id": f"model-{index}"} for index in range(10)]
    transport = Transport(*pages)
    with pytest.raises(ConnectionError, match="조회 범위"):
        list_models("anthropic", "offline-test-key", opener=transport)
    assert len(transport.requests) == 10


def dialog_with_vault(qtbot, library, tmp_path, monkeypatch):
    monkeypatch.setattr("music_sorter.ui.settings_dialog.QMessageBox.question", lambda *_: QMessageBox.StandardButton.Yes)
    values = {"openai": "offline-test-key", "anthropic": "offline-test-key"}
    monkeypatch.setattr("music_sorter.settings.CredentialStore.get", lambda _, provider: values.get(provider))
    monkeypatch.setattr("music_sorter.settings.CredentialStore.set", lambda _, provider, key: values.__setitem__(provider, key))
    monkeypatch.setattr("music_sorter.settings.CredentialStore.delete", lambda _, provider: values.pop(provider, None))
    dialog = SettingsDialog(Settings(), tmp_path / "settings.json", library)
    qtbot.addWidget(dialog)
    dialog.menu.setCurrentRow(2)
    dialog.show()
    return dialog, values


def test_threaded_connection_keeps_ui_responsive_and_guards_close(qtbot, library, tmp_path, monkeypatch):
    dialog, values = dialog_with_vault(qtbot, library, tmp_path, monkeypatch)
    started, release = threading.Event(), threading.Event()
    calls = []

    def lookup(provider, key, workspace):
        calls.append(provider)
        started.set()
        assert release.wait(5)
        return ConnectionResult((Model("model-a", "Model A"),))

    monkeypatch.setattr("music_sorter.ui.connection_worker.list_models", lookup)
    original = dialog.models[0].currentText()
    dialog.check_connection("anthropic")
    try:
        qtbot.waitUntil(started.is_set)
        assert not dialog.connection_buttons["openai"].isEnabled()
        assert not dialog.workspace.isEnabled()
        dialog.key_edits["anthropic"].setText("replacement-test-key")
        dialog.save_key("anthropic")
        assert values["anthropic"] == "offline-test-key"
        dialog.key_edits["anthropic"].clear()
        dialog.reject()
        dialog.close()
        assert dialog.isVisible()
        responsive = []
        QTimer.singleShot(0, lambda: responsive.append(True))
        qtbot.waitUntil(lambda: bool(responsive))
        dialog.check_connection("openai")
        assert calls == ["anthropic"]
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog.connection_worker is None)
    assert dialog.key_states["anthropic"].text() == "연결 확인됨"
    assert dialog.models[0].count() == 1
    assert dialog.models[0].currentText() == original
    dialog.models[0].setCurrentIndex(0)
    assert dialog.save()
    saved = Settings.load(tmp_path / "settings.json")
    assert saved.classify_model == "model-a"
    assert "offline-test-key" not in (tmp_path / "settings.json").read_text("utf-8")
    dialog.key_edits["anthropic"].setText("replacement-test-key")
    dialog.save_key("anthropic")
    assert dialog.key_states["anthropic"].text() == "등록됨 · 미확인"
    assert dialog.models[0].count() == 0
    assert dialog.models[0].currentText() == "model-a"


def test_connection_failure_and_workspace_change_invalidate_models(qtbot, library, tmp_path, monkeypatch):
    dialog, _ = dialog_with_vault(qtbot, library, tmp_path, monkeypatch)
    calls = []

    def lookup(provider, key, workspace):
        calls.append((provider, workspace))
        if len(calls) == 2:
            raise ConnectionError("인증 실패")
        return ConnectionResult((Model("model-a", "A"),))

    monkeypatch.setattr("music_sorter.ui.connection_worker.list_models", lookup)
    dialog.workspace.setText("wrkspc_test")
    dialog.check_connection("anthropic")
    qtbot.waitUntil(lambda: dialog.connection_worker is None)
    assert calls == [("anthropic", "wrkspc_test")]
    assert "anthropic" in dialog.model_lists
    dialog.check_connection("anthropic")
    qtbot.waitUntil(lambda: dialog.connection_worker is None)
    assert dialog.key_states["anthropic"].text() == "연결 오류"
    assert "anthropic" not in dialog.model_lists
    dialog.check_connection("anthropic")
    qtbot.waitUntil(lambda: dialog.connection_worker is None)
    dialog.workspace.setText("wrkspc_other")
    assert "anthropic" not in dialog.model_lists
    assert dialog.key_states["anthropic"].text() == "등록됨 · 미확인"


def test_key_draft_never_authenticates_until_registered(qtbot, library, tmp_path, monkeypatch):
    dialog, _ = dialog_with_vault(qtbot, library, tmp_path, monkeypatch)
    def forbidden(*args):
        raise AssertionError("Network lookup must not run")
    monkeypatch.setattr("music_sorter.ui.connection_worker.list_models", forbidden)
    dialog.key_edits["openai"].setText("new-test-key")
    dialog.check_connection("openai")
    assert dialog.connection_worker is None
    assert "먼저 등록" in dialog.connection_details["openai"].text()
    dialog.key_edits["openai"].clear()


def test_vault_failure_never_leaks_or_calls_provider(qtbot, library, tmp_path, monkeypatch):
    dialog, _ = dialog_with_vault(qtbot, library, tmp_path, monkeypatch)
    def broken(*args):
        raise RuntimeError("offline-test-key")
    monkeypatch.setattr("music_sorter.settings.CredentialStore.get", broken)
    dialog.check_connection("openai")
    qtbot.waitUntil(lambda: dialog.connection_worker is None)
    assert dialog.key_states["openai"].text() == "연결 오류"
    assert "저장소 접근 오류" in dialog.connection_details["openai"].text()
    assert "offline-test-key" not in dialog.connection_details["openai"].text()
