"""Apple App Attest verification for privileged Jarvis iPhone operations.

The browser bearer proves which Life user is signed in. App Attest separately
proves that the request came from a genuine build of the Jarvis iOS shell. A
server-issued, one-time challenge binds both proofs and prevents replay.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509 import ExtensionNotFound, UnrecognizedExtension
from cryptography.x509.verification import ExtensionPolicy, PolicyBuilder, Store
from pydantic import BaseModel, ConfigDict, Field

from openjarvis.life import LifeContext

APP_ATTEST_CHALLENGE_TTL_SECONDS = 300
_APP_ATTEST_NONCE_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.2")
_APPLE_APP_ATTEST_ROOT_PEM = b"""-----BEGIN CERTIFICATE-----
MIICITCCAaegAwIBAgIQC/O+DvHN0uD7jG5yH2IXmDAKBggqhkjOPQQDAzBSMSYw
JAYDVQQDDB1BcHBsZSBBcHAgQXR0ZXN0YXRpb24gUm9vdCBDQTETMBEGA1UECgwK
QXBwbGUgSW5jLjETMBEGA1UECAwKQ2FsaWZvcm5pYTAeFw0yMDAzMTgxODMyNTNa
Fw00NTAzMTUwMDAwMDBaMFIxJjAkBgNVBAMMHUFwcGxlIEFwcCBBdHRlc3RhdGlv
biBSb290IENBMRMwEQYDVQQKDApBcHBsZSBJbmMuMRMwEQYDVQQIDApDYWxpZm9y
bmlhMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAERTHhmLW07ATaFQIEVwTtT4dyctdh
NbJhFs/Ii2FdCgAHGbpphY3+d8qjuDngIN3WVhQUBHAoMeQ/cLiP1sOUtgjqK9au
Yen1mMEvRq9Sk3Jm5X8U62H+xTD3FE9TgS41o0IwQDAPBgNVHRMBAf8EBTADAQH/
MB0GA1UdDgQWBBSskRBTM72+aEH/pwyp5frq5eWKoTAOBgNVHQ8BAf8EBAMCAQYw
CgYIKoZIzj0EAwMDaAAwZQIwQgFGnByvsiVbpTKwSga0kP0e8EeDS4+sQmTvb7vn
53O5+FRXgeLhpJ06ysC5PrOyAjEAp5U4xDgEgllF7En3VcE3iexZZtKeYnpqtijV
oyFraWVIyd/dganmrduC1bmTBGwD
-----END CERTIFICATE-----
"""


class AppAttestError(RuntimeError):
    """Raised when an iPhone integrity proof cannot be trusted."""


class AppAttestAssertionProof(BaseModel):
    """A one-time assertion created by an already attested iOS installation."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=16, max_length=128)
    challenge: str = Field(min_length=16, max_length=128)
    key_id: str = Field(min_length=32, max_length=128)
    assertion: str = Field(min_length=32, max_length=16384)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _b64url_decode(value: str, *, maximum: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > maximum * 2:
        raise AppAttestError("App Attest payload has an invalid size")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise AppAttestError("App Attest payload is not valid base64url") from exc
    if not raw or len(raw) > maximum:
        raise AppAttestError("App Attest payload has an invalid size")
    return raw


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _hash(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _challenge_bytes(value: str) -> bytes:
    return _b64url_decode(value, maximum=64)


def _read_der_length(value: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(value):
        raise AppAttestError("App Attest nonce extension is malformed")
    first = value[offset]
    offset += 1
    if first < 0x80:
        return first, offset
    width = first & 0x7F
    if width == 0 or width > 4 or offset + width > len(value):
        raise AppAttestError("App Attest nonce extension is malformed")
    return int.from_bytes(value[offset : offset + width], "big"), offset + width


def _read_der(value: bytes, offset: int, tag: int) -> tuple[bytes, int]:
    if offset >= len(value) or value[offset] != tag:
        raise AppAttestError("App Attest nonce extension is malformed")
    length, content_offset = _read_der_length(value, offset + 1)
    end = content_offset + length
    if end > len(value):
        raise AppAttestError("App Attest nonce extension is malformed")
    return value[content_offset:end], end


def _nonce_extension(cert: x509.Certificate) -> bytes:
    try:
        extension = cert.extensions.get_extension_for_oid(_APP_ATTEST_NONCE_OID)
    except ExtensionNotFound as exc:
        raise AppAttestError("App Attest certificate has no nonce") from exc
    if not isinstance(extension.value, UnrecognizedExtension):
        raise AppAttestError("App Attest nonce extension is malformed")
    sequence, end = _read_der(extension.value.value, 0, 0x30)
    if end != len(extension.value.value):
        raise AppAttestError("App Attest nonce extension is malformed")
    context, end = _read_der(sequence, 0, 0xA1)
    if end != len(sequence):
        raise AppAttestError("App Attest nonce extension is malformed")
    nonce, end = _read_der(context, 0, 0x04)
    if end != len(context):
        raise AppAttestError("App Attest nonce extension is malformed")
    return nonce


@dataclass(frozen=True, slots=True)
class _AuthenticatorData:
    rp_id: bytes
    counter: int
    aaguid: bytes = b""
    credential_id: bytes = b""
    validation_category: int = 0
    bundle_version: str = ""


def _authenticator_data(raw: bytes, *, attestation: bool) -> _AuthenticatorData:
    if len(raw) < 37:
        raise AppAttestError("App Attest authenticator data is truncated")
    rp_id, flags, counter = struct.unpack(">32sBI", raw[:37])
    if not attestation:
        return _AuthenticatorData(rp_id=rp_id, counter=counter)
    if not flags & 0x40 or len(raw) < 55:
        raise AppAttestError("App Attest credential data is missing")
    aaguid = raw[37:53]
    credential_length = int.from_bytes(raw[53:55], "big")
    end = 55 + credential_length
    if credential_length != 32 or end > len(raw):
        raise AppAttestError("App Attest credential identifier is invalid")
    extensions: Dict[str, Any] = {}
    try:
        tail = raw[end:]
        credential_key = cbor2.loads(tail)
        encoded_key = cbor2.dumps(credential_key)
        if not tail.startswith(encoded_key):
            raise ValueError("non-canonical credential key")
        encoded_extensions = tail[len(encoded_key) :]
        if flags & 0x80:
            decoded_extensions = cbor2.loads(encoded_extensions)
            if not isinstance(decoded_extensions, dict):
                raise ValueError("extensions are not a map")
            if cbor2.dumps(decoded_extensions) != encoded_extensions:
                raise ValueError("trailing authenticator data")
            extensions = decoded_extensions
        elif encoded_extensions:
            raise ValueError("unexpected authenticator extensions")
    except (ValueError, TypeError, cbor2.CBORDecodeError) as exc:
        raise AppAttestError(
            "App Attest authenticator extensions are malformed"
        ) from exc
    raw_category = extensions.get("apple_validation_category_01", b"")
    if raw_category and (not isinstance(raw_category, bytes) or len(raw_category) != 4):
        raise AppAttestError("App Attest validation category is malformed")
    bundle_version = extensions.get("apple_bundle_version_01", "")
    if bundle_version and not isinstance(bundle_version, str):
        raise AppAttestError("App Attest bundle version is malformed")
    if bool(raw_category) != bool(bundle_version):
        raise AppAttestError("App Attest validation extensions are incomplete")
    return _AuthenticatorData(
        rp_id=rp_id,
        counter=counter,
        aaguid=aaguid,
        credential_id=raw[55:end],
        validation_category=(
            int.from_bytes(raw_category, "little") if raw_category else 0
        ),
        bundle_version=bundle_version,
    )


def _canonical_client_data(
    *,
    purpose: str,
    challenge: str,
    user_id: str,
    device_id: str,
    resource_id: str,
    confirmation_method: str = "",
) -> bytes:
    return json.dumps(
        {
            "challenge": challenge,
            "confirmation_method": confirmation_method,
            "device_id": device_id,
            "purpose": purpose,
            "resource_id": resource_id,
            "user_id": user_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _length_prefixed(values: tuple[str, ...]) -> bytes:
    encoded = bytearray()
    for value in values:
        raw = value.encode("utf-8")
        encoded.extend(str(len(raw)).encode("ascii"))
        encoded.extend(b":")
        encoded.extend(raw)
    return bytes(encoded)


def native_result_client_data(
    *,
    claim_token: str,
    proposal_id: str,
    device_id: str,
    event: Mapping[str, Any],
) -> bytes:
    """Canonical bytes signed by App Attest only after EventKit succeeds."""
    values = (
        "jarvis-native-result-v1",
        claim_token,
        proposal_id,
        device_id,
        str(event.get("id", "")),
        str(event.get("title", "")),
        str(event.get("startAt", event.get("start_at", ""))),
        str(event.get("endAt", event.get("end_at", ""))),
        "1" if event.get("isAllDay", event.get("is_all_day", False)) else "0",
        str(event.get("location", "")),
        str(event.get("calendarTitle", event.get("calendar_title", ""))),
    )
    result = _length_prefixed(values)
    if len(result) > 8192:
        raise AppAttestError("Native result is too large to authenticate")
    return result


class AppAttestStore:
    """Durable challenge, attested key and assertion-counter authority."""

    def __init__(
        self,
        life: LifeContext,
        *,
        team_id: str | None = None,
        bundle_id: str | None = None,
        bundle_version: str | None = None,
        environment: str | None = None,
        validation_categories: frozenset[int] = frozenset({2, 3, 4}),
        root_certificate_pem: bytes = _APPLE_APP_ATTEST_ROOT_PEM,
        now: Any = _now,
    ) -> None:
        self._life = life
        self._team_id = (team_id or os.environ.get("APPLE_TEAM_ID", "")).strip()
        self._bundle_id = (
            bundle_id
            or os.environ.get("APPLE_BUNDLE_ID", "com.lextechnology.jarvislife")
        ).strip()
        self._bundle_version = (
            bundle_version or os.environ.get("APPLE_BUNDLE_VERSION", "5")
        ).strip()
        self._environment = (
            environment or os.environ.get("APPLE_APP_ATTEST_ENVIRONMENT", "production")
        ).strip()
        if self._environment not in {"development", "production"}:
            raise AppAttestError("Apple App Attest environment is invalid")
        self._validation_categories = validation_categories
        self._root_certificate_pem = root_certificate_pem
        self._now = now

    def issue_challenge(
        self,
        user_id: str,
        *,
        purpose: str,
        resource_id: str = "",
    ) -> Dict[str, str]:
        if purpose not in {"attest", "device_grant", "native_action", "finance"}:
            raise AppAttestError("Unknown App Attest challenge purpose")
        challenge_id = secrets.token_urlsafe(24)
        challenge = _b64url(secrets.token_bytes(32))
        now = self._now()
        with self._life.connection.transaction():
            self._life.connection.execute(
                "DELETE FROM jarvis_app_attest_challenges"
                " WHERE user_id = ? AND expires_at <= ?",
                (user_id, now.isoformat()),
            )
            self._life.connection.execute(
                "INSERT INTO jarvis_app_attest_challenges"
                " (id, user_id, purpose, resource_id, challenge_hash, created_at,"
                " expires_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    challenge_id,
                    user_id,
                    purpose,
                    resource_id,
                    hashlib.sha256(_challenge_bytes(challenge)).hexdigest(),
                    now.isoformat(),
                    (
                        now + timedelta(seconds=APP_ATTEST_CHALLENGE_TTL_SECONDS)
                    ).isoformat(),
                ),
            )
        return {
            "challenge_id": challenge_id,
            "challenge": challenge,
            "expires_at": (
                now + timedelta(seconds=APP_ATTEST_CHALLENGE_TTL_SECONDS)
            ).isoformat(),
        }

    def is_registered(self, user_id: str, device_id: str, key_id: str) -> bool:
        """Return whether this attested key is active for this user's device."""
        row = self._life.connection.execute(
            "SELECT 1 AS registered FROM jarvis_app_attest_keys"
            " WHERE user_id = ? AND device_id = ? AND key_id = ? AND status = 'active'",
            (user_id, device_id, key_id),
        ).fetchone()
        return row is not None

    def client_data(
        self,
        *,
        purpose: str,
        challenge: str,
        user_id: str,
        device_id: str,
        resource_id: str,
        confirmation_method: str = "",
    ) -> str:
        """Return the exact base64url bytes the native assertion must sign."""
        return _b64url(
            _canonical_client_data(
                purpose=purpose,
                challenge=challenge,
                user_id=user_id,
                device_id=device_id,
                resource_id=resource_id,
                confirmation_method=confirmation_method,
            )
        )

    def _active_challenge(
        self,
        user_id: str,
        challenge_id: str,
        challenge: str,
        *,
        purpose: str,
        resource_id: str,
    ) -> Mapping[str, Any]:
        row = self._life.connection.execute(
            "SELECT * FROM jarvis_app_attest_challenges"
            " WHERE id = ? AND user_id = ? AND purpose = ? AND resource_id = ?",
            (challenge_id, user_id, purpose, resource_id),
        ).fetchone()
        if row is None or row["consumed_at"]:
            raise AppAttestError("App Attest challenge is invalid or already used")
        if datetime.fromisoformat(str(row["expires_at"])) <= self._now():
            raise AppAttestError("App Attest challenge expired")
        actual = hashlib.sha256(_challenge_bytes(challenge)).hexdigest()
        if not secrets.compare_digest(str(row["challenge_hash"]), actual):
            raise AppAttestError("App Attest challenge does not match")
        return row

    def _consume_challenge(self, user_id: str, challenge_id: str) -> None:
        consumed = self._life.connection.execute(
            "UPDATE jarvis_app_attest_challenges SET consumed_at = ?"
            " WHERE id = ? AND user_id = ? AND consumed_at IS NULL"
            " AND expires_at > ?",
            (
                self._now().isoformat(),
                challenge_id,
                user_id,
                self._now().isoformat(),
            ),
        ).rowcount
        if consumed != 1:
            raise AppAttestError("App Attest challenge is no longer active")

    def register_key(
        self,
        user_id: str,
        *,
        challenge_id: str,
        challenge: str,
        device_id: str,
        key_id: str,
        attestation_object: str,
        device_label: str,
    ) -> Dict[str, Any]:
        if not self._team_id or not self._bundle_id:
            raise AppAttestError("Apple App Attest identity is not configured")
        key_identifier = _b64url_decode(key_id, maximum=64)
        if len(key_identifier) != 32:
            raise AppAttestError("App Attest key identifier is invalid")
        raw_attestation = _b64url_decode(attestation_object, maximum=32768)
        with self._life.connection.transaction():
            self._active_challenge(
                user_id,
                challenge_id,
                challenge,
                purpose="attest",
                resource_id=device_id,
            )
            try:
                decoded = cbor2.loads(raw_attestation)
                auth_raw = decoded["authData"]
                statement = decoded["attStmt"]
                certificates = statement["x5c"]
                receipt = statement["receipt"]
            except (KeyError, TypeError, ValueError, cbor2.CBORDecodeError) as exc:
                raise AppAttestError(
                    "App Attest attestation object is malformed"
                ) from exc
            if decoded.get("fmt") != "apple-appattest":
                raise AppAttestError("App Attest format is invalid")
            if not isinstance(certificates, list) or not 1 <= len(certificates) <= 4:
                raise AppAttestError("App Attest certificate chain is invalid")
            try:
                chain = [x509.load_der_x509_certificate(item) for item in certificates]
                root = x509.load_pem_x509_certificate(self._root_certificate_pem)
                (
                    PolicyBuilder()
                    .store(Store([root]))
                    .extension_policies(
                        ca_policy=ExtensionPolicy.webpki_defaults_ca(),
                        # App Attest leaf certificates intentionally do not use
                        # TLS clientAuth EKU; the Apple-specific checks below
                        # provide their application semantics.
                        ee_policy=ExtensionPolicy.permit_all(),
                    )
                    .build_client_verifier()
                    .verify(chain[0], chain[1:])
                )
            except Exception as exc:  # noqa: BLE001 - cryptography has many subclasses
                raise AppAttestError("App Attest certificate chain is invalid") from exc
            auth = _authenticator_data(auth_raw, attestation=True)
            expected_rp = _hash(f"{self._team_id}.{self._bundle_id}".encode())
            if auth.rp_id != expected_rp or auth.counter != 0:
                raise AppAttestError("App Attest authenticator identity is invalid")
            expected_aaguid = (
                b"appattestdevelop"
                if self._environment == "development"
                else b"appattest" + b"\0" * 7
            )
            if auth.aaguid != expected_aaguid:
                raise AppAttestError("App Attest environment is invalid")
            # iOS 27 adds these extensions. Older supported releases do not
            # include them, so validate both only when Apple supplies them.
            if auth.validation_category:
                if auth.validation_category not in self._validation_categories:
                    raise AppAttestError(
                        "App Attest validation category is not allowed"
                    )
                if auth.bundle_version != self._bundle_version:
                    raise AppAttestError("App Attest bundle version does not match")
            if auth.credential_id != key_identifier:
                raise AppAttestError("App Attest credential identifier does not match")
            client_hash = _hash(_challenge_bytes(challenge))
            if _nonce_extension(chain[0]) != _hash(auth_raw + client_hash):
                raise AppAttestError("App Attest nonce does not match")
            public_key = chain[0].public_key()
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise AppAttestError("App Attest public key is invalid")
            point = public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
            if _hash(point) != key_identifier:
                raise AppAttestError("App Attest key identifier does not match")
            existing = self._life.connection.execute(
                "SELECT user_id FROM jarvis_app_attest_keys WHERE key_id = ?",
                (key_id,),
            ).fetchone()
            if existing is not None and str(existing["user_id"]) != user_id:
                raise AppAttestError("App Attest key is already bound to another user")
            now = self._now().isoformat()
            public_der = public_key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            # One installation may rotate an unusable key. Delete only the
            # same user's previous key for this device inside this transaction;
            # a key already owned by another user remains rejected above.
            self._life.connection.execute(
                "DELETE FROM jarvis_app_attest_keys"
                " WHERE user_id = ? AND device_id = ? AND key_id <> ?",
                (user_id, device_id, key_id),
            )
            self._life.connection.execute(
                "INSERT INTO jarvis_app_attest_keys"
                " (key_id, user_id, device_id, device_label, public_key_der, receipt,"
                " sign_counter, environment, status, attested_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'active', ?, ?)"
                " ON CONFLICT(key_id) DO UPDATE SET device_id = excluded.device_id,"
                " device_label = excluded.device_label,"
                " public_key_der = excluded.public_key_der,"
                " receipt = excluded.receipt, sign_counter = 0,"
                " environment = excluded.environment,"
                " status = 'active', updated_at = excluded.updated_at",
                (
                    key_id,
                    user_id,
                    device_id,
                    device_label.strip()[:120],
                    _b64url(public_der),
                    _b64url(bytes(receipt)),
                    self._environment,
                    now,
                    now,
                ),
            )
            self._consume_challenge(user_id, challenge_id)
        return {"device_id": device_id, "key_id": key_id, "attested": True}

    def verify_assertion(
        self,
        user_id: str,
        *,
        purpose: str,
        resource_id: str,
        confirmation_method: str,
        device_id: str,
        challenge_id: str,
        challenge: str,
        key_id: str,
        assertion: str,
    ) -> None:
        raw_assertion = _b64url_decode(assertion, maximum=8192)
        with self._life.connection.transaction():
            self._active_challenge(
                user_id,
                challenge_id,
                challenge,
                purpose=purpose,
                resource_id=resource_id,
            )
            key = self._life.connection.execute(
                "SELECT * FROM jarvis_app_attest_keys"
                " WHERE key_id = ? AND user_id = ? AND device_id = ?"
                " AND status = 'active'",
                (key_id, user_id, device_id),
            ).fetchone()
            if key is None:
                raise AppAttestError("App Attest key is not registered for this device")
            try:
                decoded = cbor2.loads(raw_assertion)
                auth_raw = decoded["authenticatorData"]
                signature = decoded["signature"]
            except (KeyError, TypeError, ValueError, cbor2.CBORDecodeError) as exc:
                raise AppAttestError("App Attest assertion is malformed") from exc
            auth = _authenticator_data(auth_raw, attestation=False)
            if auth.rp_id != _hash(f"{self._team_id}.{self._bundle_id}".encode()):
                raise AppAttestError("App Attest assertion identity is invalid")
            previous_counter = int(key["sign_counter"])
            if auth.counter <= previous_counter:
                raise AppAttestError("App Attest assertion was replayed")
            client_data = _canonical_client_data(
                purpose=purpose,
                challenge=challenge,
                user_id=user_id,
                device_id=device_id,
                resource_id=resource_id,
                confirmation_method=confirmation_method,
            )
            nonce = _hash(auth_raw + _hash(client_data))
            try:
                public_key = serialization.load_der_public_key(
                    _b64url_decode(str(key["public_key_der"]), maximum=1024)
                )
                if not isinstance(public_key, ec.EllipticCurvePublicKey):
                    raise TypeError("not an EC public key")
                public_key.verify(
                    bytes(signature),
                    nonce,
                    ec.ECDSA(utils.Prehashed(hashes.SHA256())),
                )
            except (InvalidSignature, TypeError, ValueError) as exc:
                raise AppAttestError(
                    "App Attest assertion signature is invalid"
                ) from exc
            updated = self._life.connection.execute(
                "UPDATE jarvis_app_attest_keys SET sign_counter = ?, updated_at = ?"
                " WHERE key_id = ? AND user_id = ? AND sign_counter = ?"
                " AND status = 'active'",
                (
                    auth.counter,
                    self._now().isoformat(),
                    key_id,
                    user_id,
                    previous_counter,
                ),
            ).rowcount
            if updated != 1:
                raise AppAttestError(
                    "App Attest assertion counter changed concurrently"
                )
            self._consume_challenge(user_id, challenge_id)

    def verify_native_result_assertion(
        self,
        user_id: str,
        *,
        proposal_id: str,
        device_id: str,
        claim_token: str,
        event: Mapping[str, Any],
        key_id: str,
        assertion: str,
    ) -> None:
        """Verify a post-EventKit assertion without exposing a signing key to JS."""
        client_data = native_result_client_data(
            claim_token=claim_token,
            proposal_id=proposal_id,
            device_id=device_id,
            event=event,
        )
        raw_assertion = _b64url_decode(assertion, maximum=8192)
        with self._life.connection.transaction():
            key = self._life.connection.execute(
                "SELECT * FROM jarvis_app_attest_keys"
                " WHERE key_id = ? AND user_id = ? AND device_id = ?"
                " AND status = 'active'",
                (key_id, user_id, device_id),
            ).fetchone()
            if key is None:
                raise AppAttestError("App Attest key is not registered for this device")
            try:
                decoded = cbor2.loads(raw_assertion)
                auth_raw = decoded["authenticatorData"]
                signature = decoded["signature"]
            except (KeyError, TypeError, ValueError, cbor2.CBORDecodeError) as exc:
                raise AppAttestError("App Attest assertion is malformed") from exc
            auth = _authenticator_data(auth_raw, attestation=False)
            if auth.rp_id != _hash(f"{self._team_id}.{self._bundle_id}".encode()):
                raise AppAttestError("App Attest assertion identity is invalid")
            previous_counter = int(key["sign_counter"])
            if auth.counter <= previous_counter:
                raise AppAttestError("App Attest assertion was replayed")
            nonce = _hash(auth_raw + _hash(client_data))
            try:
                public_key = serialization.load_der_public_key(
                    _b64url_decode(str(key["public_key_der"]), maximum=1024)
                )
                if not isinstance(public_key, ec.EllipticCurvePublicKey):
                    raise TypeError("not an EC public key")
                public_key.verify(
                    bytes(signature),
                    nonce,
                    ec.ECDSA(utils.Prehashed(hashes.SHA256())),
                )
            except (InvalidSignature, TypeError, ValueError) as exc:
                raise AppAttestError(
                    "App Attest assertion signature is invalid"
                ) from exc
            updated = self._life.connection.execute(
                "UPDATE jarvis_app_attest_keys SET sign_counter = ?, updated_at = ?"
                " WHERE key_id = ? AND user_id = ? AND sign_counter = ?"
                " AND status = 'active'",
                (
                    auth.counter,
                    self._now().isoformat(),
                    key_id,
                    user_id,
                    previous_counter,
                ),
            ).rowcount
            if updated != 1:
                raise AppAttestError(
                    "App Attest assertion counter changed concurrently"
                )

    def consume(
        self,
        *,
        user_id: str,
        proposal_id: str,
        confirmation_method: str,
        device_id: str,
        challenge_id: str,
        challenge: str,
        key_id: str,
        assertion: str,
    ) -> bool:
        """StrongAuthVerifier compatibility for financial proposals."""
        try:
            self.verify_assertion(
                user_id,
                purpose="finance",
                resource_id=proposal_id,
                confirmation_method=confirmation_method,
                device_id=device_id,
                challenge_id=challenge_id,
                challenge=challenge,
                key_id=key_id,
                assertion=assertion,
            )
        except (AppAttestError, TypeError, ValueError):
            return False
        return True


__all__ = [
    "APP_ATTEST_CHALLENGE_TTL_SECONDS",
    "AppAttestError",
    "AppAttestAssertionProof",
    "AppAttestStore",
    "native_result_client_data",
    "_canonical_client_data",
]
