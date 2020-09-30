from copy import copy
from typing import List, Any, Union, Optional, Tuple

import numpy as np
from log_calls import record_history
from pandas import Series, DataFrame

from ..utils import map_pipeline_names, get_columns_by_role
from ...dataset.base import LAMLDataset
from ...dataset.np_pd_dataset import PandasDataset, NumpyDataset
from ...dataset.roles import ColumnRole, NumericRole
from ...transformers.base import LAMLTransformer, SequentialTransformer, ColumnsSelector, ConvertDataset, ChangeRoles
from ...transformers.categorical import MultiClassTargetEncoder, TargetEncoder, CatIntersectstions, FreqEncoder, \
    LabelEncoder, \
    OrdinalEncoder
from ...transformers.datetime import BaseDiff, DateSeasons
from ...transformers.numeric import QuantileBinning

NumpyOrPandas = Union[PandasDataset, NumpyDataset]


@record_history()
class FeaturesPipeline:
    """
    Abstract class.
    Analyze train dataset and create composite transformer based on subset of features.
    """

    # TODO: visualize pipeline ?
    @property
    def input_features(self) -> List[str]:
        """
        Names of input features of train data.
        """
        return self._input_features

    @input_features.setter
    def input_features(self, val: List[str]):
        """
        Setter for input_features.

        Args:
            val: list of str.

        Returns:

        """
        self._input_features = copy(val)

    @property
    def output_features(self) -> List[str]:
        """
        List of feature names that produces _pipeline.
        """
        return self._pipeline.features

    @property
    def used_features(self) -> List[str]:
        """
        List of feature names from original dataset \
            that was used to produce output.
        """
        mapped = map_pipeline_names(self.input_features, self.output_features)
        return list(set(mapped))

    def create_pipeline(self, train: LAMLDataset) -> LAMLTransformer:
        """
        Analyse dataset and create composite transformer.

        Args:
            train: LAMLDataset with train data.

        Returns:
            LAMLTransformer - composite transformer (pipeline).
        """
        raise NotImplementedError

    def fit_transform(self, train: LAMLDataset) -> LAMLDataset:
        """
        Create pipeline and then fit on train data and then transform.

        Args:
            train: LAMLDataset with train data.

        Returns:
            LAMLDataset - dataset with new features
        """
        # TODO: Think about input/output features attributes
        self._input_features = train.features
        self._pipeline = self.create_pipeline(train)
        return self._pipeline.fit_transform(train)

    def transform(self, test: LAMLDataset) -> LAMLDataset:
        """
        Apply created pipeline to new data.

        Args:
            test: LAMLDataset with new data.

        Returns:
            LAMLDataset - dataset with new features
        """
        return self._pipeline.transform(test)


@record_history()
class EmptyFeaturePipeline(FeaturesPipeline):

    def create_pipeline(self, train: LAMLDataset) -> LAMLTransformer:
        """
        Create empty pipeline.

        Args:
            train: LAMLDataset with train data.

        Returns:
            composite transformer (pipeline) that do nothing.
        """
        return LAMLTransformer()


@record_history()
class TabularDataFeatures:
    """
    Class contains basic features transformations for tabular data

    """

    @staticmethod
    def get_cols_for_datetime(train: NumpyOrPandas) -> Tuple[List[str], List[str]]:
        """
        Get datetime columns to calculate features

        Args:
            train:

        Returns:

        """
        base_dates = get_columns_by_role(train, 'Datetime', base_date=True)
        datetimes = (get_columns_by_role(train, 'Datetime', base_date=False) +
                     get_columns_by_role(train, 'Datetime', base_date=True, base_feats=True))

        return base_dates, datetimes

    def get_datetime_diffs(self, train: NumpyOrPandas) -> Optional[LAMLTransformer]:
        """
        Difference for all datetimes with base date

        Args:
            train:

        Returns:

        """
        base_dates, datetimes = self.get_cols_for_datetime(train)
        if len(datetimes) == 0 or len(base_dates) == 0:
            return

        dt_processing = SequentialTransformer([

            ColumnsSelector(keys=datetimes + base_dates),
            BaseDiff(base_names=base_dates, diff_names=datetimes),

        ])
        return dt_processing

    def get_datetime_seasons(self, train: NumpyOrPandas, outp_role: Optional[ColumnRole] = None) -> Optional[LAMLTransformer]:
        """
        Get season params from dates

        Args:
            train:
            outp_role:

        Returns:

        """
        _, datetimes = self.get_cols_for_datetime(train)
        for col in copy(datetimes):
            if len(train.roles[col].seasonality) == 0 and train.roles[col].country is None:
                datetimes.remove(col)

        if len(datetimes) == 0:
            return

        if outp_role is None:
            outp_role = NumericRole(np.float32)

        date_as_cat = SequentialTransformer([

            ColumnsSelector(keys=datetimes),
            DateSeasons(outp_role),

        ])
        return date_as_cat

    @staticmethod
    def get_numeric_data(train: NumpyOrPandas, feats_to_select: Optional[List[str]] = None,
                         prob: Optional[bool] = None) -> Optional[LAMLTransformer]:
        """
        Select numeric features

        Args:
            train:
            feats_to_select:
            prob:

        Returns:

        """
        if feats_to_select is None:
            if prob is None:
                feats_to_select = get_columns_by_role(train, 'Numeric')
            else:
                feats_to_select = get_columns_by_role(train, 'Numeric', prob=prob)

        if len(feats_to_select) == 0:
            return

        num_processing = SequentialTransformer([

            ColumnsSelector(keys=feats_to_select),
            ConvertDataset(dataset_type=NumpyDataset),
            ChangeRoles(NumericRole(np.float32)),

        ])

        return num_processing

    @staticmethod
    def get_freq_encoding(train: NumpyOrPandas, feats_to_select: Optional[List[str]] = None) -> Optional[LAMLTransformer]:
        """
        Get frequency encoding part

        Args:
            train:
            feats_to_select:

        Returns:

        """
        if feats_to_select is None:
            feats_to_select = get_columns_by_role(train, 'Category', encoding_type='freq')

        if len(feats_to_select) == 0:
            return

        cat_processing = SequentialTransformer([

            ColumnsSelector(keys=feats_to_select),
            FreqEncoder(),

        ])
        return cat_processing

    def get_ordinal_encoding(self, train: NumpyOrPandas, feats_to_select: Optional[List[str]] = None
                             ) -> Optional[LAMLTransformer]:
        """
        Get order encoded part

        Args:
            train:
            feats_to_select:

        Returns:

        """
        if feats_to_select is None:
            feats_to_select = get_columns_by_role(train, 'Category', ordinal=True)

        if len(feats_to_select) == 0:
            return

        cat_processing = SequentialTransformer([

            ColumnsSelector(keys=feats_to_select),
            OrdinalEncoder(subs=self.subsample, random_state=self.random_state),

        ])
        return cat_processing

    def get_categorical_raw(self, train: NumpyOrPandas, feats_to_select: Optional[List[str]] = None) -> Optional[LAMLTransformer]:
        """
        Get categories data

        Args:
            train:
            feats_to_select:

        Returns:

        """

        if feats_to_select is None:
            feats_to_select = []
            for i in ['auto', 'oof', 'int', 'ohe']:
                feats_to_select.extend(get_columns_by_role(train, 'Category', encoding_type=i))

        if len(feats_to_select) == 0:
            return

        cat_processing = [

            ColumnsSelector(keys=feats_to_select),
            LabelEncoder(subs=self.subsample, random_state=self.random_state),

        ]
        cat_processing = SequentialTransformer(cat_processing)
        return cat_processing

    def get_target_encoder(self, train: NumpyOrPandas) -> Optional[type]:
        """
        Get target encoder func for dataset

        Args:
            train:

        Returns:

        """
        target_encoder = None
        if train.folds is not None:
            if train.task.name in ['binary', 'reg']:
                target_encoder = TargetEncoder
            elif self.multiclass_te:
                target_encoder = MultiClassTargetEncoder

        return target_encoder

    def get_binned_data(self, train: NumpyOrPandas, feats_to_select: Optional[List[str]] = None) -> Optional[LAMLTransformer]:
        """
        Get encoded quantiles of numeric features

        Args:
            train:
            feats_to_select:

        Returns:

        """
        if feats_to_select is None:
            feats_to_select = get_columns_by_role(train, 'Numeric', discretization=True)

        if len(feats_to_select) == 0:
            return

        binned_processing = SequentialTransformer([

            ColumnsSelector(keys=feats_to_select),
            QuantileBinning(nbins=self.max_bin_count),

        ])
        return binned_processing

    def get_categorical_intersections(self, train: NumpyOrPandas,
                                      feats_to_select: Optional[List[str]] = None) -> Optional[LAMLTransformer]:
        """
        Get transformer that implements categorical intersections

        Args:
            train:
            feats_to_select:

        Returns:

        """

        if feats_to_select is None:

            categories = get_columns_by_role(train, 'Category')
            feats_to_select = categories

            if len(categories) <= 1:
                return

            elif len(categories) > self.top_intersections:
                feats_to_select = self.get_top_categories(train, self.top_intersections)

        elif len(feats_to_select) <= 1:
            return

        cat_processing = [

            ColumnsSelector(keys=feats_to_select),
            CatIntersectstions(subs=self.subsample, random_state=self.random_state, max_depth=self.max_intersection_depth),

        ]
        cat_processing = SequentialTransformer(cat_processing)

        return cat_processing

    def get_uniques_cnt(self, train: NumpyOrPandas, feats: List[str]) -> Series:
        """
        Get unique values cnt

        Args:
            train:
            feats:

        Returns:

        """

        uns = []
        for col in feats:
            feat = Series(train[:, col].data)
            if self.subsample is not None and self.subsample < len(feat):
                feat = feat.sample(n=int(self.subsample) if self.subsample > 1 else None,
                                   frac=self.subsample if self.subsample <= 1 else None,
                                   random_state=self.random_state)

            un = feat.value_counts(dropna=False)
            uns.append(un.shape[0])

        return Series(uns, index=feats)

    def get_top_categories(self, train: NumpyOrPandas, top_n: int = 5) -> List[str]:
        """
        Get top categories by importance
        If feature importance is not defined, or feats has same importance - sort it by unique values counts

        Args:
            train:
            top_n:

        Returns:

        """
        if self.max_intersection_depth <= 1 or self.top_intersections <= 1:
            return []

        cats = get_columns_by_role(train, 'Category')
        if len(cats) == 0:
            return []

        df = DataFrame({'importance': 0, 'cardinality': 0}, index=cats)
        # importance if defined
        if self.feats_imp is not None:
            feats_imp = Series(self.feats_imp.get_features_score()).sort_values(ascending=False)
            df['importance'] = feats_imp[feats_imp.index.isin(cats)]
            df['importance'].fillna(-np.inf)

        # check for cardinality
        df['cardinality'] = self.get_uniques_cnt(train, cats)
        # sort
        df = df.sort_values(by=['importance', 'cardinality'], ascending=[False, self.ascending_by_cardinality])
        # get top n
        top = list(df.index[:top_n])

        return top

    def __init__(self, **kwargs: Any):
        """
        Set default parameters for tabular pipeline constructor

        Args:
            *kwargs:
        """
        self.multiclass_te = False
        self.top_intersections = 5
        self.max_intersection_depth = 3
        self.subsample = 10000
        self.random_state = 42
        self.feats_imp = None
        self.ascending_by_cardinality = False

        self.max_bin_count = 10
        self.sparse_ohe = 'auto'

        for k in kwargs:
            self.__dict__[k] = kwargs[k]