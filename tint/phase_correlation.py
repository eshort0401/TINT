"""
tint.phase_correlation
======================

Functions for performing phase correlation. Used to predict cell movement
between scans.

"""

import numpy as np
from scipy import ndimage


def get_ambient_flow(obj_extent, img1, img2, params, grid_size):
    """ Takes in object extent and two images and returns ambient flow. Margin
    is the additional region around the object used to compute the flow
    vectors. """
    margin_r = params['FLOW_MARGIN'] / grid_size[1]
    margin_c = params['FLOW_MARGIN'] / grid_size[2]
    row_lb = obj_extent['obj_center'][0] - obj_extent['obj_radius'] - margin_r
    row_ub = obj_extent['obj_center'][0] + obj_extent['obj_radius'] + margin_r
    col_lb = obj_extent['obj_center'][1] - obj_extent['obj_radius'] - margin_c
    col_ub = obj_extent['obj_center'][1] + obj_extent['obj_radius'] + margin_c
    row_lb = np.int(row_lb)
    row_ub = np.int(row_ub)
    col_lb = np.int(col_lb)
    col_ub = np.int(col_ub)

    dims = img1.shape

    row_lb = np.max([row_lb, 0])
    row_ub = np.min([row_ub, dims[0]])
    col_lb = np.max([col_lb, 0])
    col_ub = np.max([col_ub, dims[1]])

    flow_region1 = np.copy(img1[row_lb:row_ub+1, col_lb:col_ub+1])
    flow_region2 = np.copy(img2[row_lb:row_ub+1, col_lb:col_ub+1])

    flow_region1[flow_region1 != 0] = 1
    flow_region2[flow_region2 != 0] = 1
    return fft_flowvectors(flow_region1, flow_region2)


def fft_flowvectors(im1, im2, global_shift=False):
    """ Estimates flow vectors in two images using cross covariance. """
    if not global_shift and (np.max(im1) == 0 or np.max(im2) == 0):
        return None
    
    import pdb
    pdb.set_trace()
    
    dims = np.array(im1.shape)
    rd, cd = np.ceil(dims/2).astype('int')
    crosscov = fft_crosscov(im1, im2, rd, cd)
    sigma = (1/8) * min(crosscov.shape)
    cov_smooth = ndimage.filters.gaussian_filter(crosscov, sigma)
    pshift = np.argwhere(cov_smooth == np.max(cov_smooth))[0]
    # Note that the fft_shift function has translated the covariance  
    # matrix so that the [0,0] entry has moved to the [rd2+1, cd2+1]  
    # entry. Thus we must subtract this vector from pshift.  
    pshift = pshift - (dims - [rd, cd])
    return pshift


def fft_crosscov(im1, im2, rd, cd):
    """ Computes cross correlation matrix using FFT method. """
    fft1_conj = np.conj(np.fft.fft2(im1))
    fft2 = np.fft.fft2(im2)
    normalize = abs(fft2*fft1_conj)
    normalize[normalize == 0] = 1  # prevent divide by zero error
    cross_power_spectrum = (fft2*fft1_conj)/normalize
    crosscov = np.fft.ifft2(cross_power_spectrum)
    crosscov = np.real(crosscov)
    return fft_shift(crosscov, rd, cd)


def fft_shift(fft_mat, rd, cd):
    """ Rearranges the cross correlation matrix so that 'zero' frequency or DC
    component is in the middle of the matrix. Taken from stackoverflow Que.
    30630632. """
    if type(fft_mat) is np.ndarray:
        quad1 = fft_mat[:rd, :cd]
        quad2 = fft_mat[:rd, cd:]
        quad3 = fft_mat[rd:, cd:]
        quad4 = fft_mat[rd:, :cd]
        centered_t = np.concatenate((quad4, quad1), axis=0)
        centered_b = np.concatenate((quad3, quad2), axis=0)
        centered = np.concatenate((centered_b, centered_t), axis=1)
        return centered
    else:
        print('input to fft_shift() should be a matrix')
        return


def get_global_shift(im1, im2):
    """ Returns standardized global shift vector. im1 and im2 are full frames
    of raw DBZ values. """
    if im2 is None:
        return None
    shift = fft_flowvectors(im1, im2, global_shift=True)
    return shift
