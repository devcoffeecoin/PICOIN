from app.core.signatures import build_submission_signature_payload, generate_keypair, sign_payload, verify_payload_signature


def test_ed25519_signature_roundtrip() -> None:
    keypair = generate_keypair()
    payload = build_submission_signature_payload(
        task_id="task_test",
        miner_id="miner_test",
        range_start=1,
        range_end=64,
        algorithm="bbp_hex_v1",
        result_hash="a" * 64,
        signed_at="2026-05-10T00:00:00+00:00",
    )

    signature = sign_payload(keypair["private_key"], payload)

    assert verify_payload_signature(keypair["public_key"], payload, signature) is True


def test_ed25519_signature_rejects_tampering() -> None:
    keypair = generate_keypair()
    payload = build_submission_signature_payload(
        task_id="task_test",
        miner_id="miner_test",
        range_start=1,
        range_end=64,
        algorithm="bbp_hex_v1",
        result_hash="a" * 64,
        signed_at="2026-05-10T00:00:00+00:00",
    )
    signature = sign_payload(keypair["private_key"], payload)
    payload["result_hash"] = "b" * 64

    assert verify_payload_signature(keypair["public_key"], payload, signature) is False
