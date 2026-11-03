# Agent working agreement

## Project purpose

Build a **small, understandable, hackathon-reusable** FIWARE NGSI-LD + conventional ML template. Water Potability is illustrative only.

## Read first

1. `README.md`
2. `docs/architecture.md`
3. `docs/data-models.md`
4. `docs/ml-lifecycle.md`
5. `docs/acceptance-criteria.md`
6. `examples/`

## Contract invariants

- Four application entity types only in v1: `MLModel`, `MLProcessing`, `WaterQualityObserved`, `MLPrediction`.
- `Subscription` is an NGSI-LD API resource, not a custom ML Entity.
- Every `MLPrediction` points to one **immutable** source observation, one exact model version and the processing declaration.
- The broker decides triggering through standard subscriptions; adapter does input mapping, HTTP invocation and publication.
- No duplicate predictions on repeated notifications. Never have predictions trigger prediction subscriptions.
- Training and model deployment/registration are explicit separate steps; **no automatic retraining/redeployment**.
- Preserve terminology and source-vs-extension distinctions in `docs/data-models.md`.
- Always test actual broker behavior. Never claim untested JSON examples or official SDM conformance.

## Development sequence

1. Validate broker CRUD/subscription capabilities and JSON-LD context.
2. Train and serve model with a static known schema.
3. Implement one `MLProcessing` -> subscription -> observation -> `MLPrediction` vertical slice.
4. Implement minimal dashboard showing *real* events.
5. Run tests, update docs, and add a concise adaptation guide.

## Avoid scope creep

Do not add temporal aggregation, multiple-source inference, auto-retraining, ML Model Registry, distributed infra, orchestration agents or other new entities without explicit user approval.

## Documentation discipline

Treat Markdown specs as the design authority, but record and resolve implementation conflicts rather than silently invent behavior. Keep example payloads validated and keep documented commands runnable. Use direct API operations in tests and explain any limitations.

## Dashboard and demo maintenance

- Entity inspection must fetch current broker data. `refSubscription` targets the
  subscriptions API, never `/entities`. Every link or navigation button must act.
- Clear View is browser-only: clear selections, filters and messages without
  changing entities, replay position or the SQLite journal.
- Reset Demo requires explicit UI/API confirmation. Delete only locally tracked,
  ownership-checked demo observations and their adapter-generated predictions.
  Never delete by entity type, ID prefix or source relationship alone.
- Serialize reset with replay, notification acceptance and inference publication.
  Preserve dedup jobs and associated trace until broker deletions are verified.
  Keep failed/interrupted resets blocked and retryable across adapter restarts.
- Keep model/processing pins, subscriptions, unrelated jobs/events, serving and
  training artifacts. Test real broker reset/replay and controlled failure paths.
- One adapter process/worker owns the local journal. Do not run multiple workers
  against it; the runtime lock is process-local.
