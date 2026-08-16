import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.configuration import (
    _validated_reference_object_type,
    delete_reference_image,
)
from app.db.base import Base
from app.models.reference import ReferenceGroup, ReferenceImage, ReferenceObjectType


class ReferenceObjectTypeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)
        self.database.add(
            ReferenceObjectType(code="FUSE", name="保险丝", enabled=True)
        )
        self.database.commit()

    def tearDown(self) -> None:
        self.database.close()
        self.engine.dispose()

    def test_accepts_enabled_standard_library_type(self) -> None:
        result = _validated_reference_object_type(self.database, "fuse")
        self.assertEqual(result, "FUSE")

    def test_rejects_type_missing_from_standard_library(self) -> None:
        with self.assertRaises(HTTPException) as context:
            _validated_reference_object_type(self.database, "UNKNOWN_PART")
        self.assertEqual(context.exception.status_code, 422)

    def test_removing_standard_image_is_soft_delete(self) -> None:
        group = ReferenceGroup(
            code="FUSE_400A_OK",
            name="400A保险丝正确",
            object_type="FUSE",
            class_code="FUSE_400A",
        )
        self.database.add(group)
        self.database.flush()
        image = ReferenceImage(group_id=group.id, image_path="reference.jpg")
        self.database.add(image)
        self.database.commit()

        result = delete_reference_image(image.id, self.database)
        self.database.refresh(image)

        self.assertTrue(result["deleted"])
        self.assertFalse(image.enabled)
        self.assertTrue(image.is_deleted)


if __name__ == "__main__":
    unittest.main()
