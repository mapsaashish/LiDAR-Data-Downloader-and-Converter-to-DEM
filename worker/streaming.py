"""Read only selected EPT octree nodes; do not stage complete LAS/LAZ tiles."""
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import urlopen

_catalog = None
_catalog_lock = threading.Lock()
_verified = {}


def catalog_names():
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            # USGS/Entwine inventory preserves original survey names, including
            # legacy USGS_LPC_*_LAS_<publication-year> resource names.
            with urlopen('https://raw.githubusercontent.com/hobu/usgs-lidar/master/boundaries/resources.geojson',timeout=30) as response:
                data=json.load(response)
            _catalog={f['properties']['name'] for f in data['features']}
        return _catalog


def resolve_folder(folder):
    names=catalog_names()
    if folder in names:return folder
    aliases=[name for name in names if re.sub(r'_(?:LAS|LAZ)_\d{4}$','',name.removeprefix('USGS_LPC_'))==folder]
    if len(aliases)==1:return aliases[0]
    if len(aliases)>1:raise ValueError('Multiple streaming versions exist for '+folder+'. No survey was substituted.')
    raise ValueError('No streaming resource exists in the USGS inventory for '+folder+'.')


def ept_sources(records):
    folders=set()
    for record in records:
        parts=urlparse(record['url']).path.split('/')
        format_at=next((n for n,p in enumerate(parts) if p.upper() in ('LAS','LAZ')),None)
        if format_at is None or format_at<1:
            raise ValueError('No streaming source mapping for this survey. Choose source-tile mode explicitly to use full-file downloads.')
        folders.add(parts[format_at-1])
    sources=[]
    for folder in sorted(folders):
        try:
            resource=resolve_folder(folder)
            url='https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'+quote(resource,safe='')+'/ept.json'
            if time.time()-_verified.get(url,0)>3600:
                with urlopen(url,timeout=30) as response:metadata=json.load(response)
                if not any(d['name']=='Classification' for d in metadata['schema']):raise ValueError('No classification dimension')
                _verified[url]=time.time()
        except Exception as error:
            raise ValueError('Cloud streaming is unavailable for '+folder+'. No full tiles were downloaded. Choose source-tile mode if you want to use the original files.') from error
        sources.append(url)
    return sources


def build(config,folder,progress,run):
    from osgeo import ogr,osr,gdal
    sources=ept_sources(config['records'])
    resolution=config['resolution']
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:16]
    work=folder/('stream-work-'+identity);work.mkdir(exist_ok=True)
    area_path=work/'area.geojson';area_path.write_text(json.dumps({'type':'FeatureCollection','features':[config['area']]}),encoding='utf-8')
    wgs=osr.SpatialReference();wgs.ImportFromEPSG(4326);wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    utm=osr.SpatialReference();utm.SetFromUserInput(config['crs']);utm.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    area=ogr.CreateGeometryFromJson(json.dumps(config['area']['geometry']))
    area.Transform(osr.CoordinateTransformation(wgs,utm))
    xmin,xmax,ymin,ymax=area.GetEnvelope()
    # Each grid block stays bounded in memory, including at 1 m resolution.
    block=resolution*max(1,math.floor(1000/resolution))
    x0=math.floor(xmin/block)*block;y0=math.floor(ymin/block)*block
    tasks=[]
    for x in range(math.floor((xmax-x0)/block)+1):
        for y in range(math.floor((ymax-y0)/block)+1):
            left=x0+x*block;bottom=y0+y*block
            poly=ogr.CreateGeometryFromWkt(f'POLYGON(({left} {bottom},{left+block} {bottom},{left+block} {bottom+block},{left} {bottom+block},{left} {bottom}))')
            if area.Intersects(poly):tasks.extend((source,left,bottom,poly) for source in sources)
    rasters=[]
    for n,(source,left,bottom,poly) in enumerate(tasks):
        progress('streaming',n,len(tasks),None,None)
        target=work/f'block-{n:07d}.tif';marker=target.with_suffix('.complete')
        if not(target.exists() and marker.exists()):
            read_area=area.Intersection(poly).Buffer(resolution*3)
            stages=[{'type':'readers.ept','filename':source,'polygon':read_area.ExportToWkt()+'/'+config['crs'],'requests':4},
                    {'type':'filters.expression','expression':'Classification == 2'},
                    {'type':'filters.reprojection','out_srs':config['crs']},
                    {'type':'writers.gdal','filename':str(target),'resolution':resolution,'radius':resolution*math.sqrt(2),'output_type':'idw','data_type':'float','nodata':-9999,'allow_empty':True,
                     'origin_x':left,'origin_y':bottom,'width':int(block/resolution),'height':int(block/resolution),
                     'gdalopts':'TILED=YES,COMPRESS=DEFLATE,BIGTIFF=IF_SAFER'}]
            job=work/'pipeline.json';job.write_text(json.dumps({'pipeline':stages}),encoding='utf-8')
            run(['pdal','pipeline',job,'--stream'])
            if target.exists():marker.write_text('Complete',encoding='utf-8')
        if target.exists():rasters.append(target)
        progress('tile_complete',n+1,len(tasks),None,None)
    if not rasters:raise ValueError('No classified ground points were found within the selected area.')
    progress('mosaicking',len(tasks),len(tasks),None,None)
    listing=work/'rasters.txt';listing.write_text('\n'.join(str(p) for p in rasters)+'\n',encoding='utf-8')
    vrt=work/'mosaic.vrt';run(['gdalbuildvrt','-overwrite','-srcnodata','-9999','-vrtnodata','-9999','-input_file_list',listing,vrt])
    staging=work/'final.partial.tif'
    run(['gdalwarp','-overwrite','-of','GTiff','-t_srs',config['crs'],'-tr',resolution,resolution,'-tap','-cutline',area_path,'-crop_to_cutline','-dstnodata','-9999','-r','bilinear','-wm','256','-ot','Float32','-co','TILED=YES','-co','COMPRESS=DEFLATE','-co','BIGTIFF=IF_SAFER',vrt,staging])
    progress('verifying',len(tasks),len(tasks),None,None)
    dataset=gdal.Open(str(staging));transform=dataset.GetGeoTransform()
    if not math.isclose(abs(transform[1]),resolution) or not math.isclose(abs(transform[5]),resolution):raise ValueError('Output cell size verification failed.')
    if dataset.GetRasterBand(1).GetStatistics(False,True)[0] is None:raise ValueError('Output has no valid ground cells.')
    dataset=None
    output=folder/f'lidar-{config["geoid"]}-{identity}-dem-{resolution}m.tif';staging.replace(output)
    output.with_suffix('.json').write_text(json.dumps({'mode':'EPT AOI streaming; full source files were not saved','sources':sources,'resolution':resolution,'crs':config['crs'],'area':config['area'],'survey':config['survey'],'elevation_reference':'EPT source elevation units and vertical datum; verify survey metadata.'},indent=2),encoding='utf-8')
    # Only remove files generated in this job's verified intermediate directory.
    # Interrupted jobs keep their cache; successful jobs retain the final files.
    if work.resolve().is_relative_to(folder.resolve()) and work.name.startswith('stream-work-'):
        for generated in work.iterdir():
            if generated.is_file():
                try:generated.unlink()
                except OSError:pass
        try:work.rmdir()
        except OSError:pass
    return output
