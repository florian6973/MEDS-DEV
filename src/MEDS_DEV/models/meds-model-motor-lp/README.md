# meds-model-motor-lp

MOTOR pretrained with florian6973/femr_chao_meds_v3 @ 5adf7c6 (the `ora` package, loss_type=motor), then read out with THAT REPOSITORY'S OWN DEFAULT LINEAR PROBE -- LogisticRegressionCV(scoring='roc_auc') on the frozen final-layer representation. The readout-only ablation of MOTOR-FT, normally run on MOTOR-FT's own pretrained backbone.

- Implementation: [florian6973/meds-model-motor-lp](https://github.com/florian6973/meds-model-motor-lp) (private).
- Pinned revision: `e7a9e00d2e63a7c008d947c95291d3192bc8d5bc`.
- Package directory: `models/02_09_motor_lp`.
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: not used.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-motor-lp`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-motor-lp/blob/e7a9e00d2e63a7c008d947c95291d3192bc8d5bc/models/02_09_motor_lp/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
