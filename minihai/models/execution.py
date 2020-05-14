import json
import logging
import pathlib
from typing import Optional
from uuid import UUID

import pydantic
import ulid2
from docker.models.containers import Container

from minihai.conf import docker_client
from minihai.models.base import BaseModel
from minihai.services.docker import get_container_logs

log = logging.getLogger(__name__)


class ExecutionCreationData(pydantic.BaseModel):
    commit: str
    project: UUID
    inputs: dict
    parameters: dict
    environment_variables: dict
    step: str
    image: str
    title: str = ""
    environment: UUID


class Execution(BaseModel):
    kind = "execution"
    path_group_len = 8

    @property
    def outputs_path(self) -> pathlib.Path:
        path = self.path / "outputs"
        path.mkdir(parents=True)
        return path

    def get_log_path(self, name) -> pathlib.Path:
        return self.path / f"{name}.log"

    @property
    def all_json_log_path(self):
        return self.path / f"log.json"

    @property
    def status(self) -> str:
        final_state = self.metadata.get("container_final_state")
        if final_state and final_state.get("Error"):
            return "error"
        container_exit_code = self.metadata.get("container_exit_code")
        if container_exit_code is not None:
            if container_exit_code == 0:
                return "complete"
            return "error"

        if self.metadata.get("container_id"):
            return "running"
        return "queued"

    @property
    def container(self) -> Optional[Container]:
        container_id = self.metadata.get("container_id")
        if not container_id:
            return None
        return docker_client.containers.get(container_id=container_id)

    @classmethod
    def create(cls, data: ExecutionCreationData):
        id = str(ulid2.generate_ulid_as_uuid())
        execution = cls.create_with_metadata(id=id, data={**data.dict(),})
        return execution

    def check_container_status(self: "Execution"):
        container = self.container
        if not container:
            return

        state = container.attrs["State"]
        status = state.get("Status")
        if status in ("exited", "dead"):
            if self.metadata.get("container_final_state") is None:
                self.update_metadata(
                    {
                        "container_exit_code": state.get("ExitCode"),
                        "container_final_state": state,
                    }
                )
            stdout_log_path = self.get_log_path("stdout")
            if not stdout_log_path.exists():
                stdout_log_path.write_bytes(
                    container.logs(stdout=True, stderr=False, timestamps=True)
                )
                log.info(f"{self.id}: Wrote stdout to {stdout_log_path}")
            stderr_log_path = self.get_log_path("stderr")
            if not stderr_log_path.exists():
                stderr_log_path.write_bytes(
                    container.logs(stdout=False, stderr=True, timestamps=True)
                )
                log.info(f"{self.id}: Wrote stderr to {stderr_log_path}")
            all_json_log_path = self.all_json_log_path
            if not all_json_log_path.exists():
                with all_json_log_path.open("w") as outf:
                    json.dump(get_container_logs(container), outf, indent=2)
                log.info(f"{self.id}: Wrote JSON log to {all_json_log_path}")

    def get_logs(self) -> Optional[list]:
        if self.all_json_log_path.exists():
            with self.all_json_log_path.open("r") as fp:
                return json.load(fp)
        container = self.container
        if not container:
            return None
        return get_container_logs(container)
