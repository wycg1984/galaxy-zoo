from __future__ import division
import multiprocessing
import pprint
import time
from joblib import Parallel, delayed
import joblib
from sklearn import grid_search, cross_validation, clone
from sklearn.base import BaseEstimator, TransformerMixin
from classes import train_solutions, RawImage, logger, rmse_scorer, rmse, chunks
from constants import *
import numpy as np
import os


class BaseModel(object):
    # Filenames used to store the feature arrays used in fitting/predicting
    """
    Base model for training models.
    Rationale for having a class structure for models is so that we can:
      1) Do some standard utility things like timing
      2) Easily vary our models.  We only have to define several key methods in order to get a fully working model
      3) DRY code for testing.  Implements methods that handles standard CV, grid search, and training.

    The key methods/properties that subclasses need to define are:
        train_predictors_file: string
            Path to which the training X will be cached

        test_predictors_file: string
            Path to which the test X will be cached

        estimator_class: class
            The class that should be instantiated by get_estimator()

        estimator_defaults: dict
            The default estimator parameters.  Can be overridden at runtime.

        process_image(img): staticmethod
            The function that is used to process each image and generate the features.  Must decorate with @staticmethod

    Parameters
    ---------
    estimator_params: dict
        Runtime override of the default estimator parameters.

    grid_search_parameters : dict
        See Sklearn documentation for details.

    grid_search_sample: float, between 0 and 1
        The percentage of the full training set that should be used when grid searching

    cv_folds: int
        The number of folds that should be used in cross validation

    cv_sample: float, between 0 and 1
        The percentage of the full training set that should be used when cross validating

    n_jobs: int
        Controls parallelization.  Basically same as n_jobs in the Sklearn API

    Routines
    ---------------
    The main entry point for performing operations is run().  Run's first argument must be a string that is one of the following

    grid_search:
        Performs grid search with sklearn's GridSearchCV.

        If grid_search_sample is set, then the training set is downsampled before feeding into the grid search.  The grid search
        set is saved to grid_search_x and grid_search_y, while the holdout is saved to grid_search_x_test and grid_search_y_test.

        *args and **kwargs passed to run are passed to instantiating GridSearchCV

    cv:
        Performs 2-fold cross validation by default (to preserve ratios of train/test sample sizes).

        If cv_sample is set, then the training set is downsampled before performing cv.  CV set is then saved to cv_x and cv_y,
        while the holdout is saved to cv_x_test and cv_y_test

        You can override the number of folds by setting self.cv_folds.  The KFold CV iterator can also be overriden by
        setting self.cv_class

        *args and **kwargs passed to run are passed to the cross_val_score function

    train:
        Fits the estimator on the full training set and prints an in-sample RMSE

        Does not take any additional arguments

    predict:
        Predicts on the test set.  Does not take any additional arguments


    """
    # This is so that we don't have to iterate over all 70k images every time we fit.
    train_predictors_file = None
    test_predictors_file = None
    # Number of features that the model will generate
    n_features = None
    estimator_defaults = None
    estimator_class = None
    grid_search_class = grid_search.GridSearchCV
    cv_class = cross_validation.KFold

    def __init__(self, *args, **kwargs):
        # Prime some parameters that will be defined later
        self.train_x = None
        self.test_x = None
        self.grid_search_estimator = None
        self.rmse = None

        self.estimator_params = kwargs.get('estimator_params', {})
        # Parameters for the grid search
        self.grid_search_parameters = kwargs.get('grid_search_parameters', None)
        # Sample to use for the grid search.  Should be between 0 and 1
        self.grid_search_sample = kwargs.get('grid_search_sample', None)
        # Parameters for CV
        self.cv_folds = kwargs.get('cv_folds', 2)
        self.cv_sample = kwargs.get('cv_sample', 0.5)
        # Parallelization
        self.n_jobs = kwargs.get('n_jobs', 1)
        # Preload data
        self.train_y = train_solutions.data
        self.estimator = self.get_estimator()

    def do_for_each_image(self, files, func, n_features, training):
        """
        Function that iterates over a list of files, applying func to the image indicated by that function.
        Returns an (n_samples, n_features) ndarray
        """
        dims = (N_TRAIN if training else N_TEST, n_features)
        predictors = np.zeros(dims)
        counter = 0
        for row, f in enumerate(files):
            filepath = TRAIN_IMAGE_PATH if training else TEST_IMAGE_PATH
            image = RawImage(os.path.join(filepath, f))
            predictors[row] = func(image)
            counter += 1
            if counter % 1000 == 0:
                logger.info("Processed {} images".format(counter))
        return predictors

    def get_estimator(self):
        params = self.estimator_defaults.copy()
        params.update(self.estimator_params)
        estimator = self.estimator_class(**params)
        return estimator

    def build_features(self, files, training=True):
        """
        Utility method that loops over every image and applies self.process_image
        Returns a numpy array of dimensions (n_observations, n_features)
        """
        logger.info("Building predictors")
        predictors = self.do_for_each_image(files, self.process_image, self.n_features, training)
        return predictors

    def build_train_predictors(self):
        """
        Builds the training predictors.  Once the predictors are built, they are cached to a file.
        If the file already exists, the predictors are loaded from file.
        Couldn't use the @cache_to_file decorator because the decorator factory doesn't have access to self at compilation

        Returns:
            None
        """
        if self.train_x is None:
            file_list = train_solutions.filenames
            if os.path.exists(self.train_predictors_file):
                logger.info("Training predictors already exists, loading from file {}".format(self.train_predictors_file))
                res = np.load(self.train_predictors_file)
            else:
                res = self.build_features(file_list, True)
                logger.info("Caching training predictors to {}".format(self.train_predictors_file))
                np.save(self.train_predictors_file, res)
            self.train_x = res

    def build_test_predictors(self):
        """
        Builds the test predictors

        Returns:
            None
        """
        if self.test_x is None:
            test_files = sorted(os.listdir(TEST_IMAGE_PATH))
            if os.path.exists(self.test_predictors_file):
                logger.info("Test predictors already exists, loading from file {}".format(self.test_predictors_file))
                res = np.load(self.test_predictors_file)
            else:
                res = self.build_features(test_files, False)
                logger.info("Caching test predictors to {}".format(self.test_predictors_file))
                np.save(self.test_predictors_file, res)
            self.test_x = res

    def perform_grid_search_and_cv(self, *args, **kwargs):
        """
        Performs cross validation and grid search to identify optimal parameters and to score the estimator
        The grid search space is defined by self.grid_search_parameters.

        If grid_search_sample is defined, then a downsample of the full train_x is used to perform the grid search

        Cross validation is parallelized at the CV level, not the estimator level, because not all estimators
        can be parallelized.

        Parameters:
        ----------
        refit: boolean, default True
            If true, the grid search estimator is refit on the grid search set, and then is used to calculate a score
            on the holdout set.

            Really only useful if grid_search_sample < 1, otherwise the calculated score will basically be an in-sample
            error (since the training and the testing were the same dataset)

        grid_search_parameters: set on model instantiation
            The grid search parameters -- should set this when you instantiate the Model, not when you call run('grid_search')
        """
        if self.grid_search_parameters is not None:
            logger.info("Performing grid search")
            start_time = time.time()
            params = {
                'scoring': rmse_scorer,
                'verbose': 3,
                'refit':  True,
                'n_jobs': self.n_jobs,
                'cv': 2
            }
            params.update(kwargs)
            # Make sure to not parallelize the estimator if it can be parallelized
            if 'n_jobs' in self.estimator.get_params().keys():
                self.estimator.set_params(n_jobs=1)

            self.grid_search_estimator = self.grid_search_class(self.estimator,
                                                                self.grid_search_parameters,
                                                                *args, **params)
            if self.grid_search_sample is not None:
                logger.info("Using {} of the train set for grid search".format(self.grid_search_sample))
                # Downsample if a sampling rate is defined
                self.grid_search_x, \
                self.grid_search_x_test, \
                self.grid_search_y, \
                self.grid_search_y_test = cross_validation.train_test_split(self.train_x,
                                                                            self.train_y,
                                                                            train_size=self.grid_search_sample)
            else:
                logger.info("Using full train set for the grid search")
                # Otherwise use the full set
                self.grid_search_x = self.grid_search_x_test = self.train_x
                self.grid_search_y = self.grid_search_y_test = self.train_y
            self.grid_search_estimator.fit(self.grid_search_x, self.grid_search_y)
            logger.info("Found best parameters:")
            logger.info(self.grid_search_estimator.best_params_)

            if params['refit']:
                logger.info("Predicting on holdout set")
                pred = self.grid_search_estimator.predict(self.grid_search_x_test)
                res = rmse(self.grid_search_y_test, pred)
                logger.info("RMSE on holdout set: {}".format(res))

            logger.info("Grid search completed in {}".format(time.time() - start_time))

    def perform_cross_validation(self, *args, **kwargs):
        """
        Performs cross validation using the main estimator.  In some cases, when we don't need to search
        across a grid of hyperparameters, we may want to perform cross validation only.
        """
        start_time = time.time()
        if self.cv_sample is not None:
            logger.info("Performing {}-fold cross validation with {:.0%} of the sample".format(self.cv_folds, self.cv_sample))
            self.cv_x,\
            self.cv_x_test,\
            self.cv_y,\
            self.cv_y_test = cross_validation.train_test_split(self.train_x, self.train_y, train_size=self.cv_sample)
        else:
            logger.info("Performing {}-fold cross validation with full training set".format(self.cv_folds))
            self.cv_x = self.train_x
            self.cv_y = self.train_y
        self.cv_iterator = self.cv_class(self.cv_x.shape[0], n_folds=self.cv_folds)
        params = {
            'cv': self.cv_iterator,
            'scoring': rmse_scorer,
            'verbose': 2,
            'n_jobs': self.n_jobs
        }
        params.update(kwargs)
        # Make sure to not parallelize the estimator
        if 'n_jobs' in self.estimator.get_params().keys():
            self.estimator.set_params(n_jobs=1)
        self.cv_scores = cross_validation.cross_val_score(self.estimator,
                                                          self.cv_x,
                                                          self.cv_y,
                                                          *args, **params)
        logger.info("Cross validation completed in {}.  Scores:".format(time.time() - start_time))
        logger.info("{}".format(self.cv_scores))

    def train(self, *args, **kwargs):
        start_time = time.time()
        logger.info("Fitting estimator")
        if 'n_jobs' in self.estimator.get_params().keys():
            self.estimator.set_params(n_jobs=self.n_jobs)
        self.estimator.fit(self.train_x, self.train_y)
        logger.info("Finished fitting model in {}".format(time.time() - start_time))

        # Get an in sample RMSE
        logger.info("Calculating in-sample RMSE")
        self.training_predict = self.estimator.predict(self.train_x)
        self.rmse = rmse(self.training_predict, self.train_y)
        return self.estimator

    def predict(self, *args, **kwargs):
        self.build_test_predictors()
        if 'n_jobs' in self.estimator.get_params().keys():
            self.estimator.set_params(n_jobs=self.n_jobs)
        self.test_y = self.estimator.predict(self.test_x)
        return self.test_y

    def run(self, method, *args, **kwargs):
        """
        Primary entry point for executing tasks with the model

        Arguments:
        ----------
        method: string
            Must be one of 'grid_search', 'cv', 'train', or 'predict'

        *args:
            Additional arguments to be passed to the job

        **kwargs:
            Additional arguments to be passed to the job

        """

        jobs = {'grid_search', 'cv', 'train', 'predict'}

        if method not in jobs:
            raise RuntimeError("{} is not a valid job".format(method))

        start_time = time.time()
        self.build_train_predictors()
        res = None

        if method == 'grid_search':
            logger.info("Performing grid search")
            res = self.perform_grid_search_and_cv(*args, **kwargs)
        elif method == 'cv':
            logger.info("Performing cross validation")
            res = self.perform_cross_validation(*args, **kwargs)
        elif method == 'train':
            logger.info("Performing training")
            res = self.train(*args, **kwargs)
        elif method == 'predict':
            logger.info("Performing prediction")
            res = self.predict(*args, **kwargs)

        end_time = time.time()
        logger.info("Model completed in {}".format(end_time - start_time))
        return res

    @staticmethod
    def process_image(img):
        """
        A function that takes a RawImage object and returns a (1, n_features) numpy array
        Subclasses should implement this method
        """
        raise NotImplementedError("Subclasses of BaseModel should implement process_image")


class KMeansModel(BaseModel):
    """
    Borrows from BaseModel, but doesn't build the train or test predictors

    Intended for use with feature generator models
    """
    def __init__(self, feature_generator, train_source, test_source, *args, **kwargs):
        self.feature_generator = feature_generator
        self.train_source = train_source
        self.test_source = test_source
        super(KMeansModel, self).__init__(*args, **kwargs)

    def build_features(self, files, training=True, **kwargs):
        if training:
            return self.feature_generator.transform(self.train_source)
        else:
            return self.feature_generator.transform(self.test_source)


class CascadeModel(BaseModel):
    """
    A variant of the BaseModel that trains each class in sequence, then uses the predictions from prior classes as inputs
    into the models for later classes.

    Some additional things that need to be done to make this model better:
        - Follow the structure of the tree instead of just going from classes 1 to 11
    """
    def __init__(self, *args, **kwargs):
        super(CascadeModel, self).__init__(*args, **kwargs)
        # Storage for each estimator.  key is the class number, and the value is the estimator object
        self.estimator = dict((cls, self.get_estimator()) for cls in train_solutions.class_map.keys())
        # Should each class be scaled to 100% before training/predicting?
        self.scaled = kwargs.get('scaled', False)
        if self.scaled:
            # Replace train_y with the scaled version, then later when we predict, we have to be sure to multiply the
            # predictions by the scale factor for each row
            self.train_y = train_solutions.get_rebased_columns_for_class()

    def perform_cross_validation(self, *args, **kwargs):
        start_time = time.time()
        if self.cv_sample is not None:
            logger.info("Performing {}-fold cross validation with {:.0%} of the sample".format(self.cv_folds, self.cv_sample))
            self.cv_x,\
            self.cv_x_test,\
            self.cv_y,\
            self.cv_y_test = cross_validation.train_test_split(self.train_x, self.train_y, train_size=self.cv_sample)
        else:
            logger.info("Performing {}-fold cross validation with full training set".format(self.cv_folds))
            self.cv_x = self.train_x
            self.cv_y = self.train_y

        self.cv_iterator = self.cv_class(self.cv_x.shape[0], n_folds=self.cv_folds)

        params = {
            'cv': self.cv_iterator,
            'scoring': rmse_scorer,
            'verbose': 2,
            'n_jobs': self.n_jobs
        }
        params.update(kwargs)

        # Gotta roll our own cross validation
        # Cross validation will look like this:
        # For each fold:
        #   train estimator
        #   Predict estimator
        #   Store prediction
        #   Move onto next estimator

        overall_scores = []
        detailed_scores = [{}] * self.cv_folds
        for i, idx in enumerate(self.cv_iterator):
            logger.debug("Working on fold {}".format(i + 1))
            train = idx[0]
            test = idx[1]

            # Get the data
            # The actual cross val method uses safe_mask to index the arrays.  This is only required if
            # we might be handling sparse matrices
            this_train_x = self.cv_x[train]
            this_train_y = self.cv_y[train]
            this_test_x = self.cv_x[test]
            this_test_y = self.cv_y[test]

            logger.debug("Fold {} training X and Y shape: {}, {}".format(i + 1, this_train_x.shape, this_train_y.shape))
            logger.debug("Fold {} test X and Y shape: {}, {}".format(i + 1, this_test_x.shape, this_test_y.shape))

            test_preds = np.zeros(this_test_y.shape)
            train_preds = np.zeros(this_train_y.shape)

            # Should be able to refactor out this inner loop
            for cls in range(1, 12):
                cols = train_solutions.class_map[cls]

                logger.info("Performing CV on class {}".format(cls))

                # Clone the estimator
                # Need to do this for each fold
                estimator = clone(self.estimator[cls])

                existing_test_preds = np.any(test_preds, axis=0)
                existing_train_preds = np.any(train_preds, axis=0)
                this_x = np.hstack((this_train_x, train_preds[:, existing_train_preds]))
                test_x = np.hstack((this_test_x, test_preds[:, existing_test_preds]))
                this_y = this_train_y[:, cols]
                test_y = this_test_y[:, cols]

                logger.debug("Train X shape: {}".format(this_x.shape))
                logger.debug("Train Y shape: {}".format(this_y.shape))
                logger.debug("Test X shape: {}".format(test_x.shape))

                # Parallelize at the estimator level
                if 'n_jobs' in estimator.get_params().keys():
                    estimator.set_params(n_jobs=self.n_jobs)
                estimator.fit(this_x, this_y)

                train_pred = estimator.predict(this_x)
                test_pred = estimator.predict(test_x)

                # Scale things back
                if self.scaled:
                    # this does not work correctly because cv_y is already split
                    scale_factors = train_solutions.get_sum_for_class(cls)

                    assert train.shape[0] == scale_factors[0]
                    assert test.shape[0] == scale_factors[0]

                    train_scale_factors = scale_factors[train]
                    test_scale_factors = scale_factors[test]

                    assert train_scale_factors.shape[0] == train_pred.shape[0]
                    assert test_scale_factors.shape[0] == test_pred.shape[0]

                    train_pred = np.multiply(train_pred, train_scale_factors)
                    test_pred = np.multiply(test_pred, test_scale_factors)
                    test_y = np.multiply(test_y, test_scale_factors)

                score = rmse(test_y, test_pred)
                detailed_scores[i][cls] = score
                logger.info("RMSE on test set for class {}: {}".format(cls, score))

                train_preds[:, cols] = train_pred
                test_preds[:, cols] = test_pred

            if self.scaled:
                pass
            else:
                fold_rmse = rmse(this_test_y, test_preds)

            overall_scores.append(fold_rmse)
            logger.info("Overall score for fold {}: {}".format(i + 1, fold_rmse))

        self.cv_scores = np.array(overall_scores)
        logger.info("Cross validation completed in {}.  Scores:".format(time.time() - start_time))
        logger.info(detailed_scores)
        logger.info("Overall scores:")
        logger.info(overall_scores)

    def train(self, *args, **kwargs):
        start_time = time.time()
        logger.info("Fitting estimator")
        preds = np.zeros(self.train_y.shape)
        # This currently just goes from 1 to 11, but the tree doesn't actually progress in that order.
        # Maybe experiment with a more fine-grained control over which predictions get passed in
        for cls in range(1, 12):
            cols = train_solutions.class_map[cls]

            # Select the correct estimator, and get the right subsets of the data to use in training
            logger.info("Fitting estimator for class {}".format(cls))
            estimator = self.estimator[cls]

            existing_preds = np.any(preds, axis=0)  # Boolean array of which columns are populated in preds
            # X is concatenated with any predictions that have already been made
            logger.debug("Adding columns {} of predictions to X".format(np.where(existing_preds)[0]))
            this_x = np.hstack((self.train_x, preds[:, existing_preds]))
            this_y = self.train_y[:, cols]
            logger.debug("X is of shape {}".format(this_x.shape))
            logger.debug("Y is of shape {}".format(this_y.shape))

            # Train the current estimator
            if 'n_jobs' in estimator.get_params().keys():
                estimator.set_params(n_jobs=self.n_jobs)
            estimator.fit(this_x, this_y)

            # Make predictions with the current estimator, and store those predictions
            logger.info("Making predictions for class {}".format(cls))
            y_pred = estimator.predict(this_x)
            logger.debug("Ypred is of shape {}".format(this_y.shape))
            logger.info("RMSE of class {} is {}".format(cls, rmse(y_pred, this_y)))
            preds[:, cols] = y_pred

        logger.info("Finished fitting model in {}".format(time.time() - start_time))

        # Get an in sample RMSE
        logger.info("Calculating overall in-sample RMSE")
        self.training_predict = preds
        self.rmse = rmse(self.training_predict, self.train_y)
        return self.estimator

    def predict(self, *args, **kwargs):
        """
        TO BE IMPLEMENTED

        self.build_test_predictors()
        if 'n_jobs' in self.estimator.get_params().keys():
            self.estimator.set_params(n_jobs=self.n_jobs)
        self.test_y = self.estimator.predict(self.test_x)
        return self.test_y
        """


class CropScaleImageTransformer(BaseEstimator, TransformerMixin):
    """
    Processes the training or test JPGs by cropping then scaling.  Saves the resulting ndarray to a file.  If the file exists when transform() is run
    Then it loads the results from the file instead of running again.

    Executes crop first, then scale

    Arguments:
    ----------
    crop_size: integer
        Pixel length to which to crop

    scaled_size: integer
        Pixel lenggh to scale
    """
    def __init__(self, training, crop_size, scaled_size, result_path=None, n_jobs=1, force_rerun=False, verbose=3, memmap=False):
        self.training = training
        self.crop_size = crop_size
        self.scaled_size = scaled_size
        self.verbose = verbose
        self.n_jobs = (multiprocessing.cpu_count() + n_jobs + 1) if n_jobs <= -1 else n_jobs
        self.force_rerun = force_rerun
        self.memmap = memmap
        self.result_path = result_path or self._get_result_path()

    def _get_result_path(self):
        if self.training:
            return 'data/img_train_c{}_s{}.npy'.format(self.crop_size, self.scaled_size)
        else:
            return 'data/img_test_c{}_s{}.npy'.format(self.crop_size, self.scaled_size)

    def fit(self, X=None, y=None):
        return self

    def _transform(self, file_list):
        filepath = TRAIN_IMAGE_PATH if self.training else TEST_IMAGE_PATH
        out = np.zeros((len(file_list), self.scaled_size, self.scaled_size, 3))
        factor = self.scaled_size / self.crop_size

        for i, f in enumerate(file_list):
            if i % 5000 == 0:
                logger.info("Processing image {} of {}".format(i, len(file_list)))
            img = RawImage(os.path.join(filepath, f))
            img.crop(self.crop_size).rescale(factor)
            out[i] = img.data * 255
        return out

    def transform(self, X=None):
        if self.training:
            files = train_solutions.filenames
        else:
            files = sorted(os.listdir(TEST_IMAGE_PATH))

        if os.path.exists(self.result_path) and not self.force_rerun:
            logger.info("File already exists.  Loading from {}".format(self.result_path))
            if self.memmap:
                return joblib.load(self.result_path, mmap_mode='r+')
            else:
                return joblib.load(self.result_path)
        else:
            res = np.vstack(Parallel(n_jobs=self.n_jobs, verbose=self.verbose)(
                delayed(_parallel_crop_scale)(self, files) for files in chunks(files, self.n_jobs)
            ))
            logger.info("Saving results to file {}".format(self.result_path))
            joblib.dump(res, self.result_path)
            if self.memmap:
                res = joblib.load(self.result_path, mmap_mode='r+')
            return res


class SampleTransformer(BaseEstimator, TransformerMixin):
    """
    Pixel sampling
    """
    def __init__(self, training, steps, step_size, n_jobs=1, verbose=3, force_rerun=False, memmap=False):
        self.training = training
        self.steps = steps
        self.step_size = step_size
        self.n_jobs = (multiprocessing.cpu_count() + n_jobs + 1) if n_jobs <= -1 else n_jobs
        self.verbose = verbose
        self.result_path = self._get_result_path()
        self.force_rerun = force_rerun
        self.memmap = memmap

    def _get_result_path(self):
        if self.training:
            return 'data/img_train_pixel_size{}_steps{}.npy'.format(self.steps, self.step_size)
        else:
            return 'data/img_test_pixel_size{}_steps{}.npy'.format(self.steps, self.step_size)

    def transform(self, X=None):
        if self.training:
            files = train_solutions.filenames
        else:
            files = sorted(os.listdir(TEST_IMAGE_PATH))

        if os.path.exists(self.result_path) and not self.force_rerun:
            logger.info("File already exists.  Loading from {}".format(self.result_path))
            if self.memmap:
                return joblib.load(self.result_path, mmap_mode='r+')
            else:
                return joblib.load(self.result_path)
        else:
            logger.info("Sampling pixels from image, {} steps of size {}".format(self.steps, self.step_size))
            res = np.vstack(Parallel(n_jobs=self.n_jobs, verbose=self.verbose)(
                delayed(_parallel_sampler)(files, self.steps, self.step_size, self.training) for files in chunks(files, self.n_jobs)
            ))
            logger.info("Saving results to file {}".format(self.result_path))
            joblib.dump(res, self.result_path)
            if self.memmap:
                res = joblib.load(self.result_path, mmap_mode='r+')
            return res


def _parallel_sampler(file_list, steps, step_size, training):
    filepath = TRAIN_IMAGE_PATH if training else TEST_IMAGE_PATH
    rows = []
    counter = 0
    for i, f in enumerate(file_list):
        counter += 1
        if counter % 5000 == 0:
            logger.info("Processed {} images".format(counter))
        image = RawImage(os.path.join(filepath, f))
        rows.append(image.grid_sample(step_size, steps).flatten().astype('float64') / 255)
    return np.vstack(rows)


def _parallel_crop_scale(transformer, file_list):
    return transformer._transform(file_list)


class ModelWrapper(object):
    """
    BaseModel was too complicated, so this is just a simple wrapper around an estimator and a data source
    """
    def __init__(self, estimator_class, estimator_defaults=None, n_jobs=1):
        self.estimator_class = estimator_class
        self.estimator_defaults = estimator_defaults
        self.n_jobs = (multiprocessing.cpu_count() + n_jobs + 1) if n_jobs <= -1 else n_jobs

    def get_estimator(self, **kwargs):
        params = self.estimator_defaults.copy()
        params.update(**kwargs)
        return self.estimator_class(**params)

    def grid_search(self, X, y, grid_search_params, grid_search_class=None, sample=None, n_folds=2, refit=True, parallel_estimator=False):
        cls = grid_search_class or grid_search.GridSearchCV
        logger.info("Performing grid search")
        start_time = time.time()
        params = {
            'scoring': rmse_scorer,
            'verbose': 3,
            'refit':  refit,
            'n_jobs': self.n_jobs,
            'cv': n_folds
        }
        estimator = self.get_estimator()

        if 'n_jobs' in estimator.get_params().keys():
            # If the estimator can be parallelized, and parallel_estimator is True, then parallelize at that level
            # otherwise parallelize at the grid search level
            if parallel_estimator:
                estimator.set_params(n_jobs=self.n_jobs)
                params['n_jobs'] = 1
            else:
                estimator.set_params(n_jobs=1)

        self.grid_search_estimator = cls(estimator, grid_search_params, **params)

        if sample is not None:
            logger.info("Using {} of the train set for grid search".format(sample))
            # Downsample if a sampling rate is defined
            self.grid_search_x, \
            self.grid_search_x_test, \
            self.grid_search_y, \
            self.grid_search_y_test = cross_validation.train_test_split(X,
                                                                        y,
                                                                        train_size=sample)
        else:
            logger.info("Using full train set for the grid search")
            # Otherwise use the full set
            self.grid_search_x = self.grid_search_x_test = X
            self.grid_search_y = self.grid_search_y_test = y

        self.grid_search_estimator.fit(self.grid_search_x, self.grid_search_y)
        logger.info("Found best parameters:")
        logger.info(self.grid_search_estimator.best_params_)
        logger.info("All results:")
        logger.info(pprint.pformat(self.grid_search_estimator.grid_scores_))

        if params['refit']:
            logger.info("Predicting on holdout set")
            pred = self.grid_search_estimator.predict(self.grid_search_x_test)
            res = rmse(self.grid_search_y_test, pred)
            logger.info("RMSE on holdout set: {}".format(res))

        logger.info("Grid search completed in {}".format(time.time() - start_time))

    def cross_validation(self, X, y, n_folds=2, cv_class=None, sample=None, parallel_estimator=False):
        cls = cv_class or cross_validation.KFold

        start_time = time.time()
        if sample is not None:
            logger.info("Performing {}-fold cross validation with {:.0%} of the sample".format(n_folds, sample))
            self.cv_x,\
            _,\
            self.cv_y,\
            _ = cross_validation.train_test_split(X, y, train_size=sample)
            del _
        else:
            logger.info("Performing {}-fold cross validation with full training set".format(n_folds))
            self.cv_x = X
            self.cv_y = y
        self.cv_iterator = cls(self.cv_x.shape[0], n_folds=n_folds)

        params = {
            'cv': self.cv_iterator,
            'scoring': rmse_scorer,
            'verbose': 3,
            'n_jobs': self.n_jobs
        }

        estimator = self.get_estimator()
        # Make sure to not parallelize the estimator
        if 'n_jobs' in estimator.get_params().keys():
            if parallel_estimator:
                estimator.set_params(n_jobs=self.n_jobs)
                params['n_jobs'] = 1
            else:
                estimator.set_params(n_jobs=1)

        self.cv_scores = cross_validation.cross_val_score(estimator, self.cv_x, self.cv_y, **params)
        logger.info("Cross validation completed in {}.  Scores:".format(time.time() - start_time))
        logger.info("{}".format(self.cv_scores))

    def fit(self, X, y=None):
        start_time = time.time()
        logger.info("Fitting estimator")
        self.estimator_ = self.get_estimator()
        if 'n_jobs' in self.estimator_.get_params().keys():
            self.estimator_.set_params(n_jobs=self.n_jobs)

        self.estimator_.fit(X, y)
        logger.info("Finished fitting model in {}".format(time.time() - start_time))

        # Get an in sample RMSE
        logger.info("Calculating in-sample RMSE")
        self.training_predict = self.estimator_.predict(X)
        self.rmse = rmse(self.training_predict, y)

    def predict(self, X):
        if not hasattr(self, 'estimator_'):
            raise RuntimeError("Estimator has not been trained")

        logger.info("Predicting")
        return self.estimator_.predict(X)
