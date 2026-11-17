import io
import json
import logging
import os.path
import shlex
import tarfile
from tarfile import TarInfo
from time import time as ts
from types import TracebackType
from uuid import UUID

from docker import DockerClient  # type: ignore[import-untyped]
from docker.models.containers import Container  # type: ignore[import-untyped]
from slugify import slugify

from pipeline_runner.config import config
from pipeline_runner.errors import ArtifactManagementError
from pipeline_runner.models import ArtifactDefinition, Artifacts
from pipeline_runner.utils import FileStreamer, get_human_readable_size, safe_extract_tar, wrap_in_shell

logger = logging.getLogger(__name__)


class ArtifactIndex:
    """
    Maps shared artifact names to the files (relative to the artifact directory) they contain.

    The files of all shared artifacts are stored merged in the artifact directory, the same way they end up
    in the clone directory of a step. The index is stored next to (not inside) that directory, so it is never
    sent into a step container.
    """

    def __init__(self, artifact_directory: str) -> None:
        self._path = f"{os.path.normpath(artifact_directory)}.index.json"

    def load(self) -> dict[str, list[str]]:
        if not os.path.exists(self._path):
            return {}

        with open(self._path) as f:
            data: dict[str, list[str]] = json.load(f)
            return data

    def save(self, index: dict[str, list[str]]) -> None:
        with open(self._path, "w") as f:
            json.dump(index, f, indent=2, sort_keys=True)


class ArtifactManager:
    def __init__(
        self, client: DockerClient, pipeline_container_name: str, artifact_directory: str, step_uuid: UUID
    ) -> None:
        self._runner = ArtifactManagerContainerRunner(client, pipeline_container_name)
        self._artifact_directory = artifact_directory
        self._index = ArtifactIndex(artifact_directory)
        self._step_uuid = step_uuid

    def upload(self, *, selector: bool | list[str] = True) -> None:
        """
        Send artifacts of previous steps to the step container.

        :param selector: `True` for all shared artifacts, `False` for none, or a list of artifact names.
        """
        if selector is False or selector == []:
            logger.info("Artifacts downloading disabled, skipping sending artifacts to container")
            return

        logger.info("Loading artifacts")

        t = ts()

        files = self._select_files(selector=selector)

        tar_data = io.BytesIO()

        with tarfile.open(fileobj=tar_data, mode="w|") as tar:
            for relpath in files:
                full_path = os.path.join(self._artifact_directory, relpath)
                ti = TarInfo(relpath)

                stat = os.stat(full_path)
                ti.size = stat.st_size
                ti.mode = stat.st_mode

                with open(full_path, "rb") as f:
                    tar.addfile(ti, f)

        with self._runner as container:
            res = container.put_archive(config.build_dir, tar_data.getvalue())
            if not res:
                raise ArtifactManagementError("Error loading artifacts")

        t = ts() - t

        logger.info("%d artifact file(s) loaded in %.3fs", len(files), t)

    def _select_files(self, *, selector: bool | list[str]) -> list[str]:
        if isinstance(selector, bool):
            return sorted(
                os.path.relpath(os.path.join(root, f), self._artifact_directory)
                for root, _, filenames in os.walk(self._artifact_directory)
                for f in filenames
            )

        index = self._index.load()
        selected: set[str] = set()

        for name in selector:
            if name not in index:
                logger.warning("Artifact '%s' not found, skipping. Available artifacts: %s", name, sorted(index))
                continue

            selected.update(f for f in index[name] if os.path.isfile(os.path.join(self._artifact_directory, f)))

        return sorted(selected)

    def download(self, artifacts: Artifacts, exit_code: int = 0) -> None:
        """Collect the artifacts defined by the step from the step container, after the step ran."""
        definitions = artifacts.get_definitions()
        if not definitions:
            return

        logger.info("Collecting artifacts")

        t = ts()
        total_size = 0
        index = self._index.load()

        with self._runner as container:
            for i, definition in enumerate(definitions):
                if not definition.capture_on.matches(exit_code):
                    logger.info(
                        "Skipping artifact '%s': capture-on is '%s' and step exit code is %d",
                        definition.name,
                        definition.capture_on.value,
                        exit_code,
                    )
                    continue

                target_directory = (
                    self._artifact_directory if definition.is_shared else self._get_scoped_directory(definition)
                )

                files, size = self._collect(
                    container, definition, f"artifacts-{self._step_uuid}-{i}.tar", target_directory
                )
                total_size += size

                if definition.is_shared:
                    index[definition.name] = sorted(files)
                elif files:
                    logger.info(
                        "Artifact '%s' is %s, saved to %s (not available to other steps)",
                        definition.name,
                        definition.type.value,
                        target_directory,
                    )

        self._index.save(index)

        t = ts() - t

        logger.info(
            "Artifacts saved %s to %s in %.3fs",
            get_human_readable_size(total_size),
            self._artifact_directory,
            t,
        )

    def _get_scoped_directory(self, definition: ArtifactDefinition) -> str:
        return os.path.join(
            f"{os.path.normpath(self._artifact_directory)}-scoped",
            str(self._step_uuid),
            slugify(definition.name),
        )

    @staticmethod
    def _build_find_args(definition: ArtifactDefinition) -> list[str]:
        """Build the (unquoted) argv of the `find` command selecting the files of an artifact."""

        def _path_filters(globs: list[str]) -> list[str]:
            args = ["("]
            for i, glob in enumerate(globs):
                if i:
                    args.append("-o")
                args += ["-path", f"./{glob}"]
            args.append(")")
            return args

        args = ["find"]
        if definition.depth is not None:
            args += ["-maxdepth", str(definition.depth)]

        args += ["-type", "f", *_path_filters(definition.paths)]

        if definition.ignore_paths:
            args += ["!", *_path_filters(definition.ignore_paths)]

        return args

    @classmethod
    def _build_prepare_command(cls, definition: ArtifactDefinition, artifact_file: str) -> str:
        # Every argument is quoted individually; only the pipe is interpreted by the shell.
        find_args = cls._build_find_args(definition)
        tar_args = ["tar", "cf", artifact_file, "-C", config.build_dir, "-T", "-"]

        return f"{shlex.join(find_args)} | {shlex.join(tar_args)}"

    def _collect(
        self, container: Container, definition: ArtifactDefinition, artifact_file: str, target_directory: str
    ) -> tuple[list[str], int]:
        artifact_remote_path = os.path.join(config.build_dir, artifact_file)

        cmd = wrap_in_shell(self._build_prepare_command(definition, artifact_file))
        logger.debug("preparing artifact '%s' cmd: %s", definition.name, cmd)
        exit_code, output = container.exec_run(cmd)
        if exit_code != 0:
            output_str = output.decode()
            if "empty archive" in output_str:
                logger.info("No files found for artifact '%s'. Skipping", definition.name)
                return [], 0

            raise ArtifactManagementError(f"Error preparing artifact '{definition.name}': {output_str}")

        data, stats = container.get_archive(artifact_remote_path, encode_stream=True)
        logger.debug("artifact '%s' stats: %s", definition.name, stats)

        files = []
        os.makedirs(target_directory, exist_ok=True)

        # FileStreamer only implements `read` which is all that is needed.
        with tarfile.open(fileobj=FileStreamer(data), mode="r|") as wrapper_tar:  # type: ignore[abstract,call-overload]
            for entry in wrapper_tar:
                with tarfile.open(fileobj=wrapper_tar.extractfile(entry), mode="r|") as tar:
                    files += safe_extract_tar(tar, target_directory)

        container.exec_run(["rm", "-f", artifact_remote_path])

        return files, int(stats["size"])


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
