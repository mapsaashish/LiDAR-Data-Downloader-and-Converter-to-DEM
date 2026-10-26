#!/usr/bin/env python3
"""PDAL/GDAL full-source fallback used internally by the laptop worker."""
import base64
import hashlib
import html
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import webbrowser




def pipeline(source, destination, config):
    stages = [
        {"type": "readers.las", "filename": str(source)},
        {"type": "filters.expression", "expression": "Classification == 2"},
        {"type": "filters.reprojection", "out_srs": config["crs"]},
    ]
    if config.get("crop_wkt"):
        stages.append({"type": "filters.crop", "polygon": config["crop_wkt"]})
    stages += [
        {"type": "writers.gdal", "filename": str(destination),
         "resolution": config["resolution"], "radius": config["resolution"] * math.sqrt(2),
         "output_type": "idw", "data_type": "float", "nodata": -9999,
         "allow_empty": True,
         "gdalopts": "TILED=YES,COMPRESS=DEFLATE,BIGTIFF=IF_SAFER"}
    ]
    return {"pipeline": stages}


def run(args):
    subprocess.run([str(arg) for arg in args], check=True)


def acquire(record, destination):
    expected = record["size"]
    if destination.exists() and expected > 0 and destination.stat().st_size == expected:
        print("Reusing complete source:", destination.name)
        return
    partial = destination.with_name(destination.name + ".part")
    if partial.exists() and expected > 0 and partial.stat().st_size == expected:
        partial.replace(destination)
        return
    run(["curl", "--silent", "--show-error", "--fail", "--location", "--retry", "6", "--retry-delay", "10",
         "--connect-timeout", "60", "--continue-at", "-", "--output", partial, record["url"]])
    if expected > 0 and partial.stat().st_size != expected:
        raise RuntimeError("Source size differs from catalog: " + destination.name)
    partial.replace(destination)


def build_dem(config, folder, progress=lambda *args: None):
    resolution = config["resolution"]
    if resolution not in (1, 5, 10, 30):
        raise ValueError("Choose a resolution of 1, 5, 10, or 30 metres.")
    for executable in ("pdal", "gdalbuildvrt", "gdalwarp", "gdalinfo", "curl"):
        if not shutil.which(executable):
            raise RuntimeError(executable + " is missing. Activate a conda environment containing PDAL and GDAL. See the instructions at the top of this file.")
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    work = folder / ("dem-work-" + identity)
    work.mkdir(exist_ok=True)
    area = work / "area.geojson"
    area.write_text(json.dumps({"type": "FeatureCollection", "features": [config["area"]]}), encoding="utf-8")
    rasters = []
    records = config["records"]
    if not records:
        raise ValueError("No LiDAR source tiles selected.")
    # Crop points before gridding, which keeps small-AOI jobs small even when
    # the provider's complete source tile extends far beyond the drawn area.
    from osgeo import ogr, osr
    source_crs = osr.SpatialReference()
    source_crs.ImportFromEPSG(4326)
    source_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target_crs = osr.SpatialReference()
    target_crs.SetFromUserInput(config["crs"])
    target_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    crop = ogr.CreateGeometryFromJson(json.dumps(config["area"]["geometry"]))
    crop.Transform(osr.CoordinateTransformation(source_crs, target_crs))
    config = {**config, "crop_wkt": crop.Buffer(3 * resolution).ExportToWkt()}
    for index, record in enumerate(records):
        print(f"\n[{index + 1}/{len(records)}] Download / convert {record['name']}", flush=True)
        source = folder / record["name"]
        progress("downloading", index, len(records), source, record)
        acquire(record, source)
        progress("converting", index, len(records), source, record)
        raster = work / (record["name"] + ".tif")
        complete = work / (record["name"] + ".complete")
        if not (raster.exists() and complete.exists()):
            job = work / "pipeline.json"
            job.write_text(json.dumps(pipeline(source, raster, config)), encoding="utf-8")
            # Streaming avoids loading the entire LAS/LAZ tile into RAM.
            run(["pdal", "pipeline", job, "--stream"])
            if not raster.exists():
                progress("tile_complete", index + 1, len(records), source, record)
                continue
            run(["gdalinfo", "-mm", raster])
            complete.write_text("Complete", encoding="utf-8")
        rasters.append(raster)
        progress("tile_complete", index + 1, len(records), source, record)
    if not rasters:
        raise RuntimeError("No ground-classified points were found inside this area. Choose another area or survey.")
    progress("mosaicking", len(records), len(records), None, None)
    listing = work / "rasters.txt"
    listing.write_text("\n".join(str(p) for p in rasters) + "\n", encoding="utf-8")
    mosaic = work / "mosaic.vrt"
    run(["gdalbuildvrt", "-overwrite", "-srcnodata", "-9999", "-vrtnodata", "-9999", "-input_file_list", listing, mosaic])
    output = folder / ("lidar-" + config["geoid"] + "-" + identity + "-dem-" + str(resolution) + "m.tif")
    # Write to a temporary file so an interrupted mosaic is never offered as complete.
    staging = work / "final.partial.tif"
    run(["gdalwarp", "-overwrite", "-of", "GTiff", "-t_srs", config["crs"],
         "-tr", resolution, resolution, "-tap", "-cutline", area, "-crop_to_cutline",
         "-dstnodata", "-9999", "-r", "bilinear", "-wm", "256", "-ot", "Float32",
         "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", "-co", "BIGTIFF=IF_SAFER", mosaic, staging])
    info = subprocess.run(["gdalinfo", "-json", str(staging)], check=True, capture_output=True, text=True)
    metadata = json.loads(info.stdout)
    transform = metadata.get("geoTransform", [])
    if len(transform) != 6 or not math.isclose(abs(transform[1]), resolution) or not math.isclose(abs(transform[5]), resolution):
        raise RuntimeError("Output raster resolution verification failed.")
    staging.replace(output)
    progress("verifying", len(records), len(records), None, None)
    provenance = {**config, "output": output.name, "method": "Ground class 2, per-tile IDW; bilinear mosaic clipped to AOI", "nodata": -9999,
                  "vertical_reference": "Source elevation units and vertical datum are preserved. Confirm survey metadata before analysis.",
                  "coverage": "NoData is retained where ground coverage is absent. Pixel size does not imply vertical accuracy."}
    output.with_suffix(".json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return output

