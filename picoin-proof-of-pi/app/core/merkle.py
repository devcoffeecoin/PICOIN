from app.core.crypto import canonical_json, sha256_text


def leaf_hash(position: int, digit: str) -> str:
    return sha256_text(canonical_json({"digit": digit.upper(), "position": position}))


def parent_hash(left: str, right: str) -> str:
    return sha256_text(canonical_json({"left": left, "right": right}))


def build_merkle_layers(segment: str, range_start: int) -> list[list[str]]:
    if not segment:
        raise ValueError("segment cannot be empty")

    leaves = [
        leaf_hash(range_start + index, digit)
        for index, digit in enumerate(segment.upper())
    ]
    layers = [leaves]

    while len(layers[-1]) > 1:
        current = layers[-1]
        next_layer: list[str] = []
        for index in range(0, len(current), 2):
            left = current[index]
            right = current[index + 1] if index + 1 < len(current) else left
            next_layer.append(parent_hash(left, right))
        layers.append(next_layer)

    return layers


def merkle_root(segment: str, range_start: int) -> str:
    return build_merkle_layers(segment, range_start)[-1][0]


def merkle_proof(segment: str, range_start: int, position: int) -> list[dict[str, str]]:
    if position < range_start or position >= range_start + len(segment):
        raise ValueError("position is outside segment range")

    layers = build_merkle_layers(segment, range_start)
    index = position - range_start
    proof: list[dict[str, str]] = []

    for layer in layers[:-1]:
        sibling_index = index - 1 if index % 2 else index + 1
        if sibling_index >= len(layer):
            sibling_index = index
        proof.append(
            {
                "side": "left" if sibling_index < index else "right",
                "hash": layer[sibling_index],
            }
        )
        index //= 2

    return proof


def verify_merkle_proof(position: int, digit: str, proof: list[dict[str, str]], expected_root: str) -> bool:
    current = leaf_hash(position, digit)
    for item in proof:
        side = item.get("side")
        sibling_hash = item.get("hash")
        if side == "left":
            current = parent_hash(sibling_hash, current)
        elif side == "right":
            current = parent_hash(current, sibling_hash)
        else:
            return False
    return current == expected_root
