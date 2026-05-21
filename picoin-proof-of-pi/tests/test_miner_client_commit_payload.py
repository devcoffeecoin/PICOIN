import json

import miner.client as client


class DummyResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {"accepted": True}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._data


def test_commit_payload_includes_tx_fields(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent['url'] = url
        sent['json'] = json
        return DummyResponse()

    monkeypatch.setattr(client, 'sign_payload', lambda pk, payload: 'SIG')
    monkeypatch.setattr(client.requests, 'post', fake_post)

    task = {
        'task_id': 'task_1',
        'range_start': 1,
        'range_end': 2,
        'algorithm': 'bbp_hex_v1',
        'tx_merkle_root': 'abc',
        'mempool_snapshot_id': 'snapshot-123',
        'selected_tx_hashes_hash': 'hash-xyz',
        'tx_count': 5,
        'tx_fee_total_units': 100,
    }
    identity = {'miner_id': 'miner_1', 'private_key': 'pk'}

    client.commit_result('http://server', task, identity, result_hash='r', root='root', compute_ms=123)

    assert sent['url'].endswith('/tasks/commit')
    payload = sent['json']
    assert payload['mempool_snapshot_id'] == 'snapshot-123'
    assert payload['selected_tx_hashes_hash'] == 'hash-xyz'
    assert payload['tx_merkle_root'] == 'abc'
    assert payload['tx_count'] == 5
    assert payload['tx_fee_total_units'] == 100


def test_commit_payload_includes_tx_fields_empty_mempool(monkeypatch):
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent['json'] = json
        return DummyResponse()

    monkeypatch.setattr(client, 'sign_payload', lambda pk, payload: 'SIG')
    monkeypatch.setattr(client.requests, 'post', fake_post)

    task = {
        'task_id': 'task_2',
        'range_start': 1,
        'range_end': 2,
        'algorithm': 'bbp_hex_v1',
        # empty mempool: explicit empty values
        'tx_merkle_root': '',
        'mempool_snapshot_id': '',
        'selected_tx_hashes_hash': '',
        'tx_count': 0,
        'tx_fee_total_units': 0,
    }
    identity = {'miner_id': 'miner_2', 'private_key': 'pk'}

    client.commit_result('http://server', task, identity, result_hash='r', root='root', compute_ms=10)

    payload = sent['json']
    # ensure empty strings are forwarded, not null
    assert payload['mempool_snapshot_id'] == ''
    assert payload['selected_tx_hashes_hash'] == ''
    assert payload['tx_merkle_root'] == ''
    assert payload['tx_count'] == 0
    assert payload['tx_fee_total_units'] == 0
