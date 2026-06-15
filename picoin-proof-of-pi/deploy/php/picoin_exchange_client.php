<?php

declare(strict_types=1);

function picoin_b64url_decode(string $value): string
{
    $padded = strtr($value, '-_', '+/');
    $padded .= str_repeat('=', (4 - strlen($padded) % 4) % 4);
    $decoded = base64_decode($padded, true);
    if ($decoded === false) {
        throw new RuntimeException('Invalid base64url value');
    }
    return $decoded;
}

function picoin_b64url_encode(string $raw): string
{
    return rtrim(strtr(base64_encode($raw), '+/', '-_'), '=');
}

function picoin_decode_ed25519_key(string $key): string
{
    if (substr($key, 0, 8) !== 'ed25519:') {
        throw new RuntimeException('Key must use ed25519:<base64url> format');
    }
    $raw = picoin_b64url_decode(substr($key, 8));
    if (strlen($raw) !== 32) {
        throw new RuntimeException('Ed25519 key must be 32 raw bytes');
    }
    return $raw;
}

function picoin_public_key_from_private(string $privateKey): string
{
    $seed = picoin_decode_ed25519_key($privateKey);
    $keypair = sodium_crypto_sign_seed_keypair($seed);
    return 'ed25519:' . picoin_b64url_encode(sodium_crypto_sign_publickey($keypair));
}

function picoin_address_checksum(string $addressBody): string
{
    return strtoupper(substr(hash('sha256', $addressBody), 0, 8));
}

function picoin_address_from_public_key(string $publicKey): string
{
    $body = strtoupper(substr(hash('sha256', $publicKey), 0, 38));
    return 'PI' . $body . picoin_address_checksum($body);
}

function picoin_to_units($amount): int
{
    $text = trim((string)$amount);
    if (!preg_match('/^\d+(?:\.\d{1,6})?$/', $text)) {
        throw new RuntimeException('Amount must have at most 6 decimal places');
    }
    [$whole, $fraction] = array_pad(explode('.', $text, 2), 2, '');
    $fraction = str_pad($fraction, 6, '0');
    return ((int)$whole * 1000000) + (int)$fraction;
}

function picoin_units_to_amount(int $units): string
{
    if ($units < 0) {
        throw new RuntimeException('Amount units must be non-negative');
    }
    return sprintf('%d.%06d', intdiv($units, 1000000), $units % 1000000);
}

function picoin_array_is_list(array $value): bool
{
    if ($value === []) {
        return true;
    }
    return array_keys($value) === range(0, count($value) - 1);
}

function picoin_sort_for_canonical_json($value)
{
    if ($value instanceof stdClass) {
        $vars = get_object_vars($value);
        ksort($vars, SORT_STRING);
        $object = new stdClass();
        foreach ($vars as $key => $child) {
            $object->{$key} = picoin_sort_for_canonical_json($child);
        }
        return $object;
    }

    if (!is_array($value)) {
        return $value;
    }

    if (picoin_array_is_list($value)) {
        return array_map('picoin_sort_for_canonical_json', $value);
    }

    ksort($value, SORT_STRING);
    foreach ($value as $key => $child) {
        $value[$key] = picoin_sort_for_canonical_json($child);
    }
    return $value;
}

function picoin_canonical_json($payload): string
{
    $json = json_encode(
        picoin_sort_for_canonical_json($payload),
        JSON_UNESCAPED_SLASHES
    );
    if ($json === false) {
        throw new RuntimeException('JSON encode failed: ' . json_last_error_msg());
    }
    return $json;
}

function picoin_empty_payload(): stdClass
{
    return new stdClass();
}

function picoin_build_unsigned_transfer(
    string $senderAddress,
    string $recipientAddress,
    $amount,
    int $nonce,
    $fee = '0.001000',
    ?string $timestamp = null,
    $payload = null,
    string $networkId = 'picoin-mainnet-v1',
    int $chainId = 314159
): array {
    $amountUnits = picoin_to_units($amount);
    $feeUnits = picoin_to_units($fee);
    if ($payload === null || $payload === []) {
        $payload = picoin_empty_payload();
    }

    return [
        'amount' => picoin_units_to_amount($amountUnits),
        'amount_units' => $amountUnits,
        'chain_id' => $chainId,
        'fee' => picoin_units_to_amount($feeUnits),
        'fee_units' => $feeUnits,
        'network_id' => $networkId,
        'nonce' => $nonce,
        'payload' => $payload,
        'recipient' => $recipientAddress,
        'sender' => $senderAddress,
        'timestamp' => $timestamp ?: gmdate('Y-m-d\TH:i:s') . '+00:00',
        'tx_type' => 'transfer',
    ];
}

function picoin_sign_transfer(
    string $senderAddress,
    string $senderPrivateKey,
    string $recipientAddress,
    $amount,
    int $nonce,
    $fee = '0.001000',
    ?string $timestamp = null,
    $payload = null,
    string $networkId = 'picoin-mainnet-v1',
    int $chainId = 314159
): array {
    $publicKey = picoin_public_key_from_private($senderPrivateKey);
    $derivedAddress = picoin_address_from_public_key($publicKey);
    if (strtoupper($senderAddress) !== $derivedAddress) {
        throw new RuntimeException("Sender address does not match private key; expected {$derivedAddress}");
    }

    $unsigned = picoin_build_unsigned_transfer(
        $senderAddress,
        $recipientAddress,
        $amount,
        $nonce,
        $fee,
        $timestamp,
        $payload,
        $networkId,
        $chainId
    );

    $seed = picoin_decode_ed25519_key($senderPrivateKey);
    $keypair = sodium_crypto_sign_seed_keypair($seed);
    $secretKey = sodium_crypto_sign_secretkey($keypair);
    $signature = sodium_crypto_sign_detached(picoin_canonical_json($unsigned), $secretKey);
    $txHash = hash('sha256', picoin_canonical_json(['public_key' => $publicKey, 'tx' => $unsigned]));

    return array_merge($unsigned, [
        'public_key' => $publicKey,
        'signature' => picoin_b64url_encode($signature),
        'tx_hash' => $txHash,
    ]);
}

function picoin_http_json(string $method, string $url, ?array $body = null): array
{
    $headers = ['Accept: application/json'];
    $curl = curl_init($url);
    $options = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_HTTPHEADER => &$headers,
        CURLOPT_CONNECTTIMEOUT => 15,
        CURLOPT_TIMEOUT => 90,
    ];

    if ($body !== null) {
        $headers[] = 'Content-Type: application/json';
        $jsonBody = json_encode($body, JSON_UNESCAPED_SLASHES);
        if ($jsonBody === false) {
            throw new RuntimeException('JSON encode failed: ' . json_last_error_msg());
        }
        $options[CURLOPT_POSTFIELDS] = $jsonBody;
    }

    curl_setopt_array($curl, $options);
    $response = curl_exec($curl);
    if ($response === false) {
        $error = curl_error($curl);
        curl_close($curl);
        throw new RuntimeException($error);
    }

    $status = curl_getinfo($curl, CURLINFO_HTTP_CODE);
    curl_close($curl);
    $decoded = json_decode((string)$response, true);

    if ($status < 200 || $status >= 300) {
        $detail = is_array($decoded) ? ($decoded['detail'] ?? $response) : $response;
        throw new RuntimeException("HTTP {$status}: {$detail}");
    }

    return is_array($decoded) ? $decoded : [];
}

function picoin_next_nonce(string $nodeUrl, string $senderAddress): int
{
    $nodeUrl = rtrim($nodeUrl, '/');
    $response = picoin_http_json('GET', $nodeUrl . '/wallet/' . rawurlencode($senderAddress) . '/nonce');
    if (!isset($response['next_nonce'])) {
        throw new RuntimeException('Nonce endpoint did not return next_nonce');
    }
    return (int)$response['next_nonce'];
}

function get_picoin_balance(
    string $address,
    string $nodeUrl = 'http://127.0.0.1:8000'
): array {
    try {
        $nodeUrl = rtrim($nodeUrl, '/');
        $response = picoin_http_json('GET', $nodeUrl . '/wallet/balance/' . rawurlencode($address));
        $balanceUnits = isset($response['balance_units'])
            ? (int)$response['balance_units']
            : picoin_to_units((string)($response['balance'] ?? '0'));

        return [
            'success' => true,
            'address' => $response['address'] ?? $address,
            'balance' => picoin_units_to_amount($balanceUnits),
            'balance_units' => $balanceUnits,
            'available_balance' => sprintf('%.6f', (float)($response['available_balance'] ?? $response['balance'] ?? 0)),
            'total_balance' => sprintf('%.6f', (float)($response['total_balance'] ?? $response['balance'] ?? 0)),
            'updated_at' => $response['updated_at'] ?? null,
            'error' => null,
        ];
    } catch (Throwable $exception) {
        return [
            'success' => false,
            'address' => $address,
            'balance' => null,
            'balance_units' => null,
            'available_balance' => null,
            'total_balance' => null,
            'updated_at' => null,
            'error' => $exception->getMessage(),
        ];
    }
}

function get_picoin_transactions(
    string $address,
    string $nodeUrl = 'http://127.0.0.1:8000',
    int $limit = 50,
    bool $backfill = false,
    bool $confirmedOnly = true
): array {
    try {
        $nodeUrl = rtrim($nodeUrl, '/');
        $query = http_build_query([
            'address' => $address,
            'limit' => max(1, min($limit, 500)),
            'backfill' => $backfill ? 'true' : 'false',
            'confirmed_only' => $confirmedOnly ? 'true' : 'false',
        ]);
        $transactions = picoin_http_json('GET', $nodeUrl . '/transactions/history?' . $query);

        return [
            'success' => true,
            'transactions' => $transactions,
            'error' => null,
        ];
    } catch (Throwable $exception) {
        return [
            'success' => false,
            'transactions' => [],
            'error' => $exception->getMessage(),
        ];
    }
}

function send_picoin(
    string $senderAddress,
    string $senderPrivateKey,
    string $recipientAddress,
    $amount,
    string $nodeUrl = 'http://127.0.0.1:8000',
    $fee = '0.001000'
): array {
    try {
        $nodeUrl = rtrim($nodeUrl, '/');
        $nonce = picoin_next_nonce($nodeUrl, $senderAddress);
        $tx = picoin_sign_transfer(
            $senderAddress,
            $senderPrivateKey,
            $recipientAddress,
            $amount,
            $nonce,
            $fee
        );
        $response = picoin_http_json('POST', $nodeUrl . '/tx/submit', $tx);

        return [
            'success' => true,
            'tx_hash' => $response['tx_hash'] ?? $tx['tx_hash'],
            'error' => null,
        ];
    } catch (Throwable $exception) {
        return [
            'success' => false,
            'tx_hash' => null,
            'error' => $exception->getMessage(),
        ];
    }
}

function picoin_exchange_client_self_test(): void
{
    $privateKey = 'ed25519:2FDzM6exHBnQQa9IQ-bBfBVr3IJqqb9ec7X7yuHaYqc';
    $sender = 'PI2C9F1631B1EF38DE481B1CC6361657AFCBC205E5B88CA9';
    $recipient = 'PIEB4C49F30119C7B90A0DE0E338B8D3D8BFB6482A670E7C';
    $tx = picoin_sign_transfer(
        $sender,
        $privateKey,
        $recipient,
        '1.234567',
        7,
        '0.001000',
        '2026-06-14T15:55:01+00:00'
    );
    $unsigned = $tx;
    unset($unsigned['public_key'], $unsigned['signature'], $unsigned['tx_hash']);

    $expectedCanonical = '{"amount":"1.234567","amount_units":1234567,"chain_id":314159,"fee":"0.001000","fee_units":1000,"network_id":"picoin-mainnet-v1","nonce":7,"payload":{},"recipient":"PIEB4C49F30119C7B90A0DE0E338B8D3D8BFB6482A670E7C","sender":"PI2C9F1631B1EF38DE481B1CC6361657AFCBC205E5B88CA9","timestamp":"2026-06-14T15:55:01+00:00","tx_type":"transfer"}';
    $expectedHash = 'af0276ebb7bf438dc3d03a66698c4f0a821397e07072dcd603947251bffd5937';
    $expectedSignature = 'drXzCc_ehuquSkTlvwud0LNoRK-8TmWD9iUPIh7Tn2g6AmW2ksDDMACCPbTzSTBrwrg05LRGwqEXyiNs5aXTCw';

    if (picoin_canonical_json($unsigned) !== $expectedCanonical) {
        throw new RuntimeException('Self-test failed: canonical JSON mismatch');
    }
    if ($tx['tx_hash'] !== $expectedHash) {
        throw new RuntimeException('Self-test failed: transaction hash mismatch');
    }
    if ($tx['signature'] !== $expectedSignature) {
        throw new RuntimeException('Self-test failed: signature mismatch');
    }

    $output = json_encode(
        ['success' => true, 'tx_hash' => $tx['tx_hash'], 'signature' => $tx['signature']],
        JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT
    );
    if ($output === false) {
        throw new RuntimeException('JSON encode failed: ' . json_last_error_msg());
    }
    echo $output . PHP_EOL;
}

if (PHP_SAPI === 'cli' && realpath($_SERVER['SCRIPT_FILENAME'] ?? '') === __FILE__) {
    if (($argv[1] ?? '') === '--self-test') {
        picoin_exchange_client_self_test();
    }
}
