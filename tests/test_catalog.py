from copy import deepcopy

import pytest

from music_sorter.catalog import active_catalog, change_catalog, load_catalog, options
from music_sorter.classification import TAXONOMY, empty_classification, validate
from music_sorter.llm import response_schema


def test_add_retire_reactivate_preserve_existing_decisions(tmp_path):
    path = tmp_path / 'taxonomy.json'
    catalog = change_catalog(path, 'concept', '산책', 'add', 1)
    assert catalog['version'] == 2 and '산책' in catalog['concept']
    catalog = change_catalog(path, 'concept', '산책', 'retire', 2)
    assert '산책' in catalog['concept'] and '산책' not in active_catalog(catalog)['concept']
    classification = empty_classification()
    classification['concept'].update(status='confirmed', value=['산책'])
    validate(classification, catalog)
    catalog = change_catalog(path, 'concept', '산책', 'reactivate', 3)
    assert '산책' in active_catalog(catalog)['concept'] and len(catalog['history']) == 3


def test_catalog_conflicts_and_last_tag_are_blocked(tmp_path):
    path = tmp_path / 'taxonomy.json'
    change_catalog(path, 'mood', '차분한', 'add', 1)
    with pytest.raises(ValueError):
        change_catalog(path, 'mood', '새로운', 'add', 1)
    with pytest.raises(ValueError):
        change_catalog(path, 'major', '새로운', 'add', 2)
    with pytest.raises(ValueError):
        change_catalog(path, 'mood', '\x01', 'add', 2)
    version = 2
    for name in ('J-POP', '월드뮤직'):
        if name == '월드뮤직':
            with pytest.raises(ValueError):
                change_catalog(path, 'subgenre:기타', name, 'retire', version)
        else:
            version = change_catalog(path, 'subgenre:기타', name, 'retire', version)['version']
    assert load_catalog(path)['version'] == 3


def test_retired_tag_is_retained_for_editor_but_excluded_from_new_schema(tmp_path):
    path = tmp_path / 'taxonomy.json'
    catalog = change_catalog(path, 'concept', '카페', 'retire', 1)
    original = deepcopy(TAXONOMY)
    try:
        TAXONOMY.clear()
        TAXONOMY.update(catalog)
        assert '카페' not in options('concept') and '카페' in options('concept', current=['카페'])
        fields = response_schema()['properties']['tracks']['items']['properties']['classification']['properties']
        assert '카페' not in fields['concept']['properties']['value']['items']['enum']
    finally:
        TAXONOMY.clear()
        TAXONOMY.update(original)


def test_catalog_gui_add_has_no_automatic_reclassification(qtbot, library, monkeypatch):
    from music_sorter.ui.catalog_dialog import CatalogDialog
    from PySide6.QtWidgets import QMessageBox
    dialog = CatalogDialog(library)
    qtbot.addWidget(dialog)
    dialog.group.setCurrentIndex(dialog.group.findData('concept'))
    dialog.name.setText('산책')
    monkeypatch.setattr('music_sorter.ui.catalog_dialog.QMessageBox.question', lambda *_: QMessageBox.StandardButton.Yes)
    dialog.change('add')
    assert load_catalog(dialog.path)['version'] == 2
    assert library.jobs() == []
