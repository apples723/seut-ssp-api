# Tutorial Configuration API

## Architecture

Tutorial configuration is a versioned domain resource selected through the existing `STORAGE_BACKEND` repository factory. The service layer validates every incoming configuration before persistence and maps validation, conflict, and missing-resource outcomes to stable HTTP responses. Flask (`wsgi.py`), the standalone server (`local_server.py`), and Lambda-style modules under `handlers/` expose the same service functions.

Public reads are intentionally separate from administration. `/tutorial/config` without an `id` resolves the globally active tutorial selector; an explicit `?id=...` continues to read that tutorial's published version directly. Draft and disabled content is never returned by the public endpoint. Multiple tutorial IDs may be published, but the activation selector identifies exactly one site-wide active ID after the first explicit activation.

For migration compatibility, if no selector has been written and the `default` tutorial is published and enabled, `default` is treated as active at activation revision `0`. If neither an explicit selector nor a published `default` exists, there is no active tutorial during bootstrap and the public endpoint returns its existing 404 response.

## TutorialConfig schema (version 1)

A configuration is a closed JSON object; fields not listed below are rejected.

```json
{
  "schemaVersion": 1,
  "id": "default",
  "title": "Platform tutorial",
  "description": "Optional non-empty description",
  "steps": [
    {
      "id": "welcome",
      "navigationLabel": "Overview",
      "eyebrow": "1 · Overview",
      "title": "Welcome",
      "body": [
        { "markdown": "Welcome to **{{scenarioName}}**." },
        { "slot": "teaching-arc" }
      ],
      "tip": {
        "participant": "Participant guidance",
        "facilitator": "Facilitator guidance"
      },
      "differences": [
        { "omitted": "Example behavior", "actualBehavior": "Production behavior" }
      ]
    }
  ]
}
```

`steps` must contain exactly these IDs in this order:

1. `welcome`
2. `roles`
3. `waiting`
4. `sprint`
5. `responses`
6. `engineer`
7. `discussion`
8. `stakeholder`
9. `reveal`
10. `controls`
11. `cascade`
12. `wrapup`

Every step requires `id`, `navigationLabel`, `eyebrow`, `title`, a non-empty `body`, and a `differences` array. A body block is exclusively either:

- `{ "markdown": "...", "persona": "participant|facilitator", "variant": "persona-note|inline-tip" }`, where `persona` and `variant` are optional; or
- `{ "slot": "..." }`.

`welcome` and `cascade` each require exactly one `teaching-arc` slot. `wrapup` requires exactly one `actions` slot. Other steps permit no slots. `tip` is optional and is either a non-empty Markdown string or an object containing both non-empty `participant` and `facilitator` strings. Each difference contains exactly non-empty `omitted` and `actualBehavior` strings.

A tutorial ID must be non-empty, limited to 128 UTF-8 bytes, have no leading or trailing whitespace, and contain no `#`. Configurations serialize to at most 358,400 UTF-8 bytes so a complete version item remains below DynamoDB's 400 KiB limit. The only template tokens are `{{scenarioName}}`, `{{headToHeadRoleA}}`, and `{{headToHeadRoleB}}`.

## Responses and errors

A version object has this shape:

```json
{
  "id": "default",
  "version": 3,
  "revision": 5,
  "status": "draft",
  "config": {},
  "createdAt": "2025-01-01T00:00:00+00:00",
  "createdBy": "admin",
  "publishedAt": "2025-01-01T00:05:00+00:00"
}
```

`version` identifies an immutable content snapshot. `revision` identifies the current aggregate state, advances after every save, publish, or disable operation, and is used for optimistic concurrency. Activation has an independent nonnegative `activationRevision`; every successful explicit activation increments it. `publishedAt` is present when applicable. Successful public reads return `{ "ok": true, "tutorial": <version> }`. Admin endpoints return the same version envelope where applicable.

`GET /admin/tutorials` adds top-level `activeTutorialId` (nullable only during bootstrap) and `activationRevision`. Every tutorial summary adds `active`, nullable `publishedVersion`, and `canActivate`; `canActivate` is true only for a published, enabled tutorial that is not already active. Activation succeeds with `{ "ok": true, "activeTutorialId": "...", "activationRevision": n }`.

Malformed JSON and request/schema validation failures return HTTP 400. Schema failures include structured `errors` entries with `path`, `message`, and `code`. A stale `expectedRevision` returns HTTP 409 and `currentRevision`; a stale `expectedActivationRevision` returns HTTP 409 and `currentActivationRevision`. Activating a draft-only or disabled tutorial returns 409. Disabling the active tutorial returns 409 with an instruction to activate another tutorial first. Missing tutorials or versions return HTTP 404. Admin endpoints require the existing `X-Admin-Token` header.

## Endpoints

| Method | Path | Request | Behavior |
|---|---|---|---|
| GET | `/tutorial/config` | Omit `id` | Return the active tutorial's published content; use published `default` at revision 0 only when no explicit selector exists |
| GET | `/tutorial/config?id=default` | Explicit query `id` | Return that ID's published content directly, independent of the active selector |
| GET | `/admin/tutorials` | Admin header | List latest versions with activation metadata and published-version eligibility |
| POST | `/admin/tutorials/activate` | `tutorialId`, `expectedActivationRevision` | Select a published, enabled tutorial globally and increment the activation revision |
| POST | `/admin/tutorials/get` | `tutorialId`, optional `version` | Get a version; without `version`, prefer current draft |
| POST | `/admin/tutorials/history` | `tutorialId` | List immutable history newest first |
| POST | `/admin/tutorials/create` | `config`, optional `createdBy` | Create version 1 as draft |
| POST | `/admin/tutorials/update` | `tutorialId`, `config`, `expectedRevision`, optional `createdBy` | Save a new draft version |
| POST | `/admin/tutorials/diff` | `tutorialId`, `config`, optional `version` | Return recursive path-based changes |
| POST | `/admin/tutorials/import` | `config` | Validate and preview create/update; never writes |
| POST | `/admin/tutorials/publish` | `tutorialId`, `expectedRevision`, optional `version` | Publish the selected version; defaults to current draft/latest |
| POST | `/admin/tutorials/disable` | `tutorialId`, `expectedRevision` | Remove the public pointer without deleting history |

For create/import, `tutorialId` is derived from `config.id`. For update/diff, `config.id` must match the envelope's `tutorialId`.

## Persistence and lifecycle

Versions use monotonic integers. Configuration payloads are immutable after insertion. Lifecycle metadata supports `draft`, `published`, and `disabled`:

- **Create** writes the first draft and establishes the current-draft pointer.
- **Update** checks `expectedRevision`, disables the superseded draft, inserts a new immutable version, advances the current-draft/latest pointers, and increments the aggregate revision atomically. It preserves the resource's disabled state; only publishing restores public availability.
- **Publish** checks and increments the aggregate revision, revalidates the selected version, changes its lifecycle status, disables the previously published version for the same tutorial ID, and moves that ID's published pointer. Publishing one ID does not unpublish other tutorial IDs.
- **Activate** compares `expectedActivationRevision`, verifies the selected tutorial currently has an enabled published version, writes the singleton selector, and increments the independent activation revision. Switching does not modify either tutorial's publication state.
- **Disable** checks and increments the aggregate revision, marks the published version disabled, and removes the published pointer. It is rejected transactionally when the tutorial is active, including the implicit `default` selector at activation revision 0. History remains available to administrators.

SQLite stores version rows in `tutorial_versions`, per-ID pointers in `tutorial_heads`, and the explicit global selector in the single allowed `tutorial_settings` row (`singleton=1`, `active_tutorial_id`, `activation_revision`). The settings row is intentionally absent before the first activation so existing databases retain the published-`default` fallback. Tutorial writes use a dedicated connection with `BEGIN IMMEDIATE` plus a process lock. Activation checks and selector changes, and active checks plus disable changes, therefore occur under the same database write transaction.

DynamoDB reuses the existing `ssp-scenarios` table and `gsi-scenarios-list` resource. Tutorial partitions use `PK=TUTOR#<tutorialId>`, immutable `SK=VER#000001` values, and `SK=HEAD` for per-ID pointers. Head items use `GSI1PK=ALL_TUTORIALS` and `GSI1SK=TUTOR#<tutorialId>`, keeping them distinct from scenario entries. The global selector is one non-GSI item at `PK=TUTORIAL#SETTINGS`, `SK=ACTIVATION`, containing `activeTutorialId` and `activationRevision`; no table or environment variable is added. Conditional transactions compare revisions and candidate publication state. Activation and disable both include the selector and tutorial head in their transaction conflict set, so a disable/activation race cannot commit a selector that targets a disabled tutorial.

## Deployment and storage implications

No new DynamoDB table is required. Deployments must allow the application role to perform `GetItem`, `Query`, and `TransactWriteItems` on the scenario table and query its existing list GSI. Existing local Dynamo table creation already supplies the required generic key and GSI attributes.

SQLite deployments create the tutorial tables automatically in the configured `DB_PATH`; the database directory must remain writable and persistent. As with the rest of the API, select DynamoDB with `STORAGE_BACKEND=dynamodb` and configure `DYNAMODB_ENDPOINT` only for local Dynamo. Production Dynamo deployments use the default credential chain.
