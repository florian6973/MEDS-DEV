# Private MEDS model registrations

These 15 registrations use the individual private `florian6973/meds-model-*` repositories.
Each requirements file pins a full Git commit and the preserved `models/...` package subdirectory.
Future model development belongs in those repositories. Update the pin and command descriptor here
after reviewing a new model revision; do not copy model implementation code into MEDS-DEV.

| Registry name                | Implementation repository                                                                           | Python requirement |
| ---------------------------- | --------------------------------------------------------------------------------------------------- | ------------------ |
| `meds-model-meds-tab-large`  | [florian6973/meds-model-meds-tab-large](https://github.com/florian6973/meds-model-meds-tab-large)   | `>=3.11`           |
| `meds-model-meds-tab-lr`     | [florian6973/meds-model-meds-tab-lr](https://github.com/florian6973/meds-model-meds-tab-lr)         | `>=3.11`           |
| `meds-model-teco`            | [florian6973/meds-model-teco](https://github.com/florian6973/meds-model-teco)                       | `>=3.11`           |
| `meds-model-retain`          | [florian6973/meds-model-retain](https://github.com/florian6973/meds-model-retain)                   | `>=3.11`           |
| `meds-model-icu-xgboost`     | [florian6973/meds-model-icu-xgboost](https://github.com/florian6973/meds-model-icu-xgboost)         | `>=3.11`           |
| `meds-model-motor`           | [florian6973/meds-model-motor](https://github.com/florian6973/meds-model-motor)                     | `>=3.11`           |
| `meds-model-motor-cp`        | [florian6973/meds-model-motor-cp](https://github.com/florian6973/meds-model-motor-cp)               | `>=3.11`           |
| `meds-model-motor-ft`        | [florian6973/meds-model-motor-ft](https://github.com/florian6973/meds-model-motor-ft)               | `>=3.11`           |
| `meds-model-motor-lp`        | [florian6973/meds-model-motor-lp](https://github.com/florian6973/meds-model-motor-lp)               | `>=3.11`           |
| `meds-model-duett`           | [florian6973/meds-model-duett](https://github.com/florian6973/meds-model-duett)                     | `>=3.11`           |
| `meds-model-behrt`           | [florian6973/meds-model-behrt](https://github.com/florian6973/meds-model-behrt)                     | `>=3.11`           |
| `meds-model-meds-eic-ar`     | [florian6973/meds-model-meds-eic-ar](https://github.com/florian6973/meds-model-meds-eic-ar)         | `>=3.12`           |
| `meds-model-meds-eic-ar-sup` | [florian6973/meds-model-meds-eic-ar-sup](https://github.com/florian6973/meds-model-meds-eic-ar-sup) | `>=3.12`           |
| `meds-model-ora`             | [florian6973/meds-model-ora](https://github.com/florian6973/meds-model-ora)                         | `>=3.11`           |
| `meds-model-icarefm`         | [florian6973/meds-model-icarefm](https://github.com/florian6973/meds-model-icarefm)                 | `>=3.11`           |

## Authentication and runtime

Use an account with read access to the model repository and any private upstream dependencies.
For interactive GitHub CLI setup, run `gh auth login` and `gh auth setup-git`, selecting the account
with that access. Git subprocesses launched by uv must be able to authenticate non-interactively.
Do not embed tokens in requirements files, URLs, or committed configuration.

Use Linux and a compatible Python interpreter for model execution; Python 3.12 satisfies the declared
minimum for all registrations. MEDS-DEV creates model environments using its own Python interpreter,
so run MEDS-DEV under Python 3.12 for the MEDS-EIC-AR variants. GPU models also need their documented
CUDA, architecture, and dependency support. In particular, MOTOR-CP, MOTOR-FT, MOTOR-LP, ORA, and iCareFM
have upstream native/GPU dependencies; an interface match alone does not verify their installation.

This branch is based on upstream `dev`, which includes the `predicates_path` model interface.
The registry can be loaded and its command formatting tested without installing the private models.
Full integration runs require repository credentials and the appropriate model hardware. Public CI
does not gain access to these private repositories from a normal checkout token.

## Run a model

After installing this MEDS-DEV branch under the compatible interpreter, use the registered name and
absolute paths to an authorized MEDS dataset and its task labels. For example:

```bash
meds-dev-model model=meds-model-teco \
	dataset_type=full mode=full \
	dataset_name=my_dataset task_name=my_task \
	dataset_dir=/absolute/path/to/meds \
	labels_dir=/absolute/path/to/labels \
	predicates_path=/absolute/path/to/predicates.yaml \
	output_dir=/absolute/path/to/new/run
```

`dataset_type=full mode=full` runs the available pretraining, supervised training, and prediction
slots in order, carrying the previous stage's directory into `model_initialization_dir`.
Pass `predicates_path` for TECO, ICU-XGBoost, MOTOR, DuETT, ORA, and iCareFM, whose command templates
reference it. Predicate files must match the dataset and model's required concepts.
Predicate-free registrations ignore that argument. `demo=true` only changes a model when its own
descriptor forwards the demo flag; consult that model's documentation before running it.

## Scope and provenance

Registration validates discovery, metadata, pinned sources, and command formatting. It does not claim
full runtime installation, training, or evaluation. Follow each implementation's validation procedure
and existing real-data/cluster approvals before such runs.

MEDS-TAB-large and MEDS-TAB-LR omit the source-only `metadata.results_column` field because MEDS-DEV's
metadata schema does not accept it; their historical result labels are retained in their READMEs.
MOTOR-CP adds its existing model-owned FEMR version-stamping step before each command slot; all other
command recipes are taken directly from the pinned model revisions.

The existing `meds_tab/tiny` registration is unchanged. There is no separate migrated
`meds-model-meds-tab-tiny` repository, and the existing tiny recipe is not asserted to reproduce the
paper's historical implementation revision.

## Registration validation (2026-10-08)

- Registry and command-wiring checks: 57 passed, including all 15 new registrations.
- All 15 pinned source packages built and installed through authenticated Git using `uv pip install --no-deps` in a disposable Python 3.12 environment. Runtime dependencies and model training were not
    exercised by that package-install check.
- All applicable pre-commit hooks passed for the changed files.
- The fast suite on Windows had 107 passes and 10 failures; unmodified upstream `dev` at
    `67368f1b7a551f10a69ec0b18c9fbf5a86eddb8e` had 62 passes and the same 10 failures. These are existing
    platform-dependent path, temporary-file, and virtual-environment failures, not added model tests.
- Packaging fixes were committed in the new private model repositories: MOTOR-CP includes its existing
    FEMR helper in its wheel, and ICU-XGBoost avoids adding its concept YAML files twice. Their
    registrations pin those fixed revisions; model computation was not changed.
