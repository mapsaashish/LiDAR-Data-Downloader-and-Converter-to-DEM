"""Bounded, retrying HTTPS transport for PDAL's local EPT reads on Windows."""
import re
import io
from collections import OrderedDict
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen

slots=threading.BoundedSemaphore(4)
lengths={}
cache=OrderedDict()
cache_lock=threading.RLock()
cache_bytes=0


def cloud_url(path):
    match=re.fullmatch(r'/ept/([A-Za-z0-9_.-]+)/(ept\.json|ept-(?:data|hierarchy)/[0-9-]+\.(?:laz|bin|json)|ept-sources/(?:list|[0-9]+)\.json)',path)
    if not match or match[1] in ('.','..'):
        raise ValueError('Invalid streaming block path.')
    return 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'+match[1]+'/'+match[2]


def fetch_block(path):
    global cache_bytes
    url=cloud_url(path)
    with cache_lock:
        if path in cache:
            data=cache.pop(path);cache[path]=data
            return io.BytesIO(data),len(data)
    with slots:
        for attempt in range(4):
            buffer=tempfile.SpooledTemporaryFile(max_size=8*1024**2)
            try:
                with urlopen(Request(url,headers={'User-Agent':'LiDAR-Atlas-Worker/2'}),timeout=45) as response:
                    expected=response.headers.get('Content-Length')
                    size=0
                    while True:
                        chunk=response.read(256*1024)
                        if not chunk:break
                        buffer.write(chunk);size+=len(chunk)
                    if expected is not None and size!=int(expected):raise OSError('Incomplete streaming block transfer.')
                    buffer.seek(0)
                    lengths[path]=size
                    if size<=8*1024**2:
                        data=buffer.read();buffer.close()
                        with cache_lock:
                            previous=cache.pop(path,None)
                            if previous is not None:cache_bytes-=len(previous)
                            while cache and cache_bytes+size>64*1024**2:
                                _,evicted=cache.popitem(last=False);cache_bytes-=len(evicted)
                            cache[path]=data;cache_bytes+=size
                        return io.BytesIO(data),size
                    return buffer,size
            except Exception as error:
                buffer.close()
                if isinstance(error,HTTPError) and error.code in (400,401,403,404):raise
                if attempt==3:raise
                time.sleep(2**attempt)


def block_size(path):
    url=cloud_url(path)
    if path in lengths:return lengths[path]
    with slots:
        for attempt in range(4):
            try:
                with urlopen(Request(url,method='HEAD'),timeout=45) as response:
                    size=int(response.headers['Content-Length']);lengths[path]=size;return size
            except Exception as error:
                if isinstance(error,HTTPError) and error.code in (400,401,403,404):raise
                if attempt==3:raise
                time.sleep(2**attempt)
