# meds-model-motor

MOTOR implemented against the minimal MEDS model contract.

- Implementation: [florian6973/meds-model-motor](https://github.com/florian6973/meds-model-motor) (private).
- Pinned revision: `ce686d0fb0c70c3feeb232785657b67ec5080dbe`.
- Package directory: repository root (`.`).
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: required by the command template.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-motor`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-motor/blob/ce686d0fb0c70c3feeb232785657b67ec5080dbe/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
