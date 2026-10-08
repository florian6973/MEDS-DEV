# meds-model-motor-ft

MOTOR pretrained with florian6973/femr_chao_meds_v3 @ 5adf7c6 (the `ora` package, loss_type=motor), then FULL supervised finetuning of the encoder with a two-class head. The finetuning path is ported from that repository's parent's `original` branch @ 9697ce7e, which the public `main` does not carry.

- Implementation: [florian6973/meds-model-motor-ft](https://github.com/florian6973/meds-model-motor-ft) (private).
- Pinned revision: `9c250136eb4caeff233152b43fa09a41a2c5bd4e`.
- Package directory: repository root (`.`).
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: not used.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-motor-ft`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-motor-ft/blob/9c250136eb4caeff233152b43fa09a41a2c5bd4e/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
