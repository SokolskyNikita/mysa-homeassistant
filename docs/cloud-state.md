# Cloud state and model fields

The wire-level state, telemetry, model, and capability reference moved to the
standalone [`mysa-client cloud-state guide`](https://github.com/SokolskyNikita/mysa-client/blob/main/docs/cloud-state.md).
It includes response shapes, field meanings, examples, and evidence limits.

The client owns reusable per-field reconciliation, command evidence, and model
semantics. Home Assistant's polling cadence, freshness thresholds, source
preferences, entity presentation, and model feature scope remain in this
repository's [cloud implementation tracker](implementation/cloud.md) and
[new-model guide](new-cloud-models.md). The shared ownership boundary is
described in the [architecture guide](architecture.md).
