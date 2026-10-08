"""Validate private model discovery and command wiring without installing model runtimes."""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from omegaconf import OmegaConf
from packaging.requirements import Requirement

from MEDS_DEV import MODELS
from MEDS_DEV.models import CFG_YAML, model_commands

PRIVATE_MODELS = (
    "meds-model-behrt",
    "meds-model-duett",
    "meds-model-icarefm",
    "meds-model-icu-xgboost",
    "meds-model-meds-eic-ar",
    "meds-model-meds-eic-ar-sup",
    "meds-model-meds-tab-large",
    "meds-model-meds-tab-lr",
    "meds-model-motor",
    "meds-model-motor-cp",
    "meds-model-motor-ft",
    "meds-model-motor-lp",
    "meds-model-ora",
    "meds-model-retain",
    "meds-model-teco",
)


@pytest.mark.parametrize("name", PRIVATE_MODELS)
def test_private_model_source_is_pinned(name: str) -> None:
    model = MODELS[name]
    assert f"https://github.com/florian6973/{name}" in model["metadata"].links
    lines = [
        line.strip()
        for line in model["requirements"].read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    source, *dependencies = lines
    requirement = Requirement(source)
    assert requirement.url.startswith(f"git+https://github.com/florian6973/{name}.git@")
    url = urlsplit(requirement.url.removeprefix("git+"))
    assert re.fullmatch(r"[0-9a-f]{40}", url.path.rsplit("@", 1)[1])
    assert not url.fragment, "Model packages must install from the repository root"
    for dependency in dependencies:
        Requirement(dependency)


@pytest.mark.parametrize("name", PRIVATE_MODELS)
def test_private_model_full_run_carries_artifacts(name: str, tmp_path: Path) -> None:
    model = MODELS[name]
    cfg = OmegaConf.merge(
        OmegaConf.load(CFG_YAML),
        {
            "model": name,
            "dataset_type": "full",
            "mode": "full",
            "dataset_name": "synthetic",
            "task_name": "binary_signal",
            "dataset_dir": "/dataset",
            "labels_dir": "/labels",
            "predicates_path": "/predicates.yaml",
            "output_dir": str(tmp_path),
        },
    )
    stages = list(model_commands(cfg, model["commands"], model["model_dir"]))
    pretrained = "unsupervised" in model["commands"]
    assert len(stages) == (3 if pretrained else 2)
    train, train_dir = stages[-2]
    predict, _ = stages[-1]
    assert "meds-model supervised_train " in train
    assert "meds-model predict " in predict
    assert f"input_supervised_model_dir={train_dir}/model" in predict
    assert "external_labels_dir=/labels" in train
    assert "external_labels_dir=/labels" in predict
    assert "splits=[held_out]" in predict
    if pretrained:
        pretrain, pretrain_dir = stages[0]
        assert "meds-model pretrain " in pretrain
        assert f"input_pretrained_model_dir={pretrain_dir}/pretrained" in train
        assert f"input_data_dir={pretrain_dir}/data" in train
    for command, _ in stages:
        assert "{predicates_path}" not in command
        assert "{model_initialization_dir}" not in command


@pytest.mark.parametrize("name", PRIVATE_MODELS)
def test_private_model_individual_slots_format(name: str, tmp_path: Path) -> None:
    model = MODELS[name]
    for dataset_type, commands in model["commands"].items():
        for mode in commands:
            cfg = OmegaConf.create(
                {
                    "dataset_type": dataset_type,
                    "mode": mode,
                    "dataset_dir": "/dataset",
                    "labels_dir": "/labels",
                    "model_initialization_dir": "/previous",
                    "predicates_path": "/predicates.yaml",
                    "output_dir": str(tmp_path),
                    "split": "held_out" if mode == "predict" else None,
                }
            )
            stages = list(model_commands(cfg, model["commands"], model["model_dir"]))
            assert len(stages) == 1
            assert stages[0][1] == tmp_path
            assert "meds-model " in stages[0][0]
