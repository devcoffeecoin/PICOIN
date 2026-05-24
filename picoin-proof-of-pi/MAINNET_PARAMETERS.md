# Picoin Mainnet Parameters

This document freezes the first mainnet parameter draft. It is a launch target, not a live mainnet declaration. Any value marked `implementation-required` must be implemented and tested before genesis.

## Network Identity

| Parameter | Mainnet Value | Status |
| --- | --- | --- |
| `network_id` | `mainnet` | draft |
| `chain_id` | `picoin-mainnet-v1` | draft |
| `protocol_version` | `1.0` | implementation-required |
| public bootstrap API | `https://api.picoin.science` or a mainnet-specific DNS name before launch | draft |
| explorer | mainnet explorer URL to be assigned | pending |
| faucet | disabled | draft |

Mainnet must not reuse the public testnet chain ID:

```text
public testnet: picoin-public-testnet-v018
mainnet:        picoin-mainnet-v1
```

## Genesis

Mainnet genesis target:

```text
genesis_supply = 300.00000000 PI
```

Purpose:

- Provide enough genesis liquidity to run the initial validator set.
- Avoid a large undocumented premine.
- Keep all allocations auditable in the genesis allocation file.

Current implementation note:

- The code currently defaults `GENESIS_SUPPLY = 3.1416`.
- Before mainnet, implementation must support `300.00000000` genesis supply through a mainnet constant or `PICOIN_GENESIS_SUPPLY`.
- Mainnet must compute and publish the deterministic genesis allocation hash before launch.

Draft allocation plan:

| Account | Account Type | Amount |
| --- | --- | ---: |
| `validator_1ac7f254cdd9416e` | `validator` | `31.41600000` |
| `validator_c1a74dd7646642e0` | `validator` | `31.41600000` |
| `validator_d5fb490f23974e10` | `validator` | `31.41600000` |
| `mainnet_validator_bootstrap_reserve` | `validator_bootstrap_reserve` | `205.75200000` |
| Total |  | `300.00000000` |

The three validator allocations equal the current minimum validator stake:

```text
31.416 * 3 = 94.248 PI
300 - 94.248 = 205.752 PI
```

The `mainnet_validator_bootstrap_reserve` is not a private spending wallet. It is reserved for validator bootstrap, onboarding additional validators, and launch operations. Any use must be documented before mainnet.

## Proof Of Pi Protocol

| Parameter | Mainnet Value | Status |
| --- | --- | --- |
| algorithm | `bbp_hex_v1` | draft |
| validation mode | `external_commit_reveal` | draft |
| range assignment mode | `pseudo_random` | draft |
| segment size | `64` | draft |
| sample count | `32` | draft |
| task expiration | `600 seconds` | draft |
| max active tasks per miner | `1` | draft |
| max initial pi position | `10000` plus scalable windows | draft |
| range window size | `10000` | draft |

## Rewards

Base reward:

```text
base_reward = 3.1416 PI per accepted block
```

Reward split:

| Recipient | Percent | Amount per block |
| --- | ---: | ---: |
| Proof of Pi miner | `80%` | `2.513280 PI` |
| Science Compute Reserve | `7%` | `0.219912 PI` |
| Validator reward pool | `10%` | `0.314160 PI` |
| Scientific Development Treasury | `3%` | `0.094248 PI` |
| Total | `100%` | `3.141600 PI` |

Retroactive audit reward:

```text
retroactive_audit_reward = 20% of base_reward = 0.628320 PI per scheduled audit
retroactive_audit_interval_blocks = 314
retroactive_audit_sample_multiplier = 2
```

Mainnet launch note:

- Keep the current reward schedule for `v1` unless economic simulation says otherwise.
- Any future reward decay or halving schedule must be specified in a later protocol version.

## Validator Quorum

Public testnet currently runs:

```text
required_validator_approvals = 3
eligible_validators_observed = 3
effective quorum = 3/3
```

Mainnet launch target:

```text
required_validator_approvals = 3
minimum_initial_validators = 4
target_initial_validators = 7
```

Rationale:

- `3/3` proves correctness but does not tolerate validator downtime.
- `3/4` tolerates one validator outage.
- `5/7` is preferred once enough independent validators are ready.

Mainnet go/no-go rule:

- Do not launch mainnet with only three eligible validators unless the launch explicitly accepts `3/3` liveness risk.
- Preferred mainnet launch condition is at least four eligible validators with `3` required approvals.

## Faucet

Mainnet faucet:

```text
faucet_enabled = false
PICOIN_FAUCET_ALLOWED_NETWORKS must not include mainnet
```

Mainnet must not expose free minting or faucet credit endpoints for real value.

## Science Compute Reserve

Launch status:

```text
science_reserve_status = RESERVE_LOCKED
payouts_enabled = false
```

Parameters:

| Parameter | Value |
| --- | --- |
| reserve account | `science_compute_reserve` |
| reserve percent | `7%` of each accepted block |
| governance timelock | `24 hours` |
| governance threshold | `2` authorized signers |
| activation | disabled at genesis |

Mainnet rule:

- Science Reserve payouts remain locked at launch.
- Activation requires a separate governance process after network stability.

## Scientific Development Treasury

Launch status:

```text
treasury_account_id = scientific_development_treasury
treasury_percent = 3%
unlock_interval = 90 days
```

Mainnet rule:

- Treasury accrues from block rewards.
- Claims remain locked by the 90-day interval.
- Governance wallet and treasury wallet must be finalized before genesis.

Current placeholders:

```text
PICOIN_TREASURY_WALLET=picoin_scientific_development_wallet
PICOIN_GOVERNANCE_WALLET=picoin_governance_multisig
```

These placeholders must be replaced with final mainnet wallet/account IDs before launch.

## Slashing And Penalties

Current penalty constants:

| Event | Penalty |
| --- | --- |
| invalid miner result | `1` penalty point |
| duplicate miner submission | `3` penalty points |
| invalid miner signature | `5` penalty points |
| miner cooldown after rejection points | `3` |
| miner cooldown duration | `300 seconds` |
| fraud miner penalty | `20` points |
| fraud cooldown | `3600 seconds` |
| invalid validator signature slash | `3.1416 PI` |
| validator cooldown after invalid results | `3` invalid results |
| validator ban after invalid results | `9` invalid results |
| validator cooldown duration | `600 seconds` |

Mainnet work still required:

- Define validator no-show penalties.
- Define whether downtime causes slash, cooldown, or only reduced selection score.
- Define emergency governance process for a malicious validator set.
- Ensure slashing is tied to real stake, not only simulated metadata.

Recommended mainnet v1 no-show policy:

- No immediate slash for one missed job.
- Reduce availability score for repeated missed validation windows.
- Apply cooldown after repeated no-shows.
- Slash only for provable malicious behavior: invalid signature, fraudulent approval, double-signing, or equivocation.

## Difficulty And Retargeting

Launch target:

| Parameter | Value |
| --- | --- |
| target block time | `60 seconds` |
| retarget epoch blocks | `5` |
| tolerance | `20%` |
| max adjustment factor | `1.25` |
| min segment size | `16` |
| max segment size | `256` |
| min sample count | `8` |
| max sample count | `64` |
| retarget max pi position | `100000` |

Mainnet rule:

- Keep retargeting conservative until public hashrate and validator latency are better understood.

## Transaction And Mempool

Launch target:

| Parameter | Value |
| --- | --- |
| max transactions per block | `100` |
| mempool TTL | `3600 seconds` |
| max mempool fee | `31.416 PI` |
| duplicate nonce rejection | enabled |
| signed transactions | required |

Mainnet work:

- Review transaction fees and fee burning/distribution.
- Ensure faucet transaction type is rejected on mainnet.
- Add high-load mempool tests.

## Launch Freeze Checklist

Before mainnet genesis:

- [ ] Implement or configure `GENESIS_SUPPLY=300.00000000`.
- [ ] Replace treasury/governance wallet placeholders.
- [ ] Create final mainnet genesis allocation JSON.
- [ ] Compute and publish deterministic genesis hash.
- [ ] Set `PICOIN_NETWORK=mainnet`.
- [ ] Set `PICOIN_CHAIN_ID=picoin-mainnet-v1`.
- [ ] Ensure faucet is disabled.
- [ ] Confirm initial validators and identities.
- [ ] Confirm minimum four eligible validators or explicitly accept `3/3` risk.
- [ ] Run final public testnet audit.
- [ ] Tag mainnet release.
