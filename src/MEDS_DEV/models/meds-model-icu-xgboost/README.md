# meds-model-icu-xgboost

Johnson et al. reproducibility baseline: a small curated set of first-N-hours vital/lab aggregates plus static variables, fitted with a fixed-hyperparameter XGBoost. No sweep, no learned featurization.

- Implementation: [florian6973/meds-model-icu-xgboost](https://github.com/florian6973/meds-model-icu-xgboost) (private).
- Pinned revision: `718eefc2d4caa661e4fb22d5811ba59ffb81d4e7`.
- Package directory: repository root (`.`).
- Python requirement: `>=3.11`.
- Workflow: Supervised training and prediction.
- Dataset predicates argument: required by the command template.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-icu-xgboost`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-icu-xgboost/blob/718eefc2d4caa661e4fb22d5811ba59ffb81d4e7/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
