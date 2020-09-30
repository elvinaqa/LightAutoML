from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Optional, Tuple, Callable, Union, TypeVar

import optuna
from log_calls import record_history

from lightautoml.dataset.base import LAMLDataset
from lightautoml.ml_algo.base import MLAlgo
from lightautoml.ml_algo.tuning.base import ParamsTuner
from lightautoml.validation.base import TrainValidIterator, HoldoutIterator

TunableAlgo = TypeVar("TunableAlgo", bound=MLAlgo)


@record_history()
class OptunaTunableMixin(ABC):
    mean_trial_time: float = None

    @abstractmethod
    def sample_params_values(self, trial: optuna.trial.Trial, suggested_params: dict, estimated_n_trials: int) -> dict:
        """
        Args:
            trial: optuna trial object.
            suggested_params: dict with parameters.
            estimated_n_trials: maximum number of hyperparameter estimation.

        Returns:
            dict with hyperparameters and their search.

        """

    def trial_params_values(
            self: TunableAlgo, estimated_n_trials: int, trial: optuna.trial.Trial,
            train_valid_iterator: Optional[TrainValidIterator] = None
    ) -> dict:
        """
        Args:
            estimated_n_trials: maximum number of hyperparameter estiamtion.
            trial: optuna trial object.
            train_valid_iterator: iterator used for getting parameters depending on dataset.

        """

        return self.sample_params_values(
            estimated_n_trials=estimated_n_trials,
            trial=trial,
            suggested_params=self.init_params_on_input(train_valid_iterator)
        )

    def get_objective(
            self: TunableAlgo, estimated_n_trials: int, train_valid_iterator: TrainValidIterator) -> \
            Callable[[optuna.trial.Trial], Union[float, int]]:
        """
        Args:
            estimated_n_trials: maximum number of hyperparameter estiamtion.
            train_valid_iterator: used for getting parameters depending on dataset.

        Returns:
            callable objective.

        """
        assert isinstance(self, MLAlgo)

        def objective(trial: optuna.trial.Trial) -> float:
            _ml_algo = deepcopy(self)
            _ml_algo.params = _ml_algo.trial_params_values(
                estimated_n_trials=estimated_n_trials,
                train_valid_iterator=train_valid_iterator,
                trial=trial,
            )
            output_dataset = _ml_algo.fit_predict(train_valid_iterator=train_valid_iterator)

            return _ml_algo.score(output_dataset)

        return objective


@record_history()
class OptunaTuner(ParamsTuner):
    """
    Wrapper for compatibility with optuna framework.
    """

    _name: str = 'OptunaTuner'

    study: optuna.study.Study = None
    estimated_n_trials: int = None

    def __init__(
            # TODO: For now, metric is designed to be greater is better. Change maximize param after metric refactor if needed
            self, timeout: Optional[int] = 1000, n_trials: Optional[int] = 100, direction: Optional[str] = 'maximize',
            fit_on_holdout: bool = True, random_state: int = 42
    ):
        """
        Args:
            timeout: maxtime of learning.
            n_trials: maximum number of trials.
            direction: direction of optimization. Set ``minimize`` for minimization and ``maximize`` for maximization.
            fit_on_holdout: will be used holdout cv iterator.
            random_state: seed for oputna sampler.

        """

        self.timeout = timeout
        self.n_trials = n_trials
        self.estimated_n_trials = n_trials
        self.direction = direction
        self._fit_on_holdout = fit_on_holdout
        self.random_state = random_state

    def _upd_timeout(self, timeout):
        self.timeout = min(self.timeout, timeout)

    def fit(self, ml_algo: TunableAlgo, train_valid_iterator: Optional[TrainValidIterator] = None) -> \
            Tuple[Optional[TunableAlgo], Optional[LAMLDataset]]:
        """
        Tune model.

        Args:
            ml_algo: MLAlgo that is tuned.
            train_valid_iterator: classic cv iterator.

        Returns:
            Tuple (None, None) if an optuna exception raised or ``fit_on_holdout=True`` and ``train_valid_iterator`` is \
            not HoldoutIterator.

            Tuple (MlALgo, preds_ds) otherwise.

        """
        assert not ml_algo.is_fitted, 'Fitted algo cannot be tuned.'
        # upd timeout according to ml_algo timer
        estimated_tuning_time = ml_algo.timer.estimate_tuner_time(len(train_valid_iterator))
        # TODO: Check for minimal runtime!!
        estimated_tuning_time = max(estimated_tuning_time, 1)
        print('Optuna may run {0} secs'.format(estimated_tuning_time))
        self._upd_timeout(estimated_tuning_time)
        ml_algo = deepcopy(ml_algo)

        flg_new_iterator = False
        if self._fit_on_holdout and type(train_valid_iterator) != HoldoutIterator:
            train_valid_iterator = train_valid_iterator.convert_to_holdout_iterator()
            flg_new_iterator = True

        # TODO: Check if time estimation will be ok with multiprocessing
        @record_history()
        def update_trial_time(study: optuna.study.Study, trial: optuna.trial.FrozenTrial):
            """
            Callback for number of iteration with time cut-off.

            Args:
                study: optuna study object.
                trial: optuna trial object.
            """
            ml_algo.mean_trial_time = study.trials_dataframe()['duration'].mean().total_seconds()
            self.estimated_n_trials = min(self.n_trials, self.timeout // ml_algo.mean_trial_time)

        try:

            sampler = optuna.samplers.TPESampler(seed=self.random_state)
            self.study = optuna.create_study(
                direction=self.direction,
                sampler=sampler
            )

            self.study.optimize(
                func=ml_algo.get_objective(
                    estimated_n_trials=self.estimated_n_trials,
                    train_valid_iterator=train_valid_iterator
                ),
                n_trials=self.n_trials,
                timeout=self.timeout,
                callbacks=[update_trial_time],
            )

            # need to update best params here
            self._best_params = self.study.best_params
            ml_algo.params = self._best_params

            preds_ds = ml_algo.fit_predict(train_valid_iterator)

            if flg_new_iterator:
                # if tuner was fitted on holdout set we dont need to save train results
                return None, None

            return ml_algo, preds_ds
        except optuna.exceptions.OptunaError:
            return None, None

    def plot(self):
        """
        Plot optimization history of all trials in a study.

        """
        return optuna.visualization.plot_optimization_history(self.study)