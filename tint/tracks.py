"""
tint.tracks
===========

Cell_tracks class.

"""

import copy
import datetime

import numpy as np
import pandas as pd
import xarray as xr

from .grid_utils import get_grid_size, get_radar_info, extract_grid_data
from .helpers import Record, Counter
from .phase_correlation import get_global_shift
from .matching import get_pairs
from .objects import init_current_objects, update_current_objects
from .objects import get_object_prop, write_tracks 
from .objects import post_tracks, get_system_tracks

# Tracking Parameter Defaults
FIELD_THRESH = [32]
ISO_THRESH = [8]
ISO_SMOOTH = 3
MIN_SIZE = [8]
SEARCH_MARGIN = 5000
FLOW_MARGIN = 20000 # CPOL grid is 2500 m, so 20000 is 8 pixels.
MAX_DISPARITY = 999
MAX_FLOW_MAG = 60
MAX_SHIFT_DISP = 60
BOUNDARY_GRID_CELLS = set()
GS_ALT = 1500
LEVELS = np.array([[500, 20000]])
TRACK_INTERVAL = 0
UPDRAFT_THRESH = 25
UPDRAFT_START = 500

"""
Tracking Parameter Guide
------------------------

FIELD_THRESH : units of 'field' attribute
    The threshold used for object detection. Detected objects are 
    connnected pixels above this threshold.
ISO_THRESH : units of 'field' attribute
    Used in isolated cell classification. Isolated cells must not be 
    connected to any other cell by contiguous pixels above this 
    threshold.
ISO_SMOOTH : pixels
    Gaussian smoothing parameter in peak detection preprocessing. See
    single_max in tint.objects.
MIN_SIZE : square kilometers
    The minimum size threshold in square kilometres for an object to be
    detected. See extract_grid_data in grid_utils.
SEARCH_MARGIN : meters
    The radius of the search box around the predicted object center.
FLOW_MARGIN : meters 
    The margin size around the object extent on which to perform phase
    correlation.
MAX_DISPARITY : float
    Maximum allowable disparity value. Larger disparity values are 
    sent to LARGE_NUM.
MAX_FLOW_MAG : meters per second
    Maximum allowable global shift magnitude. See get_global_shift in
    tint.phase_correlation.
MAX_SHIFT_DISP : meters per second
    Maximum magnitude of difference in meters per second for 
    two shifts to be considered in agreement. 
    See correct_shift in tint.matching.
GS_ALT : meters
    Altitude in meters at which to perform phase correlation for 
    global shift calculation. See correct_shift in tint.matching.
LEVELS : n x 2 numpy array, meters
    Each row represents range of vertical levels over which to 
    identify objects. Objects will then by matched across the 
    different vertical level ranges.
TRACK_INTERVAL: integer
    Index i corresponding to the interval given in levels over 
    which to track across time.
BOUNDARY_GRID_CELLS: set
    Set of tuples of grid indices for the boundary of the in range area. 
    Use empty set to ignore this test. 
UPDRAFT_THRESH: float, DbZ
    Threshold used when tracking local maxima across vertical levels in order
    to define "updrafts". 
UPDRAFT_START: float, metres
    Height at which to begin tracking updrafts.
"""


class Cell_tracks(object):
    """
    This is the main class in the module. It allows tracks
    objects to be built using lists of pyart grid objects.

    Attributes
    ----------
    params : dict
        Parameters for the tracking algorithm.
    field : str
        String specifying pyart grid field to be used for tracking. Default is
        'reflectivity'.
    grid_size : array
        Array containing z, y, and x mesh size in meters respectively.
    last_grid : Grid
        Contains the most recent grid object tracked. This is used for dynamic
        updates.
    counter : Counter
        See Counter class in helpers.py.
    record : Record
        See Record class.
    current_objects : dict
        Contains information about objects in the current scan.
    tracks : DataFrame

    __saved_record : Record
        Deep copy of Record at the penultimate scan in the sequence. This and
        following 2 attributes used for link-up in dynamic updates.
    __saved_counter : Counter
        Deep copy of Counter.
    __saved_objects : dict
        Deep copy of current_objects.

    """

    def __init__(self, field='reflectivity'):
        self.params = {'FIELD_THRESH': FIELD_THRESH,
                       'MIN_SIZE': MIN_SIZE,
                       'SEARCH_MARGIN': SEARCH_MARGIN,
                       'FLOW_MARGIN': FLOW_MARGIN,
                       'MAX_FLOW_MAG': MAX_FLOW_MAG,
                       'MAX_DISPARITY': MAX_DISPARITY,
                       'MAX_SHIFT_DISP': MAX_SHIFT_DISP,
                       'ISO_THRESH': ISO_THRESH,
                       'ISO_SMOOTH': ISO_SMOOTH,
                       'GS_ALT': GS_ALT,
                       'LEVELS': LEVELS,
                       'TRACK_INTERVAL': TRACK_INTERVAL,
                       'BOUNDARY_GRID_CELLS': BOUNDARY_GRID_CELLS,
                       'UPDRAFT_THRESH': UPDRAFT_THRESH,
                       'UPDRAFT_START': UPDRAFT_START}
                       
        self.field = field
        self.grid_size = None
        self.radar_info = None
        self.last_grid = None
        self.counter = None
        self.record = None
        self.current_objects = None
        self.tracks = pd.DataFrame()

        self.__saved_record = None
        self.__saved_counter = None
        self.__saved_objects = None

    def __save(self):
        """ Saves deep copies of record, counter, and current_objects. """
        self.__saved_record = copy.deepcopy(self.record)
        self.__saved_counter = copy.deepcopy(self.counter)
        self.__saved_objects = copy.deepcopy(self.current_objects)

    def __load(self):
        """ Loads saved copies of record, counter, and current_objects. If new
        tracks are appended to existing tracks via the get_tracks method, the
        most recent scan prior to the addition must be overwritten to link up
        with the new scans. Because of this, record, counter and
        current_objects must be reverted to their state in the penultimate
        iteration of the loop in get_tracks. See get_tracks for details. """
        self.record = self.__saved_record
        self.counter = self.__saved_counter
        self.current_objects = self.__saved_objects

    def get_tracks(self, grids, rain=True, save_rain=True, dt=''):
        """ Obtains tracks given a list of pyart grid objects. This is the
        primary method of the tracks class. This method makes use of all of the
        functions and helper classes defined above. """
        start_time = datetime.datetime.now()
        acc_rain_list = []
        acc_rain_uid_list = []

        if self.record is None:
            # tracks object being initialized
            grid_obj2 = next(grids)
            self.grid_size = get_grid_size(grid_obj2)
            self.radar_info = get_radar_info(grid_obj2)
            self.counter = Counter()
            self.record = Record(grid_obj2)
        else:
            # tracks object being updated
            grid_obj2 = self.last_grid
            self.tracks.drop(self.record.scan + 1)  # last scan is overwritten

        if self.current_objects is None:
            newRain = True
        else:
            newRain = False

        raw2, raw_rain2, frames2, cores2, sclasses2 = extract_grid_data(
            grid_obj2, self.field, self.grid_size, self.params, rain
        )
        frame2 = frames2[self.params['TRACK_INTERVAL']]
        
        while grid_obj2 is not None:
            grid_obj1 = grid_obj2
            if not newRain:
                frame0 = copy.deepcopy(frame1)
            else:
                frame0 = np.nan
            raw1 = raw2
            raw_rain1 = raw_rain2
            frame1 = frame2
            frames1 = frames2
            cores1 = cores2
            sclasses1 = sclasses2

            try:
                # Check if next grid zero artificially
                grid_obj2 = next(grids)
                raw, raw_rain, frames, cores, sclasses = extract_grid_data(
                    grid_obj2, self.field, self.grid_size, self.params, rain
                )
                # Skip grids that are artificially zero
                while (np.max(raw1)>30 and np.max(raw)==0):
                    grid_obj2 = next(grids)
                    raw, raw_rain, frames, cores, sclasses = extract_grid_data(
                        grid_obj2, self.field, self.grid_size, self.params, rain
                    )
                    print('Skipping erroneous grid.                        ')                
            except StopIteration:
                grid_obj2 = None
                
            if grid_obj2 is not None:
                              
                [raw2, raw_rain2, frames2, cores2, sclasses2] = [raw, raw_rain, frames, cores, sclasses]
                frame2 = frames2[self.params['TRACK_INTERVAL']]
                
                self.record.update_scan_and_time(grid_obj1, grid_obj2)
                
                # Check for gaps in record. If gap exists, tell tint to start
                # define new objects in current grid. 
                if self.record.interval != None:
                    # Allow a couple of missing scans
                    if self.record.interval.seconds > 1700:
                        message = '\nTime discontinuity at {}.'.format(
                            self.record.time
                        )
                        print(message, flush=True)
                        newRain = True
                        self.current_objects = None
                
            else:
                # setup to write final scan
                self.__save()
                self.last_grid = grid_obj1
                self.record.update_scan_and_time(grid_obj1)
                raw2 = None
                frame2 = np.zeros_like(frame1)
                frames2 = np.zeros_like(frames1)

            if np.max(frame1) == 0:
                newRain = True
                print('No objects found in scan ' 
                      + str(self.record.scan) + '.', end='    \r',
                      flush=True)
                self.current_objects = None
                continue
                              
            global_shift = get_global_shift(raw1, raw2, self.params)
            pairs, obj_merge_new, u_shift, v_shift = get_pairs(
                frame1, frame2, raw1, raw2, global_shift, self.current_objects, 
                self.record, self.params
            )
                                                                                 
            if newRain:
                # first nonempty scan after a period of empty scans
                self.current_objects, self.counter = init_current_objects(
                    raw1, raw2, raw_rain1, raw_rain2, frame1, frame2,
                    frames1, frames2, pairs, self.counter, 
                    self.record.interval.total_seconds(), rain
                )
                newRain = False
            else:
                self.current_objects, self.counter, acc_rain_list, acc_rain_uid_list = update_current_objects(
                    raw1,raw2,raw_rain1,raw_rain2,frame0,frame1,frame2,
                    frames1, frames2, 
                    acc_rain_list, acc_rain_uid_list,
                    pairs,self.current_objects,self.counter,obj_merge,
                    self.record.interval.total_seconds(),rain,save_rain
                )
            obj_merge = obj_merge_new
            obj_props = get_object_prop(
                frames1, cores1, grid_obj1, u_shift, v_shift, sclasses1, 
                self.field, self.record, self.params, self.current_objects
            )
            self.record.add_uids(self.current_objects)
            self.tracks = write_tracks(self.tracks, self.record,
                                       self.current_objects, obj_props)
            del raw1, frames1, cores1, 
            del global_shift, pairs, obj_props
            # scan loop end
        
        if save_rain:    
            acc_rain = np.stack(acc_rain_list, axis=0)
            acc_rain_uid = np.array(acc_rain_uid_list)
            if len(acc_rain_uid_list)>1:
                acc_rain_uid = np.squeeze(acc_rain_uid)
            
            x = grid_obj1.x['data'].data
            y = grid_obj1.y['data'].data
            acc_rain_da = xr.DataArray(acc_rain, coords=[acc_rain_uid, y, x], dims=['uid','y','x'])
            acc_rain_da.attrs = {
                'long_name': 'Accumulated Rainfall', 
                'units': 'mm', 
                'standard_name': 'Accumulated Rainfall', 
                'description': ('Derived from rainfall rate algorithm based on '
                                + 'Thompson et al. 2016, integrated in time.')
            }
            acc_rain_da.to_netcdf('/g/data/w40/esh563/CPOL_analysis/'
                                  + 'accumulated_rainfalls/'
                                  + 'acc_rain_da_{}.nc'.format(dt))  
        
        del grid_obj1

        self = post_tracks(self)
        self = get_system_tracks(self)
          
        self.__load()
        time_elapsed = datetime.datetime.now() - start_time
        print('\n')
        print('Time elapsed:', np.round(time_elapsed.seconds/60, 1), 'minutes')
        return
