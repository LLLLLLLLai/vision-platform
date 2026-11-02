import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_parallel_staging_uses_unique_paths_for_same_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first_source = root / "camera_1" / "CAMERA1PICTURE1-CN001.jpg"
            second_source = root / "camera_2" / "CAMERA1PICTURE1-CN001.jpg"
            first_source.parent.mkdir(parents=True)
            second_source.parent.mkdir(parents=True)
            first_source.write_bytes(b"first-camera")
            second_source.write_bytes(b"second-camera")
            storage = SmbStorage(enabled=False)
            first_destination = root / "cache" / storage.staging_filename_for_source(
                first_source.as_posix(), position=1
            )
            second_destination = root / "cache" / storage.staging_filename_for_source(
                second_source.as_posix(), position=2
            )

            self.assertNotEqual(first_destination, second_destination)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(storage.stage_input, first_source.as_posix(), first_destination),
                    executor.submit(storage.stage_input, second_source.as_posix(), second_destination),
                ]
                staged = [future.result() for future in futures]

            self.assertEqual(staged[0].read_bytes(), b"first-camera")
            self.assertEqual(staged[1].read_bytes(), b"second-camera")


if __name__ == "__main__":
    unittest.main()
