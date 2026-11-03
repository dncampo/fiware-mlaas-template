# JSON-LD examples - verified with coraine

These five examples use the inline vocabulary in `contexts/context.jsonld`.
Standard NGSI-LD terms and selected official Smart Data Model terms are mapped
explicitly. Project extensions use the repository's vocabulary namespace.
See `docs/implementation.md` for the exact source-versus-extension decisions.

All five payloads pass the direct API smoke test against the pinned coraine
broker. The model and prediction examples remain illustrative metadata/output;
the CLI registers measured metadata from the trained artifact. No full official
Smart Data Model schema conformance is claimed.

`predictedValue=1` and `predictionScore=0.82` are illustrative, not observed model output. `turbidity>4` is a **synthetic demonstration filter**, not a regulatory threshold. The example observation values are synthetic and should not be cited as the real Water Potability dataset.
