# Executable commands

Run from the repository root with `.venv` activated. Full startup is in README.

| Operation | Command |
|---|---|
| Broker | `docker compose up -d broker` |
| Broker smoke | `RUN_BROKER_TESTS=1 python -m unittest tests.test_broker -v` |
| Offline training | `python -m mlaas.train --version v1` |
| Explicit deployment | `docker compose up -d --build --wait` |
| Register ready model | `python -m mlaas.cli register-model` |
| Register processing | `python -m mlaas.cli register-processing` |
| Replay demo | `python -m mlaas.cli inject --count 10 --start 0` |
| Inspect | `curl -fsS http://localhost:8000/state` |
| Trace | `curl -fsS http://localhost:8000/events` |
| Logs | `docker compose logs --tail 100 broker ml-adapter ml-serving` |
| Optional tracking UI | `mlflow ui --backend-store-uri ./mlruns --port 5002` |

## Independent endpoint check

```sh
curl -fsS http://localhost:5001/invocations \
  -H 'Content-Type: application/json' \
  -d '{"dataframe_split":{"columns":["ph","Hardness","Solids","Chloramines","Sulfate","Conductivity","Organic_carbon","Trihalomethanes","Turbidity"],"data":[[null,170.2,17500,7.4,320,400,11.8,65.2,4.5]]}}'
```

The synthetic row checks the serving contract, independently of the broker.
`register-model` also checks a real demo row before posting model metadata.

## Pause/remove a subscription

The adapter only changes subscriptions through explicit reconciliation. Patch
processing status, then reconcile:

```sh
curl -fsS -X PATCH \
  http://localhost:1026/ngsi-ld/v1/entities/urn:ngsi-ld:MLProcessing:water-demo-01/attrs \
  -H 'Content-Type: application/ld+json' \
  -d '{"processingStatus":{"type":"Property","value":"paused"},"@context":{"@vocab":"https://github.com/dncampo/fiware-mlaas-template/terms/"}}'
curl -fsS http://localhost:8000/processings/reconcile \
  -H 'Content-Type: application/json' \
  -d '{"id":"urn:ngsi-ld:MLProcessing:water-demo-01"}'
```

This deletes the subscription and retains its ID for audit. Already accepted
jobs drain. Set status to `active` and reconcile to recreate it. Pause before
deleting a processing entity. Never modify a deployed model or processing binding
in place; create new IDs for new configurations.

## Broker test network

The smoke test hosts a temporary HTTP/1.1 notification receiver and context on
port 18080. `BROKER_URL` defaults to `http://localhost:1026`.
`TEST_CALLBACK_URL` and `TEST_CONTEXT_URL` can override the container-reachable
receiver URLs. A JSON-LD body includes its context; requests without a body use
a Link header. Sending both with `application/ld+json` is rejected by coraine.
