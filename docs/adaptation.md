# Adapting the template

Keep the three operations separate: train an artifact, deploy it, register its
metadata and processing. The runtime adapter reads configuration from the broker.

1. Replace the offline feature list, target, data download and split preparation
   in `mlaas/train.py`. Retain fixed seeds, disjoint partitions and training-only
   preprocessing. For another classifier, replace the estimator inside the
   scikit-learn pipeline. Save a named MLflow signature and a scalar `.predict`
   output. Keep output labels serializable as numbers or strings.
2. Use a fresh immutable artifact directory and version. Serve it through MLflow
   at a new endpoint. Verify `/invocations` independently. The generic adapter
   sends `dataframe_split` with feature names and expects `{"predictions":[scalar]}`.
   Class probabilities are optional and not implemented in the current adapter.
3. Check the new observation's official SDM schema and vocabulary. Add its actual
   terms to `contexts/context.jsonld` and refresh the inline example contexts.
   Replace the WaterQualityObserved input type rather than adding unrelated v1
   entities. Map each feature to exactly one numeric NGSI-LD Property.
4. Register a new MLModel with the required metadata, input feature names,
   acceptable input types and container-reachable serving endpoint. The water
   CLI is an example generator: adapt its metadata generation or POST your own
   JSON-LD through the standard `/ngsi-ld/v1/entities` API. This never changes
   `mlaas/adapter.py`.
5. Register a new MLProcessing pointing to that exact model ID. Configure
   `subscriptionData.entities`, `watchedAttributes`, optional broker `q`, and
   `inputMapping`. Keep `outputEntityType=MLPrediction`. POST its ID to
   `/processings/reconcile`. Test the new q expression against the actual broker.
6. Replace the dataset-specific observation builder in `mlaas/cli.py` and replay
   loader in `mlaas/demo.py`. Keep immutable IDs and omit target labels. The
   inference adapter and provenance publication logic stay unchanged. The
   dashboard's sample/turbidity columns are water-specific presentation; adjust
   them to explain the new input fields.
7. Re-run broker, split, serving and end-to-end checks. Test matching and
   nonmatching inputs, retry idempotency and source immutability. Update examples
   and the data-model reference with tested behavior and limitations.

An absent feature is accepted only if the model's project extension
`missingInputFeatures` lists it; it becomes JSON null for the model pipeline's
imputer. All other absent, nonfinite, boolean or nonnumeric inputs fail visibly.
The broker does not store null measurement values. No adapter logic depends on
Water Potability's feature names, classification labels or turbidity filter.
