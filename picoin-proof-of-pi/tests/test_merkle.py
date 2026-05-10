from app.core.merkle import merkle_proof, merkle_root, verify_merkle_proof


def test_merkle_proof_roundtrip() -> None:
    segment = "243F6A8885A308D3"
    root = merkle_root(segment, 1)
    proof = merkle_proof(segment, 1, 4)

    assert verify_merkle_proof(4, "F", proof, root) is True


def test_merkle_proof_rejects_tampered_digit() -> None:
    segment = "243F6A8885A308D3"
    root = merkle_root(segment, 1)
    proof = merkle_proof(segment, 1, 4)

    assert verify_merkle_proof(4, "0", proof, root) is False
