"""Generate a VAPID keypair for web push, ready to paste into env vars.

VAPID keys are self-generated, not issued by any provider -- there's no
account to create here, just an EC (P-256) keypair the app uses to sign
push messages so browsers' push services can verify they came from
PracticeLoop and not an impersonator. Run once:

    python scripts/gen_vapid_keys.py

and set the two printed values as VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY.
Rotating them later just invalidates every subscriber's existing
subscription (they'll need to re-enable notifications) -- not a security
incident, just a client re-registration.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )

    print("VAPID_PUBLIC_KEY=" + _b64url(public_raw))
    print("VAPID_PRIVATE_KEY=" + _b64url(private_raw))
    print()
    print("Both are plain env vars (no PEM files needed) -- set them on")
    print("Render, and set VAPID_SUBJECT to a mailto: address that's yours.")


if __name__ == "__main__":
    main()
