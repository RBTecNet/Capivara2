"""Estado do plan isolado, preservando integralmente os artefatos do init."""

from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import shutil

from .artifacts import ArtifactStore, InitError, atomic_write, digest, now, read_status
from .init_models import FinalPrompt, Review
from .plan_models import FinalPlan, PlanReview


def load_init(project_dir: Path) -> dict:
    completed = read_status(project_dir)["modules"].get("init", {})
    if completed.get("status") != "completed":
        raise InitError("Conclua o init antes de executar o plan.")
    try:
        store = ArtifactStore(project_dir)
        markdown = store.path("prompts/final.md").read_text(encoding="utf-8")
        document = FinalPrompt.model_validate(store.read_json("prompts/final.json"))
        approval = store.read_json("control/approval.json")
        if (digest(markdown) != completed.get("prompt_sha256") or document.render() != markdown
                or approval["prompt_sha256"] != digest(markdown)
                or not Review.model_validate(approval["review"]).approved):
            raise ValueError("aprovação ou conteúdo divergente")
        original = store.path("prompts/initial.md").read_text(encoding="utf-8")
        return {"original_prompt": original, "approved_prompt": markdown,
                "source_prompt_sha256": digest(markdown), "document": document.model_dump()}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise InitError("O prompt aprovado do init está ausente, alterado ou inconsistente. Execute o init novamente.") from exc


def plan_completed(project_dir: Path, source_hash: str) -> bool:
    status = read_status(project_dir)["modules"].get("plan", {})
    if status.get("status") != "completed" or status.get("source_prompt_sha256") != source_hash:
        return False
    try:
        store = PlanStore(project_dir)
        if store.root.is_symlink():
            return False
        plan = FinalPlan.model_validate(store.read_json("final.json"))
        markdown = store.path("final.md").read_text(encoding="utf-8")
        approval = store.read_json("control/approval.json")
        return (plan.source_prompt_sha256 == source_hash and plan.render() == markdown
                and approval["source_prompt_sha256"] == source_hash
                and digest(markdown) == status.get("plan_sha256") == approval["plan_sha256"]
                and digest(store.path("final.json").read_text(encoding="utf-8")) == status.get("plan_json_sha256")
                and PlanReview.model_validate(approval["review"]).approved
                and set(approval["approved_phases"]) == {phase.number for phase in plan.phases}
                and set(approval["approved_tasks"]) == {task.id for phase in plan.phases for task in phase.tasks})
    except (OSError, ValueError, TypeError, KeyError):
        return False


class PlanStore(ArtifactStore):
    def __init__(self, project_dir: Path) -> None:
        super().__init__(project_dir)
        self.project_dir = project_dir
        self.project_root = self.root
        self.root = self.project_root / "plan"

    def begin(self, state) -> dict:
        if self.project_root.is_symlink() or self.root.is_symlink():
            raise InitError("As pastas de controle não podem ser links simbólicos.")
        load_init(self.project_dir)
        marker = ArtifactStore(self.project_dir).read_json("owner.json")
        if not isinstance(marker, dict) or marker.get("owner") != "capivara2":
            raise InitError("A pasta .harness não é reconhecida; seu conteúdo foi preservado.")
        lock_path = self.project_root / ".init.lock"
        if lock_path.is_symlink():
            raise InitError("O arquivo de controle de execução não pode ser um link simbólico.")
        self.lock = open(lock_path, "a", encoding="utf-8")
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.close()
            raise InitError("Já existe um módulo em execução para este projeto.") from None
        source = load_init(self.project_dir)
        if state.module == "plan" and state.prompt and state.prompt != source["approved_prompt"]:
            raise InitError("O prompt do init mudou após a configuração. Volte à seleção do módulo e escolha plan novamente.")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(mode=0o700)
        for directory in ("log", "control", "phases", "context"):
            self.path(directory).mkdir(mode=0o700)
        self.json("source.json", source)
        self.json("control/settings.json", {"writer": asdict(state.writer), "reviewer": asdict(state.reviewer),
                                          "context_policy": "fresh"})
        data = read_status(self.project_dir)
        data["modules"]["plan"] = {"status": "running", "run_id": self.run_id, "started_at": now(),
                                   "stage": "starting", "source_prompt_sha256": source["source_prompt_sha256"]}
        data["modules"]["build"] = {"status": "pending"}
        self.status(data)
        self.started = True
        return source

    def status(self, data: dict) -> None:
        atomic_write(self.project_root / "status.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def event(self, stage: str, message: str, **details) -> None:
        # O log é exclusivo do plan; o status compartilhado pertence ao módulo atual.
        entry = {"time": now(), "stage": stage, "message": message, **details}
        path = self.path("log/events.jsonl")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
        data = read_status(self.project_dir)
        data["modules"]["plan"].update(stage=stage, updated_at=now())
        self.status(data)

    def finish(self, status: str, **details) -> None:
        if self.started:
            data = read_status(self.project_dir)
            data["modules"]["plan"].update(status=status, finished_at=now(), **details)
            self.status(data)
