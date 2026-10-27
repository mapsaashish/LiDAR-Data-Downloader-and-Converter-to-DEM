"""Loopback-only LiDAR processing worker. No external Python packages required
apart from the isolated PDAL/GDAL runtime. See worker/start-worker.ps1.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import streaming
import relay
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '.worker-data'
DATA.mkdir(exist_ok=True)
ORIGINS = {'https://lidar-atlas-aoi.chirpy-boot-5223.chatgpt.site', 'http://127.0.0.1:4174', 'http://127.0.0.1:5173', 'http://127.0.0.1:8765'}
os.environ['PATH'] = os.pathsep.join([str(Path(sys.prefix)/'Library'/'bin'), str(Path(sys.prefix)/'Scripts'), sys.prefix, os.environ.get('PATH','')])
os.environ.setdefault('GDAL_DATA', str(Path(sys.prefix)/'Library'/'share'/'gdal'))
os.environ.setdefault('PROJ_DATA', str(Path(sys.prefix)/'Library'/'share'/'proj'))
spec = importlib.util.spec_from_file_location('converter', ROOT/'worker'/'converter.py')
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)
jobs = {}
lock = threading.RLock()
pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
local = threading.local()


def validate(config):
    if config.get('mode','stream') not in ('stream','tiles'):
        raise ValueError('Invalid source access mode.')
    if config.get('resolution') not in (1,5,10,30):
        raise ValueError('Resolution must be 1, 5, 10, or 30 metres.')
    if not re.fullmatch(r'EPSG:32[67][0-9]{2}', str(config.get('crs',''))):
        raise ValueError('A metric UTM coordinate system is required.')
    if not re.fullmatch(r'\d{5}',str(config.get('geoid',''))):
        raise ValueError('Invalid county identifier.')
    area = config.get('area',{})
    if area.get('type')!='Feature' or area.get('geometry',{}).get('type') not in ('Polygon','MultiPolygon'):
        raise ValueError('A polygon area is required.')
    records = config.get('records',[])
    if not isinstance(records,list) or not 1<=len(records)<=50000:
        raise ValueError('Select at least one source tile.')
    clean=[]
    for record in records:
        parsed=urlparse(record.get('url',''))
        host=parsed.hostname or ''
        if parsed.scheme!='https' or not (host=='usgs.gov' or host.endswith('.usgs.gov') or host=='prd-tnm.s3.amazonaws.com'):
            raise ValueError('Only official USGS source files are allowed.')
        if not parsed.path.lower().endswith(('.las','.laz')) or parsed.username or parsed.password or parsed.port:
            raise ValueError('Invalid LiDAR file URL.')
        name=hashlib.sha256(record['url'].encode()).hexdigest()[:20]+Path(parsed.path).suffix.lower()
        size=int(record.get('size',0))
        if size<0: raise ValueError('Invalid source size.')
        clean.append({'url':record['url'],'name':name,'size':size})
    return {**config,'records':clean}


def update(job, **values):
    with lock:
        job.update(values,updated_at=time.time())
        target=DATA/job['id']/'job.json'
        target.parent.mkdir(exist_ok=True)
        temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps({k:v for k,v in job.items() if k not in ('cancel',)}),encoding='utf-8')
        temp.replace(target)


def command(args):
    job=local.job
    with (DATA/job['id']/'processing.log').open('ab') as log:
        log.write(('\nRunning '+str(args[0])+'\n').encode());log.flush()
        start=log.tell()
        proc=subprocess.Popen([str(a) for a in args],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        while proc.poll() is None:
            if job['cancel'].wait(.25):
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill();proc.wait()
                raise RuntimeError('Cancelled by user.')
            if job['status']=='downloading':
                record=job['_record'];path=Path(job['_source'])
                size=path.stat().st_size if path.exists() else 0
                partial=path.with_name(path.name+'.part')
                if partial.exists(): size=partial.stat().st_size
                fraction=min(1,size/record['size']) if record['size'] else 0
                percent=2+88*(job['completed_tiles']+.65*fraction)/job['total_tiles']
                update(job,percent=round(percent,1),message=f"Downloading tile {job['completed_tiles']+1}/{job['total_tiles']} · {size/1e6:.1f} MB")
        proc.wait();log.flush()
        if proc.returncode:
            with (DATA/job['id']/'processing.log').open('rb') as source:
                source.seek(start);text=source.read().decode('utf-8',errors='replace').strip()[-1800:]
            code=proc.returncode
            raise RuntimeError(f'{Path(str(args[0])).name} failed (exit {code}, 0x{code & 0xffffffff:08X}). '+(text or 'The native process exited without diagnostic output. Completed blocks remain cached.'))


converter.run=command


def process(job,config):
    local.job=job
    try:
        if job['cancel'].is_set(): raise RuntimeError('Cancelled by user.')
        total=len(config['records'])
        known=sum(r['size'] for r in config['records'])
        if config.get('mode')=='tiles' and shutil.disk_usage(DATA).free < known + 2*1024**3:
            raise RuntimeError('Not enough free disk space for the source tiles. Choose a smaller area.')
        update(job,status='streaming' if config.get('mode','stream')=='stream' else 'downloading',percent=2,message='Checking the AOI streaming source…' if config.get('mode','stream')=='stream' else 'Starting source downloads.',total_tiles=total,completed_tiles=0)
        def progress(stage,index,count,source,record):
            if job['cancel'].is_set(): raise RuntimeError('Cancelled by user.')
            if stage=='downloading':
                job['_source']=str(source);job['_record']=record
                update(job,status=stage,percent=round(2+88*index/count,1),completed_tiles=index,message=f'Downloading tile {index+1}/{count}')
            elif stage=='converting':
                update(job,status=stage,percent=round(2+88*(index+.65)/count,1),message=f'Converting ground points · tile {index+1}/{count}')
            elif stage=='streaming':
                update(job,status='streaming',percent=round(2+88*index/count,1),total_tiles=count,completed_tiles=index,message=f'Streaming and converting area block {index+1}/{count}. No full source tiles saved.')
            elif stage=='retrying':
                update(job,status='streaming',message=f'Retrying area block {index+1}/{count} after a network/process failure (attempt {record["attempt"]}/4). Completed blocks remain cached.')
            elif stage=='tile_complete':
                update(job,percent=round(2+88*index/count,1),completed_tiles=index,message=f'Processed {index}/{count} source tiles')
            elif stage=='mosaicking': update(job,status=stage,percent=92,message='Assembling and clipping the final GeoTIFF…')
            elif stage=='verifying': update(job,status=stage,percent=98,message='Verifying GeoTIFF and requested cell size…')
        output=streaming.build(config,DATA/'cache',progress,command,relay_base='http://127.0.0.1:8765/ept') if config.get('mode','stream')=='stream' else converter.build_dem(config,DATA/'cache',progress)
        update(job,status='complete',percent=100,message='DEM verified. Starting final file download.',output=str(output),output_name=output.name,output_bytes=output.stat().st_size)
    except Exception as error:
        cancelled=job['cancel'].is_set()
        update(job,status='cancelled' if cancelled else 'failed',message=str(error),error=str(error))


def public(job):
    return {k:v for k,v in job.items() if k not in ('token','cancel','output') and not k.startswith('_')}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):
        text=fmt % args
        text=re.sub(r'token=[^ &\"]+','token=[redacted]',text)
        sys.stderr.write(text+'\n')

    def origin_allowed(self):
        return self.headers.get('Origin') in ORIGINS

    def cors(self):
        origin=self.headers.get('Origin')
        if origin in ORIGINS:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Content-Type, Authorization')
            self.send_header('Access-Control-Allow-Private-Network','true')
            self.send_header('Vary','Origin')

    def reply(self,data,status=200):
        body=json.dumps(data).encode()
        self.send_response(status);self.cors()
        self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

    def do_OPTIONS(self):
        if not self.origin_allowed():return self.reply({'error':'Origin not allowed'},403)
        self.send_response(204);self.cors();self.end_headers()

    def authorized_job(self,job_id):
        job=jobs.get(job_id)
        if not job:return None
        query=parse_qs(urlparse(self.path).query)
        token=self.headers.get('Authorization','').removeprefix('Bearer ') or query.get('token',[''])[0]
        return job if secrets.compare_digest(token,job['token']) else None

    def do_HEAD(self):
        route=urlparse(self.path).path
        if not route.startswith('/ept/'):
            self.send_response(404);self.end_headers();return
        try:
            size=relay.block_size(route)
            self.send_response(200);self.send_header('Content-Length',str(size));self.send_header('Accept-Ranges','bytes');self.end_headers()
        except Exception as error:
            self.log_message('Streaming HEAD failed: %s',str(error));self.send_response(502);self.end_headers()

    def do_GET(self):
        route=urlparse(self.path).path
        if route.startswith('/ept/'):
            try:
                buffer,size=relay.fetch_block(route)
                with buffer:
                    start,end=0,size-1
                    requested=self.headers.get('Range')
                    if requested:
                        match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
                        if not match:raise ValueError('Invalid range request.')
                        start=int(match[1]) if match[1] else max(0,size-int(match[2]))
                        end=min(size-1,int(match[2])) if match[1] and match[2] else size-1
                        if start>=size or start>end:
                            self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.end_headers();return
                    self.send_response(206 if requested else 200)
                    self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
                    if requested:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.end_headers();buffer.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=buffer.read(min(256*1024,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
                return
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):return
            except ValueError as error:return self.reply({'error':str(error)},400)
            except Exception as error:
                self.log_message('Streaming relay failed: %s: %s',route,str(error))
                return self.reply({'error':'Unable to read the requested USGS streaming block.'},502)
        if route=='/' or route.startswith('/assets/') or route in ('/filter-worker.js','/spatial.js'):
            target=(ROOT/'dist'/('index.html' if route=='/' else route.lstrip('/'))).resolve()
            if not target.is_relative_to((ROOT/'dist').resolve()) or not target.is_file():return self.reply({'error':'App file not found. Build the React app first.'},404)
            self.send_response(200)
            content_type='application/javascript' if target.suffix=='.js' else mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(target.stat().st_size))
            self.send_header('Cache-Control','no-cache');self.end_headers()
            with target.open('rb') as stream:shutil.copyfileobj(stream,self.wfile)
            return
        if route=='/health':
            ready=all(shutil.which(p) for p in ('pdal','gdalwarp','gdalbuildvrt','gdalinfo','curl'))
            return self.reply({'online':True,'ready':ready,'version':2,'worker':'This laptop','active':sum(j['status'] not in ('complete','failed','cancelled') for j in jobs.values())})
        match=re.fullmatch(r'/jobs/([a-f0-9-]+)(/download)?',route)
        job=self.authorized_job(match[1]) if match else None
        if not job:return self.reply({'error':'Job not found'},404)
        if match[2]:
            if job['status']!='complete':return self.reply({'error':'DEM is not ready'},409)
            output=Path(job['output'])
            if not output.is_file():return self.reply({'error':'Result file is no longer available'},410)
            self.send_response(200);self.cors();self.send_header('Content-Type','image/tiff')
            self.send_header('Content-Disposition',f'attachment; filename="{output.name}"')
            self.send_header('Content-Length',str(output.stat().st_size));self.end_headers()
            try:
                with output.open('rb') as stream:shutil.copyfileobj(stream,self.wfile,1024*1024)
            except (BrokenPipeError,ConnectionResetError):pass
        else:self.reply(public(job))

    def do_POST(self):
        if not self.origin_allowed():return self.reply({'error':'Origin not allowed'},403)
        route=urlparse(self.path).path
        if route=='/sources':
            try:
                length=int(self.headers.get('Content-Length',0))
                if not 0<length<=1024**2:raise ValueError('Source request is too large.')
                surveys=json.loads(self.rfile.read(length))['surveys']
                if not isinstance(surveys,list) or len(surveys)>100:raise ValueError('Too many surveys.')
                def inspect(survey):
                    records=survey['records']
                    if not isinstance(records,list) or not 1<=len(records)<=100:raise ValueError('Invalid source list.')
                    for record in records:
                        parsed=urlparse(record['url']);host=parsed.hostname or ''
                        if parsed.scheme!='https' or not(host=='usgs.gov' or host.endswith('.usgs.gov') or host=='prd-tnm.s3.amazonaws.com'):raise ValueError('Invalid source host.')
                    try:return {'key':survey['key'],'available':True,'urls':streaming.ept_sources(records)}
                    except Exception:return {'key':survey['key'],'available':False}
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as checks:
                    results=list(checks.map(inspect,surveys))
                return self.reply({'sources':results})
            except (ValueError,KeyError,TypeError) as error:return self.reply({'error':str(error)},400)
        if route=='/jobs':
            try:
                length=int(self.headers.get('Content-Length',0))
                if not 0<length<=10*1024**2:raise ValueError('Job request is too large.')
                config=validate(json.loads(self.rfile.read(length)))
                with lock:
                    if sum(j['status'] not in ('complete','failed','cancelled') for j in jobs.values())>=3:
                        return self.reply({'error':'Worker queue is full. Wait for an existing job.'},429)
                    job={'id':str(uuid.uuid4()),'token':secrets.token_urlsafe(32),'status':'queued','percent':0,'message':'Queued on this laptop.','created_at':time.time(),'cancel':threading.Event(),'completed_tiles':0,'total_tiles':len(config['records'])}
                    jobs[job['id']]=job;update(job)
                    (DATA/job['id']/'config.json').write_text(json.dumps(config),encoding='utf-8')
                    pool.submit(process,job,config)
                return self.reply({**public(job),'token':job['token']},202)
            except (ValueError,TypeError,KeyError) as error:return self.reply({'error':str(error)},400)
        match=re.fullmatch(r'/jobs/([a-f0-9-]+)/cancel',route)
        job=self.authorized_job(match[1]) if match else None
        if not job:return self.reply({'error':'Job not found'},404)
        if job['status'] not in ('complete','failed','cancelled'):job['cancel'].set()
        self.reply(public(job))


def main():
    # Restore completed jobs after a restart; interrupted work can be resubmitted
    # and reuses the source/raster cache.
    for file in DATA.glob('*/job.json'):
        try:
            job=json.loads(file.read_text(encoding='utf-8'));job['cancel']=threading.Event()
            if job['status'] not in ('complete','failed','cancelled'):
                job.update(status='failed',message='Worker restarted during processing. Submit again to resume cached work.')
            jobs[job['id']]=job
        except (ValueError,KeyError):pass
    server=ThreadingHTTPServer(('127.0.0.1',8765),Handler)
    print('LiDAR processing worker listening on http://127.0.0.1:8765',flush=True)
    server.serve_forever()


if __name__=='__main__':main()
