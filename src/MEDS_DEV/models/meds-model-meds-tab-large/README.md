# meds-model-meds-tab-large

meds-tab / XGBoost tabular baseline at the MIMICIV_TUTORIAL featurization, run at scale on the cluster.

- Implementation: [florian6973/meds-model-meds-tab-large](https://github.com/florian6973/meds-model-meds-tab-large) (private).
- Pinned revision: `0f1e2ab059db4c8def2477486430204ce5a444f7`.
- Package directory: repository root (`.`).
- Python requirement: `>=3.11`.
- Workflow: Supervised training and prediction.
- Dataset predicates argument: not used.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-meds-tab-large`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-meds-tab-large/blob/0f1e2ab059db4c8def2477486430204ce5a444f7/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.

The source implementation's historical result-column label is `medstab-full`.
