import io
import logging
import os.path
import tarfile
from tarfile import TarInfo
from time import time as ts
from uuid import UUID

from .config import config
from .container import ContainerRunner
from .utils import FileStreamer, get_human_readable_size, safe_extract_tar

logger = logging.getLogger(__name__)


class ArtifactManager:
    def __init__(self, container: ContainerRunner, artifact_directory: str, step_uuid: UUID) -> None:
        self._container = container
        self._artifact_directory = artifact_directory
        self._step_uuid = step_uuid

    def upload(self) -> None:
        logger.info("Loading artifacts")

        t = ts()

        tar_data = io.BytesIO()

        with tarfile.open(fileobj=tar_data, mode="w|") as tar:
            for root, _, files in os.walk(self._artifact_directory):
                for af in files:
                    full_path = os.path.join(root, af)

                    relpath = os.path.relpath(full_path, self._artifact_directory)
                    ti = TarInfo(relpath)

                    stat = os.stat(full_path)
                    ti.size = stat.st_size
                    ti.mode = stat.st_mode

                    with open(full_path, "rb") as f:
                        tar.addfile(ti, f)

        res = self._container.put_archive(config.build_dir, tar_data.getvalue())
        if not res:
            raise Exception(f"Error loading artifact: {af}")

        t = ts() - t

        logger.info("Artifacts loaded in %.3fs", t)

    def download(self, artifacts: list[str]) -> None:
        if not artifacts:
            return

        artifact_file = f"artifacts-{self._step_uuid}.tar"
        artifact_remote_path = os.path.join(config.build_dir, artifact_file)
        artifact_local_directory = self._artifact_directory

        logger.info("Collecting artifacts")

        t = ts()

        path_filters = " -o ".join(f"-path './{a}'" for a in artifacts)
        prepare_artifacts_cmd = ["find", "-type", "f", r"\(", path_filters, r"\)"]
        prepare_artifacts_cmd += ["|", "tar", "cf", artifact_file, "-C", config.build_dir, "-T", "-"]

        self._container.run_command(prepare_artifacts_cmd)
        if not self._container.path_exists(artifact_remote_path):
            logger.info("No artifacts found. Skipping")
            return

        data, stats = self._container.get_archive(artifact_remote_path, encode_stream=True)
        logger.debug("artifacts stats: %s", stats)

        # FileStreamer only implements `read` which is all that is needed.
        with tarfile.open(fileobj=FileStreamer(data), mode="r|") as wrapper_tar:  # type: ignore[abstract]
            for entry in wrapper_tar:
                with tarfile.open(fileobj=wrapper_tar.extractfile(entry), mode="r|") as tar:
                    safe_extract_tar(tar, artifact_local_directory)

        t = ts() - t

        logger.info(
            "Artifacts saved %s to %s in %.3fs",
            get_human_readable_size(stats["size"]),
            artifact_local_directory,
            t,
        )
