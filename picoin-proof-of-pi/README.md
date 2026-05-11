# picoin-proof-of-pi

MVP funcional de **Proof of Pi**. Un coordinador asigna rangos pequenos de digitos hexadecimales de pi, un minero calcula el segmento con BBP, el validador recalcula de forma independiente y el servidor registra bloques aceptados con recompensa simulada.

Este proyecto no implementa una blockchain completa. Usa una cadena local de bloques aceptados con `previous_hash` y `block_hash` para preparar una evolucion futura.

## Protocolo v0.15

Parametros actuales:

```text
protocol_version = 0.15
network_id = local
algorithm = bbp_hex_v1
validation_mode = external_commit_reveal
required_validator_approvals = 2
range_assignment_mode = pseudo_random
max_pi_position = 10000
range_assignment_max_attempts = 512
segment_size = 64
sample_count = 8
task_expiration_seconds = 600
max_active_tasks_per_miner = 1
genesis_supply = 3141600.0
base_reward = 3.1416
difficulty = 1.0
reward_per_block = 3.1416
validator_reward_percent = 10%
validator_reward_pool_per_block = 0.31416
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
```

El endpoint `GET /protocol` devuelve estos valores para que mineros y validadores sepan que reglas estan activas. Desde v0.15 estos parametros viven en SQLite, en `protocol_params`, y pueden cambiar automaticamente por epocas. `network_id` viene de `PICOIN_NETWORK`; por defecto es `local`.

La dificultad se calcula con una formula simple y auditable:

```text
difficulty =
  (segment_size / 64)
  * (sample_count / 8)
  * (log10(max_pi_position) / log10(10000))

miner_reward_per_block = base_reward
validator_reward_pool_per_block = base_reward * 0.10
```

La dificultad regula el trabajo, no multiplica la emision. La recompensa del minero queda fija en `3.1416` por bloque aceptado. Adicionalmente, los validadores que aprobaron el bloque reciben una emision extra total de `0.31416`, repartida en partes iguales.

El genesis acredita `3,141,600` monedas a la cuenta `genesis`. Cada bloque aceptado acredita `3.1416` monedas al minero ganador y `0.31416` monedas adicionales repartidas entre validadores aprobadores en el ledger local.

Cada bloque guarda la dificultad y recompensa usadas al momento de aceptarse.
Las tareas y bloques tambien guardan `protocol_params_id`, asi un retarget no cambia las reglas de una tarea que ya estaba asignada.

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
    services/     Tareas, bloques, recompensas, penalizaciones
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

Desde v0.15, el nodo sirve un panel web operativo en:

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
- Estado operativo del nodo, readiness de mineria y eventos recientes.

## CLI Nodo Local

Desde v0.13, Picoin incluye un CLI local unificado:

```powershell
.\.venv\Scripts\python.exe -m picoin --version
.\.venv\Scripts\python.exe -m picoin node start --reload
.\.venv\Scripts\python.exe -m picoin node status
.\.venv\Scripts\python.exe -m picoin node audit
.\.venv\Scripts\python.exe -m picoin node protocol
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
```

Config local opcional:

```powershell
Copy-Item .env.example .env
```

Variables soportadas:

```text
PICOIN_NETWORK=local
PICOIN_HOST=127.0.0.1
PICOIN_PORT=8000
PICOIN_SERVER=http://127.0.0.1:8000
```

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
- 2 identidades demo de validadores
- faucet local para el minero
- servidor FastAPI local
- ciclo completo: minar, revelar muestras, votar con 2 validadores y aceptar bloque por quorum

### Flujo automatico completo

Este comando resetea, crea identidades, levanta el servidor en segundo plano, mina un bloque, ejecuta los 2 validadores y apaga el servidor al terminar:

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
```

El primer validador deja el job en `validation_pending`; el segundo completa el quorum y el coordinador acepta el bloque.

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
  "required_approvals": 2,
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

- suma total de balances contra `genesis_supply + block_rewards`
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

- No hay consenso distribuido.
- No hay red P2P.
- No hay wallet transferible entre usuarios; el ledger solo registra emision, recompensas, stake simulado y slashing.
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

Si hay menos validadores elegibles, usa todos los disponibles. Esto mantiene velocidad para testnet local, pero reduce concentracion cuando hay mas validadores que el quorum minimo.

## Economia MVP

Reglas actuales:

```text
genesis_supply = 3141600.0
block_emission = 3.1416
validator_reward_pool = 0.31416
total_minted_per_accepted_block = 3.45576
validator_initial_stake = 31.416
validator_slash_invalid_signature = 3.1416
```

El genesis queda registrado en `ledger_entries` con `block_height = 0`. Cada bloque aceptado crea un movimiento `block_reward` para el minero y movimientos `validator_reward` para los validadores que aprobaron el bloque. Los registros de stake y slashing tambien quedan en el ledger.

Politica monetaria auditada en v0.11:

```text
expected_total_balances =
  genesis_supply
  + accepted_block_rewards
  + validator_rewards
```

`genesis`, faucet, stake y slashing son movimientos internos. Las recompensas de minero y validador son emision nueva. Por eso el total de balances puede crecer con cada bloque aceptado, mientras el endpoint `/audit/full` verifica que ese crecimiento coincida exactamente con la suma de recompensas registradas.

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

4. Ejecuta un validador externo:

```powershell
python -m validator.client register --name val1
python -m validator.client validate --once
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
curl "http://127.0.0.1:8000/validators?eligible_only=true"
```

## Pruebas

```powershell
pytest
```

Si quieres reiniciar la demo desde bloque 1:

```powershell
python -m app.tools.reset_db
```

## Siguiente Evolucion

- Ajustar el tamano de epoca y objetivo de bloque con benchmarks reales.
- Agregar protecciones de entorno para desactivar faucet fuera de testnet.
- Crear scripts equivalentes para Linux/macOS.
- Evolucionar la lista local de bloques hacia consenso blockchain.
