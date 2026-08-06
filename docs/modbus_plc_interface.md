# MELSEC iQ-R Modbus/TCP interface

## Roles and scope

- The **Mitsubishi MELSEC iQ-R PLC is the Modbus/TCP server**.
- This application is the **single Modbus/TCP client** and owns one connection in a dedicated worker thread.
- The existing PLC-to-camera hardware trigger and CONTEC DIO trigger index remain unchanged. Modbus does **not** trigger cameras, image capture, or inference.
- The hardwired CONTEC OK/NG output remains the timing-critical reject output. Modbus publishes detailed results and application state.

## Connection configuration

Edit [configs/modbus.yaml](../configs/modbus.yaml).

```yaml
connection:
  enabled: true
  host: "192.168.1.10"
  port: 502
  device_id: 1
```

The implementation uses synchronous `pymodbus==3.7.4` only in the dedicated `ModbusWorker` thread. UI, camera threads, IC4 callbacks, inference, and DIO callbacks never own or access the Modbus client.

### Addressing rule — important

**All addresses in this project are zero-based Modbus holding-register offsets.** The application reads offset 100 with FC03 and writes offset 120 with FC16. Do not add one to these values in `configs/modbus.yaml`.

A PLC-side mapping is configured separately in GX Works3. An example only:

| Modbus zero-based offset | Example GX Works3 device |
| --- | --- |
| 100 | D1000 |
| 101 | D1001 |
| 120 | D1020 |

The final mapping depends on the actual iQ-R CPU or Ethernet module configuration. Confirm the configured Modbus address base in GX Works3 before commissioning.

## Register blocks

- PLC to PC: FC03, offsets **100–119** (20 words)
- PC to PLC: FC16, offsets **120–149** (30 words)
- Unit/device ID: `1` by default
- The complete machine-readable map is in [modbus/modbus_register.yaml](../modbus/modbus_register.yaml).

### PLC to PC

| Offset | Name | Meaning |
| ---: | --- | --- |
|100|`PLC_CONTROL_WORD`|Control flags below|
|101–102|`REQUESTED_RECIPE_ID`, `REQUESTED_RECIPE_REV`|Requested numeric recipe|
|103–104|`RECIPE_CHANGE_SEQ_LO/HI`|Recipe request sequence|
|105|`LINE_SPEED_X100`|Line speed ×100|
|106|`PLC_LINE_STATE`|0 stopped, 1 setup, 2 automatic, 3 changeover, 4 fault, 5 maintenance|
|107–108|`PLC_TRIGGER_SEQ_LO/HI`|PLC diagnostic trigger sequence|
|109–110|`RESULT_ACK_SEQ_LO/HI`|Acknowledged PC result sequence|
|111|`PLC_HEARTBEAT`|PLC counter incremented at least once every 3 s|
|112|`PLC_FAULT_CODE`|PLC diagnostic fault|
|113–114|`PLC_COMMAND_CODE`, `PLC_COMMAND_SEQ`|Command and execute-once sequence|
|115|`PRODUCT_LENGTH_MM`|Optional product length|
|116–119|Reserved|0|

`PLC_CONTROL_WORD` bits: 0 inspection enable, 1 bypass, 2 save training images, 3 maintenance, 4 manual inspection, 5 production running; bits 6–15 are reserved.

PLC command codes: 0 none, 1 reset vision error, 2 reset counters, 3 reload active recipe, 4 reload models, 5 save diagnostic images, 6 restart camera acquisition. A command executes once only when `PLC_COMMAND_SEQ` changes.

### PC to PLC

| Offset | Name | Meaning |
| ---: | --- | --- |
|120|`PC_STATUS_WORD`|Status flags below|
|121–124|Active recipe ID/revision and recipe acknowledgement sequence|
|125–126|`RESULT_SEQ_LO/HI`|Result commit sequence|
|127|`RESULT_CODE`|0 no valid, 1 OK, 2 NG, 3 system error, 4 bypassed, 5 timeout, 6 recipe mismatch, 7 missing frame, 8 trigger sync error|
|128|`NG_CAMERA_MASK`|Bit 0–3 map cameras 0–3|
|129–133|Fused and camera scores ×10000|Saturated `uint16`|
|134|`INFERENCE_TIME_MS`|Saturated millisecond duration|
|135–136|Camera/model ready masks|Bit 0–3 map cameras/models|
|137|`PC_HEARTBEAT`|PC counter, increments every second|
|138–139|Vision error/warning|Enums below|
|140|`INFERENCE_QUEUE_DEPTH`|Current + queued unacknowledged result count|
|141–146|Dropped/missing/processed 32-bit counters|Low word first|
|147|`COMMAND_ACK_SEQ`|Last completed PLC command sequence|
|148–149|Reserved|0|

`PC_STATUS_WORD` bits: 0 alive, 1 all cameras ready, 2 all models ready, 3 recipe loaded, 4 inspection ready, 5 busy, 6 result pending, 7 warning, 8 fault, 9 bypass, 10 training image collection, 11 communication degraded; 12–15 reserved.

## Data conversion

All words are unsigned 16-bit. There are no Modbus floating-point registers.

- 32-bit values are **low word first**: `value = low_word | (high_word << 16)`.
- Scores use `score × 10000`, saturating to `0…65535`.
- Line speed uses `LINE_SPEED_X100 / 100`.
- Counters safely wrap at 32 bits; PC and PLC heartbeat words wrap at 65535.

## Recipe handshake

1. PLC writes recipe ID, revision, and a new 32-bit recipe sequence.
2. PC detects the **sequence change**; an unchanged ID does not reload.
3. PC clears recipe and inspection readiness, validates the mapped recipe, then queues model loading in the existing inference thread.
4. The recipe definition maps numeric ID to name, revision, models file, threshold file, camera ROIs, and optional product parameters in [configs/recipes.yaml](../configs/recipes.yaml).
5. On success, PC writes active recipe ID/revision and mirrors the sequence in `RECIPE_ACK_SEQ`; only then is recipe/inspection readiness set.
6. Errors 110–113 identify unavailable, revision-mismatched, invalid, or model-load-failed recipes.

The default recipe preserves existing files (`configs/model.yaml` and `configs/thresholds.yaml`). Add additional numeric IDs under `recipes`.

## Result handshake

Each inspection creates one `InspectionResult`. The single result publisher sends the same object to DIO, Modbus shared state, UI, and structured logs.

1. PC queues result data without replacing an unacknowledged result.
2. The Modbus worker writes result data first.
3. It writes `RESULT_SEQ_LO/HI` last, then sets the pending status bit.
4. PLC copies `RESULT_SEQ` to `RESULT_ACK_SEQ`.
5. PC clears pending only if both 32-bit values match.

One additional result is retained in a bounded queue. Further results are not silently overwritten: error 130 is set and the dropped trigger counter increments.

## Heartbeat, reconnect, and fail-safe operation

- PC increments `PC_HEARTBEAT` every second.
- PLC must change `PLC_HEARTBEAT` within `heartbeat_timeout_ms` (default 3000 ms).
- Three consecutive Modbus failures set communication-degraded state and error 100.
- A heartbeat timeout sets error 101.
- Reconnects are automatic and logs are rate-limited.
- On Modbus degradation, inspection-ready clears and the hardwired DIO output is set to fail-safe NG. Readiness returns only after connection and a valid PLC heartbeat.

Error codes: 0 none, 100 connection unavailable, 101 heartbeat timeout, 102 invalid response, 110 recipe missing, 111 revision mismatch, 112 invalid recipe, 113 model failed, 120 camera unavailable, 121 missing frame, 122 sync failure, 130 result queue full, 131 acknowledgement timeout, 140 internal inspection exception.

Warning codes: 0 none, 200 communication recovered, 201 high inference queue depth, 202 inference slow, 203 acknowledgement delayed, 204 training-image collection enabled.

## Operating modes

### Modbus enabled

1. Install dependencies from [requirements.txt](../requirements.txt).
2. Set `connection.enabled: true`, PLC IP address, port, and device ID in [configs/modbus.yaml](../configs/modbus.yaml).
3. Configure the matching holding-register blocks in the iQ-R Modbus/TCP server.
4. Start the normal application with `python run.py`.
5. Begin PLC heartbeat and request the numeric recipe before enabling inspection.

### Development without Modbus

Set `connection.enabled: false`. The worker is not started, existing mock/CONTEC DIO and camera/inference paths remain available, and PLC control bits are ignored.

### Local PLC simulator

After installing `pymodbus==3.7.4`, run:

```text
python tools/plc_simulator.py --port 5020
```

Set the PC host to `127.0.0.1`, port to `5020`, and enable Modbus. The interactive commands can change recipes, line speed, inspection enable/bypass, PLC heartbeat, commands, acknowledgement, and display PC output.

## Real iQ-R commissioning checklist

1. Configure the iQ-R CPU/Ethernet module as a Modbus/TCP server on port 502 (or update the PC config).
2. Map 20 read/write D registers for offsets 100–119 and 30 for offsets 120–149.
3. Confirm offset base and device ID using a Modbus client before enabling reject hardware.
4. Verify word order with a known recipe sequence such as `0x00010002` (low=`2`, high=`1`).
5. Test heartbeat, recipe acknowledgement, result acknowledgement, disconnected fail-safe behavior, and DIO reject timing with production safeguards in place.
