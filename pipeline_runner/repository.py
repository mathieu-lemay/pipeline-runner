import logging
from typing import Dict, Optional, Union

from .config import config
from .models import CloneSettings, Image, Repository

logger = logging.getLogger(__name__)


class RepositoryCloner:
    def __init__(
        self,
        repository: Repository,
        step_clone_settings: CloneSettings,
        global_clone_settings: CloneSettings,
        environment: Dict[str, str],
        user: Optional[Union[int, str]],
        parent_container_name: str,
        data_volume_name: str,
        output_logger: logging.Logger,
    ):
        self._repository = repository
        self._step_clone_settings = step_clone_settings
        self._global_clone_settings = global_clone_settings
        self._environment = environment
        self._user = str(user) if user is not None else None
        self._name = f"{parent_container_name}-clone"
        self._data_volume_name = data_volume_name
        self._output_logger = output_logger

        self._container = None

    def clone(self):
        # TODO: Fix cyclic import
        from .container import ContainerRunner

        if not self._should_clone():
            logger.info("Clone disabled: skipping")
            return

        image = Image(name="alpine/git", run_as_user=self._user)
        runner = ContainerRunner(
            self._name,
            image,
            None,
            self._repository.path,
            self._data_volume_name,
            self._environment,
            self._output_logger,
        )
        runner.start()

        try:
            clone_script = self._get_clone_script()
            exit_code = runner.run_script(clone_script)

            if exit_code:
                raise Exception("Error setting up repository")
        finally:
            runner.stop()

    def _get_clone_script(self) -> [str]:
        origin = self._get_origin()
        git_clone_cmd = self._get_clone_command(origin)

        return [
            git_clone_cmd,
            "git reset --hard $BITBUCKET_COMMIT",
            "git config user.name bitbucket-pipelines",
            "git config user.email commits-noreply@bitbucket.org",
            "git config push.default current",
            # "git config http.${BITBUCKET_GIT_HTTP_ORIGIN}.proxy http://localhost:29418/",
            f"git remote set-url origin {origin}",
            "git reflog expire --expire=all --all",
            "echo '.bitbucket/pipelines/generated' >> .git/info/exclude",
        ]

    @staticmethod
    def _get_origin() -> str:
        # https://x-token-auth:$REPOSITORY_OAUTH_ACCESS_TOKEN@bitbucket.org/$BITBUCKET_REPO_FULL_NAME.git
        return f"file://{config.remote_workspace_dir}"

    def _get_clone_command(self, origin) -> str:
        git_clone_cmd = []

        if not self._should_clone_lfs():
            git_clone_cmd += ["GIT_LFS_SKIP_SMUDGE=1"]

        # TODO: Add `retry n`
        branch = self._repository.get_current_branch()
        git_clone_cmd += ["git", "clone", f"--branch='{branch}'"]

        clone_depth = self._get_clone_depth()
        if clone_depth:
            git_clone_cmd += ["--depth", str(clone_depth)]

        git_clone_cmd += [origin, "$BUILD_DIR"]

        return " ".join(git_clone_cmd)

    def _should_clone(self) -> bool:
        return bool(
            self._first_non_none_value(
                self._step_clone_settings.enabled,
                self._global_clone_settings.enabled,
                CloneSettings.construct().enabled,
            )
        )

    def _should_clone_lfs(self) -> bool:
        return bool(
            self._first_non_none_value(
                self._step_clone_settings.lfs,
                self._global_clone_settings.lfs,
                CloneSettings.construct().lfs,
            )
        )

    def _get_clone_depth(self) -> Optional[int]:
        return self._first_non_none_value(
            self._step_clone_settings.depth,
            self._global_clone_settings.depth,
            CloneSettings.construct().depth,
        )

    @staticmethod
    def _first_non_none_value(*args) -> Optional[object]:
        return next((v for v in args if v is not None), None)
