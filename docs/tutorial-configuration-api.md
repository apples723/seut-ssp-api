# Tutorial Configuration API

## Architecture

Tutorial configuration is a versioned domain resource selected through the existing `STORAGE_BACKEND` repository factory. The service layer validates every incoming configuration before persistence and maps validation, conflict, and missing-resource outcomes to stable HTTP responses. Flask (`wsgi.py`), the standalone server (`local_server.py`), and Lambda-style modules under `handlers/` expose the same service functions.

Public reads are intentionally separate from administration: `/tutorial/config` resolves only the published pointer. Draft and disabled content is never returned by the public endpoint.

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

The only template tokens are `{{scenarioName}}`, `{{headToHeadRoleA}}`, and `{{headToHeadRoleB}}`.

## Responses and errors

A version object has this shape:

```json
{
  "id": "default",
  "version": 3,
  "status": "draft",
  "config": {},
  "createdAt": "2025-01-01T00:00:00+00:00",
  "createdBy": "admin",
  "publishedAt": "2025-01-01T00:05:00+00:00"
}
```

`publishedAt` is present when applicable. Successful public reads return `{ "ok": true, "tutorial": <version> }`. Admin endpoints return the same version envelope where applicable.

Malformed JSON and request/schema validation failures return HTTP 400. Schema failures include structured `errors` entries with `path`, `message`, and `code`. A stale `expectedVersion` returns HTTP 409 and `currentVersion`. Missing tutorials or versions return HTTP 404. Admin endpoints require the existing `X-Admin-Token` header.

## Endpoints

| Method | Path | Request | Behavior |
|---|---|---|---|
| GET | `/tutorial/config?id=default` | Query `id` defaults to `default` | Return published content only |
| GET | `/admin/tutorials` | Admin header | List latest versions |
| POST | `/admin/tutorials/get` | `tutorialId`, optional `version` | Get a version; without `version`, prefer current draft |
| POST | `/admin/tutorials/history` | `tutorialId` | List immutable history newest first |
| POST | `/admin/tutorials/create` | `config`, optional `createdBy` | Create version 1 as draft |
| POST | `/admin/tutorials/update` | `tutorialId`, `config`, `expectedVersion`, optional `createdBy` | Save a new draft version |
| POST | `/admin/tutorials/diff` | `tutorialId`, `config`, optional `version` | Return recursive path-based changes |
| POST | `/admin/tutorials/import` | `config` | Validate and preview create/update; never writes |
| POST | `/admin/tutorials/publish` | `tutorialId`, `expectedVersion`, optional `version` | Publish the selected version; defaults to current draft/latest |
| POST | `/admin/tutorials/disable` | `tutorialId`, `expectedVersion` | Remove the public pointer without deleting history |

For create/import, `tutorialId` is derived from `config.id`. For update/diff, `config.id` must match the envelope's `tutorialId`.

## Persistence and lifecycle

Versions use monotonic integers. Configuration payloads are immutable after insertion. Lifecycle metadata supports `draft`, `published`, and `disabled`:

- **Create** writes the first draft and establishes the current-draft pointer.
- **Update** checks `expectedVersion`, disables the superseded draft, inserts a new immutable version, and advances the current-draft/latest pointers atomically.
- **Publish** revalidates the selected version, changes its lifecycle status, disables the previously published version, and moves the published pointer.
- **Disable** marks the published version disabled and removes the published pointer. History remains available to administrators.

SQLite stores version rows in `tutorial_versions` and pointers in `tutorial_heads`. Writes use `BEGIN IMMEDIATE` plus a process lock, so version comparison and pointer changes occur in one transaction.

DynamoDB reuses the existing `ssp-scenarios` table and `gsi-scenarios-list` resource. Tutorial partitions use `PK=TUTOR#<tutorialId>`, immutable `SK=VER#000001` values, and `SK=HEAD` for pointers. Head items use `GSI1PK=ALL_TUTORIALS` and `GSI1SK=TUTOR#<tutorialId>`, keeping them distinct from scenario entries. Multi-item lifecycle writes use conditional transactions and compare `latestVersion` with `expectedVersion`.

## Deployment and storage implications

No new DynamoDB table is required. Deployments must allow the application role to perform `GetItem`, `Query`, and `TransactWriteItems` on the scenario table and query its existing list GSI. Existing local Dynamo table creation already supplies the required generic key and GSI attributes.

SQLite deployments create the tutorial tables automatically in the configured `DB_PATH`; the database directory must remain writable and persistent. As with the rest of the API, select DynamoDB with `STORAGE_BACKEND=dynamodb` and configure `DYNAMODB_ENDPOINT` only for local Dynamo. Production Dynamo deployments use the default credential chain.
