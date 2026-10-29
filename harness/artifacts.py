"""Artefatos e estado dos módulos, exclusivamente em .harness."""

from dataclasses import asdict
from datetime import datetime, timezone
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from .domain import WizardState


class InitError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_status(project_dir: Path) -> dict:
    root = project_dir / ".harness"
    if root.is_symlink():
        raise InitError("A pasta .harness não pode ser um link simbólico.")
    path = root / "status.json"
    if not path.exists():
        return {"version": 1, "modules": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("modules"), dict):
            raise ValueError()
        return data
    except (OSError, ValueError, AttributeError):
        raise InitError("Não foi possível ler .harness/status.json. O arquivo foi preservado.") from None


def suggested_module(project_dir: Path) -> str:
    modules = read_status(project_dir)["modules"]
    completed = modules.get("init", {})
    final = project_dir / ".harness/prompts/final.md"
    if completed.get("status") != "completed" or not final.is_file():
        return "init"
    if digest(final.read_text(encoding="utf-8")) != completed.get("prompt_sha256"):
        return "init"
    return "build" if modules.get("plan", {}).get("status") == "completed" else "plan"


class ArtifactStore:
    def __init__(self, project_dir: Path) -> None:
        self.root = project_dir / ".harness"
        self.run_id = str(uuid4())
        self.lock = None
        self.started = False

    def path(self, relative: str) -> Path:
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise InitError("Um caminho de artefato saiu da pasta .harness.")
        return path

    def write(self, relative: str, text: str) -> None:
        atomic_write(self.path(relative), text)

    def json(self, relative: str, data) -> None:
        self.write(relative, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def read_json(self, relative: str, default=None):
        path = self.path(relative)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def begin(self, state: WizardState) -> dict:
        if self.root.is_symlink():
            raise InitError("A pasta .harness não pode ser um link simbólico.")
        marker = self.root / "owner.json"
        if self.root.exists() and any(self.root.iterdir()):
            try:
                if json.loads(marker.read_text(encoding="utf-8")).get("owner") != "capivara2":
                    raise ValueError()
            except (OSError, ValueError, AttributeError):
                raise InitError("A pasta .harness contém material não reconhecido. Seu conteúdo foi preservado.") from None
        self.root.mkdir(mode=0o700, exist_ok=True)
        lock_path = self.root / ".init.lock"
        if lock_path.is_symlink():
            raise InitError("O arquivo de controle de execução não pode ser um link simbólico.")
        self.lock = open(lock_path, "a", encoding="utf-8")
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise InitError("Já existe um init em execução para este projeto.") from None
        # Reexecutar init substitui os artefatos anteriores e invalida plan/build.
        for child in self.root.iterdir():
            if child.name == ".init.lock":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.json("owner.json", {"owner": "capivara2", "version": 1})
        for directory in ("log", "enterviews", "prompts", "control", "context"):
            self.path(directory).mkdir(mode=0o700)
        self.json("status.json", {"version": 1, "modules": {
            "init": {"status": "running", "run_id": self.run_id, "started_at": now(), "stage": "starting"},
            "plan": {"status": "pending"}, "build": {"status": "pending"}}})
        self.started = True
        self.write("prompts/initial.md", state.prompt)
        self.json("control/settings.json", {"writer": asdict(state.writer), "reviewer": asdict(state.reviewer)})
        source = {"source": state.prompt_source, "initial_prompt": state.prompt,
                  "prompt_file": str(state.prompt_file) if state.prompt_file else None, "documents": []}
        if state.mcp_context:
            context = state.mcp_context
            source["mcp"] = {"instance_id": context.instance_id, "instance_name": context.instance_name,
                             "endpoint": context.endpoint, "project": asdict(context.project)}
            self.json("context/mcp.json", asdict(context))
            for document in context.documents:
                for content in document.contents:
                    entry = {"uri": document.uri, "name": document.name, "kind": document.kind,
                             "attachment": document.attachment, "mime_type": content.mime_type, "text": content.text}
                    if content.blob is not None:
                        blob = base64.b64decode(content.blob, validate=True)
                        filename = f"context/assets/{hashlib.sha256(blob).hexdigest()}.bin"
                        destination = self.path(filename)
                        destination.parent.mkdir(mode=0o700, exist_ok=True)
                        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(blob)
                        entry["binary_artifact"] = filename
                        entry["binary_note"] = "Anexo binário preservado; não presumir conteúdo não observado."
                    source["documents"].append(entry)
        self.json("context/source.json", source)
        return source

    def event(self, stage: str, message: str, **details) -> None:
        entry = {"time": now(), "stage": stage, "message": message, **details}
        with self.path("log/events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(self.path("log/events.jsonl"), 0o600)
        data = self.read_json("status.json")
        data["modules"]["init"].update(stage=stage, updated_at=now())
        self.json("status.json", data)

    def finish(self, status: str, **details) -> None:
        if self.started:
            data = self.read_json("status.json")
            data["modules"]["init"].update(status=status, finished_at=now(), **details)
            self.json("status.json", data)

    def close(self) -> None:
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None
