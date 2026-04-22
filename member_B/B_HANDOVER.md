# B Part Handover

## Scope

I am responsible for:

- `Workflow Service` container
- `Processing Function` serverless function

The B part is deployed as a B-only AWS stack. It keeps only my two owned components on AWS:

- `Workflow Service` on ECS/Fargate behind an ALB
- `Processing Function` on AWS Lambda

The old self-owned A/C resources from my previous full-stack deployment have been removed from the CloudFormation stack.

## Public Entry Point

The public base URL for my `Workflow Service` is:

`REPLACED_FOR_SAFETY_AND_ANONYMITY`

The public URL for my `Processing Function` is:

`REPLACED_FOR_SAFETY_AND_ANONYMITY`

Available endpoints:

- `POST /submissions`
- `GET /submissions/{id}`
- `GET /submissions/{id}/poster` when proxy mode is enabled in the integrated version
- `GET /healthz`

Examples:

- Create a submission
  `POST REPLACED_FOR_SAFETY_AND_ANONYMITY/submissions`

- Get a submission
  `GET REPLACED_FOR_SAFETY_AND_ANONYMITY/submissions/{id}`

- Call Processing Function directly
  `POST REPLACED_FOR_SAFETY_AND_ANONYMITY`

## Request Format

All fields follow the latest `API_CONTRACT.md`.

Request body for creating a submission:

```json
{
  "title": "Event title",
  "description": "This description should be at least 30 characters long.",
  "posterFilename": "poster.png",
  "posterImage": "base64-string-or-null",
  "posterMimeType": "image/png"
}
```

Notes:

- `posterImage` and `posterMimeType` are optional fields introduced by the append contract.
- Workflow only checks JSON shape and string-or-null types for these fields.
- Workflow does not decode base64 and does not validate image size limits. That remains C Data Service responsibility.

Typical response body:

```json
{
  "id": "uuid",
  "title": "Event title",
  "description": "This description should be at least 30 characters long.",
  "posterFilename": "poster.png",
  "posterMimeType": "image/png",
  "posterSize": 123456,
  "status": "PENDING",
  "note": null,
  "createdAt": "2026-04-19T09:22:24Z",
  "updatedAt": "2026-04-19T09:22:24Z"
}
```

Direct request body for the `Processing Function`:

```json
{
  "submissionId": "your-submission-id"
}
```

## Processing Rules

The `Processing Function` applies the required project rules in this order:

1. If any required field is missing, `null`, or blank after trimming, the final status is `INCOMPLETE`.
2. If all required fields are present, but `description` is shorter than 30 characters, or `posterFilename` does not end with `.jpg`, `.jpeg`, or `.png`, the final status is `NEEDS REVISION`.
3. Otherwise, the final status is `READY`.

Important notes:

- `INCOMPLETE` has the highest priority.
- `posterImage`, `posterMimeType`, and `posterSize` do not participate in the judgement rules.
- My `Processing Function` must not read `posterImage` and must not call `/submissions/{id}/poster`.
- Valid status values are:
  - `PENDING`
  - `READY`
  - `NEEDS REVISION`
  - `INCOMPLETE`

## Integration Notes

For A:

- The frontend should call my `POST /submissions` when the form is submitted.
- The frontend should call my `GET /submissions/{id}` when showing the result page.
- My Workflow Service triggers A's Submission Event Function using A's public Function URL.

For C:

- My Workflow Service writes new records to C's Data Service.
- My Workflow Service reads records from C's Data Service for status/result display.
- Under the append contract, my Workflow Service may proxy `GET /submissions/{id}/poster` to C's Data Service.
- My Processing Function reads submission data from C's Data Service.
- After processing, it calls C's `Result Update Function` by public Function URL to write the final result back.
- As long as C's Data Service and Result Update Function remain contract-compatible, the integration will work.

Current external dependencies configured in AWS:

- A Submission Event Function URL: `REPLACED_FOR_SAFETY_AND_ANONYMITY`
- C Data Service URL: `REPLACED_FOR_SAFETY_AND_ANONYMITY`
- C Result Update Function URL: `REPLACED_FOR_SAFETY_AND_ANONYMITY`

## Internal Component Note

My `Processing Function` is mainly an internal component, but it now also exposes a public Lambda Function URL for direct testing.

For normal integration, other group members mainly need:

- the public `Workflow Service` URL
- the latest `API_CONTRACT.md`

If needed for debugging or isolated testing, they can also call the public `Processing Function URL` directly with a `submissionId`.

## Verification Status

Already verified:

- local A+B with remote C integration
- B-only AWS deployment has only one ECS service: `WorkflowService`
- B-only AWS deployment has only one project Lambda: `creator-cloud-studio-processing`
- B Workflow health check returns `200`
- B Processing Function URL is public with `AuthType=NONE`

Important current integration note:

- If A's Submission Event Function returns `502`, new records can still be created in C through B Workflow, but the background processing chain will not complete until A's Function URL is fixed.

## Source Code Location

My B part code is stored in:

`REPLACED_FOR_SAFETY_AND_ANONYMITY`
