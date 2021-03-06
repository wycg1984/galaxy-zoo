"""
Run scripts for individual models in Galaxy Zoo
"""
import multiprocessing
import os
import time
import gc
from sklearn import cross_validation
from sklearn.ensemble import RandomForestRegressor

import classes
import numpy as np
import logging
from constants import *
import models
from sklearn.cross_validation import KFold, train_test_split
from IPython import embed
import cPickle as pickle
import time
from models.Base import CropScaleImageTransformer, ModelWrapper, SampleTransformer
from models.KMeansFeatures import KMeansFeatureGenerator


logger = logging.getLogger('galaxy')


def train_set_average_benchmark(outfile="sub_average_benchmark_000.csv"):
    """
    What should be the actual baseline.  Takes the training set solutions, averages them, and uses that as the
    submission for every row in the test set
    """
    start_time = time.time()
    training_data = classes.TrainSolutions().data

    solutions = np.mean(training_data, axis=0)

    # Calculate an RMSE
    train_solution = np.tile(solutions, (N_TRAIN, 1))
    rmse = classes.rmse(train_solution, training_data)

    solution = classes.Submission(np.tile(solutions, (N_TEST, 1)))
    solution.to_file(outfile)

    end_time = time.time()
    logger.info("Model completed in {}".format(end_time - start_time))


def central_pixel_benchmark(outfile="sub_central_pixel_001.csv"):
    """
    Tries to duplicate the central pixel benchmark, which is defined as:
    Simple benchmark that clusters training galaxies according to the color in the center of the image
    and then assigns the associated probability values to like-colored images in the test set.
    """

    test_averages = models.Benchmarks.CentralPixelBenchmark().execute()
    predictions = classes.Submission(test_averages)
    # Write to file
    predictions.to_file(outfile)


def random_forest_001(outfile="sub_random_forest_001.csv", n_jobs=1):
    """
    Uses a sample of central pixels in RGB space to feed in as inputs to the neural network

    # 3-fold CV using half the training set reports RMSE of .126 or so
    """
    model = models.RandomForest.RandomForestModel(n_jobs=n_jobs)
    model.run('train')
    predictions = model.run('predict')
    output = classes.Submission(predictions)
    output.to_file(outfile)


def random_forest_002(outfile="sub_random_forest_002.csv", n_jobs=4):
    """
    Random forest, but with all pixels in a 150x150 crop then rescaled to 15x15 instead of grid sampling

    CV results on 10% of the dataset with 50 trees:

    2014-01-29 16:55:38 - Base - INFO - Cross validation completed in 629.936481953.  Scores:
    2014-01-29 16:55:38 - Base - INFO - [-0.13233799 -0.13254755]
    # Not any better than the sampling
    """
    mdl = models.RandomForest.RandomForestMoreFeatures(n_jobs=n_jobs, cv_sample=0.1)
    mdl.run('cv')


def extra_trees_test(n_jobs=1):
    """
    Exact same as random_forest_001, but using ExtraTreesRegressor to see if that method is any better
    """
    # model = models.RandomForest.ExtraTreesModel()
    # model.run('cv')

    # tune the model - 15 trees already gives .13 RMSE, I think that's slightly better than RF with that number of trees
    params = {
        'n_estimators': [15, 50, 100, 250]
    }
    model = models.RandomForest.ExtraTreesModel(
        grid_search_parameters=params,
        grid_search_sample=0.5,
        n_jobs=n_jobs
    )
    model.run('grid_search', refit=True)
    # 2014-01-21 05:45:28 - Base - INFO - Found best parameters:
    # 2014-01-21 05:45:28 - Base - INFO - {'n_estimators': 250}
    # 2014-01-21 05:45:28 - Base - INFO - Predicting on holdout set
    # 2014-01-21 05:45:41 - classes - INFO - RMSE: 0.124530683233
    # 2014-01-21 05:45:41 - Base - INFO - RMSE on holdout set: 0.124530683233
    # 2014-01-21 05:45:41 - Base - INFO - Grid search completed in 8916.21896791
    # 2014-01-21 05:45:41 - Base - INFO - Model completed in 9332.45440102

    # As expected, more trees = better performance.  Seems like the performance is on par/slightly better than random forest


def random_forest_cascade_001(outfile='sub_rf_cascade_001.csv'):
    """
    Experiment to compare whether training the random forest with all Ys or training the Ys in a cascade is better

    2014-01-22 10:19:39 - Base - INFO - Cross validation completed in 7038.78176308.  Scores:
    2014-01-22 10:19:39 - Base - INFO - [ 0.13103377  0.13196983]
    """
    mdl = models.RandomForest.RandomForestCascadeModel(cv_sample=0.1)
    mdl.run('cv')

    # Unscaled classes don't seem to work better than RF.  Lets try with scaled classes
    mdl_scaled = models.RandomForest.RandomForestCascadeModel(cv_sample=0.1, scaled=True)
    mdl_scaled.run('cv')


def ridge_rf_001(outfile='sub_ridge_rf_001.csv'):
    mdl = models.Ridge.RidgeRFModel(cv_sample=0.5, cv_folds=2)
    mdl.run('cv')
    mdl.run('train')
    mdl.run('predict')
    sub = classes.Submission(mdl.test_y)
    sub.to_file(outfile)

    # Testing this with new models
    train_predictors_file = 'data/data_ridge_rf_train_001.npy'
    test_predictors_file = 'data/data_ridge_rf_test_001.npy'
    train_x = np.load(train_predictors_file)
    train_y = classes.train_solutions.data
    mdl = models.Base.ModelWrapper(models.Ridge.RidgeRFEstimator, {
        'alpha': 14,
        'n_estimators': 10,
        'verbose': 3,
        'oob_score': True
    }, n_jobs=-1)
    # mdl.cross_validation(train_x, train_y, sample=0.5, n_folds=3)
    mdl.grid_search(train_x, train_y, {
        'alpha': [1, 2],
        'n_estimators': [5]
    }, sample=0.1)

    test_x = np.load(test_predictors_file)
    mdl.fit(train_x, train_y)
    pred = mdl.predict(test_x)


def svr_rf():
    # subsample
    train_y = classes.train_solutions.data

    # randomly sample 10% Y and select the gid's
    n = 7000
    crop_size = 150
    scale = 0.1
    train_y = train_y[np.random.randint(train_y.shape[0], size=n), :]
    train_x = np.zeros((n, (crop_size * scale) ** 2 * 3))

    # load the training images and crop at the same time
    for row, gid in enumerate(train_y[:, 0]):
        img = classes.RawImage(TRAIN_IMAGE_PATH + '/' + str(int(gid)) + '.jpg')
        img.crop(crop_size)
        img.rescale(scale)
        img.flatten()
        train_x[row] = img.data
        if (row % 10) == 0:
            print row


    parameters = {'alpha': [14], 'n_estimators': [10]}
    kf = KFold(train_x.shape[0], n_folds=2, shuffle=True)

    for train, test in kf:
        ridge_rf = models.SVR.SVRRFModel()
        ridge_rf.fit(train_x[train, :], train_y[train, :])
        res = ridge_rf.predict(train_x[test, :])
        classes.rmse(train_y[test, :], res)

    # transform images

    # cv and training


def kmeans_001(fit_centroids=False):
    """
    Be sure to run classes.crop_to_mmap before using this

    This doens't work really well -- we determined that the patches are too small and aren't capturing any features.
    """
    trainX = np.memmap('data/train_cropped_150.memmap', mode='r', shape=(N_TRAIN, 150, 150, 3))
    # Not used yet
    testX = np.memmap('data/test_cropped_150.memmap', mode='r', shape=(N_TEST, 150, 150, 3))

    if fit_centroids:
        km = models.KMeansFeatures.KMeansFeatures(rf_size=6, num_centroids=1600, num_patches=400000)
        km.fit(trainX)

        km.save_to_file('mdl_kmeans_ridge_rf_001')
        # t0 = time.time()
        # pickle.dump(km, open('data/kmeans_centroids.pkl', mode='wb'))
        # print 'Pickling the KMeansFeatures object took {0} seconds'.format(time.time() - t0)
    else:
        km = models.KMeansFeatures.KMeansFeatures.load_from_file('mdl_kmeans_ridge_rf_001')
        # km = pickle.load(open('data/kmeans_centroids.pkl'))

    n = 10000

    train_x = km.transform(trainX[0:n, :])
    train_y = classes.train_solutions.data[0:n, :]
    # train_x = km.transform(trainX)
    # train_y = classes.train_solutions.data

    logger.info("Train x shape: {}".format(train_x.shape))
    logger.info("Train y shape: {}".format(train_y.shape))

    kf = KFold(n, n_folds=2, shuffle=True)

    for train, test in kf:
        # clf = models.Ridge.RidgeRFEstimator()
        # clf.rf_rgn = RandomForestRegressor(n_estimators=250, n_jobs=4, verbose=3)
        clf = RandomForestRegressor(n_estimators=20, n_jobs=4, verbose=3, random_state=0, oob_score=True)
        clf.fit(train_x[train], train_y[train])
        res = clf.predict(train_x[test])
        classes.rmse(train_y[test], res)


def kmeans_002():
    """
    Kmeans feature learning, first rescaling images down, then extracting patches, so we get more variation in each patch
    Rescaling to 15 x 15 then taking out patches of 5 x 5

    The centroids don't look like anything (splotches of color against mostly gray), but the CV score on 10000 samples and 20 trees
    was .128, which is quite promising.

    Training the kmeans then using RidgeRFEstimator got us to .107 on the leaderboard

    Broadly speaking, the pipe looks like this:

    Encoder:
    CropScaleImageTransformer -> PatchExtractorTransformer -> KMeansFeatureGenerator.fit

    Model:
    CropSCaleImageTransformer -> KMeansFeatureGenerator.transform -> RidgeRFEstimator
    """
    train_mmap_path = 'data/train_cropped_150_scale_15.memmap'
    test_mmap_path = 'data/test_cropped_150_scale_15.memmap'

    if not os.path.exists('data/train_cropped_150.memmap'):
        classes.crop_to_memmap(150, training=True)
    if not os.path.exists('data/test_cropped_150.memmap'):
        classes.crop_to_memmap(150, training=False)

    if not os.path.exists(train_mmap_path):
        logger.info("Prepping training images")
        pre_scale = np.memmap('data/train_cropped_150.memmap', mode='r', shape=(N_TRAIN, 150, 150, 3))
        trainX = classes.rescale_memmap(15, pre_scale, train_mmap_path)
        del pre_scale
    else:
        trainX = np.memmap(train_mmap_path, mode='r', shape=(N_TRAIN, 15, 15, 3))

    if not os.path.exists(test_mmap_path):
        logger.info("Prepping testing images")
        pre_scale = np.memmap('data/test_cropped_150.memmap', mode='r', shape=(N_TEST, 150, 150, 3))
        testX = classes.rescale_memmap(15, pre_scale, test_mmap_path)
        del pre_scale
    else:
        testX = np.memmap(test_mmap_path, mode='r', shape=(N_TEST, 15, 15, 3))


    n_jobs = multiprocessing.cpu_count()

    if not os.path.exists('data/mdl_kmeans_002_centroids.npy'):
        logger.info("Pretraining KMeans feature encoder")
        km = models.KMeansFeatures.KMeansFeatures(rf_size=5, num_centroids=1600, num_patches=400000)
        km.fit(trainX)
        km.save_to_file('mdl_kmeans_002')
    else:
        logger.info("Loading KMeans feature encoder from file")
        km = models.KMeansFeatures.KMeansFeatures.load_from_file('mdl_kmeans_002', rf_size=5)

    # Takes waaaay too long to finish.  At least an hour per tree.  Clearly too
    # many dimensions

    # Instead ran with ridge rf manually
    mdl = models.RandomForest.KMeansRandomForest(km, trainX, testX, n_jobs=n_jobs, cv_sample=0.5)
    # mdl.run('cv')
    mdl.run('train')
    res = mdl.run('predict')
    np.save('submissions/sub_kmeans_rf_002.npy', res)
    output = classes.Submission(res)
    output.to_file('sub_kmeans_rf_002.csv')


def kmeans_002_new():
    """
    Trying to replicate kmeans_002 with the new transformers and pipeline
    """
    train_x_crop_scale = models.Base.CropScaleImageTransformer(training=True,
                                                               result_path='data/data_train_crop_150_scale_15.npy',
                                                               crop_size=150,
                                                               scaled_size=15,
                                                               n_jobs=-1,
                                                               memmap=True)
    patch_extractor = models.KMeansFeatures.PatchSampler(n_patches=400000,
                                                         patch_size=5,
                                                         n_jobs=-1)
    images = train_x_crop_scale.transform()
    patches = patch_extractor.transform(images)

    # spherical generator
    # kmeans_generator = models.KMeansFeatures.KMeansFeatureGenerator(n_centroids=1600,
    #                                                                 rf_size=5,
    #                                                                 result_path='data/mdl_kmeans_002_new',
    #                                                                 n_iterations=20,
    #                                                                 n_jobs=-1,)

    # minibatch generator
    kmeans_generator = models.KMeansFeatures.KMeansFeatureGenerator(n_centroids=1600,
                                                                    rf_size=5,
                                                                    result_path='data/mdl_kmeans_002_new_minibatch',
                                                                    method='minibatch',
                                                                    n_init=1,
                                                                    n_jobs=-1,)


    kmeans_generator.fit(patches)

    del patches
    gc.collect()

    # Problematic here - memory usage spikes to ~ 11GB when threads return
    # train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_002_new.npy', memmap=True)
    train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_002_new_minibatch.npy', memmap=True, force_rerun=True)
    train_y = classes.train_solutions.data
    # Unload some objects
    del images
    gc.collect()
    # mdl = models.Ridge.RidgeRFEstimator(alpha=14, n_estimators=250, n_jobs=-1)
    wrapper = models.Base.ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 14, 'n_estimators': 250}, n_jobs=-1)
    # This will exceed 15GB of memory if the train_x is not memmapped and sample is < 1
    # I think this is because the wrapper object will save copies of the train_x and train_y when it splits it
    # CV of .117 and .116 on 2-fold CV of 50% sample

    # CV of .107 on full set with 5-fold CV.  Really accurate, but takes about 15 minutes to do a fold, parallellized at the estimator level
    # Also takes over 20GB of RAM, so need a larger instance to run

    # CV of .108 on full set in 3-fold, 11 minutes
    # CV of .110 on full set in 2-fold, 8 minutes
    # CV of .110 on full set in 2-fold with minibatch, n_init = 3.  In this case, minibatch is slower than spherical because of the inits (~14 min to cluster)
    # n_init = 1 takes 7 minutes, so not much faster than the spherical method, but cv score was .1102 on 2-fold, which is the best so far with this number of folds
    wrapper.cross_validation(train_x, train_y, n_folds=2, parallel_estimator=True)


def kmeans_003():
    """
    Grid search for Ridge RF parameters
    Not sure whether to use spherical or minibatch, so maybe do one run with both

    .106 on the leaderboard.  So the difference in CV scores narrowed
    """

    train_x_crop_scale = CropScaleImageTransformer(training=True,
                                                   result_path='data/data_train_crop_150_scale_15.npy',
                                                   crop_size=150,
                                                   scaled_size=15,
                                                   n_jobs=-1,
                                                   memmap=True)



    # spherical generator
    kmeans_generator = KMeansFeatureGenerator(n_centroids=1600,
                                              rf_size=5,
                                              result_path='data/mdl_kmeans_002_new',
                                              n_iterations=20,
                                              n_jobs=-1,)

    # minibatch generator
    # kmeans_generator = models.KMeansFeatures.KMeansFeatureGenerator(n_centroids=1600,
    #                                                                 rf_size=5,
    #                                                                 result_path='data/mdl_kmeans_002_new_minibatch',
    #                                                                 method='minibatch',
    #                                                                 n_init=1,
    #                                                                 n_jobs=-1,)


    # Don't need to fit, as already cached
    patches = ''
    kmeans_generator.fit(patches)
    images = train_x_crop_scale.transform()

    # Problematic here - memory usage spikes to ~ 11GB when threads return
    # train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_002_new.npy', memmap=True)
    train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_002_new.npy', memmap=True)
    train_y = classes.train_solutions.data
    # Unload some objects
    del images
    gc.collect()
    # mdl = models.Ridge.RidgeRFEstimator(alpha=14, n_estimators=250, n_jobs=-1)
    wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 14, 'n_estimators': 500}, n_jobs=-1)
    params = {
        'alpha': [150, 250, 500, 750, 1000],
        'n_estimators': [250]
    }

    # 500 trees and alpha 25 gives cv of .10972 on 2-fold CV, but 25 was on the upper range of the search space,
    # So need to re-run with larger range of alpha
    # Will hit 30GB of ram with 500 trees.
    wrapper.grid_search(train_x, train_y, params, refit=False, parallel_estimator=True)

    # [mean: -0.11024, std: 0.00018, params: {'n_estimators': 250, 'alpha': 20.0},
    # mean: -0.11000, std: 0.00019, params: {'n_estimators': 250, 'alpha': 25.0},
    # mean: -0.10969, std: 0.00018, params: {'n_estimators': 250, 'alpha': 35},
    # mean: -0.10934, std: 0.00019, params: {'n_estimators': 250, 'alpha': 50},
    # mean: -0.10892, std: 0.00025, params: {'n_estimators': 250, 'alpha': 75},
    # mean: -0.10860, std: 0.00025, params: {'n_estimators': 250, 'alpha': 100},
    # mean: -0.10828, std: 0.00019, params: {'n_estimators': 250, 'alpha': 150},
    # mean: -0.10789, std: 0.00016, params: {'n_estimators': 250, 'alpha': 250},
    # mean: -0.10775, std: 0.00024, params: {'n_estimators': 250, 'alpha': 500},
    # mean: -0.10779, std: 0.00022, params: {'n_estimators': 250, 'alpha': 750},
    # mean: -0.10784, std: 0.00023, params: {'n_estimators': 250, 'alpha': 1000}]

    # Fit the final model
    wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 500, 'n_estimators': 500}, n_jobs=-1)
    wrapper.fit(train_x, train_y)
    test_x_crop_scale = CropScaleImageTransformer(training=False,
                                                  result_path='data/data_test_crop_150_scale_15.npy',
                                                  crop_size=150,
                                                  scaled_size=15,
                                                  n_jobs=-1,
                                                  memmap=True)


    test_images = test_x_crop_scale.transform()
    test_x = kmeans_generator.transform(test_images, save_to_file='data/data_kmeans_test_features_003_new.npy', memmap=True)
    res = wrapper.predict(test_x)
    sub = classes.Submission(res)
    sub.to_file('sub_kmeans_003.csv')


def kmeans_004():
    """
    Tuning the scale/crop and RF size parameters

    First number is the scaling, cropped to 200, with rf size of 5.  75 scaling took forever ot transform, so killed
    [(30, array([-0.11374265, -0.1134896 ]))
     (50, array([-0.11677854, -0.11696837]))]

    Trying again with larger RF size of 10.
    As a note, scale to 30 with rf 10 takes about 25 minutes to extract features on the train set
    Scale to 50 with rf 10 takes almost 90 minutes.
    [(30, array([-0.10828216, -0.1081058 ])),
    (50, array([-0.10840914, -0.10868195]))]
    Interesting that scale size of 50 does worse

    Crop is not 150, so this is not really an apples to apples comparison with kmeans_003

    It is possibly worth making a submission with scale 30 and rf size 10
    """
    crops = [200]  # Should probably also add 250
    scales = [30, 50]  # Scaling is probably the most important part here

    scores = []
    for s in scales:
        crop = 200
        n_centroids = 1600
        n_patches = 400000
        # rf_size = int(round(s * .2))
        rf_size = 10
        logger.info("Training with crop {}, scale {}, patch size {}, patches {}, centroids {}".format(crop, s, rf_size, n_patches, n_centroids))

        train_x_crop_scale = CropScaleImageTransformer(training=True,
                                                       result_path='data/data_train_crop_{}_scale_{}.npy'.format(crop, s),
                                                       crop_size=crop,
                                                       scaled_size=s,
                                                       n_jobs=-1,
                                                       memmap=True)

        # spherical generator
        kmeans_generator = KMeansFeatureGenerator(n_centroids=n_centroids,
                                                  rf_size=rf_size,
                                                  result_path='data/mdl_kmeans_004_scale_{}_rf_{}'.format(s, rf_size),
                                                  n_iterations=20,
                                                  n_jobs=-1,)

        patch_extractor = models.KMeansFeatures.PatchSampler(n_patches=n_patches,
                                                             patch_size=rf_size,
                                                             n_jobs=-1)
        images = train_x_crop_scale.transform()
        logger.info("Images ndarray shape: {}".format(images.shape))
        patches = patch_extractor.transform(images)
        logger.info("Patches ndarray shape: {}".format(patches.shape))

        kmeans_generator.fit(patches)

        del patches
        gc.collect()

        train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_004_scale_{}_rf_{}.npy'.format(s, rf_size), memmap=True)
        train_y = classes.train_solutions.data
        # Unload some objects
        del images
        gc.collect()
        logger.info("Train X ndarray shape: {}".format(train_x.shape))

        wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 500, 'n_estimators': 250}, n_jobs=-1)
        wrapper.cross_validation(train_x, train_y, n_folds=2, parallel_estimator=True)
        scores.append((s, wrapper.cv_scores))
        del wrapper
        gc.collect()


def kmeans_005():
    """
    Testing whether extracting patches from train and test images works better

    [(500000, False, array([-0.10799986, -0.10744586])),
    (500000, True, array([-0.10790803, -0.10733288])),
    (600000, False, array([-0.10812188, -0.10735988])),
    (600000, True, array([-0.10778652, -0.10752664]))]
    """
    n_patches_vals = [500000, 600000, 700000]
    include_test_images = [False, True]

    scores = []
    for n_patches in n_patches_vals:
        for incl in include_test_images:
            s = 15
            crop = 150
            n_centroids = 1600
            rf_size = 5
            logger.info("Training with n_patches {}, with test images {}".format(n_patches, incl))

            train_x_crop_scale = CropScaleImageTransformer(training=True,
                                                           result_path='data/data_train_crop_{}_scale_{}.npy'.format(crop, s),
                                                           crop_size=crop,
                                                           scaled_size=s,
                                                           n_jobs=-1,
                                                           memmap=True)
            test_x_crop_scale = CropScaleImageTransformer(training=False,
                                                          result_path='data/data_test_crop_{}_scale_{}.npy'.format(crop, s),
                                                          crop_size=crop,
                                                          scaled_size=s,
                                                          n_jobs=-1,
                                                          memmap=True)

            kmeans_generator = KMeansFeatureGenerator(n_centroids=n_centroids,
                                                      rf_size=rf_size,
                                                      result_path='data/mdl_kmeans_005_patches_{}_test{}'.format(n_patches, incl),
                                                      n_iterations=20,
                                                      n_jobs=-1,)

            patch_extractor = models.KMeansFeatures.PatchSampler(n_patches=n_patches,
                                                                 patch_size=rf_size,
                                                                 n_jobs=-1)
            images = train_x_crop_scale.transform()
            if incl:
                test_images = test_x_crop_scale.transform()
                images = np.vstack([images, test_images])
            logger.info("Extracting patches from images ndarray shape: {}".format(images.shape))

            patches = patch_extractor.transform(images)
            logger.info("Patches ndarray shape: {}".format(patches.shape))

            kmeans_generator.fit(patches)

            del patches
            gc.collect()

            # Reload the original images
            images = train_x_crop_scale.transform()
            logger.info("Generating features on images ndarray shape: {}".format(images.shape))
            train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_005_patches_{}_test_{}.npy'.format(n_patches, incl), memmap=True)
            train_y = classes.train_solutions.data
            # Unload some objects
            del images
            gc.collect()

            wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 500, 'n_estimators': 250}, n_jobs=-1)
            wrapper.cross_validation(train_x, train_y, n_folds=2, parallel_estimator=True)

            score = (n_patches, incl, wrapper.cv_scores)
            logger.info("Score: {}".format(score))
            scores.append(score)

            del wrapper
            gc.collect()


def kmeans_006():
    """
    Testing number of centroids

    [(1000, array([-0.10926318, -0.10853047])),
     (2000, array([-0.10727502, -0.10710292])),
     (2500, array([-0.107019  , -0.10696262])),
     (3000, array([-0.10713973, -0.1066932 ]))]

    """
    n_centroids_vals = [1000, 2000, 2500, 3000]
    scores = []

    for n_centroids in n_centroids_vals:
        s = 15
        crop = 150
        n_patches = 400000
        rf_size = 5
        logger.info("Training with n_centroids {}".format(n_centroids))

        train_x_crop_scale = CropScaleImageTransformer(training=True,
                                                       result_path='data/data_train_crop_{}_scale_{}.npy'.format(crop, s),
                                                       crop_size=crop,
                                                       scaled_size=s,
                                                       n_jobs=-1,
                                                       memmap=True)
        test_x_crop_scale = CropScaleImageTransformer(training=False,
                                                      result_path='data/data_test_crop_{}_scale_{}.npy'.format(crop, s),
                                                      crop_size=crop,
                                                      scaled_size=s,
                                                      n_jobs=-1,
                                                      memmap=True)

        kmeans_generator = KMeansFeatureGenerator(n_centroids=n_centroids,
                                                  rf_size=rf_size,
                                                  result_path='data/mdl_kmeans_006_centroids_{}'.format(n_centroids),
                                                  n_iterations=20,
                                                  n_jobs=-1,)

        patch_extractor = models.KMeansFeatures.PatchSampler(n_patches=n_patches,
                                                             patch_size=rf_size,
                                                             n_jobs=-1)
        images = train_x_crop_scale.transform()

        patches = patch_extractor.transform(images)

        kmeans_generator.fit(patches)

        del patches
        gc.collect()

        train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_006_centroids_{}.npy'.format(n_centroids), memmap=True)
        train_y = classes.train_solutions.data
        # Unload some objects
        del images
        gc.collect()

        wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 500, 'n_estimators': 250}, n_jobs=-1)
        wrapper.cross_validation(train_x, train_y, n_folds=2, parallel_estimator=True)

        score = (n_centroids, wrapper.cv_scores)
        logger.info("Scores: {}".format(score))
        scores.append(score)

        del wrapper
        gc.collect()


def kmeans_006_submission():
    # Final submission
    n_centroids = 3000
    s = 15
    crop = 150
    n_patches = 400000
    rf_size = 5
    logger.info("Training with n_centroids {}".format(n_centroids))

    train_x_crop_scale = CropScaleImageTransformer(training=True,
                                                   result_path='data/data_train_crop_{}_scale_{}.npy'.format(crop, s),
                                                   crop_size=crop,
                                                   scaled_size=s,
                                                   n_jobs=-1,
                                                   memmap=True)

    kmeans_generator = KMeansFeatureGenerator(n_centroids=n_centroids,
                                              rf_size=rf_size,
                                              result_path='data/mdl_kmeans_006_centroids_{}'.format(n_centroids),
                                              n_iterations=20,
                                              n_jobs=-1,)

    patch_extractor = models.KMeansFeatures.PatchSampler(n_patches=n_patches,
                                                         patch_size=rf_size,
                                                         n_jobs=-1)
    images = train_x_crop_scale.transform()

    patches = patch_extractor.transform(images)

    kmeans_generator.fit(patches)

    del patches
    gc.collect()

    train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_006_centroids_{}.npy'.format(n_centroids), memmap=True)
    train_y = classes.train_solutions.data
    # Unload some objects
    del images
    gc.collect()

    wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 500, 'n_estimators': 500}, n_jobs=-1)
    wrapper.fit(train_x, train_y)

    test_x_crop_scale = CropScaleImageTransformer(training=False,
                                                  result_path='data/data_test_crop_{}_scale_{}.npy'.format(crop, s),
                                                  crop_size=crop,
                                                  scaled_size=s,
                                                  n_jobs=-1,
                                                  memmap=True)

    test_images = test_x_crop_scale.transform()
    test_x = kmeans_generator.transform(test_images, save_to_file='data/data_test_kmeans_features_006_centroids_{}.npy'.format(n_centroids), memmap=True)
    res = wrapper.predict(test_x)
    sub = classes.Submission(res)
    sub.to_file('sub_kmeans_006.csv')


def kmeans_007():
    """
    Increasing crop/scale size, rf size, centroids, and patches all at once.

    2014-02-18 02:45:15 - Base - INFO - Cross validation completed in 5426.04788399.  Scores:
    2014-02-18 02:45:15 - Base - INFO - [-0.10834319 -0.10825868]
    """
    n_centroids = 5000
    s = 50
    crop = 200
    # Originally, 1600 centroids for 400,000 patches, or 250 patches per centroid
    # 800000 / 5000 = will give us 160 patches per centroid
    n_patches = 800000
    rf_size = 20
    # 31 x 31 = 961 patches per image, which is 10x more patches than the original settings
    # If we set stride 2, then it's 16 x 16 patches = 256, only twice as many patches
    stride = 2
    train_x_crop_scale = CropScaleImageTransformer(training=True,
                                                   crop_size=crop,
                                                   scaled_size=s,
                                                   n_jobs=-1,
                                                   memmap=True)
    images = train_x_crop_scale.transform()
    patch_extractor = models.KMeansFeatures.PatchSampler(n_patches=n_patches,
                                                         patch_size=rf_size,
                                                         n_jobs=-1)
    patches = patch_extractor.transform(images)

    kmeans_generator = KMeansFeatureGenerator(n_centroids=n_centroids,
                                              rf_size=rf_size,
                                              result_path='data/mdl_kmeans_007'.format(n_centroids),
                                              n_iterations=20,
                                              n_jobs=-1,)
    kmeans_generator.fit(patches)

    del patches
    gc.collect()

    train_x = kmeans_generator.transform(images, save_to_file='data/data_kmeans_features_007.npy', stride_size=stride, memmap=True)
    train_y = classes.train_solutions.data
    # Unload some objects
    del images
    gc.collect()

    wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 500, 'n_estimators': 250}, n_jobs=-1)
    wrapper.cross_validation(train_x, train_y, parallel_estimator=True)

    """
    wrapper.fit(train_x, train_y)

    test_x_crop_scale = CropScaleImageTransformer(training=False,
                                                  crop_size=crop,
                                                  scaled_size=s,
                                                  n_jobs=-1,
                                                  memmap=True)

    test_images = test_x_crop_scale.transform()
    test_x = kmeans_generator.transform(test_images, save_to_file='data/data_test_kmeans_features_007.npy'.format(n_centroids), memmap=True)
    res = wrapper.predict(test_x)
    sub = classes.Submission(res)
    sub.to_file('sub_kmeans_006.csv')
    """


def ensemble_001():
    """
    Ensemble of kmeans and random forest results
    Conducting some analysis of whether the errors from these two models for individual Ys are different

    Ensembled error is .1149.

    Kmeans is better on every class than RF.
    """
    n_centroids = 3000
    s = 15
    crop = 150
    n_patches = 400000
    rf_size = 5

    train_x_crop_scale = CropScaleImageTransformer(training=True,
                                                   crop_size=crop,
                                                   scaled_size=s,
                                                   n_jobs=-1,
                                                   memmap=True)

    kmeans_generator = KMeansFeatureGenerator(n_centroids=n_centroids,
                                              rf_size=rf_size,
                                              result_path='data/mdl_ensemble_001',
                                              n_iterations=20,
                                              n_jobs=-1,)

    patch_extractor = models.KMeansFeatures.PatchSampler(n_patches=n_patches,
                                                         patch_size=rf_size,
                                                         n_jobs=-1)
    images = train_x_crop_scale.transform()
    patches = patch_extractor.transform(images)

    kmeans_generator.fit(patches)

    del patches
    gc.collect()

    X = kmeans_generator.transform(images, save_to_file='data/data_ensemble_001.npy', memmap=True)
    Y = classes.train_solutions.data

    # Unload some objects
    del images
    gc.collect()

    # Get the input for the RF so that we can split together
    sampler = SampleTransformer(training=True, steps=2, step_size=20, n_jobs=-1)
    pX = sampler.transform()

    # manual split of train and test
    train_x, test_x, ptrain_x, ptest_x, train_y, test_y = train_test_split(X, pX, Y, test_size=0.5)

    wrapper = ModelWrapper(models.Ridge.RidgeRFEstimator, {'alpha': 500, 'n_estimators': 500}, n_jobs=-1)
    wrapper.fit(train_x, train_y)
    kmeans_preds = wrapper.predict(test_x)

    pWrapper = ModelWrapper(RandomForestRegressor, {'n_estimators': 500, 'verbose': 3}, n_jobs=-1)
    pWrapper.fit(ptrain_x, train_y)
    pixel_preds = pWrapper.predict(ptest_x)

    logger.info('Kmeans')
    classes.colwise_rmse(kmeans_preds, test_y)
    classes.rmse(kmeans_preds, test_y)
    logger.info('Pixel RF')
    classes.colwise_rmse(pixel_preds, test_y)
    classes.rmse(pixel_preds, test_y)

    logger.info("Ensembling predictions")
    etrain_x = np.hstack((wrapper.predict(train_x), pWrapper.predict(ptrain_x)))
    etest_x = np.hstack((kmeans_preds, pixel_preds))
    eWrapper = ModelWrapper(RandomForestRegressor, {'n_estimators': 500, 'verbose': 3}, n_jobs=-1)
    eWrapper.fit(etrain_x, train_y)
    ensemble_preds = eWrapper.predict(etest_x)
    classes.colwise_rmse(ensemble_preds, test_y)
    classes.rmse(ensemble_preds, test_y)


def kmeans_centroids(fit_centroids=False):
    """
    If fit_centroids is True, we extract patches, fit the centroids and pickle the object
                        False, we unpickle the object.
    @param fit_centroids:
    @return:
    """

    trainX = np.memmap('data/train_cropped_150.memmap', mode='r', shape=(N_TRAIN, 150, 150, 3))
    # Not used yet
    testX = np.memmap('data/test_cropped_150.memmap', mode='r', shape=(N_TEST, 150, 150, 3))

    if fit_centroids:
        km = models.KMeansFeatures.KMeansFeatures(rf_size=6, num_centroids=1600, num_patches=400000)
        km.fit(trainX)

        t0 = time.time()
        pickle.dump(km, open('data/kmeans_centroids.pkl', mode='wb'))
        print 'Pickling the KMeansFeatures object took {0} seconds'.format(time.time() - t0)
    else:
        km = pickle.load(open('data/kmeans_centroids.pkl'))

    models.KMeansFeatures.show_centroids(km.centroids, 6, (6, 6, 3))
