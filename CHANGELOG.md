# Changelog — ssp-api

All notable changes to the SSP API are documented in this file.

## [v0.5.0] — 2026-07-18

First release of the SEUT Simulation Platform API as a standalone repository.

### Bug Fixes

- **Persist `assignedRoleId` for participants** (`15363e5`) — The field existed on the Participant model but was never written to the database for PIN-based joins, reconnections, or role assignments. The frontend always received `null`, breaking the head-to-head discussion alert feature.
  - Added `assigned_role_id` to `add_participant`, `update_participant_role`, `replace_player_for_role`, and `claim_role_with_pin`
  - Included `assignedRoleId` in session `to_dict()` participant serialization and the join-response `_participant_dict` helper
  - Added SQLite migration for the column; updated DynamoDB helpers
  - Updated repository protocol signature

### Contributors
- Grant Siders

[v0.5.0]: https://github.com/seut-org/ssp-api/releases/tag/v0.5.0
