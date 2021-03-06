import os
import warnings
import logging
import random

from cloud import running_on_cloud
from astropy.io import fits
from astropy.wcs import WCS
import numpy as np

from .util import _sample_and_scale

#turn off internally-triggered astropy WCS warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

_cached_field = None


def get_field(lon):
    """Create and return a new field appropriate
    for running locally or on PiCloud

    The previous return value is cached,
    to avoid repeated I/O for repeated
    requests for the same field
    """
    global _cached_field

    if _cached_field is not None and _cached_field.lon == lon:
        return _cached_field

    del _cached_field

    logging.getLogger(__name__).debug("Loading a new field at l=%i" % lon)

    if running_on_cloud():
        result = CloudField(lon)
    else:
        result = Field(lon)

    _cached_field = result
    return result


class Field(object):
    def __init__(self, lon, path=None):
        self.lon = lon
        path = path or os.path.join(os.path.dirname(__file__), 'data',
                                    'galaxy')
        self.path = path

        i4 = os.path.join(path, 'registered', '%3.3i_i4.fits' % lon)
        mips = os.path.join(path, 'registered', '%3.3i_mips.fits' % lon)
        i3 = os.path.join(path, 'registered', '%3.3i_i3.fits' % lon)

        self.i4 = fits.getdata(i4, memmap=True)
        if os.path.exists(i3):
            self.i3 = fits.getdata(i3, memmap=True)
        self.mips = fits.getdata(mips, memmap=True)
        self.wcs = WCS(fits.getheader(i4))

    def __getitem__(self, field, *slices):
        fields = dict(i4=self.i4, mips=self.mips)
        if field not in fields:
            raise ValueError("Field must be one of %s" % (fields.keys(),))
        return fields[field][slices]

    def _stamps_at_radius(self, r, step=None):
        shp = self.i4.shape
        step = step or r / 5

        y, x = np.mgrid[r / 2: shp[0] - r / 2: step,
                        r / 2: shp[1] - r / 2: step]
        y = y.ravel()
        x = x.ravel()
        lb = self.wcs.all_pix2world(np.column_stack([x, y]), 0)
        rad = r * 2. / 3600.
        for l, b in lb:
            yield (self.lon, l, b, rad)


    def small_stamps(self):
        r = 15
        while r < 40:
            for field in self._stamps_at_radius(r, r/3):
                yield field
            r = int(r * 1.4)

    def random_stamps(self, num):
        """
        Yield a random sample of all_stamps, chosen with replacement

        Parameters
        ----------
        num : int
            The number of random samples

        Returns
        -------
        An iterator over the random sample
        """
        s = list(self.all_stamps())
        return (random.choice(s) for _ in range(num))

    def all_stamps(self):
        shp = self.i4.shape
        n = max(shp[0], shp[1]) / 2
        r = 40
        while r < n:
            for field in self._stamps_at_radius(r):
                yield field
            r = int(r * 1.25)

    def extract_stamp(self, lon, lat, size, do_scale=True, limits=None,
                      shp=(40, 40), i3=False):
        """
        Extract an RGB Postage stamp at the requested position

        Parameters
        ----------
        lon : float
            Longitude of center, deg
        lat : float
            Latitude of center, deg
        size : float
            Size of stamp, deg
        do_scale : bool (optional)
            If True, apply a square-root transfer function
        limits : tuple of (lo_percent, hi_percent)
            If provided, clip the intensities at the specified percentiles
        shp : tuple of (ysize, xsize) (optional)
            The pixel size of the output stamp
        i3 : bool (optional)
            If True, include the 5.8 um data as the blue channel.
        """

        lb = np.array([[lon, lat]])
        x, y = self.wcs.wcs_world2pix(lb, 0).ravel()
        x, y = map(int, [x, y])

        pixscale = 2. / 3600.
        dx = int(size / pixscale)
        lt = x - dx
        rt = x + dx
        bt = y - dx
        tp = y + dx
        mips, i4 = self.mips, self.i4
        if lt < 0 or rt >= i4.shape[1] or bt < 0 or tp >= i4.shape[0]:
            return

        sz = 2 * dx
        stride = max(int(sz / (shp[0] * 2)), 1)

        i4 = self.i4[bt:tp:stride, lt:rt:stride]
        mips = self.mips[bt:tp:stride, lt:rt:stride]
        if i3:
            i3 = self.i3[bt:tp:stride, lt:rt:stride]
            rgb = _sample_and_scale(i4, mips, do_scale,
                                    limits, shp=shp, i3=i3)
        else:
            rgb = _sample_and_scale(i4, mips, do_scale, limits, shp=shp)
        return rgb


class CloudField(Field):

    def __init__(self, lon):
        from cloud.bucket import sync_from_cloud
        self.lon = lon
        i4 = "%3.3i_i4.fits" % lon
        mips = "%3.3i_mips.fits" % lon

        sync_from_cloud(i4)
        sync_from_cloud(mips)

        self.i4 = fits.getdata(i4, memmap=True)
        self.mips = fits.getdata(mips, memmap=True)
        self.wcs = WCS(fits.getheader(i4))
