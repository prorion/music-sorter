"""Read-only human gold vs independent model decisions; missing labels remain unevaluated."""
import argparse
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

AXES = ('major', 'subgenre', 'vocal', 'mood', 'concept')


def read_decisions(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        rows = {}
        duplicates = set()
        for fingerprint, payload in db.execute('SELECT hash,classification FROM tracks WHERE file_state!=?', ('replaced',)):
            if fingerprint in rows:
                duplicates.add(fingerprint)
            rows[fingerprint] = json.loads(payload)
        for fingerprint in duplicates:
            rows.pop(fingerprint)
        return rows


def score(manifest, gold, predictions):
    partitions = {}
    for row in manifest:
        for key in (row['hash'], row['group']):
            if key in partitions and partitions[key] != row['split']:
                raise ValueError('같은 녹음/해시가 조정·검증 집합에 중복됩니다.')
            partitions[key] = row['split']
    report = {'evaluation_source': 'human_manual_gold_vs_llm_only', 'splits': {}}
    for split in ('tune', 'holdout'):
        metrics = {axis: dict(known_gold=0, confirmed_predictions=0, unresolved_predictions=0, correct=0, tp=0, fp=0, fn=0) for axis in AXES}
        pairs = reviewed = 0
        for row in [row for row in manifest if row['split'] == split]:
            truth = gold.get(row['hash'])
            prediction = predictions.get(row['hash'])
            if not truth or any(truth[a]['source'] != 'manual' or not truth[a]['protected'] or truth[a]['status'] not in {'confirmed', 'unresolved'} for a in AXES):
                continue
            reviewed += 1
            if not prediction or any(prediction[a]['source'] != 'llm' or prediction[a]['status'] not in {'confirmed', 'unresolved'} for a in AXES):
                continue
            pairs += 1
            for axis in AXES:
                expected, actual = truth[axis], prediction[axis]
                if expected['status'] != 'confirmed':
                    continue
                metric = metrics[axis]
                metric['known_gold'] += 1
                confirmed = actual['status'] == 'confirmed'
                metric['confirmed_predictions' if confirmed else 'unresolved_predictions'] += 1
                equal = set(actual['value'] or []) == set(expected['value']) if axis in {'subgenre', 'mood', 'concept'} else actual['value'] == expected['value']
                if confirmed and equal:
                    metric['correct'] += 1
                if axis in {'subgenre', 'mood', 'concept'}:
                    target = set(expected['value'])
                    found = set(actual['value'] or []) if confirmed else set()
                    metric['tp'] += len(target & found)
                    metric['fp'] += len(found - target)
                    metric['fn'] += len(target - found)
        for axis, metric in metrics.items():
            known, completed = metric['known_gold'], metric['confirmed_predictions']
            metric['coverage'] = completed / known if known else None
            metric['conditional_exact_accuracy'] = metric['correct'] / completed if completed else None
            metric['overall_exact_accuracy'] = metric['correct'] / known if known else None
            if axis in {'subgenre', 'mood', 'concept'}:
                metric['precision'] = metric['tp'] / (metric['tp'] + metric['fp']) if metric['tp'] + metric['fp'] else None
                metric['recall'] = metric['tp'] / (metric['tp'] + metric['fn']) if metric['tp'] + metric['fn'] else None
            if completed:
                fraction = metric['correct'] / completed
                denominator = 1 + 1.96 ** 2 / completed
                radius = 1.96 * math.sqrt(fraction * (1 - fraction) / completed + 1.96 ** 2 / (4 * completed ** 2))
                metric['conditional_accuracy_wilson95'] = [(fraction + 1.96 ** 2 / (2 * completed) - radius) / denominator,
                                                         (fraction + 1.96 ** 2 / (2 * completed) + radius) / denominator]
        report['splits'][split] = dict(human_reviewed=reviewed, independently_predicted_pairs=pairs, axes=metrics)
    report['classification_accuracy_evaluated'] = any(metric['known_gold'] for metric in report['splits']['holdout']['axes'].values())
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--gold-db', type=Path, required=True)
    parser.add_argument('--prediction-db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.gold_db.resolve() == args.prediction_db.resolve():
        raise ValueError('정답과 모델 판정은 별도 DB로 준비하세요.')
    report = score(json.loads(args.manifest.read_text('utf-8')), read_decisions(args.gold_db), read_decisions(args.prediction_db))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'classification_accuracy_evaluated': report['classification_accuracy_evaluated'],
                      'human_reviewed_holdout': report['splits']['holdout']['human_reviewed'],
                      'independent_pairs_holdout': report['splits']['holdout']['independently_predicted_pairs']}))


if __name__ == '__main__':
    main()
