import gzip
import logging
import os.path
import tarfile
from time import time as ts
from typing import List

from . import utils
from .config import config
from .container import ContainerRunner

logger = logging.getLogger(__name__)


class ArtifactManager:
    def __init__(self, container: ContainerRunner, pipeline_id: str, step_id: str):
        self._container = container
        self._pipeline_id = pipeline_id
        self._step_id = step_id

    def save(self, artifacts: List[str]):
        if not artifacts:
            return

        artifact_file = f"artifacts-{self._step_id}.tar.gz"
        artifact_remote_path = os.path.join(config.build_dir, artifact_file)
        artifact_local_directory = utils.get_artifact_directory(self._pipeline_id)

        logger.info("Collecting artifacts")

        t = ts()

        self._container.exec(["tar", "zcf", artifact_file, "-C", config.build_dir] + artifacts)
        data, stats = self._container.get_archive(artifact_remote_path)
        logger.debug("artifacts stats: %s", stats)

        # noinspection PyTypeChecker
        with tarfile.open(fileobj=utils.FileStreamer(data), mode="r|") as tar:
            tar.extractall(artifact_local_directory)

        t = ts() - t

        logger.info(
            "Artifacts saved %s to %s in %.3fs",
            utils.get_human_readable_size(stats["size"]),
            artifact_local_directory,
            t,
        )

    def load(self):
        artifact_directory = utils.get_artifact_directory(self._pipeline_id)

        logger.info("Loading artifacts")

        t = ts()

        for af in os.listdir(artifact_directory):
            with gzip.open(os.path.join(artifact_directory, af), "rb") as f:
                res = self._container.put_archive(config.build_dir, f)
                if not res:
                    raise Exception(f"Error loading artifact: {af}")

        t = ts() - t

        logger.info("Artifacts loaded in %.3fs", t)
