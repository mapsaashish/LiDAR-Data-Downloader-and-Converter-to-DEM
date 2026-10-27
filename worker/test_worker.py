import json
import io
import threading
import tempfile
import urllib.request
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import server
import streaming
import relay

def config():
    return {'resolution':5,'crs':'EPSG:32617','geoid':'12095','mode':'stream','area':{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,0]]]}},'records':[{'url':'https://rockyweb.usgs.gov/Projects/Survey/County/LAZ/file.laz','name':'../../escape.laz','size':100}]}

class WorkerTests(unittest.TestCase):
    def test_relay_supports_head_and_exact_byte_ranges(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{http.server_port}/ept/Survey/ept-data/0-0-0-0.laz'
        try:
            with patch.object(relay,'block_size',return_value=4),patch.object(relay,'fetch_block',side_effect=lambda path:(io.BytesIO(b'test'),4)):
                with urllib.request.urlopen(urllib.request.Request(url,method='HEAD')) as response:
                    self.assertEqual(response.status,200);self.assertEqual(response.headers['Content-Length'],'4');self.assertEqual(response.read(),b'')
                with urllib.request.urlopen(urllib.request.Request(url,headers={'Range':'bytes=1-2'})) as response:
                    self.assertEqual(response.status,206);self.assertEqual(response.headers['Content-Range'],'bytes 1-2/4');self.assertEqual(response.read(),b'es')
        finally:http.shutdown();http.server_close();thread.join()
    def test_native_failure_always_reports_exit_code(self):
        class Process:
            returncode=1
            def poll(self):return 1
            def wait(self):return 1
        with tempfile.TemporaryDirectory(dir=server.ROOT) as folder:
            data=Path(folder);(data/'test').mkdir()
            server.local.job={'id':'test','cancel':threading.Event()}
            with patch.object(server,'DATA',data),patch.object(server.subprocess,'Popen',return_value=Process()):
                with self.assertRaisesRegex(RuntimeError,'pdal failed \\(exit 1, 0x00000001\\)') as raised:server.command(['pdal','pipeline','test.json'])
                self.assertIn('without diagnostic output',str(raised.exception))
    def test_retry_discards_partial_raster_and_stale_marker(self):
        with tempfile.TemporaryDirectory(dir=server.ROOT) as folder:
            target=Path(folder)/'tile.tif';marker=target.with_suffix('.complete')
            target.write_bytes(b'partial');marker.write_text('stale')
            calls=[]
            def command(args):
                self.assertFalse(target.exists());self.assertFalse(marker.exists());calls.append(args)
                if len(calls)==1:
                    target.write_bytes(b'incomplete');raise RuntimeError('Network failure')
                target.write_bytes(b'valid')
            with patch.object(streaming.time,'sleep'):
                streaming.process_block('job.json',target,marker,0,1,lambda *args:None,command)
            self.assertEqual(len(calls),2);self.assertEqual(target.read_bytes(),b'valid');self.assertFalse(marker.exists())
    def test_relay_only_allows_public_ept_blocks(self):
        self.assertEqual(relay.cloud_url('/ept/Survey/ept-data/0-0-0-0.laz'),'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/Survey/ept-data/0-0-0-0.laz')
        for path in ['/ept/../ept.json','/ept/Survey/../../private','/ept/Survey/other.json']:
            with self.assertRaises(ValueError):relay.cloud_url(path)
    def test_relay_retries_a_transient_network_failure(self):
        class Response:
            headers={'Content-Length':'4'}
            def __init__(self):self.body=b'test'
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):body=self.body;self.body=b'';return body
        with patch.object(relay,'urlopen',side_effect=[OSError('Network interruption'),Response()]),patch.object(relay.time,'sleep'):
            buffer,size=relay.fetch_block('/ept/Survey/ept-data/0-0-0-0.laz')
            with buffer:self.assertEqual(buffer.read(),b'test')
            self.assertEqual(size,4)
    def test_server_assigns_cache_names(self):
        result=server.validate(config())
        self.assertNotIn('/',result['records'][0]['name'])
        self.assertNotIn('..',result['records'][0]['name'])
    def test_rejects_arbitrary_download_hosts(self):
        job=config();job['records'][0]['url']='https://127.0.0.1/private.laz'
        with self.assertRaises(ValueError):server.validate(job)
    def test_resolution_is_validated(self):
        job=config();job['resolution']=2
        with self.assertRaises(ValueError):server.validate(job)
    def test_streaming_endpoint_is_verified(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'schema':[{'name':'Classification'}]}).encode()
        with patch.object(streaming,'catalog_names',return_value={'County'}),patch.object(streaming,'urlopen',return_value=Response()) as fetch:
            sources=streaming.ept_sources(config()['records'])
            self.assertEqual(sources,['https://s3-us-west-2.amazonaws.com/usgs-lidar-public/County/ept.json'])
            fetch.assert_called_once()
    def test_missing_stream_does_not_silently_download_tiles(self):
        with patch.object(streaming,'urlopen',side_effect=OSError('Unavailable')):
            with self.assertRaisesRegex(ValueError,'No full tiles were downloaded'):streaming.ept_sources(config()['records'])
    def test_legacy_martin_and_palm_beach_names(self):
        names={'USGS_LPC_FL_MartinCo_2015_LAS_2017','USGS_LPC_FL_PalmBeachCo_2016_LAS_2019','FL_Peninsular_FDEM_Martin_2018'}
        with patch.object(streaming,'catalog_names',return_value=names):
            self.assertEqual(streaming.resolve_folder('FL_MartinCo_2015'),'USGS_LPC_FL_MartinCo_2015_LAS_2017')
            self.assertEqual(streaming.resolve_folder('FL_PalmBeachCo_2016'),'USGS_LPC_FL_PalmBeachCo_2016_LAS_2019')
            with self.assertRaises(ValueError):streaming.resolve_folder('FL_MartinCo_2014')

if __name__=='__main__':unittest.main()
