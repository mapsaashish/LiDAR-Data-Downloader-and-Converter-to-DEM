import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("converter", Path(__file__).parent / "public" / "dem-converter.py")
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


class DEMTests(unittest.TestCase):
    def test_all_four_resolutions_use_metric_crs_and_ground_points(self):
        for resolution in (1, 5, 10, 30):
            stages = converter.pipeline("input.laz", "output.tif", {"resolution": resolution, "crs": "EPSG:32617"})["pipeline"]
            self.assertEqual(stages[1]["expression"], "Classification == 2")
            self.assertEqual(stages[2]["out_srs"], "EPSG:32617")
            self.assertEqual(stages[3]["resolution"], resolution)
            self.assertEqual(stages[3]["output_type"], "idw")
            self.assertEqual(stages[3]["nodata"], -9999)

    def test_missing_tools_fail_before_any_download(self):
        with patch.object(converter.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pdal is missing"):
                converter.build_dem({"resolution": 5}, "unused")

    def test_invalid_resolution_rejected(self):
        with self.assertRaisesRegex(ValueError, "resolution"):
            converter.build_dem({"resolution": 2}, "unused")

    def test_existing_complete_source_is_reused(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            source = Path(temp) / "test.laz"
            source.write_bytes(b"las-test")
            with patch.object(converter, "run") as run:
                converter.acquire({"size": 8, "url": "https://example.invalid"}, source)
                run.assert_not_called()

    def test_bad_download_size_never_promoted_to_complete(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            source = Path(temp) / "test.laz"
            source.with_name("test.laz.part").write_bytes(b"short")
            with patch.object(converter, "run"):
                with self.assertRaisesRegex(RuntimeError, "size differs"):
                    converter.acquire({"size": 100, "url": "https://example.invalid"}, source)
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
