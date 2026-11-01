"""AES-CTR encryption and decryption for the MQTT/UDP audio channel."""

from __future__ import annotations

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def aes_ctr_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
    """AES-CTR encryption.

    Args:
        key: Encryption key
        nonce: Initialization vector (must match decryption)
        plaintext: Plaintext bytes
    """
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def aes_ctr_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    """AES-CTR decryption.

    Args:
        key: Decryption key
        nonce: Same nonce used for encryption
        ciphertext: Ciphertext bytes
    """
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce), backend=default_backend())
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def build_audio_nonce(aes_nonce_hex: str, audio_len: int, sequence: int) -> str:
    """Construct UDP audio packet nonce (hex string).

    Format: 4 hex fixed prefix + 4 hex length + 16 hex original nonce + 8 hex sequence number
    """
    return (
        aes_nonce_hex[:4]
        + format(audio_len, "04x")
        + aes_nonce_hex[8:24]
        + format(sequence, "08x")
    )
