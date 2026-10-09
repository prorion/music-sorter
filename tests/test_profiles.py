import pytest

from music_sorter.profiles import load_profile, parse_env
from music_sorter.settings import Settings


def test_explicit_profile_priorities_and_no_key_fallback(tmp_path):
    path = tmp_path / '.env'
    path.write_text('UI_THEME=dark\nCLASSIFY_MODEL=from-file\nOPENAI_API_KEY=test-file-key\nMUSIC_ROOT=./music\nPLAYLIST_FORMAT=m3u\nUNSUPPORTED=chosen\n', 'utf-8')
    profile = load_profile(path, Settings(theme='light'), {'CLASSIFY_MODEL': 'from-environment', 'ANTHROPIC_API_KEY': 'test-env-key'})
    assert profile.settings.theme == 'dark' and profile.settings.classify_model == 'from-environment'
    assert profile.settings.music_root == str((tmp_path / 'music').resolve())
    assert profile.settings.playlist_format == 'm3u' and Settings().playlist_format == 'm3u8'
    assert profile.keys == {'openai': 'test-file-key', 'anthropic': 'test-env-key'}
    assert profile.ignored == ['UNSUPPORTED'] and 'test-file-key' not in repr(profile)


def test_no_interpolation_or_execution_and_literal_windows_path(tmp_path):
    path = tmp_path / '.env'
    path.write_text('MUSIC_ROOT="C:\\Music"\nCLASSIFY_MODEL=$(echo secret)\nOPENAI_API_KEY=${MISSING}\n', 'utf-8')
    values = parse_env(path)
    assert values['MUSIC_ROOT'] == 'C:\\Music' and values['OPENAI_API_KEY'] == '${MISSING}'
    assert values['CLASSIFY_MODEL'] == '$(echo secret)'


@pytest.mark.parametrize('text', ['NOT_A_LINE', 'UI_THEME="unfinished', 'UI_FONT_SCALE=garbage', 'SCAN_INCLUDE_SUBFOLDERS=yes', 'LLM_EXECUTION_MODE=automatic', 'PLAYLIST_FORMAT=cp949'])
def test_bad_profile_rejected_without_echoing_value(tmp_path, text):
    path = tmp_path / '.env'
    path.write_text(text, 'utf-8')
    with pytest.raises(ValueError):
        load_profile(path, Settings(), {})
