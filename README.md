# LiDAR Data Downloader and Converter to DEM

React UI for USGS LiDAR discovery, complete source-tile downloads, and local terrain-model conversion. PostgreSQL is provided by Supabase project **LiDAR-Data-Downloader-and-Converter-to-DEM** (`bhvwviwdxlcqfcefmnkf`).

App: https://lidar-atlas-aoi.chirpy-boot-5223.chatgpt.site

## Features

- State/county selection using Census TIGERweb boundaries.
- Draw a polygon or rectangle for a smaller area; drawings are intersected with the county.
- Hover map tiles for survey, provider, publication/update dates, format, and size; click to pin details and open metadata. Acquisition dates are not inferred from publication dates.
- Four concurrent USGS catalog requests, bounded retries, full pagination, deterministic deduplication, cancellation, and a background spatial boundary index.
- Full source LAS/LAZ downloads to a chosen folder in supporting Chrome/Edge browsers, plus a resumable Windows PowerShell downloader.
- DEM resolution choices of **1 m, 5 m, 10 m, or 30 m**. Pick one source survey to avoid mixing elevation references.
- Convert directly in the app with estimated percentage progress, cancellation, reconnect after refresh, and an automatic finished GeoTIFF download.
- Default AOI-only EPT streaming avoids saving complete LAS/LAZ files; missing streaming sources fail explicitly unless the user chooses full-tile fallback.
- A loopback-only laptop worker performs PDAL/GDAL processing in bounded raster blocks. Successful streaming jobs remove intermediate rasters.
- Supabase Auth and PostgreSQL storage for private saved areas and processing-job history, protected by row-level security.

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

`saved_areas` stores county selections and optional GeoJSON areas. `dem_jobs` stores live processing statuses and percentages synchronized by the signed-in browser. Apply `supabase/processing-jobs.sql` after the initial schema when reproducing this backend; it is already applied to the connected project. No LiDAR binaries or completed GeoTIFFs are uploaded to Supabase. Users can search/download without signing in; account creation/sign-in is only needed for private saved data. Email confirmation follows the project's Supabase Auth settings.

## Convert directly to a finished DEM

The user chooses the area, resolution, and survey, then clicks **Convert & download DEM**. The UI shows estimated processing percentage and phase, offers cancellation, and automatically starts the final `.tif` download at 100%. An explicit download link remains if the browser blocks automatic downloads. The browser's download manager shows file-transfer progress.

This deployment uses **this laptop as its worker**, as requested. The worker runs at `http://127.0.0.1:8765`, binds only to loopback, allows requests from the app's origin, and protects job status/results with per-job capability tokens. The laptop must remain awake and the worker must be running. A browser may ask for local-network access. Other devices need their own worker; there is no cloud compute server.

The isolated runtime is installed in `.runtime/` (ignored by Git). To restart the already-installed worker after reboot:

```powershell
./worker/start-worker.ps1
```

To stop it:

```powershell
./worker/stop-worker.ps1
```

For a fresh checkout, install [Miniforge](https://github.com/conda-forge/miniforge), then install the CPU-only runtime:

```sh
conda create --yes --prefix .runtime --override-channels -c conda-forge python=3.12 libpdal-core gdal
```

**Stream my area only** is the default. The worker resolves exact and legacy names against the USGS/Entwine inventory, verifies the EPT endpoint for the selected survey, reads only octree nodes intersecting buffered AOI blocks, uses all native point levels (no intentional thinning), filters ground class 2, and produces 1/5/10/30 m IDW rasters in a local UTM CRS. It mosaics and clips the final raster, verifies cell size, and serves the finished GeoTIFF directly. Streaming still transfers required data over the network, but does not store complete `.las`/`.laz` sources. Final rasters and provenance are in `.worker-data/cache`; successful streaming intermediates are removed. Interrupted work keeps reusable intermediate rasters.

For surveys without a verified EPT endpoint, the worker stops with an explanation. The DEM survey selector shows only verified usable sources for streaming mode. Legacy Martin 2015 and Palm Beach 2016 names resolve without switching acquisition years. Full-file downloads only happen when users explicitly select **Download source tiles**. That mode retains source files for reuse. Source elevation units and vertical datum follow the source product; inspect metadata before combining surveys. Missing coverage remains NoData. A finer grid does not imply improved accuracy.

Progress is estimated from completed work units and stage weights; it is not a precise time forecast. Streaming and gridding can remain at the same percentage while a block is processed. A tiny real Orange County AOI was streamed, converted, and downloaded successfully: 11 × 12 pixels at 5 m, valid ground elevations, zero raw LAS/LAZ files saved.

## Tests and verification

```sh
npm test
python -B dem.test.py
python -B worker/test_worker.py
npm run build
```

Tests cover pagination completeness, concurrency, retries, cancellation, ordering, polygon holes/islands/boundary intersections, conversion settings, missing tools, reuse, and file-size checks. Live checks returned the same 1,766 Orange County tiles in 6.6 seconds after indexing, versus 27.4 seconds before indexing in that session. Timing depends on USGS and network load. A drawn 87.88 km² area returned 49 tiles and produced a valid 5 m conversion-job configuration.

Real PDAL/GDAL streaming conversion and final HTTP file download have been verified on this laptop. Whole-county conversion is not benchmarked; time and disk needs depend on area, resolution, source density, and network conditions.

## Sources

- [USGS National Map](https://www.usgs.gov/the-national-map-data-delivery/gis-data-download)
- [Census TIGERweb](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer)
- [PDAL raster writer](https://pdal.io/en/stable/stages/writers.gdal.html)
- [GDAL raster clipping](https://gdal.org/en/stable/programs/gdalwarp.html)
