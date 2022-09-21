#!/usr/bin/env python
# encoding: utf-8
# Copyright (C) 2021 Space Science and Engineering Center (SSEC),
#  University of Wisconsin-Madison.
#
#     This program is free software: you can redistribute it and/or modify
#     it under the terms of the GNU General Public License as published by
#     the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.
#
#     This program is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU General Public License for more details.
#
#     You should have received a copy of the GNU General Public License
#     along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# This file is part of the polar2grid software package. Polar2grid takes
# satellite observation data, remaps it, and writes it to a file format for
# input into another program.
# Documentation: http://www.ssec.wisc.edu/software/polar2grid/
"""Utilities related to filtering."""

import logging

import dask.array as da
import numpy as np

try:
    # Python 3.9+
    from functools import cache
except ImportError:
    from functools import lru_cache as cache

from typing import Union

from pyresample.boundary import AreaBoundary, AreaDefBoundary, Boundary
from pyresample.geometry import (
    AreaDefinition,
    SwathDefinition,
    get_geostationary_bounding_box,
)
from pyresample.spherical import SphPolygon

logger = logging.getLogger(__name__)

PRGeometry = Union[SwathDefinition, AreaDefinition]


def boundary_for_area(area_def: PRGeometry) -> Boundary:
    """Create Boundary object representing the provided area."""
    if getattr(area_def, "is_geostationary", False):
        adp = Boundary(*get_geostationary_bounding_box(area_def, nb_points=100))
    else:
        freq_fraction = 0.05 if isinstance(area_def, SwathDefinition) else 0.30
        try:
            adp = AreaDefBoundary(area_def, frequency=int(area_def.shape[0] * freq_fraction))
        except ValueError:
            if not isinstance(area_def, SwathDefinition):
                logger.error("Unable to generate bounding geolocation polygon")
                raise

            logger.warning(
                "Geolocation data contains invalid bounding values. Computing entire array to get bounds instead."
            )
            adp = _compute_boundary_from_whole_swath(area_def)
    return adp


def _compute_boundary_from_whole_swath(swath_def: SwathDefinition):
    lons, lats = swath_def.get_lonlats()
    min_lon = np.nanmin(lons)
    max_lon = np.nanmax(lons)
    min_lat = np.nanmin(lats)
    max_lat = np.nanmax(lats)
    min_lon, max_lon, min_lat, max_lat = da.compute(min_lon, max_lon, min_lat, max_lat)
    sides = (
        ([min_lon, max_lon], [max_lat, max_lat]),  # top
        ([max_lon, max_lon], [max_lat, min_lat]),  # right
        ([max_lon, min_lon], [min_lat, min_lat]),  # bot
        ([min_lon, max_lon], [min_lat, max_lat]),  # left
    )
    return AreaBoundary(*sides)


@cache
def polygon_for_area(area_def: PRGeometry) -> SphPolygon:
    boundary = boundary_for_area(area_def)
    return boundary.contour_poly
