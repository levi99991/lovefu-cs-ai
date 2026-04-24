"""
WMS PII 解密 — AES-128-ECB
WMS 回傳的 receiver_name / receiver_phone 是密文，必須解密後立即遮罩。
解密明文絕不直接返回給呼叫端。
"""
import os
import base64
import logging
from typing import Any

logger = logging.getLogger("lovefu.cs_logistics.pii")

WMS_PII_AES_KEY = os.getenv("WMS_PII_AES_KEY", "")

# WMS 文件中加密的欄位名單
ENCRYPTED_FIELDS = {
    "receiver_name",
    "receiver_phone",
    "receiver_address",
    "buyer_name",
    "buyer_phone",
}


def _aes_decrypt(ciphertext_b64: str) -> str:
    """AES-128-ECB 解密 base64 字串。失敗回原值（避免 break flow）"""
    if not WMS_PII_AES_KEY:
        logger.warning("WMS_PII_AES_KEY 未設定，無法解密")
        return ciphertext_b64

    try:
        from Crypto.Cipher import AES  # pycryptodome
        from Crypto.Util.Padding import unpad
    except ImportError:
        logger.error("pycryptodome 未安裝，pip install pycryptodome")
        return ciphertext_b64

    try:
        key = WMS_PII_AES_KEY.encode("utf-8")[:16].ljust(16, b"\0")
        cipher = AES.new(key, AES.MODE_ECB)
        ct = base64.b64decode(ciphertext_b64)
        pt = unpad(cipher.decrypt(ct), AES.block_size)
        return pt.decode("utf-8")
    except Exception as e:
        logger.warning("AES decrypt failed for %s: %s", ciphertext_b64[:20], e)
        return ciphertext_b64


def _looks_like_aes_b64(value: Any) -> bool:
    """判斷字串是否疑似 AES base64 密文：長度 > 16、結尾 = padding、純 base64 字元"""
    if not isinstance(value, str) or len(value) < 16:
        return False
    try:
        decoded = base64.b64decode(value, validate=True)
        return len(decoded) % 16 == 0
    except Exception:
        return False


def _mask_after_decrypt(field: str, plaintext: str) -> str:
    """解密後立即遮罩 — 模仿 mask_pii 規則"""
    if "phone" in field:
        # 0912345678 → ****5678
        return "****" + plaintext[-4:] if len(plaintext) >= 4 else "****"
    if "name" in field:
        # 王小明 → 王*明 / 陳明 → 陳*
        if len(plaintext) <= 1:
            return "*"
        return plaintext[0] + "*" * (len(plaintext) - 2) + (plaintext[-1] if len(plaintext) > 2 else "")
    if "address" in field:
        # 只保留到「市/區」級
        for sep in ["路", "街", "巷", "段"]:
            if sep in plaintext:
                return plaintext.split(sep)[0] + sep + "***"
        return plaintext[:6] + "***"
    return "***"


def decrypt_and_mask(data: Any, _path: str = "") -> Any:
    """
    遞迴掃描 dict / list，遇到 ENCRYPTED_FIELDS 中的鍵 → 解密 → 立即遮罩。
    回傳處理後的資料，原始明文絕不外洩。
    """
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in ENCRYPTED_FIELDS and isinstance(v, str) and _looks_like_aes_b64(v):
                plaintext = _aes_decrypt(v)
                out[k] = _mask_after_decrypt(k, plaintext)
            else:
                out[k] = decrypt_and_mask(v, f"{_path}.{k}")
        return out
    if isinstance(data, list):
        return [decrypt_and_mask(x, f"{_path}[]") for x in data]
    return data
