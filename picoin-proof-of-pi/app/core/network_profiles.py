from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkProfile:
    name: str
    network_id: str
    chain_id: str
    protocol_version: str
    genesis_supply: float
    faucet_allowed_networks: frozenset[str]
    required_validator_approvals: int = 3
    base_reward: float = 3.1416
    validator_reward_percent: float = 0.10
    proof_of_pi_reward_percent: float = 0.67
    science_compute_reward_percent: float = 0.20
    scientific_development_reward_percent: float = 0.03
    min_validator_stake: float = 31.416
    validator_slash_invalid_signature: float = 3.1416


LOCAL_PROFILE = NetworkProfile(
    name="local",
    network_id="local",
    chain_id="picoin-local-testnet",
    protocol_version="0.18",
    genesis_supply=3.1416,
    faucet_allowed_networks=frozenset({"local"}),
)

PUBLIC_TESTNET_PROFILE = NetworkProfile(
    name="public-testnet",
    network_id="public-testnet",
    chain_id="picoin-public-testnet-v018",
    protocol_version="0.18",
    genesis_supply=3.1416,
    faucet_allowed_networks=frozenset({"local", "public-testnet"}),
    required_validator_approvals=2,
)

MAINNET_PROFILE = NetworkProfile(
    name="mainnet",
    network_id="mainnet",
    chain_id="picoin-mainnet-v1",
    protocol_version="1.0",
    genesis_supply=300.0,
    faucet_allowed_networks=frozenset(),
)

NETWORK_PROFILES = {
    LOCAL_PROFILE.name: LOCAL_PROFILE,
    PUBLIC_TESTNET_PROFILE.name: PUBLIC_TESTNET_PROFILE,
    MAINNET_PROFILE.name: MAINNET_PROFILE,
}


def profile_for_network(network_id: str) -> NetworkProfile:
    cleaned = (network_id or "local").strip().lower() or "local"
    if cleaned in NETWORK_PROFILES:
        return NETWORK_PROFILES[cleaned]
    return NetworkProfile(
        name=cleaned,
        network_id=cleaned,
        chain_id=f"picoin-{cleaned}-testnet",
        protocol_version=LOCAL_PROFILE.protocol_version,
        genesis_supply=LOCAL_PROFILE.genesis_supply,
        faucet_allowed_networks=frozenset(),
    )


def parse_network_set(value: str, default: frozenset[str]) -> frozenset[str]:
    if value.strip() == "":
        return default
    return frozenset(network.strip().lower() for network in value.split(",") if network.strip())
