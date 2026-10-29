"""Planejamento por recortes, com validação documental e sessões independentes."""

import asyncio
import json
import re

from .artifacts import InitError, digest
from .plan_artifacts import PlanStore, load_init
from .plan_control import IssueLedger, prepare_changes, stub, version
from .plan_coverage import (build_requirements, check_assignments, check_seed,
                            coverage_map, delegate_validations, requests_structural_operation)
from .plan_instructions import REVIEWER, WRITER
from .plan_models import (FinalPlan, PhaseOutline, PlannedPhase, PlannedTask,
                          PlanRepair, PlanReview, TaskContent, TaskSeed, agent_schema)
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
        self.requirements = {}
        self.ledger = IssueLedger()
        self.task_sequences = {}
        self.created_tasks = set()
        self.approved_versions = {}
        self.pending_feedback = {}
        self.repair_id = 0

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
        schema = agent_schema(model)
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
        check_seed(seed, number, self.origins[number]["text"], self.requirements)

    def checkpoint(self):
        self.store.json("control/checkpoint.json", {"round": self.round, "phase": self.phase_number,
            "approved_tasks": sorted(self.approved_tasks), "approved_phases": sorted(self.approved_phases),
            "approved_versions": self.approved_versions, "task_sequences": self.task_sequences,
            "phases": [phase.model_dump() for phase in self.phases]})
        self.store.json("control/coverage.json", coverage_map(self.phases, self.requirements))
        self.store.json("control/issues.json", self.ledger.dump())
        self.emit("counts_changed")

    def task_context(self, phase, task):
        assigned = {claim.requirement_id for claim in task.seed.coverage}
        return {"objective": self.source["document"]["objective"],
            "constraints": self.source["document"]["constraints"],
            "assigned_requirements": [self.requirements[key].model_dump() for key in sorted(assigned)],
            "scope_decisions": self.ledger.context({task.id}),
            "technology": {"stack": self.source["document"]["stack"],
                           "tools": self.source["document"]["tools"]} if phase.number else {}}

    def review_data(self, phase=None, task=None):
        if task is not None:
            data = self.task_context(phase, task)
            data["task"] = task.model_dump()
            ids = {claim.requirement_id for claim in task.seed.coverage}
            data["responsibility_map"] = [entry for entry in coverage_map([phase], self.requirements)
                                           if entry["requirement"]["id"] in ids]
            return data
        phases = [phase] if phase else self.phases
        data = {"source": self.source, "coverage_map": coverage_map(phases, self.requirements),
                "scope_decisions": self.ledger.context({t.id for p in phases for t in p.tasks})}
        if phase is not None:
            data.update(phase_source=self.origins[phase.number], phase=phase.model_dump())
            data["review_boundary"] = ("Somente catálogo de tecnologias e ferramentas; finalidades não são execução de QA."
                                       if phase.number == 0 else
                                       "Cobertura coletiva somente desta fase. Respeitar delegações ao build e tarefas das outras fases.")
        else:
            data["phases"] = [p.model_dump() for p in self.phases]
        return data

    def check_repair(self, value, phases):
        requirements = self.requirements
        for phase in phases:
            selected = [d for d in value.delegations if d.requirement_id in requirements
                        and requirements[d.requirement_id].phase_number == phase.number]
            requirements = delegate_validations(requirements, selected, phase.number)
        if any(d.requirement_id not in requirements or requirements[d.requirement_id].phase_number not in {p.number for p in phases}
               for d in value.delegations):
            raise ValueError("A delegação deve apontar requisitos existentes deste recorte.")
        ids = {t.id for p in phases for t in p.tasks}
        touched = {c.task_id for c in value.corrections}
        touched.update(identifier for change in value.structural_changes for identifier in change.task_ids)
        if not touched.issubset(ids) or any(a.phase_number not in {p.number for p in phases} for a in value.additions):
            raise ValueError("O reparo deve alterar somente tasks/fases existentes neste recorte de revisão.")
        return (*prepare_changes(self.phases, value, requirements, self.origins, self.task_sequences), requirements)

    async def review(self, scope, phase=None, task=None):
        phases = [phase] if phase is not None else self.phases
        def check(value):
            if task is not None:
                if value.additions or value.structural_changes or value.delegations:
                    raise ValueError("Na revisão individual, solicite reavaliação com category=structure; operações são coletivas.")
                for correction in value.corrections:
                    if correction.task_id != task.id:
                        raise ValueError("Corrija somente a task em revisão.")
                    if requests_structural_operation(correction.instruction):
                        raise ValueError("Solicite reavaliação com category=structure; remoção não é trabalho do escritor.")
                    if correction.replacement_seed:
                        self.check_seed(correction.replacement_seed, phase.number)
            elif not value.approved:
                self.check_repair(value, phases)
        verdict = await self.call("reviewer", f"review_{scope}", self.review_data(phase, task), PlanReview, check)
        self.review_id += 1
        self.store.json(f"control/reviews/{self.review_id:04d}.json", {"scope": scope,
            "phase": phase.number if phase else None, "task_id": task.id if task else None,
            "review": verdict.model_dump()})
        return verdict

    async def write_task(self, phase, task):
        correction = self.pending_feedback.pop(task.id, None)
        must_write = task.id not in self.created_tasks or correction is not None
        self.approved_tasks.discard(task.id)
        self.approved_phases.discard(phase.number)
        while True:
            if correction and correction.get("replacement_seed") is not None:
                old = task.seed
                task.seed = TaskSeed.model_validate(correction["replacement_seed"])
                try:
                    check_assignments(self.phases, self.requirements)
                except ValueError:
                    task.seed = old
                    return False
            if must_write:
                self.item_state(task.id, creation="running")
                self.announce("writing_task", f"Escritor redigindo {task.id} com contexto novo.")
                data = {**self.task_context(phase, task), "task_id": task.id,
                    "phase_number": phase.number, "phase_title": phase.title, "seed": task.seed.model_dump(),
                    "previous_task": task.content.model_dump() if correction else None, "correction": correction}
                task.content = await self.call("writer", "write_task", data, TaskContent)
                self.created_tasks.add(task.id)
                self.store.json(f"phases/{phase.number:02d}/tasks/{task.id}.json", task.model_dump())
            self.item_state(task.id, creation="completed", validation="running")
            self.announce("reviewing_task", f"Revisor comparando apenas o recorte de {task.id}.")
            verdict = await self.review("task", phase, task)
            if verdict.approved:
                self.approved_tasks.add(task.id)
                self.approved_versions[task.id] = version(task)
                self.ledger.resolved(task)
                self.item_state(task.id, validation="completed")
                self.checkpoint()
                return True
            self.item_state(task.id, validation="rejected")
            self.round += 1
            correction = verdict.corrections[0].model_dump()
            self.ledger.record(task, verdict.corrections[0])
            self.checkpoint()
            self.announce("revision_requested", f"Correção documental solicitada para {task.id}.")
            if self.ledger.stalled(task) or verdict.corrections[0].category in ("structure", "granularity"):
                return False
            must_write = True

    def apply(self, verdict):
        result, sequences, dirty, affected, removed, feedback, requirements = self.check_repair(verdict, self.phases)
        retired = {task.id: task.model_dump() for phase in self.phases for task in phase.tasks if task.id in removed}
        # O lote inteiro já foi validado; nenhuma chamada ao escritor ocorreu até aqui.
        self.phases, self.task_sequences = result, sequences
        self.requirements = requirements
        affected.update(requirements[d.requirement_id].phase_number for d in verdict.delegations)
        self.store.json("control/requirements.json", [r.model_dump() for r in requirements.values()])
        self.pending_feedback.update(feedback)
        self.approved_tasks.difference_update(dirty | removed)
        self.created_tasks.difference_update(removed)
        for identifier in dirty | removed:
            self.approved_versions.pop(identifier, None)
        for identifier, document in retired.items():
            self.store.json(f"control/retired/{identifier}.json", {"task": document, "repair": self.repair_id + 1})
            self.store.path(f"phases/{document['phase_number']:02d}/tasks/{identifier}.json").unlink(missing_ok=True)
        for identifier in dirty:
            self.item_states.setdefault(identifier, {}).update(validation="rejected" if identifier in self.created_tasks else "waiting")
        for issue in self.ledger.issues.values():
            if issue["task_id"] in removed:
                issue["status"] = "retired"
        for number in affected:
            self.approved_phases.discard(number)
            self.item_state(f"P{number}", validation="rejected")
        self.repair_id += 1
        self.store.json(f"control/repairs/{self.repair_id:04d}.json", verdict.model_dump())
        self.checkpoint()
        return affected

    async def reassess(self, phase):
        self.item_state(f"P{phase.number}", validation="running")
        self.announce("reassessing_phase", f"Reavaliando estrutura e pendências repetidas da fase {phase.number}.")
        data = self.review_data(phase)
        data["trigger"] = "Devoluções sem avanço ou conflito de responsabilidade. Resolver a causa, sem aprovar por tentativas."
        def check(repair):
            self.check_repair(repair, [phase])
            for decision in repair.decisions:
                issue = self.ledger.issues.get(decision.issue_id)
                if not issue or issue["task_id"] != decision.task_id or decision.task_id not in {t.id for t in phase.tasks}:
                    raise ValueError("Decisões devem apontar pendências existentes desta fase.")
        repair = await self.call("reviewer", "reassess_phase", data, PlanRepair, check)
        for decision in repair.decisions:
            self.ledger.decide(decision)
        self.apply(repair)

    async def validate_phase(self, phase):
        number = phase.number
        self.phase_number = number
        while True:
            phase = next(p for p in self.phases if p.number == number)
            stalled = False
            for task in phase.tasks:
                if task.id in self.approved_tasks and self.approved_versions.get(task.id) == version(task):
                    continue
                if not await self.write_task(phase, task):
                    stalled = True
                    break
            if stalled:
                await self.reassess(phase)
                continue
            self.item_state(f"P{number}", validation="running")
            self.announce("reviewing_phase", f"Revisor validando cobertura coletiva da fase {number}.")
            verdict = await self.review("phase", phase)
            if verdict.approved:
                self.approved_phases.add(number)
                for task in phase.tasks:
                    self.ledger.resolved(task)
                self.item_state(f"P{number}", validation="completed")
                self.store.json(f"phases/{number:02d}/approved.json", phase.model_dump())
                self.checkpoint()
                return
            self.round += 1
            for correction in verdict.corrections:
                task = next(t for t in phase.tasks if t.id == correction.task_id)
                self.ledger.record(task, correction)
            self.item_state(f"P{number}", validation="rejected")
            self.announce("revision_requested", f"Revisor devolveu recortes da fase {number}.")
            if self.ledger.repeated_phase(number, verdict):
                await self.reassess(phase)
            else:
                self.apply(verdict)

    async def run(self):
        if not self.state.writer or not self.state.reviewer:
            raise InitError("Selecione escritor e revisor antes de executar o plan.")
        try:
            self.source = self.store.begin(self.state)
            self.prepare_origins()
            self.requirements = build_requirements(self.source["document"])
            self.store.json("control/requirements.json", [r.model_dump() for r in self.requirements.values()])
            self.round = 1
            for number, origin in self.origins.items():
                self.phase_number = number
                self.item_state(f"P{number}", creation="running")
                self.announce("decomposing", f"Decompondo a fase {number} em recortes pequenos.")

                def check(outline):
                    requirements = delegate_validations(self.requirements, outline.delegations, number)
                    for seed in outline.tasks:
                        check_seed(seed, number, origin["text"], requirements)
                    proposal = PlannedPhase(**{key: origin[key] for key in ("number", "title", "goal", "depends_on")},
                        tasks=[stub(number, seed, f"P{number}-T{i:03d}") for i, seed in enumerate(outline.tasks, 1)])
                    check_assignments([proposal], requirements)

                outline = await self.call("reviewer", "outline_phase", {
                    "source": self.source, "phase_source": origin,
                    "requirements": [r.model_dump() for r in self.requirements.values() if r.phase_number == number]}, PhaseOutline, check)
                self.requirements = delegate_validations(self.requirements, outline.delegations, number)
                self.store.json("control/requirements.json", [r.model_dump() for r in self.requirements.values()])
                self.store.json(f"phases/{number:02d}/outline.json", outline.model_dump())
                phase = PlannedPhase(**{key: origin[key] for key in ("number", "title", "goal", "depends_on")},
                    tasks=[stub(number, seed, f"P{number}-T{i:03d}") for i, seed in enumerate(outline.tasks, 1)])
                self.phases.append(phase)
                self.task_sequences[number] = len(phase.tasks)
                self.item_state(f"P{number}", creation="completed")
                self.checkpoint()
                await self.validate_phase(phase)
            while True:
                self.phase_number = None
                self.announce("reviewing_plan", "Comparando o plano completo com os documentos originais.")
                verdict = await self.review("plan")
                if verdict.approved:
                    break
                self.round += 1
                self.announce("revision_requested", "Revisor apontou correções pontuais no plano.")
                for correction in verdict.corrections:
                    task = next(t for p in self.phases for t in p.tasks if t.id == correction.task_id)
                    self.ledger.record(task, correction)
                if self.ledger.repeated_phase(-1, verdict):
                    affected = {next(p.number for p in self.phases if any(t.id == c.task_id for t in p.tasks))
                                for c in verdict.corrections}
                    affected.update(a.phase_number for a in verdict.additions)
                    affected.update(self.requirements[d.requirement_id].phase_number for d in verdict.delegations)
                    affected.update(next(p.number for p in self.phases if any(t.id == change.task_ids[0] for t in p.tasks))
                                    for change in verdict.structural_changes)
                    for phase in list(self.phases):
                        if phase.number in affected:
                            self.phase_number = phase.number
                            await self.reassess(phase)
                else:
                    affected = self.apply(verdict)
                for phase in self.phases:
                    if phase.number in affected:
                        await self.validate_phase(phase)
            # Detectar edição externa do init antes de liberar o build.
            if load_init(self.state.project_dir) != self.source:
                raise InitError("Os documentos do init mudaram durante o plan. Execute o plan novamente.")
            plan = FinalPlan(title=f"Plano de implementação — {self.source['document']['title']}",
                objective=self.source["document"]["objective"], source_prompt_sha256=self.source["source_prompt_sha256"],
                phases=self.phases, requirements=list(self.requirements.values()))
            if len(self.approved_phases) != len(self.origins) or len(self.approved_tasks) != sum(len(p.tasks) for p in self.phases):
                raise InitError("Existem tasks ou fases sem aprovação documental.")
            check_assignments(self.phases, self.requirements)
            if any(self.approved_versions.get(t.id) != version(t) for p in self.phases for t in p.tasks):
                raise InitError("Uma versão de task mudou após sua aprovação.")
            markdown = plan.render()
            self.store.write("final.md", markdown)
            self.store.json("final.json", plan.model_dump())
            self.store.json("control/approval.json", {"plan_sha256": digest(markdown),
                "source_prompt_sha256": self.source["source_prompt_sha256"], "review": verdict.model_dump(),
                "approved_tasks": sorted(self.approved_tasks), "approved_phases": sorted(self.approved_phases),
                "approved_versions": self.approved_versions})
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
