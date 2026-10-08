# meds-model-meds-tab-lr

meds-tab tabular baseline with LOGISTIC REGRESSION in place of XGBoost, on the MIMICIV_TUTORIAL featurization.

- Implementation: [florian6973/meds-model-meds-tab-lr](https://github.com/florian6973/meds-model-meds-tab-lr) (private).
- Pinned revision: `bd40c09d1252f67d7d3b4b34823cebbe927ea2b5`.
- Package directory: repository root (`.`).
- Python requirement: `>=3.11`.
- Workflow: Supervised training and prediction.
- Dataset predicates argument: not used.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-meds-tab-lr`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-meds-tab-lr/blob/bd40c09d1252f67d7d3b4b34823cebbe927ea2b5/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.

The source implementation's historical result-column label is `medstab-lr`.
