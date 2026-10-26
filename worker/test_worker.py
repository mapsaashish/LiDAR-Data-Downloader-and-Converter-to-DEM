import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import server
import streaming

def config():
    return {'resolution':5,'crs':'EPSG:32617','geoid':'12095','mode':'stream','area':{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,0]]]}},'records':[{'url':'https://rockyweb.usgs.gov/Projects/Survey/County/LAZ/file.laz','name':'../../escape.laz','size':100}]}

class WorkerTests(unittest.TestCase):
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
