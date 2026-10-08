# meds-model-icarefm

iCareFM's threshold-crossing survival objective (Burger et al., medRxiv 10.1101/2025.07.25.25331635 v2), reimplemented by the ORA authors, evaluated by linear probing on frozen representations.

- Implementation: [florian6973/meds-model-icarefm](https://github.com/florian6973/meds-model-icarefm) (private).
- Pinned revision: `ef40dc2745122303a6dd04af6a3e10197c90c02f`.
- Package directory: repository root (`.`).
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: required by the command template.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-icarefm`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-icarefm/blob/ef40dc2745122303a6dd04af6a3e10197c90c02f/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
