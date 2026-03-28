# 字节 Wave? 加密协议分析

### 核心特征

| 特征 | 说明 |
|------|------|
| 密钥交换 | ECDH (P-256/secp256r1) |
| 密钥派生 | HKDF-SHA256 |
| 对称加密 | ChaCha20 (纯流加密，无认证标签) |
| 签名算法 | ECDSA-SHA256 |

---

## 握手阶段

### 握手 Endpoint

```
POST https://keyhub.zijieapi.com/handshake
Content-Type: application/json
```

### 请求格式

```json
{
  "version": 2,
  "random": "<base64(32字节客户端随机数)>",
  "app_id": "401734",
  "did": "<设备ID>",
  "key_shares": [
    {
      "curve": "secp256r1",
      "pubkey": "<base64(65字节未压缩公钥)>"
    }
  ],
  "cipher_suites": [4097]
}
```

### Cipher Suite 编码

| 值 | 加密算法 |
|----|----------|
| 4097 (0x1001) | ChaCha20 |

### 请求签名

握手请求需要携带 ECDSA 签名：

```
x-tt-s-sign: <base64(ECDSA签名)>
```

签名计算方式：

```python
# 使用 ECDH 私钥对请求 JSON 进行签名
request_json = json.dumps(request_body, separators=(',', ':'))
signature = private_key.sign(
    request_json.encode(),
    ec.ECDSA(hashes.SHA256())
)
```

**PS**: 签名私钥就是 ECDH 密钥交换所用的临时私钥，动态生成

### 响应格式

```json
{
  "version": 2,
  "random": "<base64(32字节服务器随机数)>",
  "key_share": {
    "curve": "secp256r1",
    "pubkey": "<base64(65字节服务器公钥)>"
  },
  "cipher_suite": 4097,
  "ticket": "<base64(会话 ticket)>",
  "expire_time": 1738763280
}
```

### Ticket 结构

Ticket 采用 ASN.1 DER 编码，包含多个 OCTET STRING：

```
SEQUENCE {
  OCTET STRING (12 bytes)  -- Nonce
  OCTET STRING (48 bytes)  -- 加密数据
  OCTET STRING (32 bytes)  -- 可能是密钥或 MAC?
}
```

**PS**: Ticket 内容似乎对客户端不透明，由服务器加密保存会话状态？

---

## 密钥派生

### ECDH 共享密钥计算

```python
from cryptography.hazmat.primitives.asymmetric import ec

# 使用客户端私钥和服务器公钥计算共享密钥
shared_key = client_private_key.exchange(
    ec.ECDH(),
    server_public_key
)
# shared_key: 32 字节
```

### HKDF 密钥派生

```python
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# 参数
salt = client_random + server_random  # 64 字节
info = b"4e30514609050cd3"            # 16 字节，硬编码常量

# 派生
encryption_key = HKDF(
    algorithm=hashes.SHA256(),
    length=32,
    salt=salt,
    info=info
).derive(shared_key)
```

### 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| Salt 组成 | `client_random \|\| server_random` | 64 字节，顺序重要 |
| Info 字符串 | `"4e30514609050cd3"` | 硬编码在 libsscronet.so 中 |
| 输出长度 | 32 字节 | ChaCha20 密钥长度 |

### 其他发现的 Info 字符串

逆向过程中发现多个 HKDF info 常量：

| Info 值 | 用途 |
|---------|------|
| `4e30514609050cd3` | 主加密密钥派生？ |
| `cab4ac74f61b5835` | 备用/其他场景？ |
| `6a15c5844fffc436` | TokenManager 相关？ |

---

## 加密与解密

### 算法：ChaCha20

Wave 协议使用 **ChaCha20 纯流加密**。

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

def chacha20_crypt(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """ChaCha20 加密/解密 (对称操作)"""
    # Python cryptography 库需要 16 字节 nonce
    # 格式: 4 字节 counter (固定为 0) + 12 字节 nonce
    nonce_16 = b'\x00\x00\x00\x00' + nonce

    cipher = Cipher(
        algorithms.ChaCha20(key, nonce_16),
        mode=None
    )
    encryptor = cipher.encryptor()
    return encryptor.update(data) + encryptor.finalize()
```

### Nonce 格式

- 长度：12 字节
- 生成：每次请求随机生成
- 传输：Base64 编码后放入 `x-tt-e-p` 头部

### 加密流程

```
plaintext (JSON bytes)
    ↓
ChaCha20(encryption_key, nonce)
    ↓
ciphertext
```

### 解密流程

```
ciphertext
    ↓
ChaCha20(encryption_key, nonce)  # 与加密相同
    ↓
plaintext (JSON bytes)
```

### 双向密钥

**PS**: 请求加密和响应解密使用 **同一个密钥** (`encryption_key`)，但使用不同的 nonce：
- 请求 nonce: 客户端生成，放入请求头
- 响应 nonce: 服务器生成，放入响应头

---

## HTTP 头部格式

### 加密请求头

| 头部 | 值 | 说明 |
|------|-----|------|
| `x-tt-e-b` | `"1"` | 标识请求体已加密 |
| `x-tt-e-t` | `<ticket>` | 握手获得的会话 ticket |
| `x-tt-e-p` | `<base64(nonce)>` | 12 字节 nonce 的 Base64 编码 |
| `x-ss-stub` | `<MD5>` | 密文的 MD5 哈希 (大写) |

### x-ss-stub 计算

```python
import hashlib

stub = hashlib.md5(ciphertext).hexdigest().upper()
```

### 加密响应头

| 头部 | 值 | 说明 |
|------|-----|------|
| `x-tt-e-b` | `"1"` | 标识响应体已加密 |
| `x-tt-e-p` | `<base64(nonce)>` | 解密响应所需的 nonce |

### 完整请求示例

```http
POST /api/v3/context/ime/ner?device_platform=android&... HTTP/1.1
Host: speech.bytedance.com
Content-Type: application/json
x-tt-e-b: 1
x-tt-e-t: MIGYBAwrT...（ticket）
x-tt-e-p: abc123...（nonce Base64）
x-ss-stub: A1B2C3D4E5F6...（MD5 大写）
x-api-app-key: SYlxZr6LnvBaIVmF
x-api-token: eyJhbGciOiJFUzI1NiI...

<加密后的请求体>
```
