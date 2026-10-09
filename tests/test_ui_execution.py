import threading
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QMessageBox

from music_sorter.connections import ConnectionResult, Model
from music_sorter.llm import ProviderClient, cost_micro, output_limit, price, usage_cost
from music_sorter.scanner import ScanControl, scan_library
from music_sorter.settings import Settings
from music_sorter.ui.classify_dialog import ClassifyDialog
from music_sorter.ui.external_dialog import ExternalDialog
from music_sorter.ui.settings_dialog import SettingsDialog
from music_sorter.ui.workflow import LiveStatus
from test_classifier import Client


def test_one_click_classifies_without_budget_or_confirmation_and_reports_wait(qtbot, library, root, song, fake_reader, monkeypatch):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    started, release = threading.Event(), threading.Event()
    client = Client()
    original = client.generate
    limits = []

    def generate(model, inputs, max_tokens):
        limits.append((model, max_tokens))
        started.set()
        assert release.wait(5)
        return original(model, inputs, max_tokens)

    client.generate, client.close = generate, lambda: None
    monkeypatch.setattr('music_sorter.ui.classify_dialog.ProviderClient', lambda *args, **kwargs: client)
    monkeypatch.setattr('music_sorter.settings.CredentialStore.get', lambda *_: 'offline-test-key')
    monkeypatch.setattr('music_sorter.ui.classify_dialog.QMessageBox.question', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Unexpected confirmation')))
    dialog = ClassifyDialog(library, Settings(), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.show()
    assert [b.text() for b in dialog.findChildren(QPushButton) if not b.isHidden()] == ['실행', '닫기']
    height = dialog.status.height()
    dialog.run_button.click()
    try:
        qtbot.waitUntil(started.is_set)
        qtbot.waitUntil(lambda: '응답 대기' in dialog.status.message)
        assert not dialog.run_button.isEnabled() and not dialog.purpose.isEnabled()
        assert dialog.status.height() == height and '\n' not in dialog.status.message
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog.worker is None)
    assert limits == [('claude-haiku-5-5', 4096)] and client.calls == 1
    assert dialog.engine.summary(dialog.job_id)['counts'] == {'completed': 1}
    assert '분류 완료' in dialog.status.message and dialog.run_button.isHidden()
    assert song.read_bytes() == b'original-audio'


def test_startup_failure_keeps_created_job_for_resume(qtbot, library, root, song, fake_reader, monkeypatch):
    from music_sorter.llm import ProviderError
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    monkeypatch.setattr('music_sorter.ui.classify_dialog.ProviderClient', lambda *a, **k: (_ for _ in ()).throw(ProviderError('auth')))
    monkeypatch.setattr('music_sorter.settings.CredentialStore.get', lambda *_: None)
    dialog = ClassifyDialog(library, Settings(), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.run()
    qtbot.waitUntil(lambda: dialog.worker is None)
    assert dialog.job_id and dialog.engine.summary(dialog.job_id)['counts'] == {'prepared': 1}
    assert '키·권한' in dialog.status.message and dialog.run_button.isEnabled()


def test_lookup_reports_current_song_and_source_before_response(qtbot, library, root, song, fake_reader, monkeypatch):
    scan_library(library, root)
    track = library.list_tracks()[0][0]
    started, release = threading.Event(), threading.Event()
    calls = []

    def lookup(self, track, service, force):
        calls.append(service)
        if service == 'domestic':
            self.control.report('멜론 검색 중…')
            started.set()
            assert release.wait(5)
        return {'state': 'not_found'}

    monkeypatch.setattr('music_sorter.ui.external_dialog.ExternalLookup.lookup', lookup)
    monkeypatch.setattr('music_sorter.settings.CredentialStore.get', lambda *_: None)
    dialog = ExternalDialog(library, Settings(), [track['id']], {})
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.start_button.click()
    try:
        qtbot.waitUntil(started.is_set)
        qtbot.waitUntil(lambda: '멜론 검색' in dialog.status.message)
        assert track['title'] in dialog.status.message and '1곡 중 1번째' in dialog.status.message
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog.worker is None)
    assert calls == ['domestic', 'musicbrainz', 'lastfm']
    assert '조회 완료' in dialog.status.message and song.read_bytes() == b'original-audio'


def test_model_tab_automatically_lists_and_checks_shared_selection_and_saves_id(qtbot, library, tmp_path, monkeypatch):
    monkeypatch.setattr('music_sorter.ui.settings_dialog.QMessageBox.question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    requests = []
    monkeypatch.setattr('music_sorter.settings.CredentialStore.get', lambda *_: 'offline-test-key')

    def listing(provider, key, workspace):
        requests.append(('list', provider))
        return ConnectionResult((Model('claude-haiku-5-5', 'Claude Haiku 5.5'), Model('claude-haiku-4-5-20251001', 'Claude Haiku 4.5')))

    def retrieve(provider, key, model, workspace):
        requests.append(('model', model))
        return Model(model, model, {'structured_outputs': True, 'batch': True})

    monkeypatch.setattr('music_sorter.ui.connection_worker.list_models', listing)
    monkeypatch.setattr('music_sorter.ui.connection_worker.get_model', retrieve)
    dialog = SettingsDialog(Settings(), tmp_path / 'settings.json', library)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not requests and dialog.menu.item(3).text() == 'LLM 모델 / 비용'
    dialog.menu.setCurrentRow(3)
    qtbot.waitUntil(lambda: all('모델 조회 완료' in state.message for state in dialog.model_states))
    qtbot.waitUntil(lambda: dialog.connection_worker is None and not dialog.connection_queue)
    assert requests == [('list', 'anthropic'), ('model', 'claude-haiku-5-5')]
    dialog.models[0].setCurrentIndex(dialog.models[0].findData('claude-haiku-4-5-20251001'))
    qtbot.waitUntil(lambda: '모델 조회 완료' in dialog.model_states[0].message)
    qtbot.waitUntil(lambda: dialog.connection_worker is None)
    assert dialog.save()
    saved = Settings.load(tmp_path / 'settings.json')
    assert saved.classify_model == 'claude-haiku-4-5-20251001' and saved.escalate_model == 'claude-haiku-5-5'
    assert dialog.draft() == dialog.settings
    assert 'offline-test-key' not in (tmp_path / 'settings.json').read_text('utf-8')


def test_latest_model_selection_is_not_overwritten_by_slow_previous_response(qtbot, library, tmp_path, monkeypatch):
    monkeypatch.setattr('music_sorter.ui.settings_dialog.QMessageBox.question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr('music_sorter.settings.CredentialStore.get', lambda *_: 'offline-test-key')
    monkeypatch.setattr('music_sorter.ui.connection_worker.list_models', lambda *a: ConnectionResult((Model('claude-haiku-5-5', 'Haiku 5.5'), Model('claude-haiku-4-5', 'Haiku 4.5'))))

    def retrieve(provider, key, model, workspace):
        if model == 'claude-haiku-5-5':
            started.set()
            assert release.wait(5)
            return Model(model, model, {'structured_outputs': False})
        return Model(model, model, {'structured_outputs': True})

    monkeypatch.setattr('music_sorter.ui.connection_worker.get_model', retrieve)
    dialog = SettingsDialog(Settings(), tmp_path / 'settings.json', library)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.menu.setCurrentRow(3)
    try:
        qtbot.waitUntil(started.is_set)
        dialog.models[0].setCurrentIndex(dialog.models[0].findData('claude-haiku-4-5'))
    finally:
        release.set()
    qtbot.waitUntil(lambda: dialog.connection_worker is None and '모델 조회 완료' in dialog.model_states[0].message, timeout=10000)
    assert '지원하지' in dialog.model_states[1].message
    assert dialog.model_id(0) == 'claude-haiku-4-5'


def test_live_status_replaces_message_and_keeps_one_line(qtbot):
    label = LiveStatus('처음')
    qtbot.addWidget(label)
    label.resize(200, label.height())
    label.show()
    height = label.height()
    label.setText('다음\n' + '긴 상태 메시지 ' * 100)
    assert label.height() == height and '\n' not in label.text()
    assert '처음' not in label.toolTip() and label.text().endswith('…')


def test_haiku_request_and_cost_tiers_preserve_text_blocks():
    client = ProviderClient('anthropic', None, client=SimpleNamespace())
    body = client.body('claude-haiku-5-5', [], 4096)
    assert body['output_config']['effort'] == 'low'
    assert not {'temperature', 'top_p', 'top_k'} & body.keys()
    result = client.normalize({'stop_reason': 'end_turn', 'content': [{'type': 'thinking', 'thinking': ''}, {'type': 'text', 'text': '{"tracks":[]}'}],
                               'usage': {'input_tokens': 10, 'output_tokens': 10}})
    assert result['completed'] and result['text'] == '{"tracks":[]}'
    rates = price('anthropic', 'claude-haiku-5-5')
    assert cost_micro(rates, 100000, 100) == 10050
    assert cost_micro(rates, 100001, 100) == 50251
    options = {'pricing': rates, 'model': 'claude-haiku-5-5', 'max_tokens_per_track': 1000, 'contract': {'prompt_cache': True}}
    assert usage_cost(options, {'input': 0, 'output': 100, 'cached': 100001, 'cache_write': 0}) == 5251
    assert output_limit(options, 1) == 4096 and output_limit(options, 20) == 20000


def test_existing_settings_are_preserved_and_legacy_profile_budget_is_ignored(tmp_path):
    from music_sorter.profiles import load_profile
    path = tmp_path / 'settings.json'
    Settings(classify_model='claude-haiku-4-5', escalate_model='claude-sonnet-5-5').save(path)
    assert Settings.load(path).escalate_model == 'claude-sonnet-5-5'
    profile = tmp_path / '.env'
    profile.write_text('LLM_JOB_BUDGET_USD=NaN\nBUDGET_WARNING_RATIO=0.8', 'utf-8')
    loaded = load_profile(profile, Settings(), {})
    assert not loaded.job_defaults and set(loaded.ignored) == {'LLM_JOB_BUDGET_USD', 'BUDGET_WARNING_RATIO'}
