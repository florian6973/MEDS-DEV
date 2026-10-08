# meds-model-motor-cp

MOTOR via the ChaoPang femr fork (omop_meds_v3_tutorial @ baf77cce), evaluated by linear probing on frozen representations.

- Implementation: [florian6973/meds-model-motor-cp](https://github.com/florian6973/meds-model-motor-cp) (private).
- Pinned revision: `0c360b47ce7bf0c79844eb11a8dd1648d612ba4c`.
- Package directory: `models/02_09_motor_cp`.
- Python requirement: `>=3.11`.
- Workflow: Pretraining followed by supervised training and prediction.
- Dataset predicates argument: not used.

## Setup and execution

Read [private model setup](../PRIVATE_MODELS.md) for authentication, runtime requirements,
and the full-run command. Set `model=meds-model-motor-cp`.

The [pinned implementation documentation](https://github.com/florian6973/meds-model-motor-cp/blob/0c360b47ce7bf0c79844eb11a8dd1648d612ba4c/models/02_09_motor_cp/README.md) describes the architecture, inputs,
configuration, hardware requirements, upstream dependencies, validation evidence, and limitations.
This registration uses the implementation's command slots and retains its additional runtime
requirements. Dependencies remain isolated from the MEDS-DEV environment.

MOTOR-CP runs its model-owned `stamp_femr_version.py` before each command slot. This idempotent
post-install step is required by the pinned FEMR fork and records its installed VCS revision.
