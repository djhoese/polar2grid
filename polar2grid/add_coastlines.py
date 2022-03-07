#!/usr/bin/env python3
# encoding: utf-8
# Copyright (C) 2016-2021 Space Science and Engineering Center (SSEC),
# University of Wisconsin-Madison.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# This file is part of the polar2grid software package. Polar2grid takes
# satellite observation data, remaps it, and writes it to a file format for
#     input into another program.
# Documentation: http://www.ssec.wisc.edu/software/polar2grid/
#
# Written by David Hoese    July 2016
# University of Wisconsin-Madison
# Space Science and Engineering Center
# 1225 West Dayton Street
# Madison, WI  53706
# david.hoese@ssec.wisc.edu
"""Script to add coastlines and borders to a geotiff while also creating a PNG."""

import argparse
import logging
import os
import sys

import numpy as np
import rasterio
from aggdraw import Font
from PIL import Image, ImageFont
from pkg_resources import resource_filename as get_resource_filename
from pycoast import ContourWriterAGG
from pyresample.utils import get_area_def_from_raster
from trollimage.colormap import Colormap

from polar2grid.utils.config import add_polar2grid_config_paths

LOG = logging.getLogger(__name__)
PYCOAST_DIR = os.environ.get("GSHHS_DATA_ROOT")


def _convert_table_to_cmap_or_default_bw(band_dtype, band_ct, band_count):
    max_val = np.iinfo(band_dtype).max
    # if we have an alpha band then include the entire colormap
    # otherwise assume it is using 0 as a fill value
    start_idx = 1 if band_count == 1 else 0
    if band_ct is None:
        # NOTE: the comma is needed to make this a tuple
        color_iter = ((idx / float(max_val), (int(idx / float(max_val)),) * 3 + (1.0,)) for idx in range(max_val))
    else:
        color_iter = ((idx / float(max_val), color) for idx, color in sorted(band_ct.items())[start_idx:])
        color_iter = ((idx, tuple(x / float(max_val) for x in color)) for idx, color in color_iter)
    cmap = Colormap(*color_iter)
    return cmap


def _get_rio_colormap(rio_ds, bidx):
    try:
        return rio_ds.colormap(bidx)
    except ValueError:
        return None


def find_font(font_name, size):
    try:
        font = ImageFont.truetype(font_name, size)
        return font.path
    except IOError:
        font_path = get_resource_filename("polar2grid.fonts", font_name)
        if not os.path.exists(font_path):
            raise ValueError("Font path does not exist: {}".format(font_path))
        return font_path


def _args_to_pycoast_dict(args):
    opts = {}
    if args.add_coastlines:
        outline = (
            args.coastlines_outline[0]
            if len(args.coastlines_outline) == 1
            else tuple(int(x) for x in args.coastlines_outline)
        )
        if args.coastlines_fill:
            fill = (
                args.coastlines_fill[0]
                if len(args.coastlines_fill) == 1
                else tuple(int(x) for x in args.coastlines_fill)
            )
        else:
            fill = None
        opts["coasts"] = {
            "resolution": args.coastlines_resolution,
            "level": args.coastlines_level,
            "width": args.coastlines_width,
            "outline": outline,
            "fill": fill,
        }

    if args.add_rivers:
        outline = (
            args.rivers_outline[0] if len(args.rivers_outline) == 1 else tuple(int(x) for x in args.rivers_outline)
        )
        opts["rivers"] = {
            "resolution": args.rivers_resolution,
            "level": args.rivers_level,
            "width": args.rivers_width,
            "outline": outline,
        }

    if args.add_borders:
        outline = (
            args.borders_outline[0] if len(args.borders_outline) == 1 else tuple(int(x) for x in args.borders_outline)
        )
        opts["borders"] = {
            "resolution": args.borders_resolution,
            "level": args.borders_level,
            "width": args.borders_width,
            "outline": outline,
        }

    if args.add_grid:
        outline = args.grid_outline[0] if len(args.grid_outline) == 1 else tuple(int(x) for x in args.grid_outline)
        minor_outline = (
            args.grid_minor_outline[0]
            if len(args.grid_minor_outline) == 1
            else tuple(int(x) for x in args.grid_minor_outline)
        )
        fill = args.grid_fill[0] if len(args.grid_fill) == 1 else tuple(int(x) for x in args.grid_fill)
        font_path = find_font(args.grid_font, args.grid_text_size)
        font = Font(outline, font_path, size=args.grid_text_size)
        opts["grid"] = {
            "lon_major": args.grid_D[0],
            "lat_major": args.grid_D[1],
            "lon_minor": args.grid_d[0],
            "lat_minor": args.grid_d[1],
            "font": font,
            "fill": fill,
            "outline": outline,
            "minor_outline": minor_outline,
            "write_text": args.grid_text,
            "width": args.grid_width,
            "lon_placement": args.grid_lon_placement,
            "lat_placement": args.grid_lat_placement,
        }

    if args.cache_dir:
        opts["cache"] = {
            # add "add_coastlines" prefix to cached image name
            "file": os.path.join(args.cache_dir, "add_coastlines"),
            "regenerate": args.cache_regenerate,
        }

    return opts


def _get_colorbar_vmin_vmax(arg_min, arg_max, rio_ds, input_dtype, is_palette=False):
    metadata = rio_ds.tags()
    scale = metadata.get("scale", metadata.get("scale_factor"))
    offset = metadata.get("offset", metadata.get("add_offset"))
    dtype_min = float(np.iinfo(input_dtype).min)
    dtype_max = float(np.iinfo(input_dtype).max)
    if is_palette:
        dtype_min = 0.0
        dtype_max = 1.0

    if arg_min is None and scale is None:
        LOG.warning(
            "Colorbar min/max metadata not found and not provided "
            "on the command line. Defaulting to data type limits."
        )
        return dtype_min, dtype_max

    if arg_min is not None:
        vmin = float(arg_min)
        vmax = float(arg_max)
    else:
        scale = float(scale)
        offset = float(offset)
        if np.isnan(scale) or np.isnan(offset):
            raise ValueError(
                "Can't automatically set colorbar limits with "
                "geotiff metadata as scale/offset are set to "
                "NaN. This indicates a non-linear enhancement or "
                "RGB/A composite that was enhanced. These cases "
                "cannot be represented properly by a colorbar."
            )
        delta = dtype_max - dtype_min
        vmin = offset
        vmax = delta * scale + offset
        # floating point error made it not an integer
        if abs(vmin - np.round(vmin, 0)) <= 0.001:
            vmin = np.round(vmin, 0)
        if abs(vmax - np.round(vmax, 0)) <= 0.001:
            vmax = np.round(vmax, 0)
    return vmin, vmax


def _apply_decorator_alignment(dc, align, is_vertical):
    default_align = "left" if is_vertical else "bottom"
    if align is None:
        align = default_align
    if align == "top":
        dc.align_top()
    elif align == "bottom":
        dc.align_bottom()
    elif align == "left":
        dc.align_left()
    elif align == "right":
        dc.align_right()


def _add_colorbar_to_image(input_tiff, img, num_bands, args):
    from pydecorate import DecoratorAGG

    font_color = args.colorbar_text_color
    font_color = font_color[0] if len(font_color) == 1 else tuple(int(x) for x in font_color)
    font_path = find_font(args.colorbar_font, args.colorbar_text_size)
    # this actually needs an aggdraw font
    font = Font(font_color, font_path, size=args.colorbar_text_size)

    # figure out what colormap we are dealing with
    rio_ds = rasterio.open(input_tiff)
    input_dtype = np.dtype(rio_ds.meta["dtype"])
    colormap_csv = rio_ds.tags().get("colormap")
    rio_ct = _get_rio_colormap(rio_ds, 1)
    is_palette = rio_ct is not None
    cmap = _convert_table_to_cmap_or_default_bw(input_dtype, rio_ct, num_bands)
    if num_bands in (3, 4) and colormap_csv is None:
        raise ValueError("RGB and RGBA geotiffs must have a colormap " "specified with '--colorbar-colormap-file'.")
    if num_bands in (3, 4) or colormap_csv is not None:
        cmap = Colormap.from_file(colormap_csv)
    vmin, vmax = _get_colorbar_vmin_vmax(
        args.colorbar_min, args.colorbar_max, rio_ds, input_dtype, is_palette=is_palette
    )
    cmap = cmap.set_range(vmin, vmax, inplace=False)

    dc = DecoratorAGG(img)
    _apply_decorator_alignment(dc, args.colorbar_align, args.colorbar_vertical)

    if args.colorbar_vertical:
        dc.write_vertically()
    else:
        dc.write_horizontally()

    if args.colorbar_width is None or args.colorbar_height is None:
        LOG.warning("'--colorbar-width' or '--colorbar-height' were " "not specified. Forcing '--colorbar-extend'.")
        args.colorbar_extend = True
    kwargs = {}
    if args.colorbar_width:
        kwargs["width"] = args.colorbar_width
    if args.colorbar_height:
        kwargs["height"] = args.colorbar_height
    dc.add_scale(
        cmap,
        extend=args.colorbar_extend,
        font=font,
        line=font_color,
        tick_marks=args.colorbar_tick_marks,
        title=args.colorbar_title,
        unit=args.colorbar_units,
        **kwargs,
    )


def get_parser():
    parser = argparse.ArgumentParser(
        description="Add overlays to a GeoTIFF file and save as a PNG file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_coastlines_arguments(parser)
    _add_rivers_arguments(parser)
    _add_grid_arguments(parser)
    _add_borders_arguments(parser)
    _add_colorbar_arguments(parser)
    _add_global_arguments(parser)
    parser.add_argument("input_tiff", nargs="+", help="Input geotiff(s) to process")
    return parser


def _add_coastlines_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("coastlines")
    group.add_argument("--add-coastlines", action="store_true", help="Add coastlines")
    group.add_argument(
        "--coastlines-resolution",
        choices="clihf",
        default="i",
        help="Resolution of coastlines to add (crude, low, intermediate, high, full)",
    )
    group.add_argument(
        "--coastlines-level",
        choices=range(1, 7),
        type=int,
        default=4,
        help="Level of detail from the selected resolution dataset",
    )
    group.add_argument(
        "--coastlines-outline",
        default=["yellow"],
        nargs="*",
        help="Color of coastline lines (color name or 3 RGB integers)",
    )
    group.add_argument("--coastlines-fill", default=None, nargs="*", help="Color of land")
    group.add_argument("--coastlines-width", default=1.0, type=float, help="Width of coastline lines")


def _add_rivers_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("rivers")
    group.add_argument("--add-rivers", action="store_true", help="Add rivers grid")
    group.add_argument(
        "--rivers-resolution",
        choices="clihf",
        default="c",
        help="Resolution of rivers to add (crude, low, intermediate, high, full)",
    )
    group.add_argument(
        "--rivers-level", choices=range(0, 11), type=int, default=5, help="Level of detail for river lines"
    )
    group.add_argument(
        "--rivers-outline", default=["blue"], nargs="*", help="Color of river lines (color name or 3 RGB integers)"
    )
    group.add_argument("--rivers-width", default=1.0, type=float, help="Width of rivers lines")


def _add_grid_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("grid")
    group.add_argument("--add-grid", action="store_true", help="Add lat/lon grid")
    group.add_argument("--grid-no-text", dest="grid_text", action="store_false", help="Add labels to lat/lon grid")
    group.add_argument("--grid-text-size", default=32, type=int, help="Lat/lon grid text font size")
    group.add_argument("--grid-font", default="Vera.ttf", help="Path to TTF font (package provided or custom path)")
    group.add_argument(
        "--grid-fill", nargs="*", default=["cyan"], help="Color of grid text (color name or 3 RGB integers)"
    )
    group.add_argument(
        "--grid-outline", nargs="*", default=["cyan"], help="Color of grid lines (color name or 3 RGB integers)"
    )
    group.add_argument(
        "--grid-minor-outline", nargs="*", default=["cyan"], help="Color of tick lines (color name or 3 RGB integers)"
    )
    group.add_argument(
        "--grid-D", nargs=2, default=(10.0, 10.0), type=float, help="Degrees between grid lines (lon, lat)"
    )
    group.add_argument(
        "--grid-d", nargs=2, default=(2.0, 2.0), type=float, help="Degrees between tick lines (lon, lat)"
    )
    group.add_argument(
        "--grid-lon-placement", choices=["tl", "lr", "lc", "cc"], default="tb", help="Longitude label placement"
    )
    group.add_argument(
        "--grid-lat-placement", choices=["tl", "lr", "lc", "cc"], default="lr", help="Latitude label placement"
    )
    group.add_argument("--grid-width", default=1.0, type=float, help="Width of grid lines")


def _add_borders_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("borders")
    group.add_argument("--add-borders", action="store_true", help="Add country and/or region borders")
    group.add_argument(
        "--borders-resolution",
        choices="clihf",
        default="i",
        help="Resolution of borders to add (crude, low, intermediate, high, full)",
    )
    group.add_argument(
        "--borders-level", choices=range(1, 4), default=2, type=int, help="Level of detail for border lines"
    )
    group.add_argument(
        "--borders-outline", default=["white"], nargs="*", help="Color of border lines (color name or 3 RGB integers)"
    )
    group.add_argument("--borders-width", default=1.0, type=float, help="Width of border lines")


def _add_colorbar_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("colorbar")
    group.add_argument("--add-colorbar", action="store_true", help="Add colorbar on top of image")
    group.add_argument(
        "--colorbar-colormap-file",
        help=argparse.SUPPRESS,
        # help="Specify the colormap file that was used to "
        # "colorize the provided RGB geotiff. Only used if "
        # "the provided geotiff is RGB/A. Otherwise the "
        # "geotiff is expected to include the colormap as "
        # "a geotiff color table.",
    )
    group.add_argument("--colorbar-width", type=int, help="Number of pixels wide")
    group.add_argument("--colorbar-height", type=int, help="Number of pixels high")
    group.add_argument(
        "--colorbar-extend", action="store_true", help="Extend colorbar to full width/height of the image"
    )
    group.add_argument("--colorbar-tick-marks", type=float, default=5.0, help="Tick interval in data units")
    group.add_argument("--colorbar-text-size", default=32, type=int, help="Tick label font size")
    group.add_argument(
        "--colorbar-text-color", nargs="*", default=["black"], help="Color of tick text (color name or 3 RGB integers)"
    )
    group.add_argument("--colorbar-font", default="Vera.ttf", help="Path to TTF font (package provided or custom path)")
    group.add_argument(
        "--colorbar-align",
        choices=["left", "top", "right", "bottom"],
        default=None,
        help="Which direction to align colorbar (see --colorbar-vertical)",
    )
    group.add_argument("--colorbar-vertical", action="store_true", help="Position the colorbar vertically")
    group.add_argument(
        "--colorbar-no-ticks",
        dest="colorbar_ticks",
        action="store_false",
        help="Don't include ticks and tick labels on colorbar",
    )
    group.add_argument(
        "--colorbar-min",
        type=float,
        help="Minimum data value of the colorbar."
        " Defaults to 'min_in' of input metadata or"
        " minimum value of the data otherwise.",
    )
    group.add_argument(
        "--colorbar-max",
        type=float,
        help="Maximum data value of the colorbar."
        " Defaults to 'max_in' of input metadata or"
        " maximum value of the data otherwise.",
    )
    group.add_argument("--colorbar-units", help="Units marker to include in the colorbar text")
    group.add_argument("--colorbar-title", help="Title shown with the colorbar")


def _add_global_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--shapes-dir",
        default=PYCOAST_DIR,
        help="Specify alternative directory for coastline shape files (default: GSHSS_DATA_ROOT)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Specify directory where cached coastline output can be stored and accessed in later "
        "executions. The cache will never be cleared by this script. Caching depends on the grid "
        "of the image and the decorations added to the image.",
    )
    parser.add_argument(
        "--cache-regenerate",
        action="store_true",
        help="Force regeneration of any cached overlays. Requires '--cache-dir'.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_filename",
        nargs="+",
        help="Specify the output filename (default replace '.tif' with '.png')",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbosity",
        action="count",
        default=0,
        help="each occurrence increases verbosity 1 level through ERROR-WARNING-INFO-DEBUG (default INFO)",
    )


def main(argv=sys.argv[1:]):
    parser = get_parser()
    args = parser.parse_args(argv)

    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    logging.basicConfig(level=levels[min(3, args.verbosity)])
    add_polar2grid_config_paths()

    if args.output_filename is None:
        args.output_filename = [x[:-3] + "png" for x in args.input_tiff]
    else:
        assert len(args.output_filename) == len(
            args.input_tiff
        ), "Output filenames must be equal to number of input tiffs"

    if not (args.add_borders or args.add_coastlines or args.add_grid or args.add_rivers or args.add_colorbar):
        LOG.error("Please specify one of the '--add-X' options to modify the image")
        return -1

    if args.cache_dir and not os.path.isdir(args.cache_dir):
        LOG.info(f"Creating cache directory: {args.cache_dir}")
        os.makedirs(args.cache_dir, exist_ok=True)

    # we may be dealing with large images that look like decompression bombs
    # let's turn off the check for the image size in PIL/Pillow
    Image.MAX_IMAGE_PIXELS = None
    # gather all options into a single dictionary that we can pass to pycoast
    pycoast_options = _args_to_pycoast_dict(args)
    for input_tiff, output_filename in zip(args.input_tiff, args.output_filename):
        LOG.info("Creating {} from {}".format(output_filename, input_tiff))
        img = Image.open(input_tiff)
        img_bands = img.getbands()
        num_bands = len(img_bands)
        # P = palette which we assume to be an RGBA colormap
        img = img.convert("RGBA" if num_bands in (2, 4) or "P" in img_bands else "RGB")
        if pycoast_options:
            area_id = os.path.splitext(input_tiff[0])[0]
            area_def = get_area_def_from_raster(input_tiff, area_id=area_id)
            cw = ContourWriterAGG(args.shapes_dir)
            cw.add_overlay_from_dict(pycoast_options, area_def, background=img)

        if args.add_colorbar:
            _add_colorbar_to_image(input_tiff, img, num_bands, args)

        img.save(output_filename)


if __name__ == "__main__":
    sys.exit(main())
