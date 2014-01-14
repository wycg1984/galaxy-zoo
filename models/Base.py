import time
from sklearn import grid_search, cross_validation
from classes import train_solutions, RawImage, logger, rmse_scorer, rmse
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
        self.cv_sample = kwargs.get('cv_sample', None)
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
        classifier = self.estimator_class(**params)
        return classifier

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
        """
        if self.grid_search_parameters is not None:
            logger.info("Performing grid search")
            start_time = time.time()
            params = {
                'scoring': rmse_scorer,
                'verbose': 3,
                'refit': False,
                'n_jobs': self.n_jobs
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
                self.grid_search_x = self.train_x
                self.grid_search_y = self.train_y
            self.grid_search_estimator.fit(self.grid_search_x, self.grid_search_y)
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

    def train(self):
        start_time = time.time()
        logger.info("Fitting estimator")
        if 'n_jobs' in self.estimator.get_params().keys():
            self.estimator.set_params(n_jobs=self.n_jobs)
        self.estimator.fit(self.train_x, self.train_y)  # Train only on class 1 responses for now
        logger.info("Finished fitting model in {}".format(time.time() - start_time))

        # Get an in sample RMSE
        logger.info("Calculating in-sample RMSE")
        self.training_predict = self.estimator.predict(self.train_x)
        self.rmse = rmse(self.training_predict, self.train_y)
        return self.estimator

    def predict(self):
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
            res = self.perform_grid_search_and_cv(*args, **kwargs)
        elif method == 'cv':
            res = self.perform_cross_validation(*args, **kwargs)
        elif method == 'train':
            res = self.train()
        elif method == 'predict':
            res = self.predict()

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