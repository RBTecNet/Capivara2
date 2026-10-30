"""Planejamento por recortes, com validação documental e sessões independentes."""

import asyncio
import json
import re

from .artifacts import InitError, digest
from .plan_artifacts import PlanStore, load_init
from .plan_control import IssueLedger, prepare_changes, stub, version, structure_version
from .plan_classification import source_activities, check_classification, classified_requirements
from .plan_corrections import CorrectionBook
from .plan_schema import scoped_schema
from .plan_contracts import (PlanContractError, WorkClassification, ClassificationReview,
    TaskAssessment, CollectiveAssessment, StructureRepair, ContentCorrection, ContractValidation,
    SealedOutline)
from .plan_coverage import (build_requirements, check_assignments, check_seed,
                            coverage_map, delegate_validations)
from .plan_instructions import instructions_for
from .plan_models import (FinalPlan, PlannedPhase, PlanRepair, PlanReview, TaskContent, agent_schema)
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
        self.correction_book = CorrectionBook()
        self.contract_errors = 0
        self.writer_recoveries = 0
        self.current_task_id = None
        self.structure_requests = set()
        self.applied_structures = set()

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
                tasks_approved=len(self.approved_tasks), items=self.items(), contract_errors=self.contract_errors,
                writer_recoveries=self.writer_recoveries))

    def announce(self, stage, message):
        self.stage = stage
        if stage == "revision_requested":
            self.corrections += 1
        self.store.event(stage, message, phase=self.phase_number, round=self.round)
        self.emit("stage_changed")

    async def call(self, role, task, data, model, check=None, *, max_attempts=2):
        self.operations += 1
        feedback = ""
        rejected_response = None
        selection = self.state.reviewer if role == "reviewer" else self.state.writer
        schema = scoped_schema(model, task, data, self.requirements)
        for attempt in range(max_attempts):
            self.call_id += 1
            # Nenhum histórico é reutilizado, nem em correções de formato.
            envelope = {"preserved_context": [], "context_policy": "fresh",
                        "current_request": {"task": task, "data": data}, "format_correction": feedback,
                        "rejected_response": rejected_response}
            self.store.json(f"context/call-{self.call_id:04d}.json", envelope)
            prompt = instructions_for(role, task) + "\n\nSCHEMA JSON:\n" + json.dumps(schema, ensure_ascii=False)
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
            except PlanContractError:
                raise
            except ValueError as exc:
                feedback = str(exc)
                rejected_response = output
                self.contract_errors += 1
                self.store.event("format_correction", "Contrato documental devolvido ao agente.",
                                 role=role, call=self.call_id, detail=feedback)
                self.emit("format_correction")
                if attempt + 1 == max_attempts:
                    raise PlanContractError(f"{task}: resposta ainda inválida após uma oportunidade de corrigir o contrato. {feedback}") from exc

    async def classify(self):
        raw = build_requirements(self.source["document"])
        self.announce("classifying", "Classificando responsabilidades uma vez, com partição literal da origem.")
        data = {"source": self.source, "activities": [{"source_id": r.id, "phase_number": r.phase_number, "text": r.text}
                                                        for r in source_activities(raw)]}
        classification = await self.call("reviewer", "classify_work", data, WorkClassification,
                                         lambda c: check_classification(c, raw))
        self.announce("reviewing_classification", "Conferindo e aprovando o mapa final de responsabilidades.")
        def apply_roles(verdict, proposal):
            entries = {a.source_id: a for a in proposal.activities}
            seen = set()
            for change in verdict.corrections:
                key = (change.source_id, change.part_index)
                if change.source_id not in entries or change.part_index >= len(entries[change.source_id].parts) or key in seen:
                    raise ValueError("Aponte source_id e part_index existentes, sem duplicar mudanças.")
                seen.add(key)
                part = entries[change.source_id].parts[change.part_index]
                part.responsibility, part.reason = change.responsibility, change.reason
            return proposal
        verdict = await self.call("reviewer", "review_classification", {**data,
            "classification": classification.model_dump(), "literal_partition_verified": True}, ClassificationReview,
            lambda v: apply_roles(v, classification.model_copy(deep=True)))
        self.store.json("control/classification-review.json", verdict.model_dump())
        if verdict.status == "conflict":
            raise PlanContractError(f"O revisor não aprovou o mapa de responsabilidades. {verdict.summary}")
        classification = apply_roles(verdict, classification)
        self.requirements = classified_requirements(classification, raw)
        self.store.json("control/classification.json", classification.model_dump())
        self.store.json("control/classification-approval.json", {"source_prompt_sha256": self.source["source_prompt_sha256"],
            "classification_sha256": digest(self.store.path("control/classification.json").read_text()),
            "requirements_sha256": digest(json.dumps({k: v.model_dump() for k, v in self.requirements.items()}, sort_keys=True, ensure_ascii=False)),
            "approved": True, "review": verdict.model_dump()})
        self.correction_book = CorrectionBook(self.source["source_prompt_sha256"])

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
        self.store.json("control/corrections.json", self.correction_book.dump())
        self.emit("counts_changed")

    def task_context(self, phase, task):
        assigned = {claim.requirement_id for claim in task.seed.coverage}
        return {"objective": self.source["document"]["objective"],
            "constraints": self.source["document"]["constraints"],
            "product_requirements": [{"requirement_id": r.id, "text": r.text} for r in self.requirements.values()
                                     if r.phase_number == -1],
            "assigned_requirements": [self.requirements[key].model_dump() for key in sorted(assigned)],
            "scope_decisions": self.ledger.context({task.id}),
            "correction_contracts": self.correction_book.context({task.id}),
            "base_version": version(task),
            "technology": {"stack": self.source["document"]["stack"],
                           "tools": self.source["document"]["tools"]} if phase.number else {}}

    def review_data(self, phase=None, task=None):
        def view(item):
            document = item.model_dump()
            document["seed"].pop("source_quotes", None)
            return document
        if task is not None:
            data = self.task_context(phase, task)
            data["task"] = view(task)
            ids = {claim.requirement_id for claim in task.seed.coverage}
            data["responsibility_map"] = [entry for entry in coverage_map([phase], self.requirements)
                                           if entry["requirement"]["id"] in ids]
            return data
        phases = [phase] if phase else self.phases
        data = {"source": self.source, "coverage_map": coverage_map(phases, self.requirements),
                "scope_decisions": self.ledger.context({t.id for p in phases for t in p.tasks}),
                "approved_versions": self.approved_versions,
                "correction_contracts": self.correction_book.context({t.id for p in phases for t in p.tasks})}
        if phase is not None:
            phase_view = phase.model_dump()
            phase_view["tasks"] = [view(t) for t in phase.tasks]
            data.update(phase_source=self.origins[phase.number], phase=phase_view)
            data["document_boundary"] = "Avalie SOMENTE tasks[].content como trabalho gerado. source, phase_source e requisitos build são referências da origem, nunca instruções novas das tasks."
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
        if scope not in ("task", "phase"):
            raise PlanContractError("A conclusão consolida aprovações; não existe reabertura livre do plano completo.")
        phases = [phase] if phase else self.phases
        def check(value):
            available = {t.id: t for p in phases for t in p.tasks}
            if task is not None:
                available = {task.id: task}
                if value.replan:
                    if value.replan.task_id != task.id:
                        raise ValueError("A reavaliação deve apontar a única task em revisão.")
                    for evidence in value.replan.evidence:
                        ids = {c.requirement_id for c in task.seed.coverage}
                        if evidence.requirement_id not in ids or evidence.quote not in self.requirements[evidence.requirement_id].text:
                            raise ValueError("Cite literalmente uma responsabilidade atribuída à task.")
            for spec in value.corrections:
                if spec.task_id not in available:
                    raise ValueError("A correção deve apontar uma task deste recorte.")
                self.correction_book.check(available[spec.task_id], spec, self.requirements)
            if task is None and not value.approved:
                self.check_repair(value.legacy(structural_changes=value.structural_changes, additions=value.additions), phases)
        model = TaskAssessment if task else CollectiveAssessment
        value = await self.call("reviewer", f"review_{scope}", self.review_data(phase, task), model, check)
        if task and value.replan:
            from .plan_models import TaskCorrection
            concern = value.replan
            correction = TaskCorrection(task_id=task.id, reason=concern.reason,
                instruction="Resolver a estrutura antes da redação, com operação concreta.", replacement_seed=None, category="structure")
            verdict = PlanReview(**value.model_dump(exclude={"replan", "corrections"}), corrections=[correction], additions=[])
        elif task:
            verdict = value.legacy(additions=[])
        else:
            verdict = value.legacy(structural_changes=value.structural_changes, additions=value.additions)
        self.review_id += 1
        self.store.json(f"control/reviews/{self.review_id:04d}.json", {"scope": scope,
            "phase": phase.number if phase else None, "task_id": task.id if task else None,
            "review": verdict.model_dump()})
        return verdict

    async def correct(self, phase, task, correction):
        if not correction.contract:
            raise PlanContractError(f"{task.id}: devolução sem evidência, alterações e condições de aceite verificáveis.")
        spec = ContentCorrection.model_validate(correction.contract)
        target = self.correction_book.check(task, spec, self.requirements)
        context = self.task_context(phase, task)
        for cycle in range(2):
            validation = await self.call("reviewer", "validate_correction", {**context,
                "task": task.model_dump(), "contract": spec.model_dump(), "expected_content": target.model_dump()}, ContractValidation)
            if validation.approved:
                break
            if cycle:
                raise PlanContractError(f"{task.id}: o revisor não produziu um contrato de correção coerente. {validation.reason}")
            spec = await self.call("reviewer", "repair_correction", {**context, "task": task.model_dump(),
                "rejected_contract": spec.model_dump(), "reason": validation.reason}, ContentCorrection,
                lambda c: self.correction_book.check(task, c, self.requirements))
            target = self.correction_book.check(task, spec, self.requirements)
        identifier, target = self.correction_book.issue(task, spec, self.requirements)
        self.checkpoint()
        self.announce("correcting_task", f"Escritor aplicando o contrato {identifier} de {task.id}, em um ciclo.")
        self.item_state(task.id, creation="running", validation="rejected")
        recovered = False
        try:
            candidate = await self.call("writer", "correct_task", {**context, "task_id": task.id,
                "seed": task.seed.model_dump(), "previous_task": task.content.model_dump(),
                "contract_id": identifier, "correction": spec.model_dump()}, TaskContent, max_attempts=1)
            recovered = candidate != target
        except PlanContractError:
            recovered = True
        # A correção autorizada é um patch exato. Desvios do escritor não geram nova tentativa.
        task.content = target
        self.correction_book.applied(identifier, task, recovered)
        if recovered:
            self.writer_recoveries += 1
            self.store.event("writer_contract_recovery", "Substituições aprovadas aplicadas exatamente após desvio do escritor.",
                             task_id=task.id, contract_id=identifier)
            self.emit("writer_recovered")
        self.created_tasks.add(task.id)
        self.store.json(f"phases/{phase.number:02d}/tasks/{task.id}.json", task.model_dump())
        self.item_state(task.id, creation="completed", validation="waiting")
        self.checkpoint()

    async def write_task(self, phase, task):
        self.current_task_id = task.id
        correction = self.pending_feedback.pop(task.id, None)
        self.approved_tasks.discard(task.id)
        self.approved_phases.discard(phase.number)
        if correction:
            from .plan_models import TaskCorrection
            await self.correct(phase, task, TaskCorrection.model_validate(correction))
        elif task.id not in self.created_tasks:
            self.item_state(task.id, creation="running")
            self.announce("writing_task", f"Escritor redigindo {task.id} com contexto novo.")
            task.content = await self.call("writer", "write_task", {**self.task_context(phase, task),
                "task_id": task.id, "phase_number": phase.number, "phase_title": phase.title,
                "seed": task.seed.model_dump(), "previous_task": None, "correction": None}, TaskContent)
            self.created_tasks.add(task.id)
            self.store.json(f"phases/{phase.number:02d}/tasks/{task.id}.json", task.model_dump())
        while True:
            self.correction_book.verify(task)
            self.item_state(task.id, creation="completed", validation="running")
            self.announce("reviewing_task", f"Revisor verificando {task.id} e as condições de aceite vigentes.")
            verdict = await self.review("task", phase, task)
            if verdict.approved:
                self.correction_book.verify(task, approved=True)
                self.approved_tasks.add(task.id)
                self.approved_versions[task.id] = version(task)
                self.ledger.resolved(task)
                self.item_state(task.id, validation="completed")
                self.checkpoint()
                self.current_task_id = None
                return True
            correction = verdict.corrections[0]
            self.ledger.record(task, correction)
            self.round += 1
            self.item_state(task.id, validation="rejected")
            self.announce("revision_requested", f"Correção específica solicitada para {task.id}.")
            self.checkpoint()
            if correction.category == "structure":
                return False
            await self.correct(phase, task, correction)

    def apply(self, verdict):
        result, sequences, dirty, affected, removed, feedback, requirements = self.check_repair(verdict, self.phases)
        if verdict.structural_changes or verdict.additions:
            for phase in result:
                if phase.number in affected and structure_version(phase) in self.applied_structures:
                    raise PlanContractError(f"Fase {phase.number}: a estrutura proposta repete uma versão já examinada; não haverá novo ciclo de divisão/consolidação.")
            self.applied_structures.update(structure_version(p) for p in result if p.number in affected)
        retired = {task.id: task.model_dump() for phase in self.phases for task in phase.tasks if task.id in removed}
        # O lote inteiro já foi validado; nenhuma chamada ao escritor ocorreu até aqui.
        self.phases, self.task_sequences = result, sequences
        self.requirements = requirements
        affected.update(requirements[d.requirement_id].phase_number for d in verdict.delegations)
        self.store.json("control/requirements.json", [r.model_dump() for r in requirements.values()])
        self.pending_feedback.update(feedback)
        self.approved_tasks.difference_update(dirty | removed)
        self.created_tasks.difference_update(removed)
        self.correction_book.retire(removed)
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
        fingerprint = structure_version(phase)
        if fingerprint in self.structure_requests:
            raise PlanContractError(f"Fase {phase.number}: a mesma estrutura foi questionada novamente sem alteração. Revisões incompatíveis.")
        self.structure_requests.add(fingerprint)
        dto = await self.call("reviewer", "reassess_phase", data, StructureRepair,
            lambda v: check(PlanRepair(summary=v.summary, decisions=v.decisions, corrections=[],
                                      additions=v.additions, structural_changes=v.structural_changes)))
        repair = PlanRepair(summary=dto.summary, decisions=dto.decisions, corrections=[],
                            additions=dto.additions, structural_changes=dto.structural_changes)
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
            self.apply(verdict)

    async def run(self):
        if not self.state.writer or not self.state.reviewer:
            raise InitError("Selecione escritor e revisor antes de executar o plan.")
        try:
            self.source = self.store.begin(self.state)
            self.prepare_origins()
            await self.classify()
            self.store.json("control/requirements.json", [r.model_dump() for r in self.requirements.values()])
            self.round = 1
            for number, origin in self.origins.items():
                self.phase_number = number
                self.item_state(f"P{number}", creation="running")
                self.announce("decomposing", f"Decompondo a fase {number} em recortes pequenos.")

                def check(outline):
                    requirements = self.requirements
                    for seed in outline.tasks:
                        check_seed(seed, number, origin["text"], requirements)
                    proposal = PlannedPhase(**{key: origin[key] for key in ("number", "title", "goal", "depends_on")},
                        tasks=[stub(number, seed, f"P{number}-T{i:03d}") for i, seed in enumerate(outline.tasks, 1)])
                    check_assignments([proposal], requirements)

                outline = await self.call("reviewer", "outline_phase", {
                    "source": self.source, "phase_source": origin,
                    "requirements": [r.model_dump() for r in self.requirements.values() if r.phase_number == number]}, SealedOutline, check)
                self.store.json("control/requirements.json", [r.model_dump() for r in self.requirements.values()])
                self.store.json(f"phases/{number:02d}/outline.json", outline.model_dump())
                phase = PlannedPhase(**{key: origin[key] for key in ("number", "title", "goal", "depends_on")},
                    tasks=[stub(number, seed, f"P{number}-T{i:03d}") for i, seed in enumerate(outline.tasks, 1)])
                self.phases.append(phase)
                self.applied_structures.add(structure_version(phase))
                self.task_sequences[number] = len(phase.tasks)
                self.item_state(f"P{number}", creation="completed")
                self.checkpoint()
                await self.validate_phase(phase)
            # Detectar edição externa do init antes de liberar o build.
            if load_init(self.state.project_dir) != self.source:
                raise InitError("Os documentos do init mudaram durante o plan. Execute o plan novamente.")
            plan = FinalPlan(title=f"Plano de implementação — {self.source['document']['title']}",
                objective=self.source["document"]["objective"], source_prompt_sha256=self.source["source_prompt_sha256"],
                phases=self.phases, requirements=list(self.requirements.values()), contract_version=2)
            if len(self.approved_phases) != len(self.origins) or len(self.approved_tasks) != sum(len(p.tasks) for p in self.phases):
                raise InitError("Existem tasks ou fases sem aprovação documental.")
            check_assignments(self.phases, self.requirements)
            if any(self.approved_versions.get(t.id) != version(t) for p in self.phases for t in p.tasks):
                raise InitError("Uma versão de task mudou após sua aprovação.")
            for phase in self.phases:
                if self.store.read_json(f"phases/{phase.number:02d}/approved.json") != phase.model_dump():
                    raise PlanContractError(f"Fase {phase.number}: o documento mudou após a aprovação coletiva.")
                for task in phase.tasks:
                    self.correction_book.verify(task)
            self.phase_number = None
            self.announce("reviewing_plan", "Conferindo a conclusão: aprovações explícitas, versões e cobertura integral.")
            certificate = await self.call("reviewer", "validate_completion", {
                "source_prompt_sha256": self.source["source_prompt_sha256"],
                "objective": self.source["document"]["objective"],
                "product_requirements": self.source["document"]["requirements"],
                "classification_approved": True,
                "coverage": coverage_map(self.phases, self.requirements),
                "phases": [{"number": p.number, "title": p.title, "goal": p.goal, "depends_on": p.depends_on,
                    "approved": p.number in self.approved_phases,
                    "tasks": [{"id": t.id, "title": t.content.title, "approved": t.id in self.approved_tasks,
                               "approved_version": self.approved_versions[t.id]} for t in p.tasks]} for p in self.phases],
                "local_checks": {"source_unchanged": True, "all_versions_match": True,
                                 "all_phase_documents_match": True, "all_acceptance_conditions_hold": True},
                "testing_policy": "Build decide livremente todos os testes, inclusive não citados na origem."}, ContractValidation)
            self.store.json("control/completion-certificate.json", certificate.model_dump())
            if not certificate.approved:
                raise PlanContractError(f"A conferência final recusou a conclusão: {certificate.reason}")
            if load_init(self.state.project_dir) != self.source:
                raise InitError("Os documentos do init mudaram durante a conferência final. Execute o plan novamente.")
            verdict = PlanReview(approved=True, matches_source=True, coverage_complete=True,
                tasks_are_small=True, no_test_strategy=True, summary=certificate.reason, corrections=[], additions=[])
            markdown = plan.render()
            self.store.write("final.md", markdown)
            self.store.json("final.json", plan.model_dump())
            self.store.json("control/approval.json", {"plan_sha256": digest(markdown),
                "source_prompt_sha256": self.source["source_prompt_sha256"], "review": verdict.model_dump(),
                "approved_tasks": sorted(self.approved_tasks), "approved_phases": sorted(self.approved_phases),
                "approved_versions": self.approved_versions,
                "classification_sha256": digest(self.store.path("control/classification.json").read_text()),
                "completion_sha256": digest(self.store.path("control/completion-certificate.json").read_text()),
                "corrections_sha256": digest(self.store.path("control/corrections.json").read_text())})
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
            if isinstance(exc, PlanContractError) and self.store.started:
                self.store.json("control/contract-failure.json", {"operation": self.stage, "task_id": self.current_task_id,
                    "phase": self.phase_number, "call": self.call_id, "reason": str(exc),
                    "instruction": "Corrigir a incompatibilidade do contrato; nenhuma aprovação será inferida por tentativas."})
                self.stage = "contract_conflict"
                if self.current_task_id:
                    self.item_state(self.current_task_id, validation="rejected")
            self.park_items()
            self.store.finish("failed", reason=str(exc))
            raise
        finally:
            self.store.close()
