"""Time-based one-time password (TOTP / RFC 6238) — stdlib only (FLAWS §6.2).

Steppy's automated MFA login needs to compute a TOTP code from a shared secret.
Rather than take a ``pyotp`` dependency, this implements RFC 6238 (HMAC-SHA1,
30-second window, 6 digits) in ~15 lines of the standard library. That keeps the
**generated** ``conftest.py.j2`` self-contained: it embeds a byte-identical copy
of ``_totp_now`` (generated runtime code can't import ``steppy``), so a consumer
project running MFA logins under ``steppy run`` needs no extra package. The
``tests/test_totp.py`` guard extracts both copies and asserts they agree, and
pins ``_totp_now`` against the RFC 6238 test vector.
"""

import base64
import hmac
import hashlib
import time


def _totp_now(secret: str, digits: int = 6, period: int = 30) -> str:
    """Current TOTP code for a base32 ``secret`` (RFC 6238, HMAC-SHA1)."""
    key = base64.b32decode(secret.strip().replace(" ", "").upper() + "=" * (-len(secret.strip().replace(" ", "")) % 8))
    counter = int(time.time()) // period
    mac = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)
