import importlib.util
from copy import deepcopy
from pathlib import Path


def test_groundtruth_groups_identical_bytes_and_credited_versions_transitively():
    spec = importlib.util.spec_from_file_location('prepare_evaluation', Path(__file__).parents[1] / 'scripts' / 'prepare_evaluation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pool = [dict(artist='Artist', title='Song', version='', hash='1'),
            dict(artist='ARTIST', title='Song', version='', hash='2'),
            dict(artist='Fallback', title='Another filename', version='', hash='2'),
            dict(artist='Artist', title='Song', version='live', hash='3')]
    groups = module.group_records(pool)
    assert sorted(len(group) for group in groups.values()) == [1, 3]


def scoring():
    spec = importlib.util.spec_from_file_location('evaluate_classification', Path(__file__).parents[1] / 'scripts' / 'evaluate_classification.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.score


def test_evaluation_never_uses_manual_predictions_or_unreviewed_gold():
    from music_sorter.classification import empty_classification
    score = scoring()
    manifest = [dict(hash='a', group='g', split='holdout')]
    classification = empty_classification()
    result = score(manifest, {'a': classification}, {'a': classification})
    assert not result['classification_accuracy_evaluated']
    assert result['splits']['holdout']['axes']['major']['conditional_exact_accuracy'] is None


def test_abstentions_reduce_coverage_and_recall_instead_of_inflating_accuracy():
    from music_sorter.classification import empty_classification
    truth = empty_classification()
    values = dict(major='가요', subgenre=['발라드'], vocal='보컬', mood=['잔잔한'], concept=[])
    for axis, value in values.items():
        truth[axis].update(value=value, status='confirmed', source='manual', protected=True)
    predicted = deepcopy(truth)
    for field in predicted.values():
        field.update(source='llm', protected=False)
    predicted['subgenre'].update(value=None, status='unresolved')
    report = scoring()([dict(hash='a', group='g', split='holdout')], {'a': truth}, {'a': predicted})
    metrics = report['splits']['holdout']['axes']
    assert metrics['subgenre']['coverage'] == 0 and metrics['subgenre']['recall'] == 0
    assert metrics['subgenre']['conditional_exact_accuracy'] is None
    assert metrics['major']['conditional_exact_accuracy'] == 1 and metrics['concept']['overall_exact_accuracy'] == 1


def test_evaluation_rejects_group_leak_between_splits():
    import pytest
    with pytest.raises(ValueError):
        scoring()([dict(hash='a', group='same', split='tune'), dict(hash='b', group='same', split='holdout')], {}, {})


def test_tag_order_has_no_effect_and_all_unknown_gold_is_not_accuracy():
    from music_sorter.classification import empty_classification
    truth = empty_classification()
    for field in truth.values():
        field.update(source='manual', protected=True, status='unresolved', value=None)
    prediction = deepcopy(truth)
    for field in prediction.values():
        field.update(source='llm', protected=False)
    manifest = [dict(hash='a', group='g', split='holdout')]
    assert not scoring()(manifest, {'a': truth}, {'a': prediction})['classification_accuracy_evaluated']
    truth['mood'].update(value=['잔잔한', '감성적인'], status='confirmed')
    prediction['mood'].update(value=['감성적인', '잔잔한'], status='confirmed')
    result = scoring()(manifest, {'a': truth}, {'a': prediction})
    assert result['classification_accuracy_evaluated'] and result['splits']['holdout']['axes']['mood']['conditional_exact_accuracy'] == 1
