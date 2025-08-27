#!/usr/bin/env python
"""Merge multiple CSPP VIIRS Floods output files into one single file."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
import datetime

from satpy import Scene
from pyresample.geometry import SwathDefinition
from pyresample.area_config import create_area_def
import xarray as xr
import numpy as np
import dask.array as da

from netCDF4 import Dataset

logger = logging.getLogger(__name__)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbosity",
        action="count",
        default=1,
        help="each occurrence increases verbosity 1 level through ERROR-WARNING-INFO-DEBUG (default WARNING). "
        "To suppress warnings, see the -q/--quiet option.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="count",
        default=0,
        help="each occurrence reduces verbosity 1 level through ERROR-WARNING-INFO-DEBUG (default WARNING)",
    )
    parser.add_argument("-l", "--log", dest="log_fn", default="merge_floods.log", help="specify the log filename")
    parser.add_argument("input_files", nargs="+")
    args = parser.parse_args()

    setup_logging_from_cli(args.verbosity, args.quiet, args.log_fn)

    extents = []
    min_lon_res = 10000.0
    min_lat_res = 10000.0
    ncs = []
    for fn in args.input_files:
        logger.info(f"Extracting bounding information from {fn}...")
        nc = Dataset(fn, mode="r")
        nc.set_auto_maskandscale(False)
        ncs.append(nc)

        min_lon_res = min(min_lon_res, nc.geospatial_lon_resolution)
        min_lat_res = min(min_lat_res, nc.geospatial_lat_resolution)

        lon_min = nc.geospatial_lon_min
        lat_min = nc.geospatial_lat_min
        lon_max = nc.geospatial_lon_max
        lat_max = nc.geospatial_lat_max
        if not extents:
            extents = [lon_min, lat_min, lon_max, lat_max]
        else:
            extents[0] = min(lon_min, extents[0])
            extents[1] = min(lat_min, extents[1])
            extents[2] = max(lon_max, extents[2])
            extents[3] = max(lat_max, extents[3])

    target_area = create_area_def(
        "floods",
        "+proj=longlat +datum=WGS84 +ellps=WGS84",
        units="degrees",
        resolution=min(min_lon_res, min_lat_res),
        area_extent=extents,
    )

    frames = []
    for fn, nc in zip(args.input_files, ncs, strict=True):
        logger.info(f"Loading and resampling from {fn}...")
        lons = da.from_array(nc["lon"][:])
        lats = da.from_array(nc["lat"][:])
        lons2d, lats2d = da.meshgrid(lons, lats)
        lons_data_arr = xr.DataArray(lons2d, dims=("y", "x"))
        lats_data_arr = xr.DataArray(lats2d, dims=("y", "x"))
        wd_dask = da.from_array(nc["WaterDetection"][:])
        swath_def = SwathDefinition(lons_data_arr, lats_data_arr)
        # TODO: Use an area definition?

        print(wd_dask.dtype)
        data = xr.DataArray(
            wd_dask, dims=("y", "x"), attrs={"area": swath_def, "name": "WaterDetection", "_FillValue": 1}
        )
        scn = Scene()
        scn["WaterDetection"] = data
        new_scn = scn.resample(target_area, resampler="nearest", fill_value=1)
        resampled_data = new_scn["WaterDetection"].data.compute()
        logger.info("Done resampling")
        frames.append(resampled_data)
        print(resampled_data.shape)

    logger.info("Merging resampled datasets...")
    final_data = frames[0]
    for frame in frames[1:]:
        # replace by doing np.max over one large array?
        final_data = np.where(frame <= 1, final_data, frame)

    with Dataset(args.input_files[0].replace(".nc", "_merged.nc"), mode="w") as final_nc:
        final_nc.set_auto_maskandscale(False)
        final_nc.createDimension("lon", final_data.shape[1])
        final_nc.createDimension("lat", final_data.shape[0])

        print(final_data.dtype)
        wd_var = final_nc.createVariable("WaterDetection", np.int16, dimensions=("lat", "lon"))
        wd_var[:] = final_data

        # FIXME: Get only the vectors
        final_lon, final_lat = target_area.get_lonlats(dtype=np.float32)
        lon_var = final_nc.createVariable("lon", np.float32, dimensions=("lon",))
        lon_var[:] = final_lon[0, :]
        lat_var = final_nc.createVariable("lat", np.float32, dimensions=("lat",))
        lat_var[:] = final_lat[:, 0]


def setup_logging_from_cli(
    verbosity_level: int,
    quiet_level: int,
    log_filename: Path | None,
) -> None:
    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    level_idx = max(0, min(len(levels) - 1, verbosity_level - quiet_level))
    setup_logging(console_level=levels[level_idx], log_filename=log_filename)


def setup_logging(console_level: int = logging.INFO, log_filename: Path | None = None) -> None:
    root_logger = logging.getLogger("")
    root_logger.setLevel(min(console_level, logging.DEBUG))

    # Console output is minimal
    console = logging.StreamHandler(sys.stderr)
    console_format = "%(levelname)-8s : %(message)s"
    console.setFormatter(logging.Formatter(console_format))
    console.setLevel(console_level)
    root_logger.addHandler(console)

    # Log file messages have a lot more information
    if log_filename:
        run_time = datetime.datetime.now(tz=datetime.timezone.utc)
        log_filename = Path(str(log_filename).format(run_time=run_time))
        file_handler = logging.FileHandler(log_filename)
        file_format = "[%(asctime)s] : %(levelname)-8s : %(name)s : %(funcName)s : %(message)s"
        file_handler.setFormatter(logging.Formatter(file_format))
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)

        # Make a traceback logger specifically for adding tracebacks to log file
        traceback_log = logging.getLogger("traceback")
        traceback_log.propagate = False
        traceback_log.setLevel(logging.ERROR)
        traceback_log.addHandler(file_handler)


if __name__ == "__main__":
    sys.exit(main())
