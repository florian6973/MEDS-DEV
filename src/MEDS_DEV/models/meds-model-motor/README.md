# meds-model-motor

MOTOR implemented against the minimal MEDS model contract.

- Implementation: [florian6973/meds-model-motor](https://github.com/florian6973/meds-model-motor) (private).
- Pinned revision: `8475e6ef1ee9a39846a0ae5a6e37403f2448b0fe`.
- Package directory: `models/02_09_motor`.
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: required by the command template.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-motor`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-motor/blob/8475e6ef1ee9a39846a0ae5a6e37403f2448b0fe/models/02_09_motor/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
