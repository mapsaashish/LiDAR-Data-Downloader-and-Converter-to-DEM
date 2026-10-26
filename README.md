# LiDAR Data Downloader and Converter to DEM

React UI for USGS LiDAR discovery, complete source-tile downloads, and local terrain-model conversion. PostgreSQL is provided by Supabase project **LiDAR-Data-Downloader-and-Converter-to-DEM** (`bhvwviwdxlcqfcefmnkf`).

App: https://lidar-atlas-aoi.chirpy-boot-5223.chatgpt.site

## Features

- State/county selection using Census TIGERweb boundaries.
- Draw a polygon or rectangle for a smaller area; drawings are intersected with the county.
- Four concurrent USGS catalog requests, bounded retries, full pagination, deterministic deduplication, cancellation, and a background spatial boundary index.
- Full source LAS/LAZ downloads to a chosen folder in supporting Chrome/Edge browsers, plus a resumable Windows PowerShell downloader.
- DEM resolution choices of **1 m, 5 m, 10 m, or 30 m**. Pick one source survey to avoid mixing elevation references.
- Download a self-contained Python conversion job. It downloads/reuses source files, streams ground-classified points through PDAL, mosaics/clips with GDAL, verifies grid resolution, and opens a local GeoTIFF download page.
- Supabase Auth and PostgreSQL storage for private saved areas and prepared-job history, protected by row-level security.

## Run the React app

Node.js 22.12+ is required (Node 24 recommended).

```sh
npm ci
npm run dev
```

For a production build:

```sh
npm run build
npm run preview
```

Deploy the generated `dist/` directory to a static host. The `.openai/hosting.json` file binds the existing private Sites deployment. Do not reuse that identity for a different Site.

Public browser configuration is in `src/supabase.js`; copy `.env.example` to `.env.local` to override it. Only a **publishable** key is used. Never place a Supabase secret or service-role key in frontend code.

## PostgreSQL backend

The connected Supabase project already has the schema applied. `supabase/schema.sql` records the table definitions, ownership policies, and indexes for reproducibility. Do not reapply it blindly to an existing database. `supabase/rls-test.sql` tests owner access and cross-user isolation inside a rolled-back transaction.

`saved_areas` stores county selections and optional GeoJSON areas. `dem_jobs` records prepared conversion jobs; **prepared does not mean conversion completed**. No LiDAR binaries or completed GeoTIFFs are uploaded to Supabase. Users can search/download without signing in; account creation/sign-in is only needed for private saved data. Email confirmation follows the project's Supabase Auth settings.

## Convert LiDAR to DEM

Conversion is local, not an in-browser or cloud compute service. Install [Miniforge](https://github.com/conda-forge/miniforge), then create an environment:

```sh
conda create -n lidar-atlas -c conda-forge python=3.12 pdal gdal
conda activate lidar-atlas
python path-to-downloaded-conversion-job.py
```

Choose a destination folder when prompted. The converter resumes cached source files, creates intermediate ground rasters one tile at a time, clips the final GeoTIFF to the selected area, and opens a loopback-only download page. Keep the terminal open while downloading. The file is also already in your chosen folder.

Processing uses classification 2 (ground), IDW interpolation, a local UTM horizontal CRS, and a bilinear mosaic. Source elevation units and vertical datum are preserved; inspect survey metadata before analysis. NoData remains where ground coverage is absent. Requested pixel size does not imply source accuracy. Full-county 1 m conversion can require substantial storage, time, and RAM; a smaller drawn area is preferable for laptop workflows. Boundary source-tile downloads include complete intersecting tiles; only the DEM is clipped.

## Tests and verification

```sh
npm test
python -B dem.test.py
npm run build
```

Tests cover pagination completeness, concurrency, retries, cancellation, ordering, polygon holes/islands/boundary intersections, conversion settings, missing tools, reuse, and file-size checks. Live checks returned the same 1,766 Orange County tiles in 6.6 seconds after indexing, versus 27.4 seconds before indexing in that session. Timing depends on USGS and network load. A drawn 87.88 km² area returned 49 tiles and produced a valid 5 m conversion-job configuration.

Full PDAL/GDAL conversion has not been run in the authoring workspace because those executables are not installed. Direct file transfers may be rate-limited or blocked by source-server browser policies; the local download/conversion jobs include retries and resume support.

## Sources

- [USGS National Map](https://www.usgs.gov/the-national-map-data-delivery/gis-data-download)
- [Census TIGERweb](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer)
- [PDAL raster writer](https://pdal.io/en/stable/stages/writers.gdal.html)
- [GDAL raster clipping](https://gdal.org/en/stable/programs/gdalwarp.html)

