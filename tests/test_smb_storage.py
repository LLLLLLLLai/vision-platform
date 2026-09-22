import tempfile
import unittest
from pathlib import Path

from app.services.smb_storage import SmbStorage


class SmbStorageLocalPathTest(unittest.TestCase):
    def test_stages_local_input_and_publishes_result_next_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "camera" / "CAMERA1PICTURE1-CN001.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"raw-image")
            storage = SmbStorage(enabled=False)

            staged = storage.stage_input(source.as_posix(), root / "cache" / source.name)

            self.assertEqual(staged.read_bytes(), b"raw-image")
            result_source = root / "local-result.jpg"
            result_source.write_bytes(b"processed-image")
            published = Path(storage.publish_result(result_source, source.as_posix()))

            self.assertEqual(published.name, "CAMERA1PICTURE1-CN001_result.jpg")
            self.assertEqual(published.read_bytes(), b"processed-image")

    def test_builds_result_path_for_unc_source(self) -> None:
        source = r"\\caxaprdfile.catl.com\prd-file\MFG\CAMERA2PICTURE1-CN001.png"

        self.assertEqual(
            SmbStorage.result_path_for_source(source),
            r"\\caxaprdfile.catl.com\prd-file\MFG\CAMERA2PICTURE1-CN001_result.png",
        )


if __name__ == "__main__":
    unittest.main()
