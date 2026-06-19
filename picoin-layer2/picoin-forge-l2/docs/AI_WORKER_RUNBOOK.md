# Picoin Forge L2 AI Worker Runbook

This runbook describes a local L2 pilot for a real AI model worker.

Important boundary:

- This does not touch Picoin L1.
- This does not create real PI payments.
- This does not pay a worker per prompt.
- AI requests are access receipts and audit evidence.
- Worker rewards still come from epoch share: verified capacity, uptime, reliability, and audits.

## 1. Coordinator

From the L2 directory:

```bash
cd picoin-layer2/picoin-forge-l2
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
PICOIN_FORGE_AI_ACCESS_MIN_STAKE_PI=1 picoin-forge-coordinator start --host 0.0.0.0 --port 9380
```

Dashboard:

```text
http://127.0.0.1:9380/
```

AI summary:

```bash
curl -sS http://127.0.0.1:9380/ai/summary | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/capabilities | python3 -m json.tool
```

## Preflight AI Model Smoke

Before registering a real AI worker, verify the local runtime:

```bash
picoin-forge-worker ai-smoke
```

Expected result:

```json
{
  "ready": true,
  "availability": {
    "verified": true
  },
  "inference": {
    "accepted": true
  },
  "no_l1_transaction_created": true,
  "no_per_task_payment": true
}
```

If `ready=false`, fix the model endpoint, model name, provider, or local runtime
before connecting the worker to a coordinator.

## Docker AI Worker Devnet

The base `docker-compose.yml` keeps generic CPU workers. To add one real AI
worker backed by an Ollama/OpenAI-compatible runtime, use the AI compose
overlay:

```bash
cp .env.ai.example .env.ai
docker compose --env-file .env.ai -f docker-compose.yml -f docker-compose.ai.yml --profile ai up --build coordinator ai-worker-ollama
```

Default behavior:

- Coordinator runs at `http://127.0.0.1:9380`.
- The AI worker points to `http://host.docker.internal:11434`.
- The worker runs `picoin-forge-worker ai-smoke` before joining.
- The worker loop requests `ai_model` challenges instead of generic CPU
  challenges.
- L1 is not touched.
- The worker still earns by verified network contribution, not per user prompt.

For a quick deterministic compose smoke without a real Ollama process, set:

```bash
PICOIN_FORGE_TEST_AI_MODEL_BACKEND=1
PICOIN_FORGE_AI_MODEL_PROVIDER=test-ai-model
PICOIN_FORGE_AI_MODEL_ENDPOINT=local://picoin-forge-test-ai-model
```

## Submit a User AI Request Over HTTP

Once the coordinator and AI worker are running, use the HTTP client:

```bash
picoin-forge-client ai capabilities --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai create PI_REQUESTER "Explain Picoin Forge L2 in one paragraph." 25 \
  --capabilities chat,reasoning \
  --preferred-provider ollama \
  --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai status AI_REQUEST_ID --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai result AI_REQUEST_ID --coordinator-url http://127.0.0.1:9380
picoin-forge-client ai receipt AI_REQUEST_ID --coordinator-url http://127.0.0.1:9380
```

For a one-command user smoke:

```bash
picoin-forge-client ai run PI_REQUESTER "Explain Picoin Forge L2 in one paragraph." 25 \
  --capabilities chat,reasoning \
  --preferred-provider ollama \
  --wait-timeout-seconds 180 \
  --coordinator-url http://127.0.0.1:9380
```

This path uses the coordinator's public HTTP API. It is the path exchanges,
apps, and future user-facing portals should exercise during L2 devnet tests.

Browser portal:

```text
http://127.0.0.1:9380/ai/portal
```

The portal creates stake-gated AI requests, polls status, and displays result
and receipt from the same public HTTP API used by `picoin-forge-client`.

Optional AI request failover settings:

```bash
PICOIN_FORGE_AI_REQUEST_LEASE_SECONDS=120
PICOIN_FORGE_AI_REQUEST_MAX_ASSIGNMENTS=3
```

These settings control how long a claimed AI request can stay assigned before it becomes eligible for another verified worker. They do not create per-prompt payments.

## 2. Ollama Worker

Install and run Ollama using the official installer for your platform, then pull a model:

```bash
ollama pull llama3.1:8b
ollama serve
```

In another shell:

```bash
cd picoin-layer2/picoin-forge-l2
source .venv/bin/activate

export PICOIN_FORGE_AI_MODEL_PROVIDER=ollama
export PICOIN_FORGE_AI_MODEL_NAME=llama3.1:8b
export PICOIN_FORGE_AI_MODEL_PARAMETERS_B=8
export PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS=8192
export PICOIN_FORGE_AI_MODEL_QUANTIZATION=q4
export PICOIN_FORGE_AI_MODEL_CAPABILITIES=llm,chat,reasoning
export PICOIN_FORGE_AI_MODEL_ENDPOINT=http://127.0.0.1:11434
export PICOIN_FORGE_AI_MODEL_TIMEOUT_SECONDS=20

picoin-forge-worker register --wallet PI_YOUR_WORKER_WALLET --coordinator-url http://127.0.0.1:9380
picoin-forge-worker loop-once --coordinator-url http://127.0.0.1:9380
```

After registration, create and solve an AI model challenge:

```bash
WORKER_ID=$(picoin-forge-worker status | python3 -c 'import json,sys; print(json.load(sys.stdin)["worker_id"])')

curl -sS -X POST http://127.0.0.1:9380/challenges \
  -H 'content-type: application/json' \
  -d "{\"worker_id\":\"$WORKER_ID\",\"challenge_type\":\"ai_model\",\"difficulty\":1}" \
  | python3 -m json.tool

picoin-forge-worker loop-once --coordinator-url http://127.0.0.1:9380
```

The worker must pass an `ai_model` challenge before it can serve AI access requests because the queue requires `ai_model_score > 0`.

## 3. Submit A Stake-Gated AI Request

This is a local stake snapshot, not a real L1 staking proof:

```bash
curl -sS -X POST http://127.0.0.1:9380/ai/requests \
  -H 'content-type: application/json' \
  -d '{
    "requester_wallet": "PI_REQUESTER_WALLET",
    "stake_snapshot_pi": 5,
    "prompt": "Explain Picoin Forge in one short paragraph.",
    "required_capabilities": ["chat"],
    "max_tokens": 128,
    "store_output": true
  }' | python3 -m json.tool
```

Set `"store_output": false` when the coordinator should keep only output hashes and receipts, not the model output text.

If several compatible requests are queued, the coordinator prioritizes higher `stake_snapshot_pi` first, then older requests. This is an access policy, not a per-request worker fee.

Equivalent local CLI flow:

```bash
picoin-forge-coordinator ai capabilities
picoin-forge-coordinator ai create-request PI_REQUESTER_WALLET "Explain Picoin Forge in one short paragraph." 5 --capabilities chat
picoin-forge-coordinator ai status AI_REQUEST_ID
picoin-forge-coordinator ai result AI_REQUEST_ID
picoin-forge-coordinator ai receipt AI_REQUEST_ID
picoin-forge-coordinator ai export-request AI_REQUEST_ID
```

Optional cancellation before verification:

```bash
curl -sS -X POST http://127.0.0.1:9380/ai/requests/AI_REQUEST_ID/cancel | python3 -m json.tool
```

Run the worker once:

```bash
picoin-forge-worker loop-once --coordinator-url http://127.0.0.1:9380
```

The worker claims one compatible AI request, sends the prompt to its registered model endpoint, submits the response, and produces a receipt. If the endpoint fails or returns an empty response, the worker loop reports the reason locally and does not submit a fake success; the coordinator lease can then expire and reassign the request.

Verify receipts:

```bash
curl -sS http://127.0.0.1:9380/ai/requests | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/summary | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/requests/AI_REQUEST_ID/status | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/requests/AI_REQUEST_ID/result | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/requests/AI_REQUEST_ID/receipt | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/requests/AI_REQUEST_ID/export | python3 -m json.tool
```

Expected receipt flags:

```text
no_l1_transaction_created = true
no_per_task_payment = true
```

## 4. OpenAI-Compatible Worker

For vLLM, llama.cpp server, or any OpenAI-compatible local endpoint:

```bash
export PICOIN_FORGE_AI_MODEL_PROVIDER=openai-compatible
export PICOIN_FORGE_AI_MODEL_NAME=local-open-model
export PICOIN_FORGE_AI_MODEL_PARAMETERS_B=70
export PICOIN_FORGE_AI_MODEL_CONTEXT_TOKENS=32768
export PICOIN_FORGE_AI_MODEL_CAPABILITIES=llm,chat,reasoning,tool-use
export PICOIN_FORGE_AI_MODEL_ENDPOINT=http://127.0.0.1:8000/v1
```

The worker will call:

```text
/v1/chat/completions
```

## 5. Operator Checks

Use these checks during pilots:

```bash
curl -sS http://127.0.0.1:9380/health
curl -sS http://127.0.0.1:9380/workers | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/capabilities | python3 -m json.tool
curl -sS http://127.0.0.1:9380/ai/summary | python3 -m json.tool
curl -sS -X POST http://127.0.0.1:9380/ai/requests/expire | python3 -m json.tool
curl -sS http://127.0.0.1:9380/events?limit=20 | python3 -m json.tool
```

The dashboard should show:

- AI workers.
- Model name and provider.
- Capabilities.
- AI score.
- Ready status.
- Latest AI requests.
- Receipt hashes.
- Assignment attempts and lease expiration times.

## 6. Pilot Acceptance Criteria

A first real AI pilot is acceptable when:

- At least one worker passes an `ai_model` challenge.
- `/ai/summary` shows `ai_workers_ready >= 1`.
- A stake-gated AI request is verified.
- `/ai/requests/{request_id}/status` shows `result_ready = true` and `receipt_ready = true`.
- `/ai/requests/{request_id}/result` returns the verified output.
- The request has a `receipt_hash`.
- `/ai/requests/{request_id}/receipt` returns `valid = true`.
- `/ai/requests/{request_id}/export` returns an `export_hash`.
- The receipt keeps `no_per_task_payment = true`.
- Closing an epoch rewards the worker by verified score, not by individual request count.
