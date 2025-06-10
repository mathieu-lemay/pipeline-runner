import io
import logging
import os.path
import tarfile
from tarfile import TarInfo
from time import time as ts
from types import TracebackType
from uuid import UUID

from docker import DockerClient  # type: ignore[import-untyped]
from docker.models.containers import Container  # type: ignore[import-untyped]

from pipeline_runner.config import config
from pipeline_runner.errors import ArtifactManagementError
from pipeline_runner.models import Artifacts
from pipeline_runner.utils import FileStreamer, get_human_readable_size, safe_extract_tar, stringify, wrap_in_shell

logger = logging.getLogger(__name__)


class ArtifactManager:
    def __init__(
        self, client: DockerClient, pipeline_container_name: str, artifact_directory: str, step_uuid: UUID
    ) -> None:
        self._runner = ArtifactManagerContainerRunner(client, pipeline_container_name)
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

        with self._runner as container:
            res = container.put_archive(config.build_dir, tar_data.getvalue())
            if not res:
                raise Exception(f"Error loading artifact: {af}")

        t = ts() - t

        logger.info("Artifacts loaded in %.3fs", t)

    def download(self, artifacts: Artifacts) -> None:
        if not artifacts.paths:
            return

        artifact_file = f"artifacts-{self._step_uuid}.tar"
        artifact_remote_path = os.path.join(config.build_dir, artifact_file)
        artifact_local_directory = self._artifact_directory

        logger.info("Collecting artifacts")

        t = ts()

        with self._runner as container:
            path_filters = " -o ".join(f"-path './{a}'" for a in artifacts.paths)
            prepare_artifacts_cmd = ["find", "-type", "f", r"\(", path_filters, r"\)"]
            prepare_artifacts_cmd += ["|", "tar", "cf", artifact_file, "-C", config.build_dir, "-T", "-"]

            cmd = wrap_in_shell(stringify(prepare_artifacts_cmd))
            logger.debug("preparing artifacts cmd: %s", cmd)
            exit_code, output = container.exec_run(cmd)
            if exit_code != 0:
                output_str = output.decode()
                if "empty archive" in output_str:
                    logger.info("No artifacts found. Skipping")
                    return

                raise ArtifactManagementError(f"Error preparing artifacts: {output.decode()}")

            data, stats = container.get_archive(artifact_remote_path, encode_stream=True)
            logger.debug("artifacts stats: %s", stats)

            # FileStreamer only implements `read` which is all that is needed.
            with tarfile.open(fileobj=FileStreamer(data), mode="r|") as wrapper_tar:  # type: ignore[abstract,call-overload]
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


class ArtifactManagerContainerRunner:
    def __init__(self, client: DockerClient, source_container_name: str) -> None:
        self._client = client
        self._source_container_name = source_container_name
        self._container: Container | None = None

    def __enter__(self) -> Container:
        name = f"{self._source_container_name}-artifacts"
        logger.debug("Creating artifacts manager container: %s", name)

        self._container = self._client.containers.run(
            "alpine",
            name=name,
            entrypoint="sh",
            working_dir=config.build_dir,
            volumes_from=[self._source_container_name],
            tty=True,
            detach=True,
        )

        return self._container

    def __exit__(
        self,
        type_: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._container:
            logger.debug("Deleting artifacts manager container: %s", self._container.name)
            self._container.remove(force=True)
