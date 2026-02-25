#!/usr/bin/env bash

# Title needs to match NWS AOIs for AWIPS to accept the file
# 001 should match Alaska
NETCDF_TITLE="${NETCDF_TITLE:-VIIRS_Flood_NWS001}"
# 001 -169.0  -129.0  54.0    72.0
# 002 -91.0   -66.0   35.0    52.0
# 003 -106.0  -81.0   37.0    54.0
# 004 -91.0   -75.0   24.0    36.0
# 005 -115.0  -90.0   36.0    53.0
# 006 -115.0  -90.0   23.0    40.0
# 007 -125.0  -113.0  35.0    52.0
# 008 -125.0  -113.0  28.0    45.0
if [ "$NETCDF_TITLE" == "VIIRS_Flood_NWS001" ]; then
    MERGE_FLAGS="${MERGE_FLAGS-"-ul_lr -169.0 72.0 -129.0 54.0"}"
else
    MERGE_FLAGS="${MERGE_FLAGS:-""}"
fi

if [ $# -le 2 ]; then
    echo "Usage: $0 <output-dir> flood1.nc flood2.nc flood3.nc ..."
    exit 1
fi

oops() {
    >&2 echo "ERROR: $*"
    >&2 echo "FAILURE"
    exit 1
}

debug() {
    >&2 echo "DEBUG: $*"
}

set -e

output_dir=$1
shift 1
# get sorted array of input filenames
IFS=$'\n' input_filenames=($(printf "%s\n" "${@}" | sort -n))
unset IFS

get_output_filename() {
    python - "$@" <<'EOF'
import os
import sys
from datetime import datetime, timezone

TIME_FIELD_LEN = 15

creation_time = datetime.now(timezone.utc)
cstr = creation_time.strftime("%Y%m%d%H%M%S0")
input_filenames = sys.argv[1:]
first_fn = os.path.basename(input_filenames[0])

last_fn = os.path.basename(input_filenames[-1])
end_index = first_fn.find("_e")
last_end = last_fn[end_index + 1: end_index + 1 + TIME_FIELD_LEN + 1]

prefix = first_fn[:end_index]
output_fmt = "{prefix}_{last_end}_c{cstr}.nc"

print(output_fmt.format(prefix=prefix, last_end=last_end, cstr=cstr))

EOF

}

add_attributes() {
    python - "$@" <<EOF

import sys
from netCDF4 import Dataset

def extract_metadata(first_fn, last_fn):
    var_meta = {
        "WaterDetection": {},
        "lon": {},
        "lat": {},
    }
    global_meta = {
        # pull title from bash
        "title": "$NETCDF_TITLE",
    }

    global_attrs = (
        "time_coverage_start",
        "Satellitename",
        "SensorIdentifyCode",
        "Resolution",
        "production_site",
        "production_environment",
        "source",
        "platform",
        "instrument",
        "standard_name_vocabulary",
        "Conventions",
        )
    wd_attrs = ("long_name", "ProjectionResolution", "scale_factor", "add_offset", "units", "Type", "TypeDescription")
    with Dataset(first_fn, "r") as first_nc:
        for attr_name in global_attrs:
            if not hasattr(first_nc, attr_name):
                # if upstream algorithm changes attribute names, just skip
                print("WARNING: Missing expected global attribute: {}".format(attr_name))
                continue
            global_meta[attr_name] = getattr(first_nc, attr_name)

        for attr_name in wd_attrs:
            var_meta["WaterDetection"][attr_name] = getattr(first_nc["WaterDetection"], attr_name)

        for attr_name in ("long_name", "units", "standard_name"):
            var_meta["lon"][attr_name] = getattr(first_nc["lon"], attr_name)
            var_meta["lat"][attr_name] = getattr(first_nc["lat"], attr_name)

    with Dataset(last_fn) as last_nc:
        global_meta["time_coverage_end"] = last_nc.time_coverage_end

    return global_meta, var_meta

output_filename = sys.argv[1]
input_filenames = sys.argv[2:]
global_meta, var_meta = extract_metadata(input_filenames[0], input_filenames[-1])

with Dataset(output_filename, "a") as out_nc:
    for attr_name, attr_val in global_meta.items():
        setattr(out_nc, attr_name, attr_val)

    for var_name, attr_dict in var_meta.items():
        for attr_name, attr_val in attr_dict.items():
            setattr(out_nc[var_name], attr_name, attr_val)

EOF
}


create_forced_lonlat_copies() {
    python - "$@" <<EOF

import os
import shutil
import sys
import tempfile
import numpy as np
from netCDF4 import Dataset


def force_32bit_lonlat(nc_file):
    """Force more consistent spacing in 32-bit lon/lat files."""
    with Dataset(nc_file, "a") as nc:
        if nc["lon"].dtype.itemsize != 4:
            # not 32-bit, algorithm isn't producing 32-bit floats anymore
            return

        old_lon = nc["lon"][:]
        res = nc.Resolution
        if old_lon[1] - old_lon[0] < 0:
            res = -res
        new_lon = (np.arange(old_lon.shape[0]) * res + old_lon[0]).astype(np.float32)
        # assume USA negative longitudes
        new_lon[new_lon > 0] -= 360.0
        nc["lon"][:] = new_lon

        old_lat = nc["lat"][:]
        res = nc.Resolution
        if old_lat[1] - old_lat[0] < 0:
            res = -res
        nc["lat"][:] = (np.arange(old_lat.shape[0]) * res + old_lat[0]).astype(np.float32)

tmp_dir = tempfile.mkdtemp(prefix="merge_floods_")
input_filenames = sys.argv[1:]
for orig_nc_path in input_filenames:
    orig_filename = os.path.basename(orig_nc_path)
    new_path = os.path.join(tmp_dir, orig_filename)
    shutil.copy(orig_nc_path, new_path)
    force_32bit_lonlat(new_path)

print(tmp_dir)
EOF
}


merge_dataset_string() {
    local dataset_str=""
    # Prepare GDAL dataset list
    for input_fn in "$@"; do
        if [ ! -f "${input_fn}" ]; then
            oops "Input file ${input_fn} does not exist"
        fi
        dataset_str="${dataset_str} NETCDF:\"${input_fn}\":WaterDetection"
    done
    dataset_str=${dataset_str:1}
    echo "${dataset_str}"
}


simple_merge() {
    local output_filename="$1"
    shift 1
    local dataset_str=$(merge_dataset_string "$@")
    # Specify BAND_NAMES (GDAL 3.9+) to force the NetCDF variable name
    # _FillValue for WaterDetection is 1, preserve that for the output
    debug "Merging input NetCDF files..."
    # get the resolution in degrees
    res=$(ncdump -h $1 | grep ":Resolution = " | cut -d ' ' -f 3)
    # remove floating point 'f' suffix
    res="${res/f/}"
    if ! [[ "${MERGE_FLAGS}" =~ -ps ]]; then
        MERGE_FLAGS="${MERGE_FLAGS} -ps ${res} -${res}"
        if ! [[ "${MERGE_FLAGS}" =~ -tap ]]; then
            MERGE_FLAGS="${MERGE_FLAGS} -tap"
        fi
    fi
    if [ "${MERGE_FLAGS}" != "" ]; then
        debug "Using additional merge flags: ${MERGE_FLAGS}"
    fi
    gdal_merge -a_nodata 1 -of netCDF -o "${output_filename}" ${MERGE_FLAGS} -co "COMPRESS=DEFLATE" -co "FORMAT=NC4" -co "BAND_NAMES=WaterDetection" ${dataset_str} 1>&2
}

if [[ ! -d "${output_dir}" ]]; then
    debug "Creating output directory: \"${output_dir}\""
    mkdir -p "${output_dir}"
fi

output_filename="${output_dir}/$(get_output_filename "${input_filenames[@]}")"
debug "Writing merged output to \"${output_filename}\"..."

new_input_dir=$(create_forced_lonlat_copies "${input_filenames[@]}")
debug "Created modified input NetCDF files in ${new_input_dir}"
simple_merge "${output_filename}" ${new_input_dir}/*.nc
#simple_merge "${output_filename}" "$@"


debug "Updating output netcdf attributes..."
add_attributes "${output_filename}" "${input_filenames[@]}"

debug "Deleting temporary working directory ${new_input_dir}"
rm -rf "${new_input_dir}" || debug "Couldn't delete temporary working directory ${new_input_dir}"
echo "${output_filename}"
debug "SUCCESS"
