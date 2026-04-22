# B Project: Workflow Service + Processing Function

This folder contains the B member's independent part of Creator Cloud Studio.

## Owned Components

- `Workflow Service` container: `services/workflow/app.py`
- `Processing Function`: `functions/processing/handler.py`

## Contract Source

Use `../API_CONTRACT.md` together with `API_CONTRACT_APPEND.md` as the contract source.

Important B rules:

- Workflow `POST /submissions` accepts JSON with `title`, `description`, `posterFilename`, and optionally `posterImage` and `posterMimeType`.
- Workflow only validates JSON shape and string types. It must not reject empty strings.
- Workflow must not decode `posterImage`, validate base64, validate image size, or validate MIME enum. Those checks belong to Data Service.
- Workflow must call Data Service `POST /submissions` and use the `id` returned by Data Service.
- Workflow `GET /submissions/{id}` must transparently return Data Service fields, including `posterMimeType` and `posterSize`.
- Workflow may proxy `GET /submissions/{id}/poster` to Data Service as a byte-for-byte passthrough endpoint.
- Workflow must trigger Submission Event Function asynchronously with `{"submissionId": "<id>"}`.
- Workflow must not directly call Processing Function or Result Update Function.
- Processing Function is the only owner of the business judgement rules.
- Processing must return Lambda v2 Response Envelope.
- Processing must call Result Update Function and must not `PATCH` Data Service directly.
- Processing Function must not read `posterImage` and must not call `/submissions/{id}/poster`.

## Rule Logic

Processing applies rules in this exact order:

1. Missing required field after `strip()` means `INCOMPLETE`.
2. If all fields exist but description length is less than 30 or filename is not `.jpg`, `.jpeg`, or `.png`, result is `NEEDS REVISION`.
3. Otherwise result is `READY`.

`INCOMPLETE` has priority. Later checks must not override it.
`posterImage`, `posterMimeType`, and `posterSize` are not part of the business judgement rules.

## Environment Variables

Workflow:

- `DATA_SERVICE_URL`, default `http://localhost:8080`
- `SUBMISSION_EVENT_FUNCTION_NAME`
- `SUBMISSION_EVENT_FUNCTION_URL`, used when `INVOKE_MODE=http`
- `SERVERLESS_MODE`, `local` or `lambda`
- `INVOKE_MODE`, `local`, `lambda`, or `http`
- `PORT`, default `8001`

Processing:

- `DATA_SERVICE_URL`, default `http://localhost:8080`
- `RESULT_UPDATE_FUNCTION_NAME`
- `RESULT_UPDATE_FUNCTION_URL`
- `INVOKE_MODE`, `local`, `lambda`, or `http`
- `PROCESSING_DELAY_SECONDS`, default `0`

## Run Tests

From this folder:

```powershell
python -m unittest discover -s tests
```

## Notes for Integration With A and C

- A should call Workflow `POST /submissions`.
- A may send `posterImage` and `posterMimeType` when the append contract is enabled.
- In the current AWS B-only deployment, Workflow calls A's Submission Event Function by public Function URL.
- C should expose Data Service and implement Result Update Function.
- B expects Data Service to return lowerCamelCase JSON fields.
- With the append contract enabled, B expects Data Service `GET/PATCH` responses to also include `posterMimeType` and `posterSize`.
- With the append contract enabled, C should expose `GET /submissions/{id}/poster` for byte retrieval.
