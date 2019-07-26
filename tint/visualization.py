"""
tint.visualization
==================

Visualization tools for tracks objects.

"""
import warnings
warnings.filterwarnings('ignore')

import gc
import os
import pandas as pd
import numpy as np
import shutil
import tempfile
import matplotlib as mpl
from IPython.display import display, Image
from matplotlib import pyplot as plt
from matplotlib import rcParams
import cartopy.crs as ccrs
import copy

import pyart

from .grid_utils import get_grid_alt


class Tracer(object):
    colors = ['m', 'r', 'lime', 'darkorange', 'k', 'b', 'darkgreen', 'yellow']
    colors.reverse()

    def __init__(self, tobj, persist):
        self.tobj = tobj
        self.persist = persist
        self.color_stack = self.colors * 10
        self.cell_color = pd.Series()
        self.history = None
        self.current = None

    def update(self, nframe):
        self.history = self.tobj.tracks.loc[:nframe]
        self.current = self.tobj.tracks.loc[nframe]
        if not self.persist:
            dead_cells = [key for key in self.cell_color.keys()
                          if key
                          not in self.current.index.get_level_values('uid')]
            self.color_stack.extend(self.cell_color[dead_cells])
            self.cell_color.drop(dead_cells, inplace=True)

    def _check_uid(self, uid):
        if uid not in self.cell_color.keys():
            try:
                self.cell_color[uid] = self.color_stack.pop()
            except IndexError:
                self.color_stack += self.colors * 5
                self.cell_color[uid] = self.color_stack.pop()

    def plot(self, ax):
        for uid, group in self.history.groupby(level='uid'):
            self._check_uid(uid)
            tracer = group[['lon', 'lat']]
            if (self.persist or 
                (uid in self.current.reset_index(level=['time']).index)):
                ax.plot(tracer.lon, tracer.lat, self.cell_color[uid])

def full_domain(tobj, grids, tmp_dir, dpi=100, vmin=-8, vmax=64,
                start_datetime = None, end_datetime = None,
                cmap=None, alt=None, isolated_only=False,
                tracers=False, persist=False,
                projection=None, **kwargs):

    # Create a copy of tobj for use by this function
    f_tobj = copy.deepcopy(tobj)

    n_lvl = tobj.params['LEVELS'].shape[0]
    tracks_low = f_tobj.tracks.xs(0, level='level')
    tracks_high = f_tobj.tracks.xs(n_lvl-1, level='level')

    # Consider just the track data at the tracking interval.
    f_tobj.tracks = f_tobj.tracks.xs(f_tobj.params['TRACK_INTERVAL'], 
                                 level='level')

    grid_size = tobj.grid_size
    if cmap is None:
        cmap = pyart.graph.cm_colorblind.HomeyerRainbow
    if alt is None:
        alt = f_tobj.params['GS_ALT']
    if projection is None:
        projection=ccrs.PlateCarree()
    if tracers:
        tracer = Tracer(f_tobj, persist)

    radar_lon = f_tobj.radar_info['radar_lon']
    radar_lat = f_tobj.radar_info['radar_lat']
    lon = np.arange(round(radar_lon-5,2),round(radar_lon+5,2), 1)
    lat = np.arange(round(radar_lat-5,2),round(radar_lat+5,2), 1)

    time_ind = f_tobj.tracks.index.get_level_values('time')    
    
    # Restrict tracks data to start and end datetime arguments
    if start_datetime != None:
        cond = (time_ind >= start_datetime)
        f_tobj.tracks = f_tobj.tracks[cond]
        time_ind = f_tobj.tracks.index.get_level_values('time')
    else:
        start_datetime = time_ind[0]
        time_ind = f_tobj.tracks.index.get_level_values('time')    
    if end_datetime != None:
        cond = (time_ind <= end_datetime)
        f_tobj.tracks = f_tobj.tracks[cond]
        time_ind = f_tobj.tracks.index.get_level_values('time')    
    else:
        end_datetime = time_ind[-1]
        time_ind = f_tobj.tracks.index.get_level_values('time')
    
    # Create index of scan numbers so scans can be easily
    # retrieved for a given time.
    scan_ind = f_tobj.tracks.index.get_level_values('scan')     

    print('Animating from {} to {}.'.format(str(start_datetime), 
                                            str(end_datetime)))

    for counter, grid in enumerate(grids):
        
        grid_time = np.datetime64(grid.time['units'][15:])

        if grid_time > end_datetime:
            del grid
            print('\nReached {}.\n'.format(str(end_datetime)) 
                  + 'Breaking loop.', flush=True)
            break
        elif grid_time < start_datetime:
            print('\nCurrent grid earlier than {}.\n'
                  + 'Moving to next grid.'.format(str(start_datetime)))
            continue

        fig_grid = plt.figure(figsize=(10, 8))

        # Initialise fonts
        rcParams.update({'font.family' : 'serif'})
        rcParams.update({'font.serif': 'Liberation Serif'})
        rcParams.update({'mathtext.fontset' : 'dejavuserif'}) 
        rcParams.update({'font.size': 12})
                
        print('Plotting scan at {}.'.format(grid_time), 
              end='\r', flush=True)
        
        display = pyart.graph.GridMapDisplay(grid)
        ax = fig_grid.add_subplot(111, projection=projection)
        transform = projection._as_mpl_transform(ax)
        display.plot_crosshairs(lon=radar_lon, lat=radar_lat)
        display.plot_grid(tobj.field, level=get_grid_alt(grid_size, alt),
                          vmin=vmin, vmax=vmax, mask_outside=False,
                          cmap=cmap, transform=projection, ax=ax, **kwargs)

        if grid_time in time_ind:

            nframe = scan_ind[time_ind == grid_time][0]
            frame_tracks = f_tobj.tracks.loc[nframe]
            frame_tracks_low = tracks_low.loc[nframe]
            frame_tracks_high = tracks_high.loc[nframe]
            frame_tracks = frame_tracks.reset_index(level=['time'])
            frame_tracks_low = frame_tracks_low.reset_index(level=['time'])
            frame_tracks_high = frame_tracks_high.reset_index(level=['time'])
            
            if tracers:
                tracer.update(nframe)
                tracer.plot(ax)

            for ind, uid in enumerate(frame_tracks.index):
                if isolated_only and not frame_tracks['isolated'].iloc[ind]:
                    continue
                x = frame_tracks['lon'].iloc[ind]
                y = frame_tracks['lat'].iloc[ind]
                x_low = frame_tracks_low['lon'].iloc[ind]
                y_low = frame_tracks_low['lat'].iloc[ind]
                x_high = frame_tracks_high['lon'].iloc[ind]
                y_high = frame_tracks_high['lat'].iloc[ind]
                ax.text(x, y, uid, transform=projection, fontsize=12)
                ax.plot([x_low, x_high], [y_low, y_high], '--b', linewidth=2.0)

        plt.savefig(tmp_dir + '/frame_' + str(counter).zfill(3) + '.png',
                    bbox_inches = 'tight', dpi=dpi)
        plt.close()
        del grid, display, ax
        gc.collect()


def lagrangian_view(tobj, grids, tmp_dir, uid=None, dpi=100, 
                    vmin=-8, vmax=64,
                    start_datetime=None, end_datetime=None,
                    cmap=None, alt_low=None, alt_high=None, 
                    box_rad=.75, projection=None):

    if uid is None:
        print("Please specify 'uid' keyword argument.")
        return
    stepsize = 0.2
    title_font = 14
    axes_font = 10
    mpl.rcParams['xtick.labelsize'] = 10
    mpl.rcParams['ytick.labelsize'] = 10

    field = tobj.field
    grid_size = tobj.grid_size

    if cmap is None:
        cmap = pyart.graph.cm_colorblind.HomeyerRainbow
    if alt_low is None:
        alt_low = tobj.params['GS_ALT']
    if alt_high is None:
        alt_high = tobj.params['GS_ALT']
    if projection is None:
        projection = ccrs.PlateCarree()

    low = tobj.params['LEVELS'][0].mean()/1000
    high = tobj.params['LEVELS'][-1].mean()/1000
        
    cell = tobj.tracks.xs(uid, level='uid')
    cell = cell.reset_index(level=['time'])
    
    n_lvl = tobj.params['LEVELS'].shape[0]
    cell_low = cell.xs(0, level='level')
    cell_high = cell.xs(n_lvl-1, level='level')
    cell = cell.xs(tobj.params['TRACK_INTERVAL'] ,level='level')

    nframes = len(cell)
    print('Animating', nframes, 'frames')
    nframe = 0
        
    for grid in grids:
        grid_time = np.datetime64(grid.time['units'][15:])        
        if nframe >= nframes:
            info_msg = ('Object died at ' 
                        + str(cell.iloc[nframe-1].time)
                        + '.\n' + 'Ending loop.')
            print(info_msg)
            del grid
            gc.collect()
            break        
        elif cell.iloc[nframe].time > grid_time:
            info_msg = ('Object not yet initiated at '
                        + '{}.\n'.format(grid_time) 
                        + 'Moving to next grid.') 
            print(info_msg)
            continue
        while cell.iloc[nframe].time < grid_time:
            
            info_msg = ('Current grid at {}.\n'
                        + 'Object initialises at {}.\n'
                        + 'Moving to next object frame.') 
            print(info_msg.format(grid_data, str(cell.iloc[nframe].time)))
            nframe += 1
             
        print('Plotting frame at {}'.format(grid_time),
              end='\r', flush=True)

        row = cell.iloc[nframe]
        row_low = cell_low.iloc[nframe]
        row_high = cell_high.iloc[nframe]
        display = pyart.graph.GridMapDisplay(grid)

        # Box Size
        tx_met = np.int(np.round(row['grid_x']))
        ty_met = np.int(np.round(row['grid_y']))
        tx_low = row_low['grid_x']/1000
        tx_high = row_high['grid_x']/1000
        ty_low = row_low['grid_y']/1000
        ty_high = row_high['grid_y']/1000
        #tx_met = grid.x['data'][tx]
        #ty_met = grid.y['data'][ty]
        lat = row['lat']
        lon = row['lon']
        
        lat_low = row_low['lat']
        lat_high = row_high['lat']
        lon_low = row_low['lon']
        lon_high = row_high['lon']
        
        box_rad_met = box_rad 
        box = np.array([-1*box_rad_met, box_rad_met])
        
        lvxlim = (lon) + box
        lvylim = (lat) + box
        xlim = (tx_met + np.array([-75000, 75000]))/1000
        ylim = (ty_met + np.array([-75000, 75000]))/1000

        fig = plt.figure(figsize=(12, 10))

        # Initialise fonts
        rcParams.update({'font.family' : 'serif'})
        rcParams.update({'font.serif': 'Liberation Serif'})
        rcParams.update({'mathtext.fontset' : 'dejavuserif'}) 
        rcParams.update({'font.size': 12})

        fig.suptitle('Cell ' + uid + ' at ' + str(grid_time), fontsize=16)
        plt.axis('off')

        # Lagrangian View
        alts = [alt_low, alt_high]
        for i in range(len(alts)):
            ax = fig.add_subplot(2, 2, 2*i+1, projection=projection)

            display.plot_grid(field, level=get_grid_alt(grid_size, alts[i]),
                              vmin=vmin, vmax=vmax, mask_outside=False,
                              cmap=cmap, colorbar_flag=True,
                              ax=ax, projection=projection)

            display.plot_crosshairs(lon=lon, lat=lat, linestyle='--', 
                                    color='k', linewidth=3, ax=ax)

            ax.plot([lon_low, lon_high], [lat_low, lat_high], '--b', linewidth=2.0)

            ax.set_xlim(lvxlim[0], lvxlim[1])
            ax.set_ylim(lvylim[0], lvylim[1])

            ax.set_xticks(np.arange(lvxlim[0], lvxlim[1], stepsize))
            ax.set_yticks(np.arange(lvylim[0], lvylim[1], stepsize))
            
            ax.set_xticklabels(
                np.round(np.arange(lvxlim[0], lvxlim[1], stepsize), 1)
            )
            ax.set_yticklabels(
                np.round(np.arange(lvylim[0], lvylim[1], stepsize), 1)
            )

            ax.set_title('Altitude {} km'.format(alts[i]), 
                         fontsize=title_font)
            ax.set_xlabel('Longitude of grid cell center [degree_E]',
                           fontsize=axes_font)
            ax.set_ylabel('Latitude of grid cell center [degree_N]',
                           fontsize=axes_font)
                                      
        # Latitude Cross Section
        ax = fig.add_subplot(2, 2, 2)
        display.plot_latitude_slice(field, lon=lon, lat=lat,
                                    title_flag=False,
                                    colorbar_flag=False, edges=False,
                                    vmin=vmin, vmax=vmax, mask_outside=False,
                                    cmap=cmap,
                                    ax=ax)      
        ax.plot([tx_low, tx_high], [low, high], '--b', linewidth=2.0)

        ax.set_xlim(xlim[0], xlim[1])
        ax.set_xticks(np.arange(xlim[0], xlim[1], 25))
        ax.set_xticklabels(
            np.round((np.arange(xlim[0], xlim[1], 25)), 1)
        )

        ax.set_title('Latitude Cross Section', fontsize=title_font)
        ax.set_xlabel('East West Distance From Origin (km)' + '\n',
                       fontsize=axes_font)
        ax.set_ylabel('Distance Above Origin (km)', fontsize=axes_font)

        # Longitude Cross Section
        ax = fig.add_subplot(2, 2, 4)
        display.plot_longitude_slice(field, lon=lon, lat=lat,
                                     title_flag=False,
                                     colorbar_flag=False, edges=False,
                                     vmin=vmin, vmax=vmax, mask_outside=False,
                                     cmap=cmap,
                                     ax=ax)
        ax.plot([ty_low, ty_high], [low, high], '--b', linewidth=2.0)
        ax.set_xlim(ylim[0], ylim[1])
        ax.set_xticks(np.arange(ylim[0], ylim[1], 25))
        ax.set_xticklabels(np.round(np.arange(ylim[0], ylim[1], 25), 1))

        ax.set_title('Longitudinal Cross Section', fontsize=title_font)
        ax.set_xlabel('North South Distance From Origin (km)',
                       fontsize=axes_font)
        ax.set_ylabel('Distance Above Origin (km)', fontsize=axes_font)
    
        plt.tight_layout()

        # plot and save figure
        fig.savefig(tmp_dir + '/frame_' + str(nframe).zfill(3) + '.png', 
                    dpi=dpi)
        plt.close()
        nframe += 1
        del grid, display
        gc.collect()


def make_mp4_from_frames(tmp_dir, dest_dir, basename, fps):
    os.chdir(tmp_dir)
    os.system(" ffmpeg -framerate " + str(fps)
              + " -pattern_type glob -i '*.png'"
              + " -movflags faststart -pix_fmt yuv420p -vf"
              + " 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -y "
              + basename + '.mp4')
    try:
        shutil.move(basename + '.mp4', dest_dir)
    except FileNotFoundError:
        print('Make sure ffmpeg is installed properly.')

def make_gif_from_frames(tmp_dir, dest_dir, basename, fps):
    print('\nCreating GIF - may take a few minutes.', flush=True)
    os.chdir(tmp_dir)
    delay = round(100/fps)
    
    command = "convert -delay {} frame_*.png -loop 0 {}.gif"
    os.system(command.format(str(delay), basename))
    try:
        shutil.move(basename + '.gif', dest_dir)
    except FileNotFoundError:
        print('Make sure Image Magick is installed properly.')


def animate(tobj, grids, outfile_name, style='full', fps=2, 
            start_datetime = None, end_datetime = None, 
            keep_frames=False, dpi=100, **kwargs):
    """
    Creates gif animation of tracked cells.

    Parameters
    ----------
    tobj : Cell_tracks
        The Cell_tracks object to be visualized.
    grids : iterable
        An iterable containing all of the grids used to generate tobj
    outfile_name : str
        The name of the output file to be produced.
    alt : float
        The altitude to be plotted in meters.
    vmin, vmax : float
        Limit values for the colormap.
    arrows : bool
        If True, draws arrow showing corrected shift for each object. Only used
        in 'full' style.
    isolation : bool
        If True, only annotates uids for isolated objects. Only used in 'full'
        style.
    uid : str
        The uid of the object to be viewed from a lagrangian persepective. Only
        used when style is 'lagrangian'.
    fps : int
        Frames per second for output gif.

    """

    styles = {'full': full_domain,
              'lagrangian': lagrangian_view}
    anim_func = styles[style]

    dest_dir = os.path.dirname(outfile_name)
    basename = os.path.basename(outfile_name)
    if len(dest_dir) == 0:
        dest_dir = os.getcwd()

    if os.path.exists(basename + '.mp4'):
        print('Filename already exists.')
        return

    tmp_dir = tempfile.mkdtemp()

    try:
        anim_func(tobj, grids, tmp_dir, dpi=dpi,
                  start_datetime=start_datetime, 
                  end_datetime=end_datetime,
                  **kwargs)
        if len(os.listdir(tmp_dir)) == 0:
            print('Grid generator is empty.')
            return
        make_gif_from_frames(tmp_dir, dest_dir, basename, fps)
        if keep_frames:
            frame_dir = os.path.join(dest_dir, basename + '_frames')
            shutil.copytree(tmp_dir, frame_dir)
            os.chdir(dest_dir)
    finally:
        shutil.rmtree(tmp_dir)

def embed_mp4_as_gif(filename):
    """ Makes a temporary gif version of an mp4 using ffmpeg for embedding in
    IPython. Intended for use in Jupyter notebooks. """
    if not os.path.exists(filename):
        print('file does not exist.')
        return

    dirname = os.path.dirname(filename)
    basename = os.path.basename(filename)
    newfile = tempfile.NamedTemporaryFile()
    newname = newfile.name + '.gif'
    if len(dirname) != 0:
        os.chdir(dirname)

    os.system('ffmpeg -i ' + basename + ' ' + newname)

    try:
        with open(newname, 'rb') as f:
            display(Image(f.read(), format='png'))
    finally:
        os.remove(newname)
