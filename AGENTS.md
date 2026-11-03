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
