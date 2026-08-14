"""Cryptographic App Attest registration, assertion and replay controls."""

from __future__ import annotations

import base64
import hashlib
import struct
from datetime import datetime, timedelta, timezone

import cbor2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID

from openjarvis.life.app_attest import (
    AppAttestError,
    AppAttestStore,
    native_result_client_data,
)

TEAM_ID = "NP9X453K55"
BUNDLE_ID = "com.lextechnology.jarvislife"
DEVICE_ID = "ios-device-attested-1"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _hash(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _der_length(length: int) -> bytes:
    if length < 128:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _der_length(len(value)) + value


def _certificate(
    subject: x509.Name,
    issuer: x509.Name,
    public_key,
    issuer_key,
    now: datetime,
    *,
    ca: bool,
    nonce: bytes | None = None,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
    )
    if nonce is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(
                x509.ObjectIdentifier("1.2.840.113635.100.8.2"),
                _der(0x30, _der(0xA1, _der(0x04, nonce))),
            ),
            critical=False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


def _attestation_fixture(
    now: datetime,
    challenge: str,
    *,
    root_key=None,
    root: x509.Certificate | None = None,
    include_extensions: bool = True,
):
    root_key = root_key or ec.generate_private_key(ec.SECP384R1())
    intermediate_key = ec.generate_private_key(ec.SECP384R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    intermediate_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Test Intermediate")]
    )
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test App")])
    root = root or _certificate(
        root_name, root_name, root_key.public_key(), root_key, now, ca=True
    )
    intermediate = _certificate(
        intermediate_name,
        root_name,
        intermediate_key.public_key(),
        root_key,
        now,
        ca=True,
    )
    point = leaf_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    key_id = _hash(point)
    credential_key = {
        1: 2,
        3: -7,
        -1: 1,
        -2: point[1:33],
        -3: point[33:65],
    }
    extensions = {
        "apple_bundle_version_01": "5",
        "apple_validation_category_01": (3).to_bytes(4, "little"),
    }
    auth_data = (
        _hash(f"{TEAM_ID}.{BUNDLE_ID}".encode())
        + struct.pack(">BI", 0xC0 if include_extensions else 0x40, 0)
        + b"appattestdevelop"
        + len(key_id).to_bytes(2, "big")
        + key_id
        + cbor2.dumps(credential_key)
        + (cbor2.dumps(extensions) if include_extensions else b"")
    )
    challenge_bytes = base64.urlsafe_b64decode(challenge + "=" * (-len(challenge) % 4))
    nonce = _hash(auth_data + _hash(challenge_bytes))
    leaf = _certificate(
        leaf_name,
        intermediate_name,
        leaf_key.public_key(),
        intermediate_key,
        now,
        ca=False,
        nonce=nonce,
    )
    attestation = cbor2.dumps(
        {
            "fmt": "apple-appattest",
            "attStmt": {
                "x5c": [
                    leaf.public_bytes(serialization.Encoding.DER),
                    intermediate.public_bytes(serialization.Encoding.DER),
                ],
                "receipt": b"test-receipt",
            },
            "authData": auth_data,
        }
    )
    root_pem = root.public_bytes(serialization.Encoding.PEM)
    return leaf_key, _b64url(key_id), _b64url(attestation), root_pem


def _assertion(private_key, client_data: str, counter: int = 1) -> str:
    auth_data = _hash(f"{TEAM_ID}.{BUNDLE_ID}".encode()) + struct.pack(
        ">BI", 0, counter
    )
    raw_client_data = base64.urlsafe_b64decode(
        client_data + "=" * (-len(client_data) % 4)
    )
    nonce = _hash(auth_data + _hash(raw_client_data))
    signature = private_key.sign(nonce, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    return _b64url(
        cbor2.dumps({"signature": signature, "authenticatorData": auth_data})
    )


def test_attested_device_assertion_is_one_time_and_bound_to_operation(life, user):
    clock = datetime.now(timezone.utc)
    bootstrap = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
    )
    registration_challenge = bootstrap.issue_challenge(
        user.id, purpose="attest", resource_id=DEVICE_ID
    )
    private_key, key_id, attestation, root_pem = _attestation_fixture(
        clock, registration_challenge["challenge"]
    )
    store = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
        root_certificate_pem=root_pem,
    )

    registered = store.register_key(
        user.id,
        challenge_id=registration_challenge["challenge_id"],
        challenge=registration_challenge["challenge"],
        device_id=DEVICE_ID,
        key_id=key_id,
        attestation_object=attestation,
        device_label="iPhone de teste",
    )
    challenge = store.issue_challenge(
        user.id, purpose="native_action", resource_id="proposal-1"
    )
    client_data = store.client_data(
        purpose="native_action",
        challenge=challenge["challenge"],
        user_id=user.id,
        device_id=DEVICE_ID,
        resource_id="proposal-1",
        confirmation_method="explicit",
    )
    assertion = _assertion(private_key, client_data)

    store.verify_assertion(
        user.id,
        purpose="native_action",
        resource_id="proposal-1",
        confirmation_method="explicit",
        device_id=DEVICE_ID,
        challenge_id=challenge["challenge_id"],
        challenge=challenge["challenge"],
        key_id=key_id,
        assertion=assertion,
    )

    assert registered == {"device_id": DEVICE_ID, "key_id": key_id, "attested": True}
    with pytest.raises(AppAttestError, match="already used"):
        store.verify_assertion(
            user.id,
            purpose="native_action",
            resource_id="proposal-1",
            confirmation_method="explicit",
            device_id=DEVICE_ID,
            challenge_id=challenge["challenge_id"],
            challenge=challenge["challenge"],
            key_id=key_id,
            assertion=assertion,
        )

    event = {
        "id": "eventkit-123",
        "title": "Dentista",
        "startAt": "2026-08-14T15:00:00-03:00",
        "endAt": "2026-08-14T16:00:00-03:00",
        "isAllDay": False,
        "location": "Clínica",
        "calendarTitle": "Pessoal",
    }
    result_data = native_result_client_data(
        claim_token="calendarclaimtoken_abcdefghijklmnopqrstuvwxyz",
        proposal_id="proposal-1",
        device_id=DEVICE_ID,
        event=event,
    )
    auth_data = _hash(f"{TEAM_ID}.{BUNDLE_ID}".encode()) + struct.pack(">BI", 0, 2)
    result_nonce = _hash(auth_data + _hash(result_data))
    result_signature = private_key.sign(
        result_nonce, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
    )
    result_assertion = _b64url(
        cbor2.dumps({"signature": result_signature, "authenticatorData": auth_data})
    )

    store.verify_native_result_assertion(
        user.id,
        proposal_id="proposal-1",
        device_id=DEVICE_ID,
        claim_token="calendarclaimtoken_abcdefghijklmnopqrstuvwxyz",
        event=event,
        key_id=key_id,
        assertion=result_assertion,
    )

    with pytest.raises(AppAttestError, match="replayed|signature"):
        store.verify_native_result_assertion(
            user.id,
            proposal_id="proposal-1",
            device_id=DEVICE_ID,
            claim_token="calendarclaimtoken_abcdefghijklmnopqrstuvwxyz",
            event={**event, "id": "forged-event"},
            key_id=key_id,
            assertion=result_assertion,
        )


def test_assertion_signed_for_another_resource_is_rejected(life, user):
    clock = datetime.now(timezone.utc)
    bootstrap = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
    )
    registration = bootstrap.issue_challenge(
        user.id, purpose="attest", resource_id=DEVICE_ID
    )
    private_key, key_id, attestation, root_pem = _attestation_fixture(
        clock, registration["challenge"]
    )
    store = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
        root_certificate_pem=root_pem,
    )
    store.register_key(
        user.id,
        challenge_id=registration["challenge_id"],
        challenge=registration["challenge"],
        device_id=DEVICE_ID,
        key_id=key_id,
        attestation_object=attestation,
        device_label="iPhone",
    )
    challenge = store.issue_challenge(
        user.id, purpose="native_action", resource_id="proposal-real"
    )
    wrong_data = store.client_data(
        purpose="native_action",
        challenge=challenge["challenge"],
        user_id=user.id,
        device_id=DEVICE_ID,
        resource_id="proposal-other",
        confirmation_method="explicit",
    )

    with pytest.raises(AppAttestError, match="signature"):
        store.verify_assertion(
            user.id,
            purpose="native_action",
            resource_id="proposal-real",
            confirmation_method="explicit",
            device_id=DEVICE_ID,
            challenge_id=challenge["challenge_id"],
            challenge=challenge["challenge"],
            key_id=key_id,
            assertion=_assertion(private_key, wrong_data),
        )


@pytest.mark.parametrize("include_extensions", [False, True])
def test_registration_accepts_legacy_and_ios_27_attestations(
    life, user, include_extensions
):
    clock = datetime.now(timezone.utc)
    bootstrap = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
    )
    challenge = bootstrap.issue_challenge(
        user.id, purpose="attest", resource_id=DEVICE_ID
    )
    _, key_id, attestation, root_pem = _attestation_fixture(
        clock,
        challenge["challenge"],
        include_extensions=include_extensions,
    )
    store = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
        root_certificate_pem=root_pem,
    )

    result = store.register_key(
        user.id,
        challenge_id=challenge["challenge_id"],
        challenge=challenge["challenge"],
        device_id=DEVICE_ID,
        key_id=key_id,
        attestation_object=attestation,
        device_label="iPhone",
    )

    assert result["attested"] is True


def test_registration_rotates_the_key_for_the_same_user_device(life, user):
    clock = datetime.now(timezone.utc)
    root_key = ec.generate_private_key(ec.SECP384R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    root = _certificate(
        root_name, root_name, root_key.public_key(), root_key, clock, ca=True
    )
    store = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
        root_certificate_pem=root.public_bytes(serialization.Encoding.PEM),
    )
    key_ids = []
    for _ in range(2):
        challenge = store.issue_challenge(
            user.id, purpose="attest", resource_id=DEVICE_ID
        )
        _, key_id, attestation, _ = _attestation_fixture(
            clock,
            challenge["challenge"],
            root_key=root_key,
            root=root,
        )
        store.register_key(
            user.id,
            challenge_id=challenge["challenge_id"],
            challenge=challenge["challenge"],
            device_id=DEVICE_ID,
            key_id=key_id,
            attestation_object=attestation,
            device_label="iPhone",
        )
        key_ids.append(key_id)

    rows = life.connection.execute(
        "SELECT key_id FROM jarvis_app_attest_keys WHERE user_id = ? AND device_id = ?",
        (user.id, DEVICE_ID),
    ).fetchall()
    assert [row["key_id"] for row in rows] == [key_ids[-1]]


def test_production_rejects_a_development_attestation(life, user):
    """A TestFlight backend must never accept an App Attest sandbox key."""
    clock = datetime.now(timezone.utc)
    bootstrap = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="development",
        now=lambda: clock,
    )
    challenge = bootstrap.issue_challenge(
        user.id, purpose="attest", resource_id=DEVICE_ID
    )
    _, key_id, attestation, root_pem = _attestation_fixture(
        clock, challenge["challenge"]
    )
    production = AppAttestStore(
        life,
        team_id=TEAM_ID,
        bundle_id=BUNDLE_ID,
        bundle_version="5",
        environment="production",
        root_certificate_pem=root_pem,
        now=lambda: clock,
    )

    with pytest.raises(AppAttestError, match="environment"):
        production.register_key(
            user.id,
            challenge_id=challenge["challenge_id"],
            challenge=challenge["challenge"],
            device_id=DEVICE_ID,
            key_id=key_id,
            attestation_object=attestation,
            device_label="iPhone",
        )
