"""Password hashing for the admin login.

CircuitPython's built-in ``hashlib`` is documented as optional and may be
missing from a build, so this falls back to a small pure-Python SHA-256.
The fallback is perhaps a thousand times slower, which is why the number of
rounds is chosen to suit whichever one is in use and then stored alongside
the hash, so a password hashed on one still verifies on the other.

What this is for: stopping a guest at the party reopening the admin page and
reshaping the tree.  The board speaks plain HTTP over its own hotspot, so
anyone already on that network who is willing to watch the traffic can read
the session cookie.  Treat the wifi password as the real boundary.
"""

try:
    import os as _os
except ImportError:  # pragma: no cover
    _os = None

try:
    import hashlib as _hashlib

    _hashlib.sha256(b"probe").digest()
    FAST = True
except Exception:  # pragma: no cover - exercised on builds without hashlib
    _hashlib = None
    FAST = False

#: Rounds of stretching.  Generous with the native implementation, modest
#: with the pure-Python one, which would otherwise take minutes on a Pico.
ROUNDS = 20000 if FAST else 150

_K = (
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5, 0x3956C25B, 0x59F111F1,
    0x923F82A4, 0xAB1C5ED5, 0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
    0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174, 0xE49B69C1, 0xEFBE4786,
    0x0FC19DC6, 0x240CA1CC, 0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7, 0xC6E00BF3, 0xD5A79147,
    0x06CA6351, 0x14292967, 0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
    0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85, 0xA2BFE8A1, 0xA81A664B,
    0xC24B8B70, 0xC76C51A3, 0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5, 0x391C0CB3, 0x4ED8AA4A,
    0x5B9CCA4F, 0x682E6FF3, 0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
    0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
)

_MASK = 0xFFFFFFFF


def _rotr(value, count):
    return ((value >> count) | (value << (32 - count))) & _MASK


def _sha256_slow(message):
    """Pure-Python SHA-256, for builds with no hashlib."""
    h = [
        0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
        0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
    ]
    length = len(message) * 8
    message = bytearray(message)
    message.append(0x80)
    while len(message) % 64 != 56:
        message.append(0)
    for shift in range(56, -1, -8):
        message.append((length >> shift) & 0xFF)

    words = [0] * 64
    for start in range(0, len(message), 64):
        chunk = message[start : start + 64]
        for i in range(16):
            j = i * 4
            words[i] = (
                (chunk[j] << 24) | (chunk[j + 1] << 16) | (chunk[j + 2] << 8) | chunk[j + 3]
            )
        for i in range(16, 64):
            w15 = words[i - 15]
            w2 = words[i - 2]
            s0 = _rotr(w15, 7) ^ _rotr(w15, 18) ^ (w15 >> 3)
            s1 = _rotr(w2, 17) ^ _rotr(w2, 19) ^ (w2 >> 10)
            words[i] = (words[i - 16] + s0 + words[i - 7] + s1) & _MASK

        a, b, c, d, e, f, g, hh = h
        for i in range(64):
            s1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
            ch = (e & f) ^ (~e & g)
            t1 = (hh + s1 + ch + _K[i] + words[i]) & _MASK
            s0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
            maj = (a & b) ^ (a & c) ^ (b & c)
            t2 = (s0 + maj) & _MASK
            hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & _MASK, c, b, a, (t1 + t2) & _MASK

        h = [
            (h[0] + a) & _MASK, (h[1] + b) & _MASK, (h[2] + c) & _MASK, (h[3] + d) & _MASK,
            (h[4] + e) & _MASK, (h[5] + f) & _MASK, (h[6] + g) & _MASK, (h[7] + hh) & _MASK,
        ]

    out = bytearray(32)
    for i, value in enumerate(h):
        out[i * 4] = (value >> 24) & 0xFF
        out[i * 4 + 1] = (value >> 16) & 0xFF
        out[i * 4 + 2] = (value >> 8) & 0xFF
        out[i * 4 + 3] = value & 0xFF
    return bytes(out)


def sha256(data):
    if _hashlib is not None:
        return _hashlib.sha256(data).digest()
    return _sha256_slow(data)


def _hex(data):
    return "".join("%02x" % b for b in data)


def random_hex(length):
    """``length`` random bytes as hex.  Used for salts and session tokens."""
    raw = None
    if _os is not None and hasattr(_os, "urandom"):
        try:
            raw = _os.urandom(length)
        except Exception:  # pragma: no cover
            raw = None
    if raw is None:  # pragma: no cover - only on builds with no urandom
        import random

        raw = bytes(random.randrange(256) for _ in range(length))
    return _hex(raw)


def hash_password(password, salt, rounds=None):
    """Stretch ``password`` with ``salt``.  Returns (hex digest, rounds)."""
    if rounds is None:
        rounds = ROUNDS
    rounds = max(1, int(rounds))
    seed = (salt + "\x00" + password).encode("utf-8")
    digest = sha256(seed)
    for _ in range(rounds - 1):
        digest = sha256(digest + seed)
    return _hex(digest), rounds


def verify_password(password, salt, expected_hex, rounds):
    """Check a password without leaking where a mismatch happened."""
    if not expected_hex or not salt:
        return False
    actual, _ = hash_password(password, salt, rounds)
    return constant_time_equals(actual, expected_hex)


def constant_time_equals(a, b):
    """Compare two strings in time that does not depend on the match."""
    a = a or ""
    b = b or ""
    if len(a) != len(b):
        return False
    difference = 0
    for x, y in zip(a, b):
        difference |= ord(x) ^ ord(y)
    return difference == 0
