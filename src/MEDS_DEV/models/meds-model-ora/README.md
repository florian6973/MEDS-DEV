# meds-model-ora

ORA (marked time-to-event, loss_type=mtpp_shared), evaluated by linear probing on frozen representations.

- Implementation: [florian6973/meds-model-ora](https://github.com/florian6973/meds-model-ora) (private).
- Pinned revision: `a48c10cbdb105ad9c5ceb019b67f404d802712af`.
- Package directory: `models/03_08_ora`.
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: required by the command template.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-ora`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-ora/blob/a48c10cbdb105ad9c5ceb019b67f404d802712af/models/03_08_ora/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.
