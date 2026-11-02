"""Print a Fernet-compatible key for encrypting VLM API keys in the database."""

import base64
import secrets


if __name__ == "__main__":
    print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"))
