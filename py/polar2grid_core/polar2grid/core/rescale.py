#!/usr/bin/env python
# encoding: utf-8
"""Functions and mappings for taking rempapped polar-orbitting data and
rescaling it to a useable range for the backend using the data, usually a
0-255 8-bit range or a 0-65535 16-bit range.

:attention:
    A scaling function is not guarenteed to not change the
    original data array passed.  If fact, it is faster in most cases
    to change the array in place.

:author:       David Hoese (davidh)
:author:       Eva Schiffer (evas)
:contact:      david.hoese@ssec.wisc.edu
:organization: Space Science and Engineering Center (SSEC)
:copyright:    Copyright (c) 2012 University of Wisconsin SSEC. All rights reserved.
:date:         Dec 2012
:license:      GNU GPLv3
"""
__docformat__ = "restructuredtext en"

from .constants import *
from . import roles

import os
import sys
import logging
import numpy

log = logging.getLogger(__name__)

# Default fills for individual functions, see Rescaler and RescalerRole for
# other defaults
DEFAULT_FILL_IN  = DEFAULT_FILL_VALUE
DEFAULT_FILL_OUT = DEFAULT_FILL_VALUE

def _make_lin_scale(m, b):
    """Factory function to make a static linear scaling function
    """
    def linear_scale(img, fill_in=DEFAULT_FILL_IN, fill_out=DEFAULT_FILL_OUT):
        log.debug("Running 'linear_scale' with (m: %f, b: %f)..." % (m,b))
        # Faster than assigning
        numpy.multiply(img, m, img)
        numpy.add(img, b, img)
        return img
    return linear_scale

def linear_scale(img, m, b, fill_in=DEFAULT_FILL_IN, fill_out=DEFAULT_FILL_OUT):
    log.debug("Running 'linear_scale' with (m: %f, b: %f)..." % (m,b))

    fill_mask = numpy.nonzero(img == fill_in)

    numpy.multiply(img, m, img)
    numpy.add(img, b, img)

    img[fill_mask] = fill_out

    return img

def unlinear_scale(img, m, b, fill_in=DEFAULT_FILL_IN, fill_out=DEFAULT_FILL_OUT):
    log.debug("Running 'unlinear_scale' with (m: %f, b: %f)..." % (m,b))
    fill_mask = numpy.nonzero(img == fill_in)

    # Faster than assigning
    numpy.subtract(img, b, img)
    numpy.divide(img, m, img)

    img[fill_mask] = fill_out

    return img

def passive_scale(img, fill_in=DEFAULT_FILL_IN, fill_out=DEFAULT_FILL_OUT):
    """When there is no rescaling necessary or it hasn't
    been determined yet, use this function.
    """
    log.debug("Running 'passive_scale'...")
    return img

def sqrt_scale(img, inner_mult, outer_mult, fill_in=DEFAULT_FILL_IN, fill_out=DEFAULT_FILL_OUT):
    log.debug("Running 'sqrt_scale'...")
    mask = img == fill_in
    img[mask] = 0 # For invalids because < 0 cant be sqrted
    numpy.multiply(img, inner_mult, img)
    numpy.sqrt(img, out=img)
    numpy.multiply(img, outer_mult, img)
    numpy.round(img, out=img)
    img[mask] = fill_out
    return img

def bt_scale(img, threshold, high_max, high_mult, low_max, low_mult, fill_in=DEFAULT_FILL_IN, fill_out=DEFAULT_FILL_OUT):
    log.debug("Running 'bt_scale'...")
    high_idx = img >= threshold
    low_idx = img < threshold
    z_idx = img == fill_in
    img[high_idx] = high_max - (high_mult*img[high_idx])
    img[low_idx] = low_max - (low_mult*img[low_idx])
    img[z_idx] = fill_out
    return img

def fog_scale(img, m, b, floor, floor_val, ceil, ceil_val, fill_in=DEFAULT_FILL_IN, fill_out=DEFAULT_FILL_OUT):
    """Scale data linearly. Then clip the data to `floor` and `ceil`,
    but instead of a usual clipping set the lower clipped values to
    `floor_val` and the upper clipped values to `ceil_val`.
    """
    # Put -10 - 10 range into 5 - 205
    log.debug("Running 'fog_scale'...")
    mask = img == fill_in
    numpy.multiply(img, m, out=img)
    numpy.add(img, b, out=img)
    img[img < floor] = floor_val
    img[img > ceil] = ceil_val
    img[mask] = fill_out
    return img

# Needs to be declared after all of the scaling functions
KNOWN_RESCALE_KINDS = {
        'sqrt' : sqrt_scale,
        'linear' : linear_scale,
        'raw' : passive_scale,
        'btemp' : bt_scale
        }

# DEFAULTS
RESCALE_FOR_KIND = {
        DKIND_RADIANCE    : (linear_scale, (255.0,0)),
        DKIND_REFLECTANCE : (sqrt_scale,   (100.0, 25.5)),
        DKIND_BTEMP       : (bt_scale,     (242.0,660.0,2,418.0,1)),
        DKIND_FOG         : (fog_scale,    (10.0,105.0,5,4,205,206))
        }

class Rescaler(roles.RescalerRole):
    DEFAULT_FILL_IN = DEFAULT_FILL_IN
    DEFAULT_FILL_OUT = DEFAULT_FILL_OUT

    @property
    def default_config_dir(self):
        """Return the default search path to find a configuration file if
        the configuration file provided is not an absolute path and the
        configuration filename was not found in the current working
        directory.
        """
        return os.path.split(os.path.realpath(__file__))[0]

    _known_rescale_kinds = {
                'sqrt'   : sqrt_scale,
                'linear' : linear_scale,
                'raw'    : passive_scale,
                'btemp'  : bt_scale,
                'fog'    : fog_scale
                }
    @property
    def known_rescale_kinds(self):
        # Override the role's rescale property
        return self._known_rescale_kinds

    def __init__(self, *args, **kwargs):
        self.inc_by_one = kwargs.pop("inc_by_one", False)
        super(Rescaler, self).__init__(*args, **kwargs)

    def __call__(self, sat, instrument, kind, band, data_kind, data,
            fill_in=None, fill_out=None, inc_by_one=None):
        """Function that uses previously loaded configuration files to choose
        how to rescale the provided data.  If the `config` keyword is not provided
        then a best guess will be made on how to rescale the data.  Usually this
        best guess is a 0-255 scaling based on the `data_kind`.

        `inc_by_one` is meant to make scaling easier in the case of data
        needing the lowest value of data to be the new fill value.  When this
        keyword is set to True (default uses value passed to __init__) it will
        add 1 to the scaled data excluding the invalid values.
        """
        log_level = logging.getLogger('').handlers[0].level or 0
        band_id = self._create_config_id(sat, instrument, kind, band, data_kind)
        fill_in = fill_in or self.fill_in
        fill_out = fill_out or self.fill_out

        if self.config is None or band_id not in self.config:
            # Run the default scaling functions
            log.debug("Config ID '%s' was not found in '%r'" % (band_id,self.config.keys()))
            log.info("Running default rescaling method for kind: %s, band: %s" % (kind,band))
            if data_kind not in RESCALE_FOR_KIND:
                log.error("No default rescaling is set for data of kind %s" % data_kind)
                raise ValueError("No default rescaling is set for data of kind %s" % data_kind)
            rescale_func,rescale_args = RESCALE_FOR_KIND[data_kind]
        else:
            # We know how to rescale using the onfiguration file
            log.info("'%s' was found in the rescaling configuration" % (band_id))
            rescale_func,rescale_args = self.config[band_id]

        # Only perform this calculation if it will be shown, its very time consuming
        if log_level <= logging.DEBUG:
            try:
                log.debug("Data min: %f, max: %f" % (data[ data != fill_in ].min(),data[ data != fill_in ].max()))
            except StandardError:
                log.debug("Couldn't get min/max values for %s %s (all fill data?)" % (kind,band))

        log.debug("Using rescale arguments: %r" % (rescale_args,))
        log.debug("Using fill in/out values: (%s,%s)" % (fill_in,fill_out))
        data = rescale_func(data, *rescale_args, fill_in=fill_in, fill_out=fill_out)

        # Increment by one to help the backend product
        inc_by_one = inc_by_one if inc_by_one is not None else self.inc_by_one
        if inc_by_one:
            data[ data != fill_out ] += 1

        # Only perform this calculation if it will be shown, its very time consuming
        if log_level <= logging.DEBUG:
            try:
                log.debug("Data min: %f, max: %f" % (data[ data != fill_out ].min(),data[ data != fill_out ].max()))
            except StandardError:
                log.debug("Couldn't get min/max values for %s %s (all fill data?)" % (kind,band))

        return data

def main():
    from argparse import ArgumentParser
    description="""
Run polar2grid rescaling via the command line.  This is not the preferred
way to do production level rescaling, but is useful for testing.
"""
    parser = ArgumentParser(description=description)
    parser.add_argument('--doctest', dest="doctest", action="store_true",
            help="run document tests")
    parser.add_argument('-v', '--verbose', dest='verbosity', action="count", default=0,
                    help='each occurrence increases verbosity 1 level through ERROR-WARNING-INFO-DEBUG')
    args = parser.parse_args()

    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    logging.basicConfig(level = levels[min(3, args.verbosity)])

    if args.doctest:
        import doctest
        return doctest.testmod()

    print "Command line interface not implemented yet"
    parser.print_help()
    
    # FUTURE when this allows use of the rescale functions, also allow use of the histogram equalization
    # functions from histogram.py

if __name__ == "__main__":
    sys.exit(main())

