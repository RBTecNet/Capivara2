"""Ciclo documental revisor → entrevista/escritor → revisor, até aprovação."""

import asyncio
from dataclasses import asdict
import json
from typing import Awaitable, Callable

from pydantic import ValidationError

from .agents import CLIAgents
from .artifacts import ArtifactStore, InitError, digest
from .agents import AgentError
from .domain import WizardState
from .init_instructions import REVIEWER, WRITER
from .init_models import Answer, FinalPrompt, Gap, Interview, Question, Review
from .interview_policy import internal_question_reason
from .run_events import InitEvent


def normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


class InitEngine:
    def __init__(self, state: WizardState, agents: CLIAgents, *,
                 interview: Callable[[Question, int, int], Awaitable[Answer]],
                 progress: Callable[[str], None] | None = None,
                 on_event: Callable[[InitEvent], None] | None = None) -> None:
        self.state = state
        self.agents = agents
        self.interview = interview
        self.progress = progress or (lambda message: None)
        self.store = ArtifactStore(state.project_dir)
        self.call_id = 0
        self.round = 0
        self.interviews = []
        self.inferred_decisions = []
        self.on_event = on_event
        self.stage = "starting"
        self.active_role = None
        self.last_role = None
        self.operations = 0
        self.corrections = 0
        self.question_count = 0
        self.answer_count = 0
        self.inference_count = 0

    def emit(self, kind: str) -> None:
        if self.on_event:
            self.on_event(InitEvent(kind, self.stage, self.round, self.active_role, self.last_role,
                                   self.operations, self.call_id, self.corrections,
                                   self.question_count, self.answer_count, self.inference_count))

    def announce(self, stage: str, message: str) -> None:
        self.stage = stage
        if stage == "revision_requested":
            self.corrections += 1
        self.store.event(stage, message, round=self.round)
        self.progress(message)
        self.emit("stage_changed")

    async def call(self, role: str, task: str, data: dict, model, check=None):
        self.operations += 1
        selection = self.state.reviewer if role == "reviewer" else self.state.writer
        instructions = REVIEWER if role == "reviewer" else WRITER
        context_path = f"control/{role}-context.json"
        history = self.store.read_json(context_path, {"role": role, "selection": asdict(selection), "messages": []})
        request = {"task": task, "data": data}
        feedback = ""
        provider_failures = 0
        while True:
            self.call_id += 1
            self.active_role = self.last_role = role
            self.emit("agent_started")
            schema = model.model_json_schema()
            prompt = instructions + "\n\nSCHEMA JSON:\n" + json.dumps(schema, ensure_ascii=False) + "\n\nREQUEST:\n" + json.dumps({
                "preserved_context": history["messages"], "current_request": request,
                "format_correction": feedback}, ensure_ascii=False)
            try:
                output = await self.agents.generate(selection, role, prompt, schema, self.store, self.call_id)
            except asyncio.CancelledError:
                self.active_role = None
                self.emit("agent_cancelled")
                raise
            except AgentError as exc:
                self.active_role = None
                self.emit("agent_failed")
                provider_failures += 1
                feedback = f"A chamada anterior do CLI falhou: {exc}. Repita somente esta operação."
                self.announce("agent_recovery", f"Recuperação automática do agente: {exc}")
                await asyncio.sleep(min(0.25 * 2 ** min(provider_failures - 1, 6), 15))
                continue
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
            except (ValueError, ValidationError) as exc:
                self.corrections += 1
                feedback = str(exc)
                self.store.event("format_correction", "Resposta documental devolvida para correção de formato.",
                                 call=self.call_id, role=role, detail=feedback)
                self.emit("format_correction")
                continue
            history["messages"].extend([{"role": "user", "content": request},
                                        {"role": "assistant", "content": value.model_dump()}])
            self.store.json(context_path, history)
            return value

    def technology_gaps(self, review: Review, text: str) -> Review:
        gaps = list(review.gaps)
        evidence = review.technology
        for topic, defined, quote in (("stack", evidence.stack_specified, evidence.stack_quote),
                                      ("tools", evidence.tools_specified, evidence.tools_quote)):
            if not defined or not quote or normalized(quote) not in normalized(text):
                if not any(gap.topic == topic for gap in gaps):
                    description = ("Definir explicitamente a stack do projeto, adequada à finalidade solicitada."
                                   if topic == "stack" else
                                   "Definir as ferramentas e serviços do projeto; não perguntar ferramentas internas do harness.")
                    gaps.append(Gap(id=f"required_{topic}", topic=topic, description=description))
        return review.model_copy(update={"gaps": gaps, "approved": review.approved and not gaps})

    def check_technology_evidence(self, review: Review, text: str) -> None:
        evidence = review.technology
        for topic, specified, quote in (("stack", evidence.stack_specified, evidence.stack_quote),
                                        ("tools", evidence.tools_specified, evidence.tools_quote)):
            if specified and (not quote or normalized(quote) not in normalized(text)):
                raise ValueError(f"A citação literal de {topic} não foi encontrada no documento. "
                                 "Corrija a evidência na revisão; não transforme um erro de citação em nova entrevista. "
                                 "Ferramentas de QA e testes são inferidas pelo harness, não lacunas de ferramentas do produto.")

    def final_technology_corrections(self, review: Review) -> Review:
        missing = []
        if not review.technology.stack_specified:
            missing.append("stack")
        if not review.technology.tools_specified:
            missing.append("ferramentas do produto")
        if not missing:
            return review
        correction = (f"Explicite {' e '.join(missing)} na fase 0 com base no pedido e nas decisões já recebidas. "
                      "Corrija a documentação sem repetir perguntas já respondidas. Infira as ferramentas internas de testes e QA.")
        return review.model_copy(update={"approved": False, "deviations": [*review.deviations, correction]})

    async def conduct_interview(self, review: Review, source_text: str) -> None:
        gap_ids = {gap.id for gap in review.gaps}

        def complete_interview(value: Interview):
            covered = set()
            for question in value.questions:
                if reason := internal_question_reason(question):
                    raise ValueError(reason)
                if not set(question.gap_ids).issubset(gap_ids):
                    raise ValueError("A entrevista citou lacunas que não estão nesta revisão.")
                covered.update(question.gap_ids)
                if normalized(question.goal_reference) not in normalized(source_text):
                    raise ValueError("A recomendação precisa citar literalmente a finalidade no material original.")
                topics = {gap.topic for gap in review.gaps if gap.id in question.gap_ids}
                if question.topic not in topics:
                    raise ValueError("O tópico da pergunta precisa corresponder às lacunas cobertas.")
            for decision in value.inferred_decisions:
                if not set(decision.gap_ids).issubset(gap_ids):
                    raise ValueError("A decisão inferida citou lacunas que não estão nesta revisão.")
                covered.update(decision.gap_ids)
            if covered != gap_ids:
                raise ValueError(f"Resolva as lacunas com perguntas do produto ou decisões internas inferidas; faltam: {sorted(gap_ids - covered)}")

        self.announce("preparing_interview", f"Rodada {self.round}: escritor preparando a entrevista sobre {len(review.gaps)} lacuna(s).")
        interview = await self.call("writer", "interview", {"review": review.model_dump(), "previous_interviews": self.interviews,
                                    "previous_inferred_decisions": self.inferred_decisions},
                                    Interview, complete_interview)
        decisions = [decision.model_dump() for decision in interview.inferred_decisions]
        self.question_count += len(interview.questions)
        self.emit("interview_prepared")
        record = {"round": self.round, "questions": interview.model_dump()["questions"], "answers": [],
                  "inferred_decisions": decisions}
        if decisions:
            self.inference_count += len(decisions)
            self.inferred_decisions.append({"round": self.round, "decisions": decisions})
            self.store.json("control/inferred-decisions.json", self.inferred_decisions)
            self.announce("inferring_internal_tools", f"Escritor inferiu {len(decisions)} decisão(ões) interna(s), sem entrevistar o usuário.")
        filename = f"enterviews/round-{self.round:04d}.json"
        self.store.json(filename, record)
        for index, question in enumerate(interview.questions, 1):
            self.announce("interviewing", f"Entrevista · rodada {self.round} · pergunta {index}/{len(interview.questions)}")
            answer = await self.interview(question, index, len(interview.questions))
            options = {option.id: option for option in question.options}
            if answer.question_id != question.id or (answer.option_id not in options and answer.option_id != "other"):
                raise InitError("A resposta não corresponde às opções da entrevista.")
            if answer.option_id == "other":
                if not answer.text.strip():
                    raise InitError("A opção livre precisa conter uma resposta.")
            else:
                answer = answer.model_copy(update={"label": options[answer.option_id].label, "text": ""})
            record["answers"].append(answer.model_dump())
            self.store.json(filename, record)
            self.answer_count += 1
            self.emit("answer_saved")
        self.interviews.append(record)
        self.store.json("control/interviews.json", self.interviews)

    async def run(self):
        if not self.state.prompt.strip() or self.state.writer is None or self.state.reviewer is None:
            raise InitError("Configure o prompt, o escritor e o revisor antes de executar o init.")
        try:
            source = self.store.begin(self.state)
            source_text = self.state.prompt + "\n" + "\n".join(document.get("text") or "" for document in source["documents"])
            self.announce("reviewing_initial", "Revisor analisando o pedido original e a documentação recebida.")
            review = await self.call("reviewer", "initial_review", {"source": source}, Review,
                                     lambda value: self.check_technology_evidence(value, source_text))
            review = self.technology_gaps(review, source_text)
            baseline = list(review.requirements)
            self.store.json("control/baseline-requirements.json", baseline)
            self.store.json("control/initial-review.json", review.model_dump())
            # O escritor também recebe a fonte integral antes de iniciar a entrevista.
            self.store.json("control/writer-context.json", {"role": "writer", "selection": asdict(self.state.writer),
                "messages": [{"role": "user", "content": {"source": source, "baseline_requirements": baseline}}]})
            previous = None
            while True:
                self.round += 1
                self.store.json("control/checkpoint.json", {"round": self.round, "review": review.model_dump(),
                                                            "interviews": self.interviews, "inferred_decisions": self.inferred_decisions})
                if review.gaps:
                    await self.conduct_interview(review, source_text)
                self.announce("writing", f"Rodada {self.round}: escritor elaborando o prompt por fases e corrigindo os apontamentos.")
                candidate = await self.call("writer", "write", {"review": review.model_dump(),
                    "baseline_requirements": baseline, "interviews": self.interviews,
                    "inferred_decisions": self.inferred_decisions,
                    "previous_prompt": previous}, FinalPrompt)
                markdown = candidate.render()
                self.store.write(f"prompts/round-{self.round:04d}.md", markdown)
                self.store.json(f"prompts/round-{self.round:04d}.json", candidate.model_dump())
                self.announce("reviewing_final", f"Rodada {self.round}: revisor retomando seu contexto e verificando fidelidade, lacunas, fases e viabilidade.")
                review = await self.call("reviewer", "final_review", {"candidate": markdown,
                    "baseline_requirements": baseline, "interviews": self.interviews,
                    "inferred_decisions": self.inferred_decisions}, Review,
                    lambda value: self.check_technology_evidence(value, markdown))
                review = self.final_technology_corrections(review)
                report = {"round": self.round, "prompt_sha256": digest(markdown), "review": review.model_dump()}
                self.store.json(f"control/review-{self.round:04d}.json", report)
                if review.approved:
                    self.store.write("prompts/final.md", markdown)
                    self.store.json("prompts/final.json", candidate.model_dump())
                    self.store.json("control/approval.json", report)
                    self.announce("completed", "Init concluído: prompt final aprovado pelo revisor.")
                    self.store.finish("completed", prompt_sha256=digest(markdown), rounds=self.round,
                                      final_prompt="prompts/final.md", approval="control/approval.json")
                    return self.store.path("prompts/final.md")
                self.announce("revision_requested", f"Rodada {self.round}: revisor solicitou ajustes. {review.summary}")
                previous = markdown
        except asyncio.CancelledError:
            self.store.finish("cancelled", reason="Execução interrompida pelo usuário.")
            raise
        except Exception as exc:
            self.store.finish("failed", reason=str(exc))
            raise
        finally:
            self.store.close()
