#!/usr/bin/env python3
"""LiDAR Atlas local DEM conversion job. Run in a PDAL/GDAL environment.

Suggested environment (install Miniforge first if needed):
  conda create -n lidar-atlas -c conda-forge python=3.12 pdal gdal
  conda activate lidar-atlas
  python this-downloaded-file.py
"""
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

CONFIG_B64 = "__LIDAR_ATLAS_CONFIG__"


def pipeline(source, destination, config):
    return {"pipeline": [
        {"type": "readers.las", "filename": str(source)},
        {"type": "filters.expression", "expression": "Classification == 2"},
        {"type": "filters.reprojection", "out_srs": config["crs"]},
        {"type": "writers.gdal", "filename": str(destination),
         "resolution": config["resolution"], "radius": config["resolution"] * math.sqrt(2),
         "output_type": "idw", "data_type": "float", "nodata": -9999,
         "gdalopts": "TILED=YES,COMPRESS=DEFLATE,BIGTIFF=IF_SAFER"}
    ]}


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
    run(["curl", "--fail", "--location", "--retry", "6", "--retry-delay", "10",
         "--connect-timeout", "60", "--continue-at", "-", "--output", partial, record["url"]])
    if expected > 0 and partial.stat().st_size != expected:
        raise RuntimeError("Source size differs from catalog: " + destination.name)
    partial.replace(destination)


def build_dem(config, folder):
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
    for index, record in enumerate(records):
        print(f"\n[{index + 1}/{len(records)}] Download / convert {record['name']}", flush=True)
        source = folder / record["name"]
        acquire(record, source)
        raster = work / (record["name"] + ".tif")
        complete = work / (record["name"] + ".complete")
        if not (raster.exists() and complete.exists()):
            job = work / "pipeline.json"
            job.write_text(json.dumps(pipeline(source, raster, config)), encoding="utf-8")
            # Streaming avoids loading the entire LAS/LAZ tile into RAM.
            run(["pdal", "pipeline", job, "--stream"])
            if not raster.exists():
                raise RuntimeError("No ground raster produced. Source must contain ground-classified points (class 2).")
            run(["gdalinfo", "-mm", raster])
            complete.write_text("Complete", encoding="utf-8")
        rasters.append(raster)
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
    provenance = {**config, "output": output.name, "method": "Ground class 2, per-tile IDW; bilinear mosaic clipped to AOI", "nodata": -9999,
                  "vertical_reference": "Source elevation units and vertical datum are preserved. Confirm survey metadata before analysis.",
                  "coverage": "NoData is retained where ground coverage is absent. Pixel size does not imply vertical accuracy."}
    output.with_suffix(".json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return output


def serve_result(output):
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/" + token:
                body = ("<!doctype html><title>Your DEM is ready</title><main style='font:18px system-ui;max-width:700px;margin:80px auto'>"
                        "<h1>Your DEM is ready.</h1><p>GeoTIFF saved to " + html.escape(str(output)) + "</p>"
                        "<p><a href='/" + token + "/dem'>Download converted DEM (.tif)</a></p>"
                        "<p>Missing ground coverage remains NoData. Keep this terminal open while downloading.</p></main>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/" + token + "/dem":
                self.send_response(200)
                self.send_header("Content-Type", "image/tiff")
                self.send_header("Content-Disposition", 'attachment; filename="' + output.name + '"')
                self.send_header("Content-Length", str(output.stat().st_size))
                self.end_headers()
                with output.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
            else:
                self.send_error(404)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/{token}"
        print("\nDEM complete:", output, "\nDownload page:", url, "\nPress Ctrl+C to stop the download page.", flush=True)
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    config = json.loads(base64.b64decode(CONFIG_B64))
    print(config["title"], "|", config["resolution"], "m |", config["crs"], "|", len(config["records"]), "source tiles")
    print("Only ground-classified points are used. Elevation units/datum follow the source survey.")
    folder = input("Folder for source tiles and converted DEM (existing Atlas download folder can be reused): ").strip().strip('"')
    if not folder:
        raise ValueError("A destination folder is required.")
    output = build_dem(config, folder)
    serve_result(output)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print("\nConversion stopped:", error, file=sys.stderr)
        print("Completed source files and tile rasters are retained. Rerun this job to resume.", file=sys.stderr)
        sys.exit(1)
