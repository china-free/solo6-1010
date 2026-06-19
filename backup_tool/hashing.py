"""哈希计算模块，支持 MD5 和 SHA256"""

import hashlib
from pathlib import Path
from typing import Optional


def _hash_file(filepath: Path, algorithm: str, chunk_size: int = 65536) -> str:
    """计算文件哈希值，分块读取大文件"""
    h = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def md5_file(filepath: str | Path) -> str:
    """计算文件 MD5 值"""
    return _hash_file(Path(filepath), "md5")


def sha256_file(filepath: str | Path) -> str:
    """计算文件 SHA256 值"""
    return _hash_file(Path(filepath), "sha256")


def hash_file(filepath: str | Path, algorithm: str = "sha256") -> str:
    """计算指定算法的文件哈希值"""
    algorithm = algorithm.lower()
    if algorithm not in ("md5", "sha256"):
        raise ValueError(f"不支持的哈希算法: {algorithm}，请使用 md5 或 sha256")
    return _hash_file(Path(filepath), algorithm)


def hash_bytes(data: bytes, algorithm: str = "sha256") -> str:
    """计算字节数据的哈希值"""
    algorithm = algorithm.lower()
    if algorithm not in ("md5", "sha256"):
        raise ValueError(f"不支持的哈希算法: {algorithm}")
    h = hashlib.new(algorithm)
    h.update(data)
    return h.hexdigest()
