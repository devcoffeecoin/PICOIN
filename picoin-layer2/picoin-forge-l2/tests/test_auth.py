from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from picoin_forge_l2.common.crypto import request_signing_payload, sign_message
from picoin_forge_l2.coordinator import main as coordinator_main
from picoin_forge_l2.worker.registration import load_private_key, register_worker, rotate_worker_key


def test_write_token_is_optional_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PICOIN_FORGE_COORDINATOR_TOKEN", raising=False)
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PIAUTHTOKENOPTIONAL", tmp_path / "worker")

    response = client.post("/workers/register", json=registration.model_dump(mode="json"))

    assert response.status_code == 200


def test_write_token_rejects_missing_header_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_FORGE_COORDINATOR_TOKEN", "secret-token")
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PIAUTHTOKENREJECT", tmp_path / "worker")

    response = client.post("/workers/register", json=registration.model_dump(mode="json"))

    assert response.status_code == 401


def test_write_token_accepts_matching_header_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("PICOIN_FORGE_COORDINATOR_TOKEN", "secret-token")
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path))
    client = TestClient(coordinator_main.api)
    registration = register_worker("PIAUTHTOKENACCEPT", tmp_path / "worker")

    response = client.post(
        "/workers/register",
        headers={"X-Picoin-Forge-Token": "secret-token"},
        json=registration.model_dump(mode="json"),
    )

    assert response.status_code == 200


def test_worker_signature_can_be_required(tmp_path, monkeypatch):
    monkeypatch.delenv("PICOIN_FORGE_COORDINATOR_TOKEN", raising=False)
    monkeypatch.setenv("PICOIN_FORGE_REQUIRE_WORKER_SIGNATURES", "1")
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path / "coordinator"))
    client = TestClient(coordinator_main.api)
    worker_dir = tmp_path / "worker"
    registration = register_worker("PIWORKERSIGNATURE", worker_dir)

    missing_signature = client.post("/workers/register", json=registration.model_dump(mode="json"))
    assert missing_signature.status_code == 401

    payload = registration.model_dump(mode="json")
    body = json.dumps(payload, default=str).encode("utf-8")
    timestamp = str(int(time.time()))
    private_key = load_private_key(worker_dir)
    assert private_key is not None
    signature = sign_message(private_key, request_signing_payload("POST", "/workers/register", timestamp, body))
    signed = client.post(
        "/workers/register",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Picoin-Forge-Worker-Id": registration.worker_id,
            "X-Picoin-Forge-Timestamp": timestamp,
            "X-Picoin-Forge-Signature": signature,
        },
    )

    assert signed.status_code == 200

    replay = client.post(
        "/workers/register",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Picoin-Forge-Worker-Id": registration.worker_id,
            "X-Picoin-Forge-Timestamp": timestamp,
            "X-Picoin-Forge-Signature": signature,
        },
    )

    assert replay.status_code == 401
    assert replay.json()["detail"] == "replayed worker signature"


def test_rotated_worker_key_can_update_registration_with_required_signature(tmp_path, monkeypatch):
    monkeypatch.delenv("PICOIN_FORGE_COORDINATOR_TOKEN", raising=False)
    monkeypatch.setenv("PICOIN_FORGE_REQUIRE_WORKER_SIGNATURES", "1")
    monkeypatch.setattr(coordinator_main, "DEFAULT_COORDINATOR_STATE_DIR", str(tmp_path / "coordinator"))
    client = TestClient(coordinator_main.api)
    worker_dir = tmp_path / "worker"
    registration = register_worker("PIWORKERROTATEKEY", worker_dir)

    rotated = rotate_worker_key(worker_dir)

    assert rotated.worker_id == registration.worker_id
    assert rotated.public_key != registration.public_key

    payload = rotated.model_dump(mode="json")
    body = json.dumps(payload, default=str).encode("utf-8")
    timestamp = str(int(time.time()))
    private_key = load_private_key(worker_dir)
    assert private_key is not None
    signature = sign_message(private_key, request_signing_payload("POST", "/workers/register", timestamp, body))
    response = client.post(
        "/workers/register",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Picoin-Forge-Worker-Id": rotated.worker_id,
            "X-Picoin-Forge-Timestamp": timestamp,
            "X-Picoin-Forge-Signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.json()["registration"]["worker_id"] == registration.worker_id
    assert response.json()["registration"]["public_key"] == rotated.public_key
