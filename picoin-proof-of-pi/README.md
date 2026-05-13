# picoin-proof-of-pi

MVP funcional de **Proof of Pi** con una extension L1 llamada **Science Compute Access Layer**. Un coordinador asigna rangos pequenos de digitos hexadecimales de pi, un minero calcula el segmento con BBP, el validador recalcula de forma independiente y el servidor registra bloques aceptados con recompensa simulada. La capa Science deja preparada la red para un futuro marketplace L2 de computo cientifico e IA.

Este proyecto no ejecuta IA/computo cientifico pesado. Desde v0.18 incluye un Public Testnet Deployment Kit para correr nodos en droplets/servidores reales con env publico, systemd, health checks y backups. En L1 coordina stake, acceso, jobs, hashes, reserva y pagos verificados para preparar una evolucion futura.

## Protocolo v0.18

Parametros actuales:

```text
protocol_version = 0.18
network_id = local
algorithm = bbp_hex_v1
validation_mode = external_commit_reveal
required_validator_approvals = 3
range_assignment_mode = pseudo_random
max_pi_position = 10000
range_assignment_max_attempts = 512
segment_size = 64
sample_count = 32
task_expiration_seconds = 600
max_active_tasks_per_miner = 1
genesis_supply = 3.1416
base_reward = 3.1416
difficulty = 4.0
reward_per_block = 3.1416
validator_reward_percent = 10%
validator_reward_pool_per_block = 0.31416
proof_of_pi_reward_percent = 67%
proof_of_pi_reward_per_block = 2.104872
science_compute_reward_percent = 20%
science_compute_reserve_per_block = 0.62832
science_reserve_account_id = science_compute_reserve
science_reserve_status = RESERVE_LOCKED
science_reserve_governance_timelock = 86400 seconds
science_reserve_multisig_threshold = 2
scientific_development_reward_percent = 3%
scientific_development_treasury_per_block = 0.094248
scientific_development_unlock_interval_days = 90
science_base_monthly_quota_units = 100
validator_auditor_reward_percent = 10%
retroactive_audit_interval_blocks = 314
retroactive_audit_sample_multiplier = 2
retroactive_audit_reward_percent = 20%
retroactive_audit_reward_per_audit = 0.62832
fraud_miner_penalty_points = 20
fraud_validator_invalid_results = 3
fraud_cooldown_seconds = 3600
min_validator_stake = 31.416
validator_slash_invalid_signature = 3.1416
penalty_invalid_result = 1
penalty_duplicate = 3
penalty_invalid_signature = 5
cooldown_after_rejections = 3
cooldown_seconds = 300
task_rate_limit = 12 assignments / 60 seconds
faucet_enabled_networks = local
faucet_rate_limit = 3 credits / account / hour
validator_selection_mode = weighted_reputation_stake_rotation
max_transactions_per_block = 100
```

El endpoint `GET /protocol` devuelve estos valores para que mineros y validadores sepan que reglas estan activas. Desde v0.16 estos parametros viven en SQLite, en `protocol_params`, y pueden cambiar automaticamente por epocas. `network_id` viene de `PICOIN_NETWORK`; por defecto es `local`.

La dificultad se calcula con una formula simple y auditable:

```text
difficulty =
  (segment_size / 64)
  * (sample_count / 8)
  * (log10(max_pi_position) / log10(10000))

miner_reward_per_block = base_reward * 0.67
validator_reward_pool_per_block = base_reward * 0.10
science_compute_reserve_per_block = base_reward * 0.20
scientific_development_treasury_per_block = base_reward * 0.03
retroactive_audit_reward_per_audit = base_reward * 0.20
```

La dificultad regula el trabajo, no multiplica la emision. El `base_reward` es la emision base total del bloque y se distribuye como `67/20/10/3`: `2.104872` para el minero Proof of Pi, `0.62832` para `science_compute_reserve`, `0.31416` para validadores/auditores y `0.094248` para el Scientific Development Fund con timelock.

Picoin finances scientific infrastructure and protocol development through a time-locked treasury sustained by ongoing network activity rather than large upfront premine allocations. La cuenta `genesis` ya no representa una gran premine: queda limitada a una emision normal de `3.1416` para compatibilidad local de testnet/faucet. El stake de validador actual es metadata/collateral simulado hasta implementar staking real por transaccion.

Cada bloque aceptado acredita `2.104872` monedas al minero ganador, `0.62832` a la reserva cientifica, `0.094248` al Scientific Development Fund bloqueado y `0.31416` monedas repartidas entre validadores aprobadores cuando el flujo externo de validacion alcanza quorum. Cada auditoria retroactiva automatica acredita `0.62832` monedas adicionales a `audit_treasury`.

El 20% cientifico no se paga automaticamente a workers. Por defecto se acumula como reserva bloqueada con `status = RESERVE_LOCKED`. Mientras siga bloqueada, no se puede transferir, reclamar, reservar presupuesto ni pagar workers. Solo cuando una futura L2 sea activada por gobernanza/multisig con timelock, la reserva podra usarse para jobs `accepted`, con worker, `result_hash`, `proof_hash` y presupuesto reservado. Jobs `rejected`, `disputed` o `expired` no pagan.

Cada bloque guarda la dificultad y recompensa usadas al momento de aceptarse.
Las tareas y bloques tambien guardan `protocol_params_id`, asi un retarget no cambia las reglas de una tarea que ya estaba asignada.

### Transacciones en bloques

La ruta hacia mainnet ya incluye contabilidad basica de transacciones firmadas:

- Las wallets usan Ed25519 y direcciones `PI...`.
- La mempool valida `tx_hash`, firma, `chain_id`, `network_id`, nonce y fee maximo.
- Al minar un bloque, el nodo selecciona transacciones ejecutables, rechaza las que no tengan firma/saldo/nonce valido y calcula `tx_merkle_root`.
- El bloque guarda `tx_count`, `tx_hashes`, `tx_merkle_root`, `fee_reward` y `state_root`.
- Al aceptar/importar el bloque, la L1 aplica debito al sender, credito al recipient, fee al minero y marca la transaccion como `confirmed`.
- `state_root` es una huella SHA-256 del estado contable despues del replay del bloque. Si un nodo cambia el ledger local, `verify_chain()` detecta que el estado ya no coincide.
- `verify_chain()` recalcula el hash canonico incluyendo el compromiso de transacciones y compara `state_root` cuando existe, por lo que el bloque es auditable.
- Los checkpoints canonicos guardan `height`, `block_hash`, `state_root`, `balances_hash`, `snapshot_hash` y contadores de ledger para acelerar sync futura y verificar snapshots sin confiar en archivos pesados.
- Un snapshot canonico exportado incluye metadata del checkpoint y balances agregados por cuenta. El import valida `chain_id`, `network_id`, `genesis_hash`, `balances_hash`, `state_root` y `snapshot_hash` antes de guardarlo como referencia externa.
- Un snapshot importado puede activarse como `active_snapshot_base`; desde ahi el nodo pide a peers solo bloques con `height` posterior al snapshot y acepta el siguiente bloque si su `previous_hash` apunta al `block_hash` del checkpoint.
- Para fast-sync real, un snapshot importado puede aplicarse como estado inicial local si el nodo aun no tiene bloques locales. Esto restaura balances agregados desde el snapshot y luego permite replay canonico de bloques posteriores.
- `stake` bloquea PI desde la wallet hacia `science_stake:<address>` y actualiza el tier cientifico de forma deterministica.
- `unstake` libera el stake cientifico completo si la direccion no tiene jobs activos.
- `science_job_create` crea jobs L1 desde payload firmado, con `job_id` deterministico si no se provee uno.
- `governance_action` ejecuta acciones canonicas de `science_reserve`: `propose_activation`, `approve_activation`, `execute_activation`, `pause` y `unpause`.

Por ahora se ejecutan dentro del bloque `transfer`, `stake`, `unstake`, `science_job_create`, `governance_action` y `treasury_claim`.

`treasury_claim` mueve fondos del Scientific Development Treasury solo si la wallet firmante es la governance/owner wallet configurada, el destino es la treasury wallet configurada, el timelock ya desbloqueo fondos y el `claim_id` no fue usado antes.

Retarget automatico:

```text
epoch_blocks = 5
target_block_ms = 60000
tolerance = 20%
max_adjustment_factor = 1.25
```

Cuando se aceptan suficientes bloques para cerrar una epoca, el coordinador mide `blocks.total_task_ms`. El objetivo es que cada bloque aceptado dure cerca de 1 minuto. Si el promedio fue demasiado rapido, sube dificultad para los siguientes trabajos. Si fue demasiado lento, la baja. El ajuste es conservador y crea una nueva fila activa en `protocol_params`; los bloques anteriores conservan la dificultad con la que fueron aceptados.

## Arquitectura

```text
picoin-proof-of-pi/
  app/
    api/          Endpoints REST FastAPI
    core/         Configuracion, hashing SHA-256 y calculo BBP de pi
    db/           SQLite y migraciones simples
    models/       Schemas Pydantic
    services/     Tareas, bloques, recompensas, penalizaciones, transacciones
    web/          Dashboard local estatico servido por FastAPI
  validator/      Verificacion independiente del Proof of Pi
  miner/          Cliente minero ejecutable por usuarios
  tests/          Pruebas basicas del calculo y validador
```

## Requisitos

- Python 3.11+
- SQLite incluido con Python

## Instalacion

```powershell
cd C:\Users\LOQ\Documents\personal\PROYECTOS\PICOIN\picoin-proof-of-pi
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Correr El Servidor

```powershell
.\.venv\Scripts\python.exe -m picoin node start --reload
```

La API queda en:

- `http://127.0.0.1:8000`
- Docs interactivas: `http://127.0.0.1:8000/docs`
- Dashboard local: `http://127.0.0.1:8000/dashboard`

La base SQLite se crea automaticamente en `data/picoin.sqlite3`.

## Dashboard Local

Desde v0.16, el nodo sirve un panel web operativo en:

```text
http://127.0.0.1:8000/dashboard
```

El dashboard consume la API REST del mismo nodo y muestra:

- Explorador de bloques aceptados con height, minero, rango, recompensa, dificultad y hash.
- Estado de validadores, incluyendo reputacion, stake, score de seleccion, votos recientes y recompensas.
- Faucet visual para acreditar balances demo a mineros o validadores en red local.
- Metricas de dificultad, progreso de epoca y preview de retarget.
- Metricas de performance por asignacion, compute, commit, validacion y total.
- Resumen de auditoria economica y estado de integridad de la cadena local.
- Auditorias retroactivas manuales con doble muestra.
- Estado operativo del nodo, readiness de mineria y eventos recientes.

## CLI Nodo Local

Desde v0.13, Picoin incluye un CLI local unificado:

```powershell
.\.venv\Scripts\python.exe -m picoin --version
.\.venv\Scripts\python.exe -m picoin node start --reload
.\.venv\Scripts\python.exe -m picoin node status
.\.venv\Scripts\python.exe -m picoin node audit
.\.venv\Scripts\python.exe -m picoin node protocol
.\.venv\Scripts\python.exe -m picoin node doctor
```

El CLI tambien envuelve minero, validador y testnet:

```powershell
.\.venv\Scripts\python.exe -m picoin miner register --name alice
.\.venv\Scripts\python.exe -m picoin miner mine --once
.\.venv\Scripts\python.exe -m picoin validator register --name val1
.\.venv\Scripts\python.exe -m picoin validator validate --once
.\.venv\Scripts\python.exe -m picoin testnet reset
.\.venv\Scripts\python.exe -m picoin testnet bootstrap
.\.venv\Scripts\python.exe -m picoin testnet cycle
.\.venv\Scripts\python.exe -m picoin testnet continuous --miners 3 --loops 3
```

CLI de Science Compute Access Layer:

```powershell
.\.venv\Scripts\python.exe -m picoin science stake --amount 31416
.\.venv\Scripts\python.exe -m picoin science account
.\.venv\Scripts\python.exe -m picoin science create-job --type "ai_inference" --metadata-hash "hash..." --storage-pointer "ipfs://payload" --max-compute-units 10 --reward-per-unit 0.25 --max-reward 2.5
.\.venv\Scripts\python.exe -m picoin science jobs
.\.venv\Scripts\python.exe -m picoin science accept-job --job-id science_job_xxxxxxxxxxxxxxxx --worker-address worker-1 --result-hash hash... --proof-hash proof... --compute-units-used 8
.\.venv\Scripts\python.exe -m picoin science pay-worker --job-id science_job_xxxxxxxxxxxxxxxx
.\.venv\Scripts\python.exe -m picoin science reserve
.\.venv\Scripts\python.exe -m picoin science reserve-governance
.\.venv\Scripts\python.exe -m picoin science propose-l2-activation --signer signer-1
.\.venv\Scripts\python.exe -m picoin science approve-l2-activation --signer signer-2
.\.venv\Scripts\python.exe -m picoin science execute-l2-activation
.\.venv\Scripts\python.exe -m picoin reserve status
.\.venv\Scripts\python.exe -m picoin reserve pause --signer signer-1
.\.venv\Scripts\python.exe -m picoin reserve unpause --signer signer-2
.\.venv\Scripts\python.exe -m picoin treasury status
.\.venv\Scripts\python.exe -m picoin treasury claim
```

`pay-worker` existe para dejar el camino L2 listo, pero falla con `science compute reserve is locked until L2 marketplace activation` mientras la reserva este bloqueada.
`treasury claim` solo mueve fondos si ya existe balance desbloqueado por el timelock de 90 dias y si el solicitante/destino coinciden con la governance wallet y treasury wallet configuradas.

Config local opcional:

```powershell
Copy-Item .env.example .env
```

Variables soportadas:

```text
PICOIN_NETWORK=local
PICOIN_CHAIN_ID=picoin-local-testnet
PICOIN_NODE_ID=local-node
PICOIN_NODE_TYPE=full
PICOIN_NODE_ADDRESS=http://127.0.0.1:8000
PICOIN_BOOTSTRAP_PEERS=
PICOIN_HOST=127.0.0.1
PICOIN_PORT=8000
PICOIN_SERVER=http://127.0.0.1:8000
```

## Public Testnet Deployment Kit v0.18

Picoin incluye una carpeta `deploy/` para levantar un nodo publico en Ubuntu/DigitalOcean sin mezclar la web institucional con el nodo:

- `deploy/public-testnet.env.example`: variables para bootstrap, full node, miner, validator o auditor.
- `deploy/systemd/picoin-node.service`: servicio `systemd` con restart automatico.
- `deploy/scripts/install-systemd-service.sh`: instalador del servicio y `/etc/picoin/picoin.env`.
- `deploy/scripts/health-check.sh`: revision externa de `/health`, sync, auditoria y checkpoint.
- `deploy/scripts/backup-sqlite.sh`: backup comprimido de `data/picoin.sqlite3`.
- `deploy/README-public-testnet.md`: guia de despliegue paso a paso.

Comandos base en el droplet:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sqlite3 curl ufw
sudo useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
sudo -u picoin git clone https://github.com/devcoffeecoin/PICOIN.git /opt/picoin/PICOIN
sudo -u picoin bash -lc 'cd /opt/picoin/PICOIN/picoin-proof-of-pi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
sudo -u picoin bash -lc 'ln -s /opt/picoin/PICOIN/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi'
sudo PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi/deploy/scripts/install-systemd-service.sh
sudo nano /etc/picoin/picoin.env
sudo systemctl start picoin-node
```

Chequeos de readiness:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node doctor --require-checkpoint
.venv/bin/python -m picoin node audit
.venv/bin/python -m picoin node sync-status
```

Para conectar un segundo droplet, configura `PICOIN_BOOTSTRAP_PEERS=http://BOOTSTRAP_PUBLIC_IP:8000`, usa un `PICOIN_NODE_ID` unico, reinicia el servicio y ejecuta `python -m picoin node reconcile` en ambos nodos.

Para sincronizar un nodo atrasado en una sola operacion:

```bash
python -m picoin node catch-up --peer http://BOOTSTRAP_PUBLIC_IP:8000
```

`node catch-up` ejecuta rondas de reconcile, consensus replay, sync-status y audit. Si se pasa `--peer`, tambien compara `network_id`, `chain_id`, `genesis_hash`, altura y ultimo block hash contra el peer. Termina con `status=ok` cuando no quedan bloques pendientes, la auditoria economica es valida y el nodo coincide con el peer.

## Correr Un Minero

En otra terminal:

```powershell
cd C:\Users\LOQ\Documents\personal\PROYECTOS\PICOIN\picoin-proof-of-pi
.\.venv\Scripts\Activate.ps1
python -m miner.client register --name alice
python -m miner.client mine --once
```

El minero:

1. Genera una identidad Ed25519 local en `miner_identity.json`.
2. Registra el minero con su `public_key`.
3. Pide una tarea a `GET /tasks/next`.
4. Recibe un rango pseudoaleatorio de posiciones hexadecimales de pi.
5. Calcula el segmento hexadecimal asignado.
6. Genera `result_hash` con SHA-256.
7. Construye un Merkle root del segmento.
8. Envia un commit firmado a `POST /tasks/commit`.
9. Recibe posiciones de muestra generadas por el servidor.
10. Revela solo esas muestras con Merkle proofs en `POST /tasks/reveal`.
11. Queda esperando votos de validadores externos.
12. Cuando alcanza quorum de aprobaciones, el servidor registra el bloque.

Comandos del minero:

```powershell
python -m miner.client register --name alice
python -m miner.client mine --once
python -m miner.client mine --loops 10
python -m miner.client mine --loops 10 --workers 2
python -m miner.client stats
```

Para usar otro archivo de identidad:

```powershell
python -m miner.client --identity alice_identity.json register --name alice
python -m miner.client --identity alice_identity.json mine --loops 10
```

Para reemplazar una identidad local existente:

```powershell
python -m miner.client register --name alice --overwrite
```

La clave privada queda solo en el archivo local de identidad. El servidor solo recibe la `public_key`.

## Correr Un Validador

En otra terminal:

```powershell
python -m validator.client register --name val1
python -m validator.client validate --once
```

El validador:

1. Genera una identidad Ed25519 local en `validator_identity.json`.
2. Registra el validador con su `public_key`.
3. Pide un job a `GET /validation/jobs`.
4. Recalcula con BBP cada posicion revelada.
5. Verifica cada Merkle proof contra el `merkle_root`.
6. Firma el resultado.
7. Envia aprobacion o rechazo a `POST /validation/results`.

## Mining Testnet Local

La testnet local trae un flujo repetible con:

- reset controlado de SQLite y archivos demo
- identidad demo de minero
- 3 identidades demo de validadores
- faucet local para el minero
- servidor FastAPI local
- ciclo completo: minar, revelar 32 muestras, votar con 3 validadores y aceptar bloque por quorum
- mineria continua con varios mineros y auditorias retroactivas de doble muestra

### Flujo automatico completo

Este comando resetea, crea identidades, levanta el servidor en segundo plano, mina un bloque, ejecuta los 3 validadores y apaga el servidor al terminar:

```powershell
.\scripts\testnet-all.ps1
```

Si PowerShell bloquea scripts locales por politica de ejecucion:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\testnet-all.ps1
```

Con puerto distinto:

```powershell
.\scripts\testnet-all.ps1 -Port 8001
```

### Flujo manual recomendado

1. Reset controlado:

```powershell
.\scripts\testnet-reset.ps1
```

2. Crear identidades demo y faucet:

```powershell
.\scripts\testnet-bootstrap.ps1
```

Esto crea:

```text
data/testnet/identities/miner-alice.json
data/testnet/identities/validator-one.json
data/testnet/identities/validator-two.json
data/testnet/identities/validator-three.json
data/testnet/manifest.json
```

3. Levantar servidor:

```powershell
.\scripts\testnet-server.ps1
```

4. En otra terminal, ejecutar un ciclo completo:

```powershell
.\scripts\testnet-cycle.ps1
```

Tambien puedes ejecutar cada rol por separado:

```powershell
.\scripts\testnet-mine-once.ps1
.\scripts\testnet-validator1.ps1
.\scripts\testnet-validator2.ps1
.\scripts\testnet-validator3.ps1
```

Los dos primeros validadores dejan el job en `validation_pending`; el tercero completa el quorum y el coordinador acepta el bloque.

### Mineria continua multi-minero

Con el servidor levantado, puedes probar varios mineros compitiendo de forma repetible:

```powershell
.\.venv\Scripts\python.exe -m picoin testnet continuous --miners 3 --loops 3 --workers 1
```

Cada bloque aceptado dispara por defecto una auditoria retroactiva con `sample_multiplier = 2`, es decir, 64 muestras para bloques del protocolo v0.16. Para desactivarla en una corrida:

```powershell
.\.venv\Scripts\python.exe -m picoin testnet continuous --miners 3 --loops 3 --no-retro-audit
```

### Faucet local

El faucet existe para pruebas locales, no para mainnet. Desde CLI:

```powershell
python -m app.tools.faucet miner_xxxxxxxxxxxxxxxx --type miner --amount 10
```

Desde API:

```powershell
curl -X POST http://127.0.0.1:8000/faucet `
  -H "Content-Type: application/json" `
  -d '{"account_id":"miner_xxxxxxxxxxxxxxxx","account_type":"miner","amount":10}'
```

## Endpoints

### `GET /health`

Devuelve salud operativa del nodo: conexion a SQLite, version activa, uptime, altura actual, hash mas reciente, verificacion de cadena, auditoria basica y si hay quorum suficiente para mineria.

```powershell
curl http://127.0.0.1:8000/health
```

### `GET /node/status`

Devuelve un snapshot mas amplio del nodo local: contadores de mineros, validadores, tareas, jobs de validacion, dificultad activa, performance y economia resumida.

```powershell
curl http://127.0.0.1:8000/node/status
```

### `GET /events`

Lista eventos recientes normalizados para dashboard y debugging: bloques aceptados, votos de validadores, faucet, penalizaciones y retargets.

```powershell
curl "http://127.0.0.1:8000/events?limit=20"
```

### `GET /protocol`

Devuelve parametros activos del protocolo, incluyendo `base_reward`, `difficulty` y `reward_per_block`.

Tambien expone la distribucion conceptual de trabajo util:

```text
proof_of_pi_reward_percent = 0.67
science_compute_reward_percent = 0.20
validator_auditor_reward_percent = 0.10
scientific_development_reward_percent = 0.03
```

En esta L1, el porcentaje de Science se registra como reserva por bloque y no como pago directo. El 3% del Scientific Development Fund se registra en una treasury separada, bloqueada 90 dias por epoch trimestral.

### `POST /science/stake`

Registra o actualiza stake de acceso cientifico. El tier se deriva automaticamente:

```text
researcher   3,141.6 PI    multiplier 1x    priority low
lab          31,416 PI     multiplier 10x   priority medium
institution  314,160 PI    multiplier 100x  priority high
```

```powershell
curl -X POST http://127.0.0.1:8000/science/stake `
  -H "Content-Type: application/json" `
  -d '{"address":"lab-1","amount":31416}'
```

### `POST /science/jobs`

Crea un job cientifico L1. No ejecuta computo real ni guarda archivos pesados: solo `metadata_hash`, `storage_pointer`, unidades abstractas, limite economico y estado. La L2 futura certificara `compute_units_used`; la L1 solo liquidara el pago si el job llega a `accepted` y la reserva esta activa.

```powershell
curl -X POST http://127.0.0.1:8000/science/jobs `
  -H "Content-Type: application/json" `
  -d '{"requester_address":"lab-1","job_type":"ai_inference","metadata_hash":"hash...","storage_pointer":"ipfs://payload","max_compute_units":10,"reward_per_compute_unit":0.25,"max_reward":2.5}'
```

El pago maximo queda acotado por:

```text
payout_amount = min(compute_units_used * reward_per_compute_unit, max_reward)
```

### `POST /science/jobs/{job_id}/transition`

Avanza el estado del job con validaciones de transicion. Estados soportados:

```text
created -> queued -> assigned -> committed -> submitted -> verified -> accepted -> paid
created/queued/assigned/committed/submitted/verified -> rejected/disputed/expired
```

`submitted`, `verified` y `accepted` requieren `worker_address`, `result_hash` y `proof_hash`. `accepted` requiere ademas `compute_units_used`, certificado en el futuro por L2. Por defecto el requester no puede ser worker de su propio job.

### `POST /science/jobs/{job_id}/pay`

Paga al worker solo si el job esta `accepted`, no fue pagado antes, tiene worker y tiene `payout_amount > 0`. Jobs `rejected`, `disputed`, `expired`, `submitted`, `verified` o incompletos no pagan. Mientras `science reserve status != L2_ACTIVE`, `payouts_enabled = false` o `emergency_paused = true`, este endpoint esta deshabilitado y no mueve fondos.

### `GET /science/reserve`

Devuelve la reserva cientifica de la epoca actual:

```text
total_reserved
total_pending
total_paid
available
status
activation_requested_at
activation_available_at
activated_at
governance_approvals
authorized_signers
payouts_enabled
emergency_paused
max_reward_per_job
max_payout_per_epoch
max_pending_per_requester
```

### `GET /reserve/status`

Alias operativo de `/science/reserve` para consultar la Science Compute Marketplace Reserve:

```text
total_reserved
total_pending
total_paid
available
status
payouts_enabled
emergency_paused
```

### `POST /reserve/pause`

Pausa pagos de emergencia. Requiere signer autorizado.

### `POST /reserve/unpause`

Quita la pausa. Si la reserva ya fue activada por timelock + multisig vuelve a `L2_ACTIVE`; si no, queda bloqueada.

### `GET /treasury/status`

Devuelve el Scientific Development Fund:

```text
total_accumulated
total_claimed
locked_balance
unlocked_balance
claimable
current_epoch
next_unlock_at
treasury_wallet
governance_wallet
history
```

### `POST /treasury/claim`

Reclama solo el balance desbloqueado. Antes de 90 dias responde con timelock activo. La operacion queda auditada en `ledger_entries` y `scientific_development_treasury_claims`.

```powershell
curl -X POST http://127.0.0.1:8000/treasury/claim `
  -H "Content-Type: application/json" `
  -d '{"requested_by":"picoin_governance_multisig","claim_to":"picoin_scientific_development_wallet"}'
```

### `GET /science/reserve/governance`

Devuelve el estado de gobernanza de la reserva cientifica. Por defecto:

```text
status = RESERVE_LOCKED
threshold = 2
timelock_seconds = 86400
```

### `POST /science/reserve/governance/propose-activation`

Inicia el proceso timelocked de activacion L2. Mantiene la reserva bloqueada y registra la primera firma.

```powershell
curl -X POST http://127.0.0.1:8000/science/reserve/governance/propose-activation `
  -H "Content-Type: application/json" `
  -d '{"signer":"signer-1"}'
```

### `POST /science/reserve/governance/approve-activation`

Agrega una aprobacion multisig. El MVP requiere 2 firmantes distintos.

### `POST /science/reserve/governance/execute-activation`

Activa la reserva solo si se cumplio el umbral multisig de signers autorizados y vencio el timelock. Antes de eso, `payouts_enabled = false` y no se puede ejecutar ningun pago.

### `GET /science/events`

Eventos L1 preparados para sincronizacion futura L2:

```text
ScienceStakeUpdated
ScienceJobCreated
ScienceJobAssigned
ScienceJobCommitted
ScienceJobSubmitted
ScienceJobVerified
ScienceJobAccepted
ScienceJobRejected
ScienceJobPaid
ScienceJobDisputed
ScienceReserveAccrued
ScienceReserveActivationProposed
ScienceReserveActivationApproved
ScienceReserveActivated
ScienceReserveLocked
ScienceReserveUnlocked
ScienceReservePaused
ScienceReserveUnpaused
ScientificTreasuryClaimed
```

### `GET /protocol/history`

Devuelve el historial de parametros de protocolo guardados en SQLite. Cada retarget que cambia dificultad desactiva el set anterior y crea uno nuevo.

### `GET /difficulty`

Devuelve el estado del retarget automatico: altura actual, ultima altura ajustada, bloques faltantes para la siguiente epoca, dificultad activa y recompensa activa.

### `GET /difficulty/history`

Lista eventos de retarget ya ejecutados.

### `GET /difficulty/preview`

Simula el siguiente retarget sin cambiar la base de datos. Devuelve si la epoca esta lista, promedio observado, accion propuesta (`increase`, `decrease`, `keep` o `wait`) y el protocolo propuesto.

```powershell
curl http://127.0.0.1:8000/difficulty/preview
```

### `POST /difficulty/retarget`

Ejecuta el retarget si la epoca esta completa. Para pruebas locales se puede usar:

```powershell
curl -X POST "http://127.0.0.1:8000/difficulty/retarget?force=true"
```

`force=true` permite probar la logica con menos bloques, pero el flujo normal no lo necesita: al aceptar bloques, el coordinador intenta retarget automaticamente.

### `POST /miners/register`

Registra un minero.

```json
{
  "name": "alice",
  "public_key": "ed25519:base64url_public_key"
}
```

### `GET /tasks/next?miner_id=...`

Asigna el siguiente rango de posiciones hexadecimales de pi. Si el minero ya tiene una tarea activa no expirada, devuelve esa misma tarea.

La asignacion ya no es secuencial. El servidor deriva una semilla con:

```text
previous_hash
miner_id
task_id
task_counter
nonce
segment_size
max_pi_position
algorithm
```

Luego convierte esa semilla en `range_start` y busca un rango sin solape con tareas activas, comprometidas o aceptadas. La tarea guarda:

```text
assignment_seed
assignment_mode = pseudo_random
```

### `POST /tasks/commit`

Recibe el compromiso del resultado. No recibe el segmento completo.

```json
{
  "task_id": "task_xxxxxxxxxxxxxxxx",
  "miner_id": "miner_xxxxxxxxxxxxxxxx",
  "result_hash": "64_hex_chars",
  "merkle_root": "64_hex_chars",
  "signature": "base64url_signature",
  "signed_at": "2026-05-10T15:00:00+00:00"
}
```

Respuesta:

```json
{
  "accepted": true,
  "status": "committed",
  "challenge_seed": "64_hex_chars",
  "samples": [
    {"position": 12},
    {"position": 33}
  ]
}
```

### `POST /tasks/reveal`

Revela las muestras pedidas y sus Merkle proofs.

```json
{
  "task_id": "task_xxxxxxxxxxxxxxxx",
  "miner_id": "miner_xxxxxxxxxxxxxxxx",
  "samples": [
    {
      "position": 12,
      "digit": "A",
      "proof": [
        {"side": "right", "hash": "64_hex_chars"}
      ]
    }
  ],
  "signature": "base64url_signature",
  "signed_at": "2026-05-10T15:01:00+00:00"
}
```

Respuesta esperada:

```json
{
  "accepted": true,
  "status": "validation_pending",
  "message": "reveal accepted; waiting for external validator"
}
```

### `POST /validators/register`

Registra un validador externo.

```json
{
  "name": "val1",
  "public_key": "ed25519:base64url_public_key"
}
```

### `POST /faucet`

Acredita monedas demo desde `genesis` a una cuenta registrada. Esta ruta es solo para `network_id = local` y tiene limite por cuenta para evitar abuso en demos.

```json
{
  "account_id": "miner_xxxxxxxxxxxxxxxx",
  "account_type": "miner",
  "amount": 10
}
```

### `POST /maintenance/expire-tasks`

Ejecuta limpieza controlada de tareas y jobs de validacion vencidos.

```powershell
curl -X POST http://127.0.0.1:8000/maintenance/expire-tasks
```

### `GET /validators/{validator_id}`

Consulta identidad, historial y reputacion de un validador. Incluye:

- `accepted_jobs`
- `rejected_jobs`
- `completed_jobs`
- `invalid_results`
- `trust_score`
- `cooldown_until`
- `avg_validation_ms`
- `is_banned`

### `GET /validators`

Lista validadores ordenados por score de seleccion. Con `eligible_only=true` devuelve solo validadores aptos para recibir jobs.

```powershell
curl "http://127.0.0.1:8000/validators?eligible_only=true"
```

Cada validador incluye campos de seleccion:

- `selection_score`
- `selection_weight`
- `recent_validation_votes`
- `availability_score`

### `GET /validation/jobs?validator_id=...`

Entrega el siguiente job pendiente si el validador pertenece al pool seleccionado para ese job. El pool se calcula con reputacion, stake, disponibilidad y rotacion reciente. El mismo job puede ser entregado a varios validadores distintos hasta alcanzar quorum. Un validador no puede votar dos veces el mismo job.

### `POST /validation/results`

Recibe el voto firmado del validador. El bloque se acepta solo cuando `approvals >= required_validator_approvals`.

```json
{
  "job_id": "job_xxxxxxxxxxxxxxxx",
  "validator_id": "validator_xxxxxxxxxxxxxxxx",
  "approved": true,
  "reason": "external validator accepted samples",
  "signature": "base64url_signature",
  "signed_at": "2026-05-10T15:02:00+00:00"
}
```

Respuesta antes de quorum:

```json
{
  "accepted": true,
  "status": "validation_pending",
  "approvals": 1,
  "required_approvals": 3,
  "block": null
}
```

### `POST /tasks/submit`

Endpoint heredado para validacion completa del segmento. El minero actual usa `commit` y `reveal`.

### `GET /blocks`

Lista bloques aceptados.

### `GET /blocks/verify`

Audita la cadena local de bloques aceptados. Verifica:

- `height` incremental
- `previous_hash`
- `block_hash`
- rangos duplicados
- `result_hash` duplicados

### `GET /blocks/{height}`

Consulta un bloque por altura.

### `GET /miners/{miner_id}`

Consulta datos, reputacion y recompensas simuladas de un minero.

### `GET /balances`

Lista balances persistentes de cuentas `genesis`, mineros y validadores.

### `GET /balances/{account_id}`

Consulta el balance de una cuenta.

### `GET /ledger`

Lista movimientos del ledger local. Puede filtrarse por cuenta:

```powershell
curl "http://127.0.0.1:8000/ledger?account_id=genesis"
```

### `GET /audit/summary`

Devuelve resumen de emision, circulante, stake bloqueado, stake recortado, bloques aceptados y validadores elegibles.

### `GET /audit/full`

Ejecuta auditoria economica completa y devuelve un JSON verificable. Comprueba:

- suma total de balances contra `genesis_supply + block_rewards + science_reserve + validator_rewards + audit_rewards`
- suma total del ledger contra la misma politica monetaria
- balance de cada cuenta contra sus movimientos de ledger
- bloques aceptados contra tabla `rewards`
- recompensas de bloque contra movimientos `block_reward`
- recompensas adicionales de validadores contra movimientos `validator_reward`
- stake bloqueado y slashing de validadores contra ledger

```powershell
curl http://127.0.0.1:8000/audit/full
```

Si `valid = false`, la respuesta incluye `issues` con codigos como `account_balance_mismatch`, `total_balances_mismatch` o `rewards_table_mismatch`.

### `GET /audit/retroactive`

Lista auditorias retroactivas recientes. Cada auditoria guarda bloque, seed, cantidad de muestras, hash esperado, hash recalculado, si fue automatica, recompensa y resultado.

```powershell
curl "http://127.0.0.1:8000/audit/retroactive?limit=20"
```

### `POST /audit/retroactive/run`

Ejecuta una auditoria manual sobre un bloque aceptado, o sobre una altura especifica. Por defecto usa el doble de muestras del protocolo activo del bloque. Las auditorias manuales no emiten recompensa; la recompensa del 20% solo aplica a la auditoria automatica programada cada 314 bloques.

```powershell
curl -X POST "http://127.0.0.1:8000/audit/retroactive/run?sample_multiplier=2"
curl -X POST "http://127.0.0.1:8000/audit/retroactive/run?block_height=3&sample_multiplier=2"
```

En v0.16 un bloque nuevo usa 32 muestras durante validacion normal y 64 muestras durante auditoria retroactiva. Como el MVP no guarda el segmento completo de pi ni una prueba criptografica completa, la auditoria recalcula el segmento del bloque auditado con BBP y compara el `result_hash` registrado.

### `GET /stats`

Devuelve estadisticas globales del MVP.

### `GET /stats/performance`

Devuelve metricas de velocidad:

```json
{
  "accepted_blocks": 1,
  "avg_compute_ms": 589.0,
  "avg_assignment_ms": 1.0,
  "avg_commit_ms": 2.0,
  "avg_validation_ms": 4.0,
  "avg_total_task_ms": 900.0,
  "pending_validation_jobs": 0,
  "bbp_digit_cache_hits": 8,
  "bbp_digit_cache_misses": 64
}
```

## Bloques

Cada bloque aceptado contiene:

- `height`
- `previous_hash`
- `miner_id`
- `range_start`
- `range_end`
- `algorithm`
- `result_hash`
- `merkle_root`
- `samples`
- `timestamp`
- `block_hash`
- `reward`
- `difficulty`
- `protocol_params_id`
- `protocol_version`
- `validation_mode`
- `total_task_ms`
- `validation_ms`

## Seguridad MVP

Implementado:

- SHA-256 para resultados y bloques.
- Encadenamiento por `previous_hash`.
- Identificacion de minero por `miner_id`.
- Identidad Ed25519 por minero.
- Firma Ed25519 obligatoria en commit y reveal.
- Identidad Ed25519 por validador.
- Firma Ed25519 obligatoria en resultados de validacion.
- Quorum de multiples validadores por bloque.
- Reputacion de validadores con `trust_score`.
- Seleccion/gating de validadores por reputacion y stake minimo.
- Seleccion inteligente de validadores por score ponderado.
- Rotacion para evitar concentracion excesiva de validaciones.
- Stake simulado de validadores y slashing por firmas invalidas.
- Recompensa adicional para validadores aprobadores.
- Balances persistentes y ledger auditable.
- Auditoria economica completa en `/audit/full`.
- Science Compute Access Layer en L1.
- Staking cientifico por tiers `researcher`, `lab`, `institution`.
- Reserva `science_compute_reserve` acumulada por bloque.
- Reserva Science bloqueada por defecto con `RESERVE_LOCKED`.
- Activacion futura por timelock + multisig antes de cualquier pago.
- Registro de jobs cientificos por hashes y punteros externos.
- Pagos a workers solo para jobs aceptados y no pagados previamente.
- Eventos Science para futura sincronizacion L2.
- Auditorias retroactivas aleatorias en `/audit/retroactive/run`.
- Auditoria retroactiva automatica cada 314 bloques.
- Marcado de bloques fraudulentos si una auditoria retroactiva falla.
- Penalizacion reforzada y cooldown de 1 hora por fraude detectado.
- Cooldown y ban por firmas invalidas repetidas.
- Commit-reveal con `result_hash` y `merkle_root`.
- Merkle proofs para cada muestra revelada.
- Recalculo independiente por validador externo.
- Muestras deterministicas generadas despues del commit.
- Tareas con expiracion.
- Limpieza manual de tareas y jobs expirados.
- Maximo de una tarea activa por minero.
- Rate limit simple de asignacion de tareas por minero.
- Asignacion pseudoaleatoria de rangos basada en `previous_hash`.
- Rechazo de solapes con rangos activos o aceptados.
- Rechazo de tareas ya enviadas o expiradas.
- Rechazo de `result_hash` duplicado.
- Penalizaciones por resultado invalido, duplicado o firma invalida.
- `trust_score` por minero.
- Cooldown temporal si acumula demasiadas penalizaciones.
- Restricciones SQLite para evitar doble bloque por tarea.
- Cache LRU para digitos BBP.
- Metricas de performance por tarea, commit, validacion y bloque.
- Dificultad dinamica inicial basada en tamano de segmento, muestras y posicion maxima.
- Modo de red con `PICOIN_NETWORK`.
- Faucet habilitado solo en red local.
- Rate limit de faucet por cuenta.

Limites intencionales:

- El consenso distribuido sigue en evolucion; v0.18 agrega el kit de despliegue publico sobre peers, mempool, wallets, propuestas, votos, finalizacion y replay inicial.
- La red P2P actual es basica: REST/WebSocket, heartbeat y cola de replay, no gossip optimizado.
- Las wallets firman transacciones para mempool; `transfer`, `stake` y `science_job_create` ya se liquidan en ledger al entrar en bloque.
- No hay ejecucion real de IA ni computo cientifico pesado en L1.
- No hay marketplace L2 todavia; la L1 solo deja acceso, reserva, jobs, estados y pagos verificados.
- La validacion actual es probabilistica por muestras, no una prueba criptografica completa del calculo entero.

## Performance

El calculo BBP usa cache LRU en memoria para digitos hexadecimales individuales. Esto acelera validaciones repetidas de samples y auditorias sobre posiciones ya vistas.

Metricas guardadas:

```text
tasks.assignment_ms
tasks.compute_ms
commitments.commit_ms
validation_jobs.validation_ms
blocks.total_task_ms
blocks.validation_ms
```

El minero mide `compute_ms` localmente y lo envia en `POST /tasks/commit`. El servidor mide asignacion, commit, validacion externa y tiempo total hasta bloque aceptado.

El minero tambien puede calcular segmentos usando procesos paralelos:

```powershell
python -m miner.client mine --once --workers 2
```

Para rangos pequenos, `--workers 1` suele ser mas rapido por menor overhead. Para posiciones o segmentos mas pesados, compara con el benchmark antes de cambiar parametros del protocolo.

Benchmark BBP:

```powershell
python -m app.tools.benchmark_bbp --start 5000 --length 32 --workers 1 --rounds 1
python -m app.tools.benchmark_bbp --start 5000 --length 32 --workers 2 --rounds 1
```

Ejemplo observado en este entorno:

```text
start=5000 length=32 workers=1 avg_ms=311
start=5000 length=32 workers=2 avg_ms=302
```

## Reputacion De Validadores

Cada validador mantiene reputacion local en SQLite:

```text
completed_jobs = accepted_jobs + rejected_jobs
trust_score = (completed_jobs + 1) / (completed_jobs + 1 + invalid_results * 2)
```

Aceptar o rechazar un job firmado correctamente cuenta como trabajo completado. Un rechazo no baja reputacion por si solo, porque puede ser una decision honesta. Las firmas invalidas si bajan `trust_score`; despues de 3 resultados invalidos el validador entra en cooldown, y despues de 9 queda baneado.

Cada validador recibe un stake simulado inicial de `31.416`, financiado desde la cuenta `genesis`. Para recibir jobs debe mantener al menos ese stake y `trust_score >= 0.25`. Cada firma invalida recorta `3.1416` del stake y lo devuelve a `genesis`.

Este modelo todavia no es staking real transferible. Es una capa MVP para priorizar validadores confiables, agregar costo anti-Sybil simulado y detectar comportamiento roto o malicioso.

## Seleccion De Validadores

Desde v0.12, el coordinador no entrega jobs solamente por orden de llegada. Para cada `validation_job`, calcula un pool de validadores seleccionados con:

```text
selection_score =
  trust_score * 0.55
  + stake_score * 0.25
  + availability_score * 0.10
  + rotation_score * 0.10
```

`stake_score` se normaliza contra el stake minimo, `availability_score` sube cuando el validador ha estado activo recientemente, y `rotation_score` baja si ese validador ya voto muchas veces en la ultima hora. Un pequeno desempate deterministico basado en `challenge_seed` evita que los empates siempre favorezcan al mismo ID.

El tamano del pool es:

```text
required_validator_approvals * 2
```

En v0.16, con `required_validator_approvals = 3`, el pool objetivo es de 6 validadores. Si hay menos validadores elegibles, usa todos los disponibles. Esto mantiene velocidad para testnet local, pero reduce concentracion cuando hay mas validadores que el quorum minimo.

## Economia MVP

Reglas actuales:

```text
genesis_supply = 3.1416
block_emission = 3.1416
miner_reward = 2.104872
science_compute_reserve = 0.62832
validator_reward_pool = 0.31416
scientific_development_treasury = 0.094248
total_minted_per_accepted_block = 3.1416
validator_initial_stake = 31.416
validator_slash_invalid_signature = 3.1416
```

El genesis queda registrado en `ledger_entries` con `block_height = 0` para compatibilidad de testnet local, pero solo por `3.1416`. Cada bloque aceptado crea un movimiento `block_reward` para el minero, `science_reserve_accrual` para la reserva compute, `scientific_development_treasury_accrual` para el treasury time-locked y movimientos `validator_reward` para los validadores que aprobaron el bloque. El stake inicial de validador no se financia desde genesis; es un parametro simulado de elegibilidad hasta activar staking real.

Politica monetaria auditada en v0.11:

```text
expected_total_balances =
  genesis_supply
  + accepted_block_rewards
  + science_compute_reserve_accruals
  + validator_rewards
  + scientific_development_treasury_accruals
  + retroactive_audit_rewards
```

`genesis`, faucet local, claims de treasury y slashing son movimientos internos o metadata de testnet. Las recompensas de minero, reserva cientifica, treasury, validador y auditoria son emision nueva. Por eso el total de balances puede crecer con cada bloque aceptado o auditoria automatica, mientras el endpoint `/audit/full` verifica que ese crecimiento coincida exactamente con la suma de recompensas registradas.

## Scientific Development Fund

El Scientific Development Fund reemplaza el concepto de gran premine por una treasury financiada continuamente por actividad real de la red. Recibe el `3%` de la emision base de cada bloque y queda bloqueado por `90` dias antes de poder reclamarse.

Uso previsto:

- desarrollo del protocolo;
- auditorias;
- infraestructura;
- investigacion;
- grants cientificos;
- desarrollo del marketplace cientifico/IA;
- soporte de nodos y tooling.

La treasury es separada de `science_compute_reserve`. El 20% de compute solo paga jobs cientificos `completed/verified/accepted` cuando la futura L2 este activada; el 3% de treasury financia desarrollo del ecosistema mediante desbloqueos trimestrales auditables.

## Science Compute Access Layer

La capa Science es una extension L1 para preparar un marketplace L2 futuro de computo cientifico e IA. No ejecuta modelos, simulaciones ni workloads pesados en L1. Su objetivo es coordinar acceso, reserva, jobs, estados y pagos verificables.

Entidades principales:

```text
science_stake_accounts
science_jobs
science_reward_reserve
scientific_development_treasury
scientific_development_treasury_epochs
scientific_development_treasury_claims
science_events
```

Tiers de acceso:

```text
Researcher    stake 3,141.6 PI    multiplier 1x    priority low
Lab           stake 31,416 PI     multiplier 10x   priority medium
Institution   stake 314,160 PI    multiplier 100x  priority high
```

El `compute_multiplier` no garantiza computo fijo. Es prioridad y acceso proporcional para que una L2 futura calcule cupos contra capacidad real de workers. En el MVP L1, cada job consume una unidad abstracta de cuota mensual: `science_base_monthly_quota_units * compute_multiplier`.

Reglas:

- solo cuentas Science activas pueden crear jobs;
- si el stake baja de minimo, no crea nuevos jobs;
- no se permite unstake si hay jobs activos;
- jobs guardan hashes, punteros y compute units abstractas, no datos pesados;
- `max_reward` queda reservado como pendiente contra `science_compute_reserve`;
- `payout_amount = min(compute_units_used * reward_per_compute_unit, max_reward)`;
- mientras `status != L2_ACTIVE`, `payouts_enabled = false` o `emergency_paused = true`, no se paga;
- jobs `rejected`, `disputed` o `expired` liberan presupuesto pendiente y no pagan;
- workers solo cobran si el job esta `accepted` y luego queda `status = paid`;
- cada job se paga una sola vez;
- los limites `max_reward_per_job`, `max_payout_per_epoch` y `max_pending_per_requester` protegen la reserva;
- por defecto el requester no puede ser su propio worker.

## Firma Ed25519

El minero firma un mensaje canonico con:

```text
task_id
miner_id
range_start
range_end
algorithm
result_hash
signed_at
```

El servidor reconstruye el mismo mensaje desde la tarea guardada y verifica la firma con la `public_key` registrada. Si alguien cambia el rango, el algoritmo, el hash o intenta enviar el resultado como otro minero, la firma deja de ser valida.

## Commit-Reveal, Merkle Root Y Validadores

El minero calcula el segmento completo localmente, pero no lo envia al servidor. En su lugar:

```text
result_hash = sha256(segment + range + algorithm)
merkle_root = root(leaves(position, digit))
```

Luego firma y envia el commit. El servidor genera el reto con:

```text
challenge_seed = sha256(previous_hash + task_id + result_hash + merkle_root)
```

Con ese seed el servidor elige `sample_count` posiciones. El minero revela solo esas posiciones:

```text
position
digit
merkle proof
```

El servidor verifica dos cosas por muestra:

1. El digito coincide con BBP para esa posicion.
2. La prueba Merkle conecta ese digito con el `merkle_root` comprometido.

Si todas las muestras pasan, cada validador firma una aprobacion. El coordinador registra un voto por validador en `validation_votes`. Solo cuando el job alcanza `required_validator_approvals` aprobaciones de validadores distintos se acepta el bloque. Los rechazos firmados tambien se acumulan; si alcanzan el mismo quorum, la tarea se rechaza.

Esto evita guardar pi, evita transmitir el segmento completo, separa el rol de validacion del rol de coordinacion y reduce el riesgo de depender de un unico validador.

## Auditorias Retroactivas

La validacion normal revisa 32 muestras reveladas por el minero. La auditoria retroactiva revisa un bloque ya aceptado con un reto nuevo y el doble de muestras. En v0.16 eso significa 64 posiciones.

El coordinador ejecuta una auditoria aleatoria automatica cada 314 bloques aceptados. La auditoria escoge un bloque aceptado al azar, no necesariamente el ultimo. Si la auditoria fue automatica, emite una recompensa adicional del 20% de la recompensa base del bloque auditado: `0.62832` PICOIN en la configuracion actual. Esa recompensa se registra como `retroactive_audit_reward` en el ledger y se acredita a la cuenta de protocolo `audit_treasury` hasta que existan auditores externos.

El flujo es:

1. El coordinador elige un bloque aceptado al azar, o usa `block_height` si se indica.
2. Genera `audit_seed` aleatorio.
3. Recalcula el segmento BBP del bloque auditado.
4. Comprueba que `hash_result(segmento, rango, algoritmo)` coincida con el `result_hash` guardado.
5. Deriva 64 posiciones de muestra desde `audit_seed` y guarda los digitos observados.
6. Registra el resultado en `retroactive_audits` y lo expone como evento reciente.

Si la auditoria detecta fraude:

- el bloque se marca con `fraudulent = true`;
- se guarda `fraud_reason` y `fraud_detected_at`;
- el minero recibe `20` puntos de penalizacion;
- el cooldown del minero sube a 1 hora;
- cada validador que aprobo ese bloque suma `3` resultados invalidos;
- esos validadores reciben cooldown de 1 hora y pierden reputacion de forma mas agresiva.

Esto no guarda pi completo en la base de datos. Solo guarda muestras de auditoria, hashes y metadatos. La version actual recalcula el segmento porque los rangos del MVP son pequenos; mas adelante se puede reemplazar por pruebas mas compactas sin cambiar la interfaz de auditoria.

## Algoritmo De Pi

El MVP usa `bbp_hex_v1`, basado en la formula Bailey-Borwein-Plouffe:

```text
pi = sum(k=0..infinito) 1/16^k * (
  4/(8k+1) - 2/(8k+4) - 1/(8k+5) - 1/(8k+6)
)
```

Pi en hexadecimal empieza asi:

```text
3.243F6A8885A308D313198A2E...
```

Por eso el rango `1..5` devuelve:

```text
243F6
```

BBP permite calcular un digito hexadecimal en una posicion remota sin calcular todos los digitos anteriores. Esa propiedad hace que sea una mejor base para Picoin que los decimales tradicionales.

Para que el MVP local sea rapido, `max_pi_position` esta en `10000`. Se puede subir cuando optimicemos el calculo BBP o movamos el trabajo pesado a una implementacion mas eficiente.

## Persistencia

SQLite usa estas tablas:

```text
miners
validators
tasks
commitments
validation_jobs
validation_votes
submissions
blocks
retroactive_audits
science_stake_accounts
science_jobs
science_reward_reserve
science_reserve_governance
scientific_development_treasury
scientific_development_treasury_epochs
scientific_development_treasury_claims
science_events
protocol_params
retarget_events
rewards
balances
ledger_entries
penalties
rejected_submissions
```

La separacion permite auditar tareas, intentos, bloques aceptados, recompensas y castigos sin mezclar conceptos.

## Flujo Completo

1. Inicia el servidor:

```powershell
uvicorn app.main:app --reload
```

2. Consulta protocolo:

```powershell
curl http://127.0.0.1:8000/protocol
```

3. Ejecuta un minero:

```powershell
python -m miner.client register --name alice
python -m miner.client mine --once
```

4. Ejecuta tres validadores externos:

```powershell
python -m validator.client --identity validator1.json register --name val1
python -m validator.client --identity validator1.json validate --once
python -m validator.client --identity validator2.json register --name val2
python -m validator.client --identity validator2.json validate --once
python -m validator.client --identity validator3.json register --name val3
python -m validator.client --identity validator3.json validate --once
```

5. Consulta bloques:

```powershell
curl http://127.0.0.1:8000/blocks
```

6. Verifica la cadena local:

```powershell
curl http://127.0.0.1:8000/blocks/verify
```

7. Consulta estadisticas:

```powershell
curl http://127.0.0.1:8000/stats
```

8. Consulta salud operativa:

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/node/status
curl "http://127.0.0.1:8000/events?limit=20"
```

9. Consulta performance:

```powershell
curl http://127.0.0.1:8000/stats/performance
```

10. Consulta historial de parametros:

```powershell
curl http://127.0.0.1:8000/protocol/history
```

11. Consulta dificultad:

```powershell
curl http://127.0.0.1:8000/difficulty
curl http://127.0.0.1:8000/difficulty/preview
curl http://127.0.0.1:8000/difficulty/history
```

12. Consulta economia y auditoria:

```powershell
curl http://127.0.0.1:8000/balances
curl http://127.0.0.1:8000/ledger
curl http://127.0.0.1:8000/audit/summary
curl http://127.0.0.1:8000/audit/full
curl http://127.0.0.1:8000/audit/retroactive
curl -X POST "http://127.0.0.1:8000/audit/retroactive/run?sample_multiplier=2"
curl "http://127.0.0.1:8000/validators?eligible_only=true"
```

## Distributed Testnet v0.18

Picoin ahora incluye una base L1 para testnet distribuida multi-nodo. Esta fase agrega networking, peers, mempool, wallets y transacciones firmadas sin activar IA real, marketplace L2, bridges, zk proofs ni smart contracts complejos.

Componentes nuevos:

- `network_peers`: registro de peers con `node_id`, `peer_address`, tipo, version, `network_id`, `chain_id` y `genesis_hash`.
- `mempool_transactions`: transacciones firmadas Ed25519 con nonce, fee, payload canonico, estado y expiracion.
- `network_block_headers`: cola de headers/bloques propagados para replay distribuido.
- `network_sync_events`: bitacora de peers, heartbeats, tx y bloques recibidos.
- `consensus_block_proposals`: propuestas de bloque propagables.
- `consensus_votes`: votos Ed25519 de validadores por propuesta.
- `consensus_finalizations`: finalizaciones con quorum e importacion/replay.
- Wallets con direcciones `PI...` derivadas de la clave publica Ed25519.

Endpoints principales:

```powershell
curl http://127.0.0.1:8000/node/identity
curl http://127.0.0.1:8000/node/peers
curl http://127.0.0.1:8000/node/sync-status
curl "http://127.0.0.1:8000/node/sync/blocks?from_height=0"
curl http://127.0.0.1:8000/mempool
curl http://127.0.0.1:8000/consensus/status
curl http://127.0.0.1:8000/consensus/proposals
```

Registrar peer:

```powershell
curl -X POST http://127.0.0.1:8000/node/peers/register `
  -H "Content-Type: application/json" `
  -d "{\"node_id\":\"validator-1\",\"peer_address\":\"http://validator-1:8000\",\"peer_type\":\"validator\",\"protocol_version\":\"0.18\",\"network_id\":\"local\",\"chain_id\":\"picoin-local-testnet\",\"genesis_hash\":\"0000000000000000000000000000000000000000000000000000000000000000\"}"
```

CLI distribuida:

```powershell
python -m picoin node peers
python -m picoin node sync-status
python -m picoin node doctor
python -m picoin node reconcile
python -m picoin node reconcile --peer http://peer-node:8000
python -m picoin node catch-up --peer http://peer-node:8000
python -m picoin node checkpoint create --height 10
python -m picoin node checkpoint latest
python -m picoin node checkpoint verify --height 10
python -m picoin node checkpoint export --height 10 --output data/checkpoint-10.json
python -m picoin node checkpoint import --file data/checkpoint-10.json --source bootstrap-node
python -m picoin node checkpoint activate --snapshot-hash <snapshot_hash>
python -m picoin node checkpoint apply --snapshot-hash <snapshot_hash>
python -m picoin node checkpoint imports
python -m picoin wallet create --name alice --output data/alice-wallet.json
python -m picoin wallet balance --address PI...
python -m picoin tx send --wallet data/alice-wallet.json --to PI... --amount 1.5 --nonce 1 --fee 0.01
python -m picoin tx send --wallet data/alice-wallet.json --type stake --amount 3141.6 --nonce 2 --fee 0.01
python -m picoin tx send --wallet data/alice-wallet.json --type unstake --nonce 3 --fee 0.01
python -m picoin tx send --wallet data/alice-wallet.json --type science_job_create --nonce 4 --fee 0.01 --payload "{\"job_type\":\"ai_inference\",\"metadata_hash\":\"meta\",\"storage_pointer\":\"ipfs://job\",\"max_compute_units\":0,\"reward_per_compute_unit\":0,\"max_reward\":0}"
python -m picoin tx send --wallet data/signer-one.json --type governance_action --nonce 1 --fee 0.01 --payload "{\"scope\":\"science_reserve\",\"action\":\"propose_activation\"}"
python -m picoin tx send --wallet data/signer-two.json --type governance_action --nonce 1 --fee 0.01 --payload "{\"scope\":\"science_reserve\",\"action\":\"approve_activation\"}"
python -m picoin tx send --wallet data/signer-one.json --type governance_action --nonce 2 --fee 0.01 --payload "{\"scope\":\"science_reserve\",\"action\":\"execute_activation\"}"
python -m picoin tx send --wallet data/owner.json --type treasury_claim --nonce 1 --fee 0.01 --payload "{\"claim_to\":\"PI_TREASURY_WALLET\"}"
python -m picoin consensus status
python -m picoin consensus proposals
python -m picoin consensus votes --proposal-id ...
python -m picoin consensus propose-block --block data/block.json --proposer miner-node-1
python -m picoin consensus vote --proposal-id ... --identity data/testnet/identities/validator-one.json
python -m picoin consensus finalize --proposal-id ...
python -m picoin consensus replay
```

Consenso distribuido v0.18:

1. Un nodo minero propone automaticamente el bloque cuando el flujo de mining alcanza quorum local, y tambien puede proponer manualmente con `POST /consensus/proposals`.
2. Cada validador firma un voto Ed25519 sobre `proposal_id`, `block_hash`, `height`, decision y razon.
3. Los votos se propagan por gossip HTTP best-effort a peers conectados.
4. Si hay dos propuestas para la misma altura/padre, el fork-choice elige por peso de aprobaciones ponderado por reputacion/stake, luego peso de rechazos, votos planos, creacion mas antigua y `block_hash` lexicografico.
5. Un validador no puede votar dos propuestas competidoras del mismo fork.
6. Cuando hay `required_validator_approvals = 3`, solo la propuesta ganadora del fork-choice se finaliza.
7. El replay canonico valida `previous_hash`, recalcula `block_hash`, rechaza rangos/resultados duplicados y crea el contexto minimo faltante (`miner`, `task`) antes de insertar el bloque.
8. Al importar, aplica contabilidad deterministica: reward del minero, pool de validadores, Science Compute Reserve 20% y Scientific Development Treasury 3%.

Gossip automatico:

- `POST /tx/submit` propaga a peers usando `/tx/receive`.
- `POST /consensus/proposals` propaga a peers usando `?gossip=false` para evitar loops.
- `POST /consensus/proposals/{proposal_id}/vote` propaga votos usando `?gossip=false`.
- Cuando `/tasks/submit`, `/tasks/reveal` o `/validation/results` producen un bloque aceptado, la API propaga automaticamente una propuesta de consenso con el bloque completo.

Variables utiles:

```text
PICOIN_GOSSIP_ENABLED=1
PICOIN_GOSSIP_TIMEOUT_SECONDS=2.0
PICOIN_GOSSIP_MAX_PEERS=16
```

Reconciliacion:

- `POST /node/reconcile` consulta peers conectados y fusiona identidad, peers, mempool y propuestas.
- `POST /node/reconcile?peer_address=http://peer:8000` fuerza reconciliacion contra un peer especifico.
- La reconciliacion es pull-based y complementa el gossip: si un mensaje no llego, el nodo puede recuperar estado despues.

Esta version ya mueve el protocolo hacia propuesta/voto/finalizacion multi-nodo con gossip, reconciliacion y fork-choice ponderado por reputacion/stake. La siguiente mejora es gossip WebSocket persistente, jobs de reconciliacion periodica en background y fork-choice con slashing/finality mas estricta.

Docker testnet:

```powershell
docker compose up
```

El `docker-compose.yml` levanta:

- 1 bootstrap node
- 3 miner nodes
- 3 validator nodes
- 1 auditor node

Cada nodo usa su propio volumen SQLite. La capa actual sincroniza identidad, peers, mempool, propuestas, votos, finalizaciones y replay canonico inicial.

## Pruebas

```powershell
pytest
```

Si quieres reiniciar la demo desde bloque 1:

```powershell
python -m app.tools.reset_db
```

## Ruta a Mainnet

Estado actual: Picoin ya tiene una L1 experimental con Proof of Pi, validadores, auditorias, economics 67/20/10/3, treasury timelocked, Science Reserve locked, mempool, wallets, peers, gossip, propuestas, votos, finalizacion y replay canonico inicial. Todavia no esta listo para mainnet con valor real.

Fase 1 - Testnet distribuida estable:

- Ejecutar nodos reales en maquinas distintas, no solo Docker local.
- Reconciliacion periodica automatica en background.
- Gossip WebSocket persistente con backoff, deduplicacion y limites por peer.
- Persistir reputacion de peers y desconectar peers con spam o datos invalidos.
- Exportar snapshots de cadena y restore deterministico.

Fase 2 - Consenso y seguridad:

- Definir fork-choice final: peso por stake/reputacion, edad, finality y penalizaciones.
- Slashing real para doble voto, firma invalida, voto sobre bloque invalido y fraude confirmado.
- Separar claramente bloque propuesto, bloque pre-finalizado y bloque final.
- Agregar finality delay y ventana de disputa/auditoria antes de considerar irreversible.
- Simular particiones de red, forks, nodos maliciosos y validadores offline.

Fase 3 - Transacciones y estado:

- Ejecutar transfers firmados desde mempool dentro de bloques.
- Nonce/balance enforcement por cuenta.
- Fees reales y politica anti-spam.
- Incluir Merkle root de transacciones por bloque.
- Rebuild completo de estado desde genesis usando solo bloques.

Fase 4 - Nodos y operacion:

- CLI de operador: backup, restore, snapshot, peer ban/unban, metrics.
- Observabilidad: Prometheus/logs estructurados/alertas.
- Configuracion de testnet publica: seeds, chain_id, genesis, puertos, dominios.
- Binaries o Docker images versionadas.
- Upgrade/migration plan por version de protocolo.

Fase 5 - Auditoria economica/protocolo:

- Revisar supply total, rewards, treasury, reserve y validator rewards bajo replay completo.
- Congelar parametros iniciales de mainnet: reward, intervalo bloque, quorum, epoch, slashing, faucet off.
- Auditoria externa de criptografia, firmas, replay, consenso y contabilidad.
- Bug bounty en testnet publica.

Fase 6 - Science L1 mainnet-ready:

- Mantener Science Reserve locked hasta activar L2 por gobernanza timelocked.
- Auditar staking cientifico, jobs abstractos, quotas y eventos L2-ready.
- Definir condiciones exactas para activar marketplace L2 en el futuro.
- No activar pagos de compute hasta tener workers/verificacion L2 probados.

Fase 7 - Mainnet candidate:

- Testnet publica con uptime sostenido.
- Reset final de genesis mainnet y chain_id mainnet.
- Desactivar faucet y endpoints de demo.
- Multisig/governance real para treasury y reserve.
- Publicar spec del protocolo, explorer y guia de nodo.

Mainnet deberia esperar hasta que un nodo nuevo pueda descargar/reconciliar bloques, reconstruir balances desde cero, validar consensus/finality, rechazar forks invalidos y sobrevivir pruebas con varios nodos desconectandose/reconectandose sin intervencion manual.
