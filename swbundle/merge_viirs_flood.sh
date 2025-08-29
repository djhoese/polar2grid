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

dataset_str=""
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

if [[ ! -d "${output_dir}" ]]; then
    debug "Creating output directory: \"${output_dir}\""
    mkdir -p "${output_dir}"
fi

output_filename="${output_dir}/$(get_output_filename "${input_filenames[@]}")"
debug "Writing merged output to \"${output_filename}\"..."

# Prepare dataset list for GDAL
for input_fn in "${input_filenames[@]}"; do
    if [ ! -f "${input_fn}" ]; then
        oops "Input file ${input_fn} does not exist"
    fi
    dataset_str="${dataset_str} NETCDF:\"${input_fn}\":WaterDetection"
done
dataset_str=${dataset_str:1}

# Specify BAND_NAMES (GDAL 3.9+) to force the NetCDF variable name
# _FillValue for WaterDetection is 1, preserve that for the output
debug "Merging input NetCDF files..."
if [ "${MERGE_FLAGS}" != "" ]; then
    debug "Using additional merge flags: ${MERGE_FLAGS}"
fi
gdal_merge -a_nodata 1 -of netCDF -o "${output_filename}" ${MERGE_FLAGS} -co "COMPRESS=DEFLATE" -co "FORMAT=NC4" -co "BAND_NAMES=WaterDetection" ${dataset_str} 1>&2

debug "Updating output netcdf attributes..."
add_attributes "${output_filename}" "${input_filenames[@]}"

echo "${output_filename}"
debug "SUCCESS"
