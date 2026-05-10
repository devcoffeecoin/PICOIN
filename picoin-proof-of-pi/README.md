# picoin-proof-of-pi

MVP funcional de **Proof of Pi**: un coordinador asigna rangos pequeños de digitos hexadecimales de pi, un minero calcula el segmento con BBP, el validador recalcula de forma independiente y el servidor registra bloques aceptados con recompensa simulada.

Este proyecto no implementa una blockchain completa. Usa una cadena simple de bloques aceptados con `previous_hash` y `block_hash` para preparar la arquitectura hacia una blockchain futura.

## Arquitectura

```text
picoin-proof-of-pi/
  app/
    api/          Endpoints REST FastAPI
    core/         Configuracion, hashing SHA-256 y calculo BBP de pi
    db/           Inicializacion SQLite
    models/       Schemas Pydantic
    services/     Logica de minado, bloques y recompensas
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

## Correr el servidor coordinador

```powershell
uvicorn app.main:app --reload
```

La API quedara en:

- `http://127.0.0.1:8000`
- Docs interactivas: `http://127.0.0.1:8000/docs`

La base de datos SQLite se crea automaticamente en `data/picoin.sqlite3`.

## Correr un minero

En otra terminal:

```powershell
cd C:\Users\LOQ\Documents\personal\PROYECTOS\PICOIN\picoin-proof-of-pi
.\.venv\Scripts\Activate.ps1
python -m miner.client --name alice
```

El minero:

1. Registra un minero si no se pasa `--miner-id`.
2. Pide una tarea a `GET /tasks/next`.
3. Calcula el segmento hexadecimal asignado de pi.
4. Genera `result_hash` con SHA-256.
5. Firma de forma simple el submit con `sha256(miner_id:task_id:result_hash)`.
6. Envia el resultado a `POST /tasks/submit`.
7. Muestra si el bloque fue aceptado o rechazado.

Para reutilizar un minero existente:

```powershell
python -m miner.client --miner-id miner_xxxxxxxxxxxxxxxx
```

## Endpoints

### `POST /miners/register`

Registra un minero.

```json
{
  "name": "alice",
  "public_key": "simple:alice"
}
```

### `GET /tasks/next?miner_id=...`

Asigna el siguiente rango de posiciones hexadecimales de pi.

### `POST /tasks/submit`

Recibe el segmento calculado por el minero.

```json
{
  "task_id": "task_xxxxxxxxxxxxxxxx",
  "miner_id": "miner_xxxxxxxxxxxxxxxx",
  "result_hash": "64_hex_chars",
  "segment": "243F6A8885",
  "signature": "64_hex_chars"
}
```

### `GET /blocks`

Lista bloques aceptados.

### `GET /blocks/{height}`

Consulta un bloque por altura.

### `GET /miners/{miner_id}`

Consulta datos y recompensas simuladas de un minero.

### `GET /stats`

Devuelve estadisticas globales del MVP.

## Bloques

Cada bloque aceptado contiene:

- `height`
- `previous_hash`
- `miner_id`
- `range_start`
- `range_end`
- `algorithm`
- `result_hash`
- `samples`
- `timestamp`
- `block_hash`
- `reward`

## Seguridad MVP

Implementado:

- SHA-256 para resultados y bloques.
- Encadenamiento por `previous_hash`.
- Identificacion de minero por `miner_id`.
- Firma simple opcional del submit.
- Recalculo independiente del rango por el validador.
- Muestras deterministicas del segmento validado.
- Rechazo de tareas ya enviadas.
- Rechazo de `result_hash` duplicado.
- Restricciones SQLite para evitar doble bloque por tarea.

Limites intencionales:

- No hay consenso distribuido.
- No hay red P2P.
- No hay wallet real ni token transferible.
- La firma simple no reemplaza criptografia asimetrica real.
- El calculo de pi usa BBP hexadecimal, adecuado para calcular posiciones lejanas sin recorrer todos los digitos anteriores.

## Algoritmo de pi

El MVP arranca con `bbp_hex_v1`, basado en la formula Bailey-Borwein-Plouffe:

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

BBP es una mejor base para Picoin que los decimales tradicionales porque permite calcular un digito hexadecimal en una posicion remota sin calcular todos los digitos anteriores. Esta aislado en `app/core/pi.py` para poder ajustar precision, introducir auditorias por muestras o agregar otro algoritmo versionado mas adelante.

El algoritmo decimal anterior queda como referencia interna (`machin_decimal_v1`), pero el algoritmo base del coordinador es:

```text
bbp_hex_v1
```

## Flujo completo de ejemplo

1. Inicia el servidor:

```powershell
uvicorn app.main:app --reload
```

2. Ejecuta un minero:

```powershell
python -m miner.client --name alice
```

3. Consulta bloques:

```powershell
curl http://127.0.0.1:8000/blocks
```

4. Consulta estadisticas:

```powershell
curl http://127.0.0.1:8000/stats
```

## Pruebas

```powershell
pytest
```

## Siguiente evolucion sugerida

- Sustituir firma simple por claves publicas reales.
- Separar repositorio de tareas pendientes y mempool.
- Agregar dificultad o scoring por rango.
- Agregar expiracion y reasignacion de tareas.
- Introducir nodos validadores independientes.
- Evolucionar la lista de bloques aceptados hacia consenso blockchain.
