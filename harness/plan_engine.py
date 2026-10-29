"""Planejamento por recortes, com validação documental e sessões independentes."""

import asyncio
import json
import re

from .artifacts import InitError, digest
from .plan_artifacts import PlanStore, load_init
from .plan_instructions import REVIEWER, WRITER
from .plan_models import (FinalPlan, PhaseOutline, PlannedPhase, PlannedTask,
                          PlanReview, TaskContent, TaskSeed)
from .run_events import InitEvent, PlanItem


class PlanEngine:
    def __init__(self, state, agents, *, on_event=None):
        self.state = state
        self.agents = agents
        self.store = PlanStore(state.project_dir)
        self.on_event = on_event
        self.stage = "starting"
        self.round = self.call_id = self.operations = self.corrections = 0
        self.active_role = self.last_role = None
        self.source = {}
        self.origins = {}
        self.phases = []
        self.phase_number = None
        self.approved_tasks = set()
        self.approved_phases = set()
        self.review_id = 0
        self.item_states = {}

    def item_state(self, item_id, **states):
        self.item_states.setdefault(item_id, {}).update(states)
        self.emit("items_changed")

    def park_items(self):
        for states in self.item_states.values():
            for key, state in states.items():
                if state == "running":
                    states[key] = "waiting"
        self.emit("items_changed")

    def items(self):
        rows = []
        for number, origin in self.origins.items():
            phase_id = f"P{number}"
            rows.append(PlanItem(phase_id, origin["title"], "phase", **self.item_states.get(phase_id, {})))
            phase = next((p for p in self.phases if p.number == number), None)
            if phase:
                rows.extend(PlanItem(task.id, task.content.title, "task", **self.item_states.get(task.id, {}))
                            for task in phase.tasks)
        return tuple(rows)

    def emit(self, kind):
        if self.on_event:
            self.on_event(InitEvent(kind, self.stage, self.round, self.active_role, self.last_role,
                self.operations, self.call_id, self.corrections, 0, 0, 0, module="plan",
                phase_number=self.phase_number, phases_total=len(self.origins),
                phases_approved=len(self.approved_phases),
                tasks_total=sum(len(phase.tasks) for phase in self.phases),
                tasks_approved=len(self.approved_tasks), items=self.items()))

    def announce(self, stage, message):
        self.stage = stage
        if stage == "revision_requested":
            self.corrections += 1
        self.store.event(stage, message, phase=self.phase_number, round=self.round)
        self.emit("stage_changed")

    async def call(self, role, task, data, model, check=None):
        self.operations += 1
        feedback = ""
        selection = self.state.reviewer if role == "reviewer" else self.state.writer
        schema = model.model_json_schema()
        while True:
            self.call_id += 1
            # Nenhum histórico é reutilizado, nem em correções de formato.
            envelope = {"preserved_context": [], "context_policy": "fresh",
                        "current_request": {"task": task, "data": data}, "format_correction": feedback}
            self.store.json(f"context/call-{self.call_id:04d}.json", envelope)
            prompt = (REVIEWER if role == "reviewer" else WRITER) + "\n\nSCHEMA JSON:\n" + json.dumps(schema, ensure_ascii=False)
            prompt += "\n\nREQUEST:\n" + json.dumps(envelope, ensure_ascii=False)
            self.active_role = self.last_role = role
            self.emit("agent_started")
            try:
                output = await self.agents.generate(selection, role, prompt, schema, self.store, self.call_id)
            except asyncio.CancelledError:
                self.active_role = None
                self.emit("agent_cancelled")
                raise
            except Exception:
                self.active_role = None
                self.emit("agent_failed")
                raise
            self.active_role = None
            self.emit("agent_finished")
            self.store.write(f"log/{role}-{self.call_id:04d}.response.txt", output)
            try:
                text = output.strip()
                if text.startswith("```") and text.endswith("```"):
                    text = "\n".join(text.splitlines()[1:-1])
                value = model.model_validate_json(text)
                if check:
                    check(value)
                return value
            except ValueError as exc:
                feedback = str(exc)
                self.corrections += 1
                self.store.event("format_correction", "Contrato documental devolvido ao agente.",
                                 role=role, call=self.call_id, detail=feedback)
                self.emit("format_correction")

    def prepare_origins(self):
        document = self.source["document"]
        text = self.source["approved_prompt"]
        sections = list(re.finditer(r"^## Fase (\d+) — ", text, re.MULTILINE))
        for index, match in enumerate(sections):
            end = sections[index + 1].start() if index + 1 < len(sections) else text.find("## Critérios de conclusão", match.end())
            self.origins[int(match.group(1))] = {"text": text[match.start():end if end >= 0 else len(text)].strip()}
        self.origins[0].update(number=0, title="Stack de implantação e dependências de desenvolvimento e testes",
            goal="Documentar a stack e as dependências aprovadas de implantação, desenvolvimento e testes.", depends_on=[])
        for phase in document["phases"]:
            self.origins[phase["number"]].update({key: phase[key] for key in ("number", "title", "goal", "depends_on")})

    def check_seed(self, seed, number):
        text = self.origins[number]["text"]
        if any(not quote.strip() or quote not in text for quote in seed.source_quotes):
            raise ValueError("As referências devem ser recortes literais da fase correspondente; não invente nem concatene citações.")

    def correct_seed(self, task, correction):
        if correction.get("replacement_seed") is not None:
            task.seed = TaskSeed.model_validate(correction["replacement_seed"])

    def new_task(self, phase, seed):
        number = max((int(task.id.split("-T")[1]) for task in phase.tasks), default=0) + 1
        return self.new_task_stub(phase.number, seed, number)

    def checkpoint(self):
        self.store.json("control/checkpoint.json", {"round": self.round, "phase": self.phase_number,
            "approved_tasks": sorted(self.approved_tasks), "approved_phases": sorted(self.approved_phases),
            "phases": [phase.model_dump() for phase in self.phases]})
        self.emit("counts_changed")

    def review_data(self, phase=None, task=None):
        data = {"source": self.source}
        if phase is not None:
            data["phase_source"] = self.origins[phase.number]
        if task is not None:
            data["task"] = task.model_dump()
        elif phase is not None:
            data["phase"] = phase.model_dump()
        else:
            data["phases"] = [phase.model_dump() for phase in self.phases]
        return data

    async def review(self, scope, phase=None, task=None):
        phases = [phase] if phase is not None else self.phases
        task_ids = {task.id} if task is not None else {item.id for p in phases for item in p.tasks}
        phase_ids = {p.number for p in phases}

        def check(value):
            for correction in value.corrections:
                if correction.task_id not in task_ids:
                    raise ValueError("A correção deve apontar uma task existente no recorte em revisão.")
                if correction.replacement_seed is not None:
                    number = next(p.number for p in phases if any(t.id == correction.task_id for t in p.tasks))
                    self.check_seed(correction.replacement_seed, number)
            if task is not None and value.additions:
                raise ValueError("Na revisão individual, peça correção da única task. Adições pertencem à revisão da fase.")
            for addition in value.additions:
                if addition.phase_number not in phase_ids:
                    raise ValueError("A adição deve pertencer a uma fase em revisão.")
                target = next(p for p in phases if p.number == addition.phase_number)
                if addition.after_task_id and addition.after_task_id not in {item.id for item in target.tasks}:
                    raise ValueError("after_task_id deve ser vazio ou uma task existente nessa fase.")
                self.check_seed(addition.task, addition.phase_number)

        verdict = await self.call("reviewer", f"review_{scope}", self.review_data(phase, task), PlanReview, check)
        self.review_id += 1
        self.store.json(f"control/reviews/{self.review_id:04d}.json", {"scope": scope,
            "phase": phase.number if phase else None, "task_id": task.id if task else None,
            "review": verdict.model_dump()})
        return verdict

    async def write_task(self, phase, task, correction=None):
        self.approved_tasks.discard(task.id)
        self.approved_phases.discard(phase.number)
        while True:
            if correction:
                self.correct_seed(task, correction)
            self.item_state(task.id, creation="running")
            self.announce("writing_task", f"Escritor redigindo {task.id} com contexto novo.")
            data = {"task_id": task.id, "phase_number": phase.number, "phase_title": phase.title,
                    "objective": self.source["document"]["objective"],
                    "constraints": self.source["document"]["constraints"], "seed": task.seed.model_dump(),
                    "previous_task": task.content.model_dump() if correction else None,
                    "correction": correction}
            task.content = await self.call("writer", "write_task", data, TaskContent)
            self.store.json(f"phases/{phase.number:02d}/tasks/{task.id}.json", task.model_dump())
            self.item_state(task.id, creation="completed", validation="running")
            self.announce("reviewing_task", f"Revisor comparando {task.id} com a origem.")
            verdict = await self.review("task", phase, task)
            if verdict.approved:
                self.item_state(task.id, validation="completed")
                self.approved_tasks.add(task.id)
                self.checkpoint()
                return
            self.item_state(task.id, validation="rejected")
            self.announce("revision_requested", f"Correção documental solicitada para {task.id}.")
            correction = verdict.corrections[0].model_dump()

    async def apply(self, verdict):
        affected = set()
        for correction in verdict.corrections:
            phase = next(p for p in self.phases if any(t.id == correction.task_id for t in p.tasks))
            task = next(t for t in phase.tasks if t.id == correction.task_id)
            self.phase_number = phase.number
            self.item_state(f"P{phase.number}", validation="rejected")
            self.item_state(task.id, validation="rejected")
            await self.write_task(phase, task, correction.model_dump())
            affected.add(phase.number)
        # Inserções na mesma posição mantêm a ordem indicada pelo revisor.
        anchors = {}
        for addition in verdict.additions:
            phase = next(p for p in self.phases if p.number == addition.phase_number)
            task = self.new_task(phase, addition.task)
            anchor = anchors.get((phase.number, addition.after_task_id), addition.after_task_id)
            index = next((i + 1 for i, item in enumerate(phase.tasks) if item.id == anchor), 0)
            phase.tasks.insert(index, task)
            self.approved_phases.discard(phase.number)
            self.item_state(f"P{phase.number}", validation="rejected")
            anchors[(phase.number, addition.after_task_id)] = task.id
            self.phase_number = phase.number
            await self.write_task(phase, task)
            affected.add(phase.number)
        return affected

    async def validate_phase(self, phase):
        self.phase_number = phase.number
        while True:
            self.item_state(f"P{phase.number}", validation="running")
            self.announce("reviewing_phase", f"Revisor validando cobertura da fase {phase.number}.")
            verdict = await self.review("phase", phase)
            if verdict.approved:
                self.item_state(f"P{phase.number}", validation="completed")
                self.approved_phases.add(phase.number)
                self.store.json(f"phases/{phase.number:02d}/approved.json", phase.model_dump())
                self.checkpoint()
                return
            self.round += 1
            self.item_state(f"P{phase.number}", validation="rejected")
            self.announce("revision_requested", f"Revisor devolveu recortes da fase {phase.number}.")
            await self.apply(verdict)

    async def run(self):
        if not self.state.writer or not self.state.reviewer:
            raise InitError("Selecione escritor e revisor antes de executar o plan.")
        try:
            self.source = self.store.begin(self.state)
            self.prepare_origins()
            self.round = 1
            for number, origin in self.origins.items():
                self.phase_number = number
                self.item_state(f"P{number}", creation="running")
                self.announce("decomposing", f"Decompondo a fase {number} em recortes pequenos.")

                def check(outline):
                    for seed in outline.tasks:
                        self.check_seed(seed, number)

                outline = await self.call("reviewer", "outline_phase", {
                    "source": self.source, "phase_source": origin}, PhaseOutline, check)
                self.store.json(f"phases/{number:02d}/outline.json", outline.model_dump())
                phase = PlannedPhase(**{key: origin[key] for key in ("number", "title", "goal", "depends_on")},
                    tasks=[self.new_task_stub(number, seed, i) for i, seed in enumerate(outline.tasks, 1)])
                self.phases.append(phase)
                self.item_state(f"P{number}", creation="completed")
                self.checkpoint()
                for task in list(phase.tasks):
                    await self.write_task(phase, task)
                await self.validate_phase(phase)
            while True:
                self.phase_number = None
                self.announce("reviewing_plan", "Comparando o plano completo com os documentos originais.")
                verdict = await self.review("plan")
                if verdict.approved:
                    break
                self.round += 1
                self.announce("revision_requested", "Revisor apontou correções pontuais no plano.")
                affected = await self.apply(verdict)
                for phase in self.phases:
                    if phase.number in affected:
                        await self.validate_phase(phase)
            # Detectar edição externa do init antes de liberar o build.
            if load_init(self.state.project_dir) != self.source:
                raise InitError("Os documentos do init mudaram durante o plan. Execute o plan novamente.")
            plan = FinalPlan(title=f"Plano de implementação — {self.source['document']['title']}",
                objective=self.source["document"]["objective"], source_prompt_sha256=self.source["source_prompt_sha256"],
                phases=self.phases)
            if len(self.approved_phases) != len(self.origins) or len(self.approved_tasks) != sum(len(p.tasks) for p in self.phases):
                raise InitError("Existem tasks ou fases sem aprovação documental.")
            markdown = plan.render()
            self.store.write("final.md", markdown)
            self.store.json("final.json", plan.model_dump())
            self.store.json("control/approval.json", {"plan_sha256": digest(markdown),
                "source_prompt_sha256": self.source["source_prompt_sha256"], "review": verdict.model_dump(),
                "approved_tasks": sorted(self.approved_tasks), "approved_phases": sorted(self.approved_phases)})
            self.announce("completed", "Plan concluído: tasks, fases e plano aprovados.")
            self.store.finish("completed", plan_sha256=digest(markdown),
                plan_json_sha256=digest(self.store.path("final.json").read_text(encoding="utf-8")),
                phases=len(self.phases), tasks=len(self.approved_tasks), rounds=self.round,
                final_plan="plan/final.md", approval="plan/control/approval.json")
            return self.store.path("final.md")
        except asyncio.CancelledError:
            self.park_items()
            self.store.finish("cancelled", reason="Execução interrompida pelo usuário.")
            raise
        except Exception as exc:
            self.park_items()
            self.store.finish("failed", reason=str(exc))
            raise
        finally:
            self.store.close()

    @staticmethod
    def new_task_stub(number, seed, index):
        return PlannedTask(id=f"P{number}-T{index:03d}", phase_number=number, seed=seed,
            content=TaskContent(title=seed.title, objective=seed.scope,
                                implementation_steps=[seed.scope], deliverables=[seed.title]))
