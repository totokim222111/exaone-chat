import os
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def load_secret(path="/data/app_secret") -> bytes:
    secret = os.getenv("APP_SECRET", "")
    if secret:
        return secret.encode()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as f:
            os.chmod(target, 0o600)
            f.write(os.urandom(32))
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        pass
    secret = target.read_bytes()
    if len(secret) != 32:
        raise RuntimeError("저장된 암호화 비밀값이 올바르지 않습니다.")
    return secret


class KeyCipher:
    def __init__(self, secret: bytes):
        key = HKDF(algorithm=hashes.SHA256(), length=32,
                   salt=b"exaone-web-v1", info=b"api-key-encryption").derive(secret)
        self.aes = AESGCM(key)

    def encrypt(self, value: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + self.aes.encrypt(nonce, value.encode(), b"exaone-api-key-v1")

    def decrypt(self, value: bytes) -> str:
        return self.aes.decrypt(value[:12], value[12:], b"exaone-api-key-v1").decode()
