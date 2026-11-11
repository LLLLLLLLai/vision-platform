from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.vlm_models import VlmModelCreate, VlmModelUpdate, create_vlm_model, update_vlm_model
from app.core.config import settings
from app.db.base import Base
from app.models.intelligence import VlmModelConfig
from app.services.openai_compatible_vlm import decrypt_api_key, encrypt_api_key, resolve_api_key, VlmConfigurationError


class VlmSecretTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.key_file = Path(self.directory.name) / "secrets" / "vlm.key"
        self.patches = [patch.object(settings, "vlm_secret_key", ""), patch.object(settings, "vlm_secret_key_file", str(self.key_file))]
        for item in self.patches:
            item.start()
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.database = Session(self.engine)

    def tearDown(self):
        self.database.close()
        self.engine.dispose()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def create(self, **values):
        return create_vlm_model(VlmModelCreate(code="TEST", name="测试 VLM", base_url="http://localhost:8000", model_name="test", **values), database=self.database)

    def test_auto_create_encrypt_no_secret_in_response_and_edit_preserves(self):
        response = self.create(api_key="test-api-key-not-real")
        self.assertTrue(self.key_file.is_file())
        model = self.database.get(VlmModelConfig, response["id"])
        self.assertNotEqual(model.api_key_ciphertext, "test-api-key-not-real")
        self.assertNotIn("api_key_ciphertext", response)
        self.assertNotIn("api_key", response)
        self.assertEqual(resolve_api_key(model), "test-api-key-not-real")
        saved_key = self.key_file.read_bytes()
        update_vlm_model(model.id, VlmModelUpdate(name="改名", api_key=""), database=self.database)
        self.assertEqual(resolve_api_key(model), "test-api-key-not-real")
        self.assertEqual(self.key_file.read_bytes(), saved_key)

    def test_concurrent_first_write_has_one_durable_key(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            ciphertexts = list(executor.map(encrypt_api_key, ["dummy"] * 20))
        self.assertTrue(all(decrypt_api_key(value) == "dummy" for value in ciphertexts))

    def test_explicit_master_key_is_preserved(self):
        explicit = Fernet.generate_key().decode("ascii")
        with patch.object(settings, "vlm_secret_key", explicit):
            ciphertext = encrypt_api_key("dummy")
            self.assertEqual(Fernet(explicit.encode()).decrypt(ciphertext.encode()), b"dummy")
        self.assertFalse(self.key_file.exists())

    def test_read_missing_key_does_not_generate(self):
        with self.assertRaises(VlmConfigurationError):
            decrypt_api_key("dummy-ciphertext")
        self.assertFalse(self.key_file.exists())

    def test_existing_ciphertext_prevents_accidental_new_master_key(self):
        self.database.add(VlmModelConfig(code="OLD", name="old", model_name="old", base_url="http://localhost:8000", api_key_ciphertext="old-encrypted-secret"))
        self.database.commit()
        with self.assertRaises(HTTPException) as error:
            self.create(api_key="new-secret")
        self.assertEqual(error.exception.status_code, 400)
        self.assertFalse(self.key_file.exists())

    def test_environment_can_switch_to_saved_key_and_back(self):
        response = self.create(api_key_env_name="TEST_ENV_KEY")
        model = self.database.get(VlmModelConfig, response["id"])
        update_vlm_model(model.id, VlmModelUpdate(api_key="dummy"), database=self.database)
        self.assertIsNone(model.api_key_env_name)
        self.assertEqual(resolve_api_key(model), "dummy")
        update_vlm_model(model.id, VlmModelUpdate(api_key_env_name="TEST_ENV_KEY"), database=self.database)
        self.assertIsNone(model.api_key_ciphertext)
        with patch.dict("os.environ", {"TEST_ENV_KEY": "environment-dummy"}):
            self.assertEqual(resolve_api_key(model), "environment-dummy")

    def test_invalid_master_key_does_not_fallback(self):
        with patch.object(settings, "vlm_secret_key", "invalid"), self.assertRaises(VlmConfigurationError):
            encrypt_api_key("dummy")
        self.assertFalse(self.key_file.exists())
