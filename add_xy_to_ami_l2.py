"""Add 'x' and 'y' variables to an AMI L2 NetCDF file.

Input projection must be geostationary.

"""

from __future__ import annotations

import logging
import pathlib
import shutil

from netCDF4 import Dataset
import numpy as np
import sys

logger = logging.getLogger(__name__)


def add_xy_to_file(nc_file: pathlib.Path, units: Literal["radians", "meters"] = "radians") -> None:
    nc = Dataset(nc_file, mode="r+")

    num_rows = nc.dimensions["ydim"].size
    num_cols = nc.dimensions["xdim"].size
    gmap = nc["gk2a_imager_projection"]
    x, y = _calc_xy(num_rows, num_cols, gmap, units=units)
    scale_factors = 500.0 if units == "meters" else 500.0 / gmap.perspective_point_height

    x_var = nc.createVariable("x", "i2", dimensions=("xdim",))
    x_var.scale_factor = 500.0
    x_var.add_offset = x[0]
    x_var.units = units
    x_var.axis = "X"
    x_var.long_name = "Geostationary projection x-coordinate"
    x_var.standard_name = "projection_x_coordinate"
    x_var[:] = x

    y_var = nc.createVariable("y", "i2", dimensions=("ydim",))
    y_var.scale_factor = 500.0
    y_var.add_offset = y[0]
    y_var.units = units
    y_var.axis = "Y"
    y_var.long_name = "Geostationary projection y-coordinate"
    y_var.standard_name = "projection_y_coordinate"
    y_var[:] = y


def _calc_xy(
    num_rows: int, num_cols: int, gmap: dict, units: Literal["radians", "meters"] = "radians"
) -> tuple[np.ndarray, np.ndarray]:
    h = gmap.perspective_point_height
    lfac = gmap.line_scale_factor
    loff = gmap.line_offset
    cfac = gmap.column_scale_factor
    coff = gmap.column_offset
    # Count starts at 1
    # So 0.5 would be the left extent of the left-most pixel, we want the center point
    x_deg = (np.arange(num_cols) + 1.0 - coff) / (float(cfac) / 2**16)
    y_deg = (np.arange(num_rows) + 1.0 - loff) / (float(lfac) / 2**16)
    x_rads = np.deg2rad(x_deg)
    y_rads = np.deg2rad(y_deg)

    if units == "meters":
        x_meters = x_rads * h
        y_meters = y_rads * h
        return x_meters, y_meters
    return x_rads, y_rads


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-o",
        "--output-dir",
        type=pathlib.Path,
        help="Directory to place output NetCDF files. Required if 'inplace' is not specified.",
    )
    parser.add_argument("--inplace", action="store_true", help="Update the input file inplace. Ignores '--output-dir'.")
    parser.add_argument(
        "input_files",
        nargs="+",
        type=pathlib.Path,
        help="Input AMI L2 geostationary NetCDF files to add 'x' and 'y' variables to.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if not args.inplace and args.output_dir is None:
        parser.error("Either '--inplace' or '--output-dir' must be specified.")
    if args.output_dir and not args.output_dir.exists():
        logger.info(f"Creating output directory: {args.output_dir}")
        args.output_dir.mkdir(parents=True)

    for input_file in args.input_files:
        if not args.inplace:
            out_file = args.output_dir / input_file.name
            shutil.copy(input_file, out_file)
        else:
            out_file = input_file

        add_xy_to_file(out_file)
        print(out_file)


if __name__ == "__main__":
    sys.exit(main())
