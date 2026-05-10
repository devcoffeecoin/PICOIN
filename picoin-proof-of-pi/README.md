# picoin-proof-of-pi

MVP funcional de **Proof of Pi**. Un coordinador asigna rangos pequenos de digitos hexadecimales de pi, un minero calcula el segmento con BBP, el validador recalcula de forma independiente y el servidor registra bloques aceptados con recompensa simulada.

Este proyecto no implementa una blockchain completa. Usa una cadena local de bloques aceptados con `previous_hash` y `block_hash` para preparar una evolucion futura.

## Protocolo v0.8

Parametros actuales:

```text
protocol_version = 0.8
algorithm = bbp_hex_v1
validation_mode = external_commit_reveal
required_validator_approvals = 1
range_assignment_mode = pseudo_random
max_pi_position = 10000
range_assignment_max_attempts = 512
segment_size = 64
sample_count = 8
task_expiration_seconds = 600
max_active_tasks_per_miner = 1
base_reward = 3.14159
difficulty = 1.0
reward_per_block = 3.14159
penalty_invalid_result = 1
penalty_duplicate = 3
penalty_invalid_signature = 5
cooldown_after_rejections = 3
cooldown_seconds = 300
```

El endpoint `GET /protocol` devuelve estos valores para que mineros y validadores sepan que reglas estan activas. Desde v0.8 estos parametros viven en SQLite, en `protocol_params`, para que luego podamos cambiar dificultad sin reescribir el codigo.

La dificultad se calcula con una formula simple y auditable:

```text
difficulty =
  (segment_size / 64)
  * (sample_count / 8)
  * (log10(max_pi_position) / log10(10000))

reward_per_block = base_reward * difficulty
```

Cada bloque guarda la dificultad y recompensa usadas al momento de aceptarse.

## Arquitectura

```text
picoin-proof-of-pi/
  app/
    api/          Endpoints REST FastAPI
    core/         Configuracion, hashing SHA-256 y calculo BBP de pi
    db/           SQLite y migraciones simples
    models/       Schemas Pydantic
    services/     Tareas, bloques, recompensas, penalizaciones
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
uvicorn app.main:app --reload
```

La API queda en:

- `http://127.0.0.1:8000`
- Docs interactivas: `http://127.0.0.1:8000/docs`

La base SQLite se crea automaticamente en `data/picoin.sqlite3`.

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
11. Queda esperando aprobacion de un validador externo.
12. Cuando el validador aprueba, el servidor registra el bloque.

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

## Endpoints

### `GET /protocol`

Devuelve parametros activos del protocolo, incluyendo `base_reward`, `difficulty` y `reward_per_block`.

### `GET /protocol/history`

Devuelve el historial de parametros de protocolo guardados en SQLite. En este MVP solo hay un set activo por defecto, pero la tabla ya prepara el camino para ajustes de dificultad por epoca.

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

### `GET /validation/jobs?validator_id=...`

Entrega el siguiente job pendiente a un validador.

### `POST /validation/results`

Recibe el resultado firmado del validador.

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
- Commit-reveal con `result_hash` y `merkle_root`.
- Merkle proofs para cada muestra revelada.
- Recalculo independiente por validador externo.
- Muestras deterministicas generadas despues del commit.
- Tareas con expiracion.
- Maximo de una tarea activa por minero.
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

Limites intencionales:

- No hay consenso distribuido.
- No hay red P2P.
- No hay wallet real ni token transferible.
- La validacion v0.8 es probabilistica por muestras, no una prueba criptografica completa del calculo entero.

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

Si todas las muestras pasan, el validador firma una aprobacion. Solo entonces el coordinador acepta el bloque. Esto evita guardar pi, evita transmitir el segmento completo y separa el rol de validacion del rol de coordinacion.

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
submissions
blocks
protocol_params
rewards
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

8. Consulta performance:

```powershell
curl http://127.0.0.1:8000/stats/performance
```

9. Consulta historial de parametros:

```powershell
curl http://127.0.0.1:8000/protocol/history
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

- Ajuste automatico por epocas segun tiempo promedio de bloque.
- Politica para subir o bajar `segment_size`, `sample_count` y `max_pi_position`.
- Agregar slashing/staking simulado.
- Mayor quorum de validadores independientes.
- Evolucionar la lista local de bloques hacia consenso blockchain.
