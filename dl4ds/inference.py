import os
import numpy as np
import tensorflow as tf

from .utils import resize_array, spatial_to_temporal_samples
from . import SPATIAL_MODELS, SPATIOTEMP_MODELS, POSTUPSAMPLING_METHODS


def predict(
    model, 
    data, 
    scale, 
    data_in_hr=True,
    topography=None, 
    landocean=None, 
    predictors=None, 
    time_window=None,
    interpolation='bicubic', 
    mean_std=None,
    save_path=None,
    save_fname='y_hat.npy',
    return_lr=False,
    stochastic_output=False):
    """Inference with ``model``. The ``data`` is super-resolved or downscaled 
    using the trained super-resolution network. 

    Parameters
    ----------
    model : tf.keras model
        Trained model.
    data : numpy.ndarray
        Batch of HR grids. 
    scale : int
        Scaling factor. 
    data_in_hr : bool, optional
        If True, the data is assumed to be a HR groundtruth to be downsampled. 
        Otherwise, data is a LR gridded dataset to be downscaled.
    topography : None or 2D ndarray, optional
        Elevation data.
    landocean : None or 2D ndarray, optional
        Binary land-ocean mask.
    predictors : tuple of 4D ndarrays 
        Predictor variables, with dimensions [nsamples, lat, lon, 1].
    time_window : int or None, optional
        If None, then the function assumes the ``model`` is spatial only. If an 
        integer is given, then the ``model`` should be spatio-temporal and the 
        samples are pre-processed accordingly.
    interpolation : str, optional
        Interpolation used when upsampling/downsampling the training samples.
        By default 'bicubic'. 
    save_path : str or None, optional
        If not None, the prediction (gridded variable at HR) is saved to disk.
    save_fname : str, optional
        Filename to complete the path were the prediciton is saved. 
    return_lr : bool, optional
        If True, the LR array is returned along with the downscaled one. 
    stochastic_output : bool, optional
        If True, the output will be stochastic rather than deterministic. This 
        works only when certain layers, such as dropout, are present in the 
        trained ``model``.
    """     
    model_architecture = model.name
    upsampling = model_architecture.split('_')[-1]

    if time_window is not None:
        data = spatial_to_temporal_samples(data, time_window)

    if model_architecture in SPATIAL_MODELS:
        if data_in_hr:
            n_samples, hr_y, hr_x, _ = data.shape
            lr_x = int(hr_x / scale)
            lr_y = int(hr_y / scale)
        else:
            n_samples, lr_y, lr_x, _ = data.shape
        
        n_channels = 1
        pos = {'pred':1, 'topo':1,'laoc':1}
        if predictors is not None:
            n_predictors = len(predictors)
            n_channels += n_predictors   
            pos['topo'] += n_predictors  
            pos['laoc'] += n_predictors 
        if topography is not None:
            n_channels += 1
            pos['laoc'] += 1
        if landocean is not None:
            n_channels += 1
        
        if upsampling in POSTUPSAMPLING_METHODS:
            if topography is not None:
                topo_interp = resize_array(topography, (lr_x, lr_y), interpolation)
            if landocean is not None:
                # integer array can only be interpolated with nearest method
                lando_interp = resize_array(landocean, (lr_x, lr_y), interpolation='nearest')
            
            x_test_lr = np.zeros((n_samples, lr_y, lr_x, n_channels))  # array for inference
        
            for i in range(data.shape[0]):
                if data_in_hr:
                    # the gridded variable is downsampled
                    x_test_lr[i, :, :, 0] = resize_array(data[i], (lr_x, lr_y), interpolation)
                else:
                    # the gridded variable is in LR
                    x_test_lr[i, :, :, 0] = data[i]

                if predictors is not None:
                    # we create a tuple of 3D ndarrays [lat, lon, 1]
                    tuple_predictors = tuple([var[i] for var in predictors])
                    # turned into a 3d ndarray, [lat, lon, variables]
                    array_predictors = np.asarray(tuple_predictors)
                    array_predictors = np.rollaxis(np.squeeze(array_predictors), 0, 3)
                    x_test_lr[i, :, :, pos['pred']:n_predictors+1] = array_predictors
            if topography is not None:                                                          
                x_test_lr[:, :, :, pos['topo']] = topo_interp
            if landocean is not None:
                x_test_lr[:, :, :, pos['laoc']] = lando_interp        
            print('Downsampled x_test shape: ', x_test_lr.shape)

        elif upsampling == 'pin':
            x_test_lr = np.zeros((n_samples, hr_y, hr_x, n_channels))

            for i in range(data.shape[0]):
                if data_in_hr:
                    # downsampling
                    x_test_resized = resize_array(data[i], (lr_x, lr_y), interpolation)
                else:
                    x_test_resized = data[i]  # data in LR
                # upsampling via interpolation
                x_test_resized = resize_array(x_test_resized, (hr_x, hr_y), interpolation)
                x_test_lr[i, :, :, 0] = x_test_resized
                if predictors is not None:
                    # we create a tuple of 3D ndarrays [lat, lon, 1]
                    tuple_predictors = tuple([var[i] for var in predictors])
                    # turned into a 3d ndarray, [lat, lon, variables]
                    array_predictors = np.asarray(tuple_predictors)
                    array_predictors = np.rollaxis(np.squeeze(array_predictors), 0, 3)
                    array_predictors = resize_array(array_predictors, (hr_x, hr_y), interpolation)
                    x_test_lr[i, :, :, pos['pred']:n_predictors+1] = array_predictors
            if topography is not None:                                                         
                x_test_lr[:, :, :, pos['topo']] = topography
            if landocean is not None:
                x_test_lr[:, :, :, pos['laoc']] = landocean
            print('Downsampled x_test shape: ', x_test_lr.shape)
    
    elif model_architecture in SPATIOTEMP_MODELS:
        if data_in_hr:
            n_samples, n_t, hr_y, hr_x, n_channels = data.shape
            lr_x = int(hr_x / scale)
            lr_y = int(hr_y / scale)
        else:
            n_samples, n_t, lr_y, lr_x, n_channels = data.shape

        if upsampling in POSTUPSAMPLING_METHODS:
            x_test_lr = np.zeros((n_samples, n_t, lr_y, lr_x, n_channels))  # array for inference
            for i in range(n_samples):
                if data_in_hr:
                    x_test_lr[i] = resize_array(data[i], (lr_x, lr_y), interpolation, squeezed=False)
                else:
                    x_test_lr[i] = data[i]

            print('Downsampled x_test shape: ', x_test_lr.shape)
            if topography is not None or landocean is not None:
                topography = resize_array(topography, (lr_x, lr_y), interpolation, squeezed=False)
                landocean = resize_array(landocean, (lr_x, lr_y), 'nearest', squeezed=False)
                topography = np.expand_dims(topography, -1)
                landocean = np.expand_dims(landocean, -1)
                static_array = np.concatenate([topography, landocean], axis=-1)
                static_array = np.expand_dims(static_array, 0)
                static_array = np.repeat(static_array, n_samples, 0)

        elif upsampling == 'pin':
            x_test_lr = np.zeros((n_samples, n_t, hr_y, hr_x, n_channels))  # array for inference
            for i in range(n_samples):
                if data_in_hr:
                    temp = resize_array(data[i], (lr_x, lr_y), interpolation, squeezed=False)
                else:
                    temp = data[i]
                x_test_lr[i] = resize_array(temp, (hr_x, hr_y), interpolation, squeezed=False)
            
            print('Downsampled x_test shape: ', x_test_lr.shape)
            if topography is not None or landocean is not None:
                topography = np.expand_dims(topography, -1)
                landocean = np.expand_dims(landocean, -1)
                static_array = np.concatenate([topography, landocean], axis=-1)
                static_array = np.expand_dims(static_array, 0)
                static_array = np.repeat(static_array, n_samples, 0)

    ### Casting as TF tensors --------------------------------------------------
    x_test_lr = tf.cast(x_test_lr, tf.float32)
    if model_architecture in SPATIAL_MODELS:
        inputs = x_test_lr
    elif model_architecture in SPATIOTEMP_MODELS:
        if topography is not None or landocean is not None:
            static_array = tf.cast(static_array, tf.float32)
            inputs = [x_test_lr, static_array]
        else:
            inputs = x_test_lr
    
    ### Inference --------------------------------------------------------------
    # Stochasticity via dropout. It usually only applies when training (no values 
    # are dropped during inference). With training=True, the Dropout layer will 
    # behave in training mode and dropout will be applied at inference time
    x_test_pred = model(inputs, training=stochastic_output)
    x_test_pred = x_test_pred.numpy()

    if mean_std is not None:
        mean, std = mean_std
        x_test_pred *= std
        x_test_pred += mean
    
    if save_path is not None and save_fname is not None:
        name = os.path.join(save_path, save_fname)
        np.save(name, x_test_pred.astype('float32'))
    
    if return_lr:
        return x_test_pred, x_test_lr
    else:
        return x_test_pred

