# FIWARE NGSI-LD MLaaS Template

> **Status: design/specification only (v0.1). No services or ML model have been implemented yet.**

A small, reusable proof of concept for **subscription-driven conventional ML inference** using an NGSI-LD Context Broker. Water Potability is the first demonstration dataset, **not a hardcoded limitation** of the integration pattern.

## The idea

1. **Train (offline):** split data, train and evaluate a scikit-learn pipeline; persist an MLflow-compatible model artifact and evaluation report.
2. **Deploy & register (explicit):** start local HTTP inference serving; verify `/invocations`; register an NGSI-LD `MLModel` describing the concrete version and endpoint; create an `MLProcessing` declaring source type, filter and attribute mapping; adapter creates a real NGSI-LD subscription.
3. **Infer (automatic):** an injector posts a `WaterQualityObserved`; the broker filters it and notifies the adapter; the adapter loads the full immutable observation, maps features, calls model serving and posts a traceable `MLPrediction`.

Only **four application entity types** (`MLModel`, `MLProcessing`, `WaterQualityObserved`, `MLPrediction`) and standard NGSI-LD **Subscription resources** are required. Training runs live outside the Context Broker. The adapter can expose a simple event stream for the demonstration dashboard.

## Repository reading order

1. [`docs/architecture.md`](docs/architecture.md) — boundaries, phases, sequence and technical risks.
2. [`docs/data-models.md`](docs/data-models.md) — four types, relationships, fields, invariants.
3. [`docs/ml-lifecycle.md`](docs/ml-lifecycle.md) — offline training, manual deployment/registration, runtime.
4. [`docs/acceptance-criteria.md`](docs/acceptance-criteria.md) — objective implementation checks.
5. [`AGENTS.md`](AGENTS.md) — instructions for Codex or another coding agent.
6. [`examples/`](examples/) — **illustrative draft** JSON-LD; syntax checked only, broker interoperability still to be tested.

## Non-goals for v1

No retraining/drift automation, model auto-promotion, historical window joins, multiple source observations per inference, user auth, Kafka, Kubernetes or distributed serving. No training button is required in the first implementation.

## Reference material

- User-supplied *Fluid Machine Learning lifecycle with FIWARE* (Benoit Orihuela, 2021), particularly `MLModel` and its three-phase vision.
- User-supplied *ML as a Service – Specifications*, particularly `MLProcessing`, subscription-driven triggering and publishing results.
- [Smart Data Models: Machine Learning](https://github.com/smart-data-models/dataModel.MachineLearning)
- [Smart Data Models: Water Quality](https://github.com/smart-data-models/dataModel.WaterQuality)

These documents inspire the model. **Do not assume proposed extensions are official Smart Data Model properties.** Verify schemas and supported broker behavior during implementation.

## Getting started with Git

Create a new empty GitHub repository (preferably without adding an initial README). In this folder:

```bash
git init -b main
git config user.name "Your GitHub display name"
git config user.email "your-verified-github-email@example.com"
git add .
git commit -m "docs: define initial FIWARE MLaaS template specification"
git remote add origin https://github.com/OWNER/fiware-mlaas-template.git
git push -u origin main
```

These commands are for the **documentation commit**, not a runnable application yet.
