# meds-model-duett

DuETT implemented against the minimal MEDS model contract.

- Implementation: [florian6973/meds-model-duett](https://github.com/florian6973/meds-model-duett) (private).
- Pinned revision: `9ba453064ad9e11a6f45cf4d2e48d296973638d6`.
- Package directory: `models/02_11_duett`.
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: required by the command template.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-duett`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-duett/blob/9ba453064ad9e11a6f45cf4d2e48d296973638d6/models/02_11_duett/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
