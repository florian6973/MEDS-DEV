# meds-model-ora

ORA (marked time-to-event, loss_type=mtpp_shared), evaluated by linear probing on frozen representations.

- Implementation: [florian6973/meds-model-ora](https://github.com/florian6973/meds-model-ora) (private).
- Pinned revision: `0667969892f7469f2ffed5971c94098021bbf3a5`.
- Package directory: repository root (`.`).
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: required by the command template.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-ora`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-ora/blob/0667969892f7469f2ffed5971c94098021bbf3a5/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
