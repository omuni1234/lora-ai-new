"""Small offline study: python examples/tuning_bandit.py --data data
Then run `lorasim worker --data data --workers 2 --once`, or use the existing worker.
Results appear under Model Library > tuning history. No browser session is needed.
"""
import argparse
from lorasim.config import Profile
from lorasim.jobs import Store
from lorasim.tuning import StudyRequest, create


def main():
    parser = argparse.ArgumentParser(description='小規模なハイパーパラメータ探索を登録')
    parser.add_argument('--data', default='data')
    args = parser.parse_args()
    profile = Profile.model_validate({
        'name': 'bandit-tuning-example', 'duration_s': 1200, 'area_m': [2000, 2000],
        'groups': [{'name': 'devices', 'count': 12, 'policy': {'kind': 'bandit', 'action_space': {}},
                    'timing': {'kind': 'periodic', 'options': {'interval_s': 30, 'start': {'mode': 'uniform', 'min_s': 0, 'max_s': 30}}}}],
    })
    req = StudyRequest(yaml=profile.model_dump_json(), name='Bandit epsilon example',
        definition={'kind': 'bandit', 'action_space': {}}, groups=['devices'],
        dimensions=[{'path': 'epsilon', 'values': [.02, .05, .1, .2, .4]}],
        method='halving', budgets_s=[300, 600, 1200], max_candidates=5,
        seeds=[1, 2], validation_seeds=[101, 102])
    result = create(Store(args.data), req)
    print('Study:', result['id'])
    print('Experiment:', result['experiment_id'])
    print('Open Model Library > tuning history, or start the worker.')


if __name__ == '__main__':
    main()
