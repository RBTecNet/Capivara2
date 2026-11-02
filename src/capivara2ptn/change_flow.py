"""Investigação, entrevista, aprovação e execução sequencial próprias do CHANGE."""

import asyncio
import json
import time
from dataclasses import asdict

from capivara2ptn.build_g1 import evaluate
from capivara2ptn.change_patch import recover_patch, stage_patch, capture_before, task_diff
from capivara2ptn.change_prompts import change_prompt
from capivara2ptn.change_protocol import (response, text, strings, validate_gate, validate_investigation,
                                        validate_plan, validate_question, render_plan, task_states)
from capivara2ptn.change_source import (ChangeError, ChangeConflict, app_path, available_tools, catalog, fingerprint,
                                      git_context, project_snapshot, read_chunk, search, sha)
from capivara2ptn.change_verify import verify_change
from capivara2ptn.providers import ProviderError
from capivara2ptn.change_state import now


class ChangeRun:
    def __init__(self, store, writer, auditor, options, secrets, update, ask, approve, scope, conflict):
        self.store, self.writer, self.auditor = store, writer, auditor
        self.options, self.secrets, self.update = options, secrets, update
        self.ask, self.approve, self.scope, self.conflict = ask, approve, scope, conflict
        self.entries = []

    @property
    def state(self):
        return self.store.state

    def emit(self, message):
        self.store.save()
        self.update(self.state, self.secrets.safe(message))

    def phase(self, name, message):
        self.state.update(phase=name, status=name, error='')
        self.emit(message)

    async def human(self, callback, *args):
        self.store.pause_clock()
        try:
            return await callback(*args)
        finally:
            self.store.start_clock()

    def add_context(self, item):
        context = self.state.setdefault('context', [])
        if item.get('path'):
            previous = self.state['read_files'].get(item['path'])
            if previous is not None and previous != item['fingerprint']:
                raise ChangeConflict([item['path']])
            context[:] = [old for old in context if not (old.get('path') == item['path'] and old.get('start') == item.get('start'))]
        context.append(item)
        if item.get('path'):
            self.state['read_files'][item['path']] = item['fingerprint']
        self.store.save()

    def context_payload(self):
        selected, remaining = [], 20000
        for item in reversed(self.state.get('context', [])):
            if item.get('path') and item.get('fingerprint') != fingerprint(self.store.root, item['path']):
                continue
            if any(fingerprint(self.store.root, p) != h for p, h in item.get('fingerprints', {}).items()):
                continue
            size = len(json.dumps(item, ensure_ascii=False))
            if size <= remaining:
                selected.append(item)
                remaining -= size
        return list(reversed(selected))

    def payload(self, extra):
        page = self.entries[:120]
        data = {'request_id': self.store.identifier, 'request': self.state['original'],
                'investigation': self.state['investigation'], 'history': self.state['history'],
                'context': self.context_payload(), 'catalog': page, 'catalog_total': len(self.entries),
                'tools_available': available_tools(), 'stale_paths': self.state.get('stale_paths', []),
                'valid_previous_findings': [k for k in self.state['knowledge'] if not k.get('stale')][-12:],
                'forbid_automated_tests': self.options.forbid_automated_tests,
                'completed_tasks': [{'id': t['id'], 'title': t['title'], 'files': t['files']} for t in self.state['tasks'] if t['status'] == 'done']}
        data.update(extra)
        return self.secrets.protect(data)

    async def obtain_context(self, value):
        kind = value.get('kind')
        if kind == 'read':
            requested = value.get('files')
            if not isinstance(requested, list) or not requested:
                raise ChangeError('Pedido de leitura sem arquivos.')
            supplied, deferred, budget = [], [], 16000
            for request in requested:
                if not isinstance(request, dict):
                    raise ChangeError('Pedido de arquivo inválido.')
                if budget < 1000:
                    deferred.append(request)
                    continue
                item = read_chunk(self.store.root, request.get('path'), request.get('start', 1), self.secrets, budget=min(10000, budget))
                budget -= len(item['text'])
                supplied.append(item)
                self.add_context(item)
            return {'files': supplied, 'deferred': deferred}
        if kind == 'search':
            result, inspected = search(self.store.root, self.entries, value.get('query'), self.secrets,
                                       value.get('offset', 0), value.get('pattern', '*'))
            if any(p in self.state['read_files'] and self.state['read_files'][p] != h for p, h in inspected.items()):
                raise ChangeConflict([p for p, h in inspected.items() if p in self.state['read_files'] and self.state['read_files'][p] != h])
            self.state['read_files'].update(inspected)
            self.add_context(result)
            return result
        if kind == 'catalog':
            page = value.get('page', 0)
            if type(page) is not int or page < 0:
                raise ChangeError('Página do catálogo inválida.')
            return {'page': page, 'files': self.entries[page * 120:(page + 1) * 120], 'total': len(self.entries)}
        if kind == 'verification_output':
            index, offset = value.get('command', 1), value.get('offset', 0)
            commands = (self.state.get('verification') or {}).get('comandos', [])
            if type(index) is not int or not 1 <= index <= len(commands) or type(offset) is not int or offset < 0:
                raise ChangeError('Trecho de diagnóstico inválido.')
            output = commands[index - 1]['saida']
            return {'command': index, 'offset': offset, 'text': output[offset:offset + 14000], 'total': len(output)}
        if kind == 'diff':
            task = next((t for t in self.state['tasks'] if t['id'] == value.get('task', self.state.get('current_task'))), None)
            if task is None:
                raise ChangeError('Task de diff desconhecida.')
            return task_diff(self.store, task, self.secrets, value.get('offset', 0))
        return None

    async def model(self, role, prompt, extra, validate):
        state = self.state
        feedback_key = role + ':' + str(extra.get('task', {}).get('id', 'request'))
        feedback = state.setdefault('model_feedback', {}).get(feedback_key, '')
        last_context = None
        while True:
            self.store.check_sources()
            attempts = state['attempts'].get(feedback_key, 0) + 1
            state['attempts'][feedback_key] = attempts
            current = next((t for t in state['tasks'] if t['id'] == extra.get('task', {}).get('id')), None)
            if current is not None:
                current['attempts'][role] = attempts
            self.emit(f'{role} · tentativa {attempts}.')
            try:
                payload = self.payload({**extra, 'protocol_feedback': feedback, 'requested_context': last_context})
                runner = self.auditor if role in {'G0', 'G2'} and self.auditor else self.writer
                raw = await runner.invoke(change_prompt(prompt), payload, access='text')
                self.store.check_sources()
                value = self.secrets.protect(response(raw))
                self.store.write_json(f'modelos/{len(state["events"])}-{sha(feedback_key)[:10]}-{attempts}.json', value)
                if value.get('kind') == 'blocked':
                    raise ExternalBlock(text(value.get('reason'), 'impedimento externo'))
                context = await self.obtain_context(value)
                if context is not None:
                    last_context, feedback = context, ''
                    state['model_feedback'].pop(feedback_key, None)
                    self.store.save()
                    continue
                if value.get('kind') == 'scope':
                    if not extra.get('task'):
                        raise ChangeError('A investigação e o plano devem descrever o escopo, sem pedir autorização de escrita antecipada.')
                    return value
                result = validate(value)
                state['model_feedback'].pop(feedback_key, None)
                self.store.save()
                return result
            except ExternalBlock:
                raise
            except ChangeConflict:
                raise
            except (ProviderError, asyncio.TimeoutError) as error:
                feedback = self.secrets.safe(str(error))
                state['model_feedback'][feedback_key] = feedback
                self.emit('Falha recuperável da chamada: ' + feedback + ' Nova tentativa da mesma operação; Interromper permanece disponível.')
                await asyncio.sleep(2)
            except (ChangeError, OSError, UnicodeError) as error:
                feedback = self.secrets.safe(str(error))
                state['model_feedback'][feedback_key] = feedback
                self.emit('Reexaminar resposta sem avançar: ' + feedback)
                await asyncio.sleep(0)

    async def investigate(self):
        self.phase('investigation', 'Investigando somente os componentes relacionados ao pedido.')
        if not self.entries:
            raise ChangeError('Não há arquivos de aplicação disponíveis neste diretório para investigar.')
        result = await self.model('Investigador', 'change-investigate.md', {},
                                  lambda v: validate_investigation(v, self.state['read_files']))
        self.state['investigation'] = result
        self.state['knowledge'].append({'summary': result['summary'], 'paths': result['files'], 'stale': False})
        self.store.write_json('investigacao.json', result)
        self.phase('interview', 'Investigação registrada; esclarecendo apenas lacunas reais.')

    async def interview(self):
        while not self.state['interview_done']:
            pending = self.state['question']
            if pending is None:
                def validate(value):
                    if value.get('kind') == 'done':
                        return value
                    if value.get('kind') != 'question':
                        raise ChangeError('Entrevista deve retornar question ou done.')
                    question = validate_question(value)
                    if any(' '.join(h['question'].casefold().split()) == ' '.join(question['text'].casefold().split()) for h in self.state['history']):
                        raise ChangeError('Esta pergunta já foi respondida; use o histórico confirmado.')
                    return {'kind': 'question', **question}
                value = await self.model('Entrevista', 'change-interview.md', {}, validate)
                if value.get('kind') == 'done':
                    self.state['interview_done'] = True
                    self.store.save()
                    break
                self.state['question'] = value
                self.store.save()
                pending = value
            answer = await self.human(self.ask, pending)
            if answer is None:
                self.state.update(status='interrupted', error='Entrevista interrompida; pergunta e respostas confirmadas preservadas.')
                self.store.save()
                return False
            self.state['history'].append({'question': pending['text'], 'choices': pending['choices'], 'answer': self.secrets.prose(answer)})
            self.state['question'] = None
            self.store.save()
            self.store.write_json('entrevista.json', {'history': self.state['history']})
        self.phase('planning', 'Produzindo plano localizado para aprovação.')
        return True

    async def plan(self):
        def validate(value):
            plan = validate_plan(value, self.store.root, self.options.forbid_automated_tests)
            definitions = {t['id']: t for t in plan['tasks']}
            for task in self.state['tasks']:
                if task['status'] == 'done' and (task['id'] not in definitions or any(task.get(k) != v for k, v in definitions[task['id']].items())):
                    raise ChangeError('Preserve as tasks concluídas e válidas ao atualizar somente o plano pendente.')
            return plan
        plan = await self.model('Planejador', 'change-plan.md', {'previous_plan': self.state['plan'],
                                'plan_adjustments': self.state.get('plan_adjustments', []),
                                'scope_proposal': self.state.get('scope_proposal')}, validate)
        self.state['plan'] = plan
        self.state['tasks'] = task_states(plan, self.state['tasks'])
        previous = self.store.path('plano.md')
        if previous.exists():
            self.store.write(f'planos/plano-{time.time_ns()}.md', previous.read_text())
        self.store.write('plano.md', render_plan(plan))
        self.phase('approval', 'Plano pronto; nenhuma alteração funcional é permitida antes da aprovação.')

    async def approval(self):
        self.store.check_sources()
        recovery = self.secrets.protect(git_context(self.store.root))
        decision = await self.human(self.approve, render_plan(self.state['plan']), recovery)
        action = (decision or {}).get('action', 'pause')
        if action == 'adjust':
            self.state.setdefault('plan_adjustments', []).append(self.secrets.prose(text(decision.get('text'), 'ajuste solicitado')))
            self.phase('planning', 'Ajuste de plano solicitado pelo usuário; implementação ainda não autorizada.')
            return True
        if action == 'cancel':
            self.state.update(status='cancelled', error='Solicitação cancelada; alterações já aplicadas, se houver, permanecem preservadas.')
            self.store.event('cancelled', self.state['error'])
            return False
        if action != 'approve':
            self.state.update(status='interrupted', error='Plano aguardando aprovação explícita.')
            self.store.save()
            return False
        self.store.check_sources()
        self.state['recovery'] = recovery
        self.store.write_json('baseline.json', project_snapshot(self.store.root))
        self.state['baseline_sha256'] = sha(self.store.path('baseline.json').read_bytes())
        for task in self.state['tasks']:
            for path in task['files']:
                self.state['expected'][path] = fingerprint(self.store.root, path)
        self.state['approval'] = {'at': now(), 'sha256': sha(json.dumps(self.state['plan'], ensure_ascii=False, sort_keys=True)),
                                  'markdown_sha256': sha(render_plan(self.state['plan']))}
        self.state['verification'] = None
        if self.state.get('scope_proposal'):
            proposal = self.state.pop('scope_proposal')
            self.state['history'].append({'question': 'Ampliação funcional apresentada no plano revisado',
                                         'choices': ['Aprovar plano revisado'], 'answer': proposal['reason']})
        self.store.event('approved', 'Plano aprovado explicitamente pelo usuário.')
        self.phase('execution', 'Executando as tasks aprovadas, uma por vez.')
        return True

    async def handle_scope(self, task, request):
        files = strings(request.get('files'), 'arquivos adicionais')
        reason = text(request.get('reason'), 'motivo de ampliação')
        for path in files:
            app_path(self.store.root, path, writable=True)
        if request.get('scope_type') == 'functional':
            self.state['scope_proposal'] = {'reason': reason, 'files': files, 'task': task['id']}
            self.state['approval'] = None
            self.phase('planning', 'Ampliação funcional exige um plano revisado para aprovação; nenhuma nova alteração foi aplicada.')
            return True
        approved_files = {p for item in self.state['plan']['tasks'] for p in item['files']}
        if files and set(files) <= approved_files and (self.state.get('verification') or {}).get('status') == 'falha':
            marker = self.state.get('verification_repair')
            if marker and marker['task'] == task['id'] and all(fingerprint(self.store.root, p) == h for p, h in marker['files'].items()):
                task.update(marker['previous'])
            owners = {next(item['id'] for item in reversed(self.state['tasks']) if path in item['files']) for path in files}
            for item in self.state['tasks']:
                if item['id'] in owners:
                    item.update(status='pending', writer='pending', g1='pending', g2='pending', operation='writer',
                                error='Verificação técnica: ' + reason)
            self.phase('execution', 'Correção encaminhada à task responsável, dentro do plano já aprovado.')
            return True
        if set(files) <= set(task['files']):
            task['error'] = 'Os arquivos solicitados já estão autorizados nesta task. Obtenha o contexto necessário e continue sem pedir nova autorização.'
            task['operation'] = 'preparation' if self.options.gates_enabled and task['g0'] != 'done' else 'writer'
            self.phase('execution', task['error'])
            return True
        self.state['scope_request'] = {'task': task['id'], 'files': files, 'reason': reason}
        self.phase('scope_review', 'A task solicita arquivos fora do escopo; nenhuma edição adicional foi aplicada.')
        return await self.scope_decision()

    async def scope_decision(self):
        request = self.state['scope_request']
        task = next(t for t in self.state['tasks'] if t['id'] == request['task'])
        decision = await self.human(self.scope, request)
        if decision == 'pause' or decision is None:
            self.state.update(status='interrupted', error='Ampliação de escopo aguardando decisão.')
            self.store.save()
            return False
        if decision == 'approve':
            for path in request['files']:
                app_path(self.store.root, path, writable=True)
                current = fingerprint(self.store.root, path)
                self.state['expected'][path] = current
                if 'before' in task:
                    task['before'].setdefault(path, current)
            definition = next(t for t in self.state['plan']['tasks'] if t['id'] == task['id'])
            definition['files'] = sorted(set(definition['files']) | set(request['files']))
            task['files'] = definition['files'][:]
            markdown = render_plan(self.state['plan'])
            self.store.write('plano.md', markdown)
            self.state['approval'] = {'at': now(), 'sha256': sha(json.dumps(self.state['plan'], ensure_ascii=False, sort_keys=True)), 'markdown_sha256': sha(markdown)}
            self.store.event('scope_approved', request['reason'] + ': ' + ', '.join(request['files']))
            self.state['history'].append({'question': 'Ampliação técnica autorizada', 'choices': ['Autorizar arquivos'],
                                         'answer': request['reason'] + ': ' + ', '.join(request['files'])})
            task.update(operation='g0', g0='pending', error='Novo escopo aprovado; conferir preparação.')
        else:
            task.update(operation='writer', error='Ampliação de escopo recusada. Resolva somente a task e os arquivos originalmente aprovados.')
            self.store.event('scope_declined', request['reason'])
        self.state.pop('scope_request')
        self.phase('execution', 'Continuando a execução com a decisão de escopo registrada.')
        return True

    def task_context(self, task):
        budget = 15000
        for path in task['files']:
            if fingerprint(self.store.root, path) is not None and budget > 1000:
                chunk = read_chunk(self.store.root, path, 1, self.secrets, budget=min(8000, budget))
                budget -= len(chunk['text'])
                self.add_context(chunk)
        return {'task': {k: task[k] for k in ('id', 'title', 'description', 'files', 'dependencies', 'inspection_only')},
                'objective': self.state['plan']['objective'], 'preserve': self.state['plan']['preserve'],
                'verification_plan': self.state['plan']['verification'],
                'feedback': task.get('error', ''), 'preparation': task.get('preparation', ''),
                'changes': task.get('changes', []), 'verification': self.verification_payload()}

    def verification_payload(self):
        result = self.state.get('verification')
        if not result:
            return None
        return {**result, 'comandos': [{**c, 'saida': c.get('saida', '')[-14000:]} for c in result.get('comandos', [])]}

    async def execute_task(self, task):
        task['status'] = 'running'
        while task['status'] != 'done':
            recover_patch(self.store)
            self.store.check_approval()
            self.store.check_sources()
            operation = task['operation']
            self.state['current_task'] = task['id']
            self.emit(f"Task {task['id']} — {task['title']} · {operation}.")
            extra = self.task_context(task)
            extra['actual_diff'] = task_diff(self.store, task, self.secrets)
            if operation == 'g0':
                if not self.options.gates_enabled:
                    task.update(g0='skipped', operation='writer')
                    continue
                task['g0'] = 'running'
                verdict = await self.model('G0', 'change-review.md', {**extra, 'gate': 'G0'}, validate_gate)
                if verdict.get('kind') == 'scope':
                    return await self.handle_scope(task, verdict)
                if verdict.get('scope_expansion'):
                    return await self.handle_scope(task, verdict['scope_expansion'])
                if verdict['approved']:
                    task.update(g0='done', operation='writer', error='')
                else:
                    task.update(g0='failed', operation='preparation', error='G0 REPROVADO: ' + '\n'.join(verdict['issues']))
            elif operation == 'preparation':
                def validate(value):
                    if value.get('kind') != 'preparation':
                        raise ChangeError('Preparação corrige entendimento sem propor alterações de código.')
                    text(value.get('summary'), 'preparação')
                    return value
                result = await self.model('Escritor', 'change-write.md', {**extra, 'operation': 'preparation'}, validate)
                if result.get('kind') == 'scope':
                    return await self.handle_scope(task, result)
                task.update(preparation=result['summary'], operation='g0', g0='pending')
            elif operation == 'writer':
                task['writer'] = 'running'
                capture_before(self.store, task)
                def validate(value):
                    if value.get('kind') != 'patch':
                        raise ChangeError('O escritor deve devolver patch ou pedir contexto/escopo.')
                    text(value.get('summary'), 'relato da task')
                    return value
                result = await self.model('Escritor', 'change-write.md', {**extra, 'operation': 'writer'}, validate)
                if result.get('kind') == 'scope':
                    return await self.handle_scope(task, result)
                try:
                    applied = stage_patch(self.store, task, result, self.secrets)
                    if 'scope_expansion' in applied:
                        return await self.handle_scope(task, applied['scope_expansion'])
                    task['writer_report'] = result['summary']
                except ChangeConflict:
                    raise
                except (ChangeError, OSError) as error:
                    task.update(writer='failed', error=self.secrets.safe(str(error)), operation='writer')
                    self.emit('Edição não aplicada: ' + task['error'])
            elif operation == 'g1':
                task['attempts']['G1'] = task['attempts'].get('G1', 0) + 1
                before = {p: h for p, h in task['before'].items() if h is not None}
                after = {p: fingerprint(self.store.root, p) for p in task['files'] if fingerprint(self.store.root, p) is not None}
                result = evaluate(before, after, {**task, 'technical_check': task['inspection_only']})
                if not result['arquivos'] and not task['inspection_only']:
                    result.update(aprovado=False, motivo='Nenhuma alteração funcional foi identificada nos arquivos da task.')
                task['changes'] = result['arquivos']
                self.store.write_json(f'tasks/{task["id"]}-g1.json', result)
                if result['aprovado']:
                    task.update(g1='done', operation='g2')
                else:
                    task.update(g1='failed', writer='pending', operation='writer', error='G1 REPROVADO: ' + result['motivo'])
            elif operation == 'g2':
                if not self.options.gates_enabled:
                    task.update(g2='skipped', status='done', operation='done')
                    continue
                task['g2'] = 'running'
                verdict = await self.model('G2', 'change-review.md', {**extra, 'gate': 'G2', 'writer_report': task.get('writer_report', '')}, validate_gate)
                if verdict.get('kind') == 'scope':
                    return await self.handle_scope(task, verdict)
                if verdict.get('scope_expansion'):
                    return await self.handle_scope(task, verdict['scope_expansion'])
                if verdict['approved']:
                    task.update(g2='done', status='done', operation='done', error='')
                else:
                    task.update(g2='failed', writer='pending', operation='writer', error='G2 REPROVADO: ' + '\n'.join(verdict['issues']))
            else:
                raise ChangeError('Operação de task não reconhecida; resultados preservados.')
            self.store.save()
        self.emit(f"Task {task['id']} concluída.")
        return True

    async def verification(self):
        self.store.check_approval()
        self.store.check_sources()
        self.phase('verification', 'Executando verificação técnica localizada em cópia isolada.')
        result = await verify_change(self.store, lambda message: self.update(self.state, self.secrets.safe(message)), self.secrets)
        self.state['verification'] = result
        self.store.save()
        self.store.check_sources()
        if result['status'] == 'falha':
            task = self.state['tasks'][-1]
            issues = 'Verificação técnica falhou; use a saída real para corrigir a implementação.'
            if self.options.gates_enabled:
                verdict = await self.model('G2', 'change-review.md', {**self.task_context(task), 'gate': 'G2',
                    'verification_failed': True, 'approved_tasks': [{'id': t['id'], 'files': t['files']} for t in self.state['tasks']]}, validate_gate)
                if verdict.get('kind') == 'scope':
                    return await self.handle_scope(task, verdict)
                repair = verdict.get('repair_task')
                if repair:
                    task = next((t for t in self.state['tasks'] if t['id'] == repair), None)
                    if task is None:
                        raise ChangeError('O auditor indicou reparo fora das tasks aprovadas.')
                if verdict.get('scope_expansion'):
                    return await self.handle_scope(task, verdict['scope_expansion'])
                issues += '\n' + '\n'.join(verdict['issues'])
            self.state['verification_repair'] = {'task': task['id'],
                'previous': {key: task[key] for key in ('status', 'writer', 'g1', 'g2', 'operation', 'error')},
                'files': {p: fingerprint(self.store.root, p) for p in task['files']}}
            task.update(status='pending', writer='pending', g1='pending', g2='pending', operation='writer', error=issues)
            self.phase('execution', 'Falha real devolvida ao escritor; as tasks válidas permanecem concluídas.')
            return True
        limitations = list(self.state['plan']['limitations'])
        if self.state['plan']['verification']['limitation']:
            limitations.append(self.state['plan']['verification']['limitation'])
        if result['status'] == 'indisponivel':
            limitations.append(result['motivo'])
        verification_limited = result['status'] == 'indisponivel' or bool(self.state['plan']['verification']['limitation'])
        status = 'done_with_limits' if verification_limited else 'done'
        self.state['limitations'] = limitations
        self.state.pop('verification_repair', None)
        changed = [dict(item, task=t['id']) for t in self.state['tasks'] for item in t.get('changes', [])]
        self.store.write_json('alteracoes.json', {'files': changed})
        report = '# Conclusão CHANGE\n\n' + self.state['original'] + '\n\n'
        report += '\n\n'.join(f"## Task {t['id']} — {t['title']}\nEstado: {t['status']}\n\n{t.get('writer_report', '')}" for t in self.state['tasks'])
        report += '\n\n## Arquivos alterados\n' + '\n'.join(f"- {c['arquivo']}: {c['tipo']}" for c in changed)
        report += '\n\n## Verificação real\n' + json.dumps(result, ensure_ascii=False, indent=2)
        if limitations:
            report += '\n\n## Limitações\n' + '\n'.join('- ' + v for v in limitations)
        self.store.write('conclusao.md', self.secrets.safe(report) + '\n')
        self.state.update(status=status, phase='done', error='')
        self.store.event('finished', 'Solicitação concluída' + (' com limitações explícitas de verificação.' if verification_limited else ' com verificação técnica aprovada.'))
        self.emit(self.state['events'][-1]['message'])
        return False

    async def run(self):
        identifier = self.store.identifier
        with self.store.execution():
            self.store.select(identifier)
            if self.state['status'] in {'done', 'cancelled'}:
                return self.store.path('conclusao.md') if self.state['status'] == 'done' else None
            self.state['selection'] = asdict(self.writer.selection)
            self.state['auditor'] = asdict(self.auditor.selection) if self.auditor else None
            self.store.start_clock()
            try:
                while True:
                    try:
                        recover_patch(self.store)
                        self.store.check_sources()
                        self.entries = catalog(self.store.root)
                        phase = self.state['phase']
                        if phase == 'done':
                            if self.state['status'] == 'done_with_limits':
                                self.phase('verification', 'Reverificando a solicitação por início explícito; tasks concluídas serão preservadas.')
                            else:
                                return self.store.path('conclusao.md')
                        elif phase == 'investigation':
                            await self.investigate()
                        elif phase == 'interview':
                            if not await self.interview():
                                return None
                        elif phase == 'planning':
                            await self.plan()
                        elif phase == 'approval':
                            if not await self.approval():
                                return None
                        elif phase == 'scope_review':
                            if not await self.scope_decision():
                                return None
                        elif phase == 'execution':
                            task = next((t for t in self.state['tasks'] if t['status'] != 'done'), None)
                            if task is None:
                                self.phase('verification', 'Tasks concluídas; verificando os componentes diretamente afetados.')
                            elif not await self.execute_task(task):
                                return None
                        elif phase == 'verification':
                            if not await self.verification():
                                return self.store.path('conclusao.md')
                        else:
                            raise ChangeError('Etapa da solicitação não reconhecida.')
                    except ChangeConflict as error:
                        self.state.update(status='conflict', error=str(error))
                        self.store.save()
                        if await self.human(self.conflict, error.paths):
                            self.store.refresh_conflicts(error.paths)
                        else:
                            return None
            except asyncio.CancelledError:
                self.state.update(status='interrupted', error='CHANGE interrompido; plano, respostas, tasks e checkpoints preservados.')
                self.store.event('interrupted', self.state['error'])
                raise
            except Exception as error:
                if self.state['phase'] == 'done':
                    self.state['phase'] = 'verification'
                self.state.update(status='failed', error=self.secrets.safe(str(error)))
                self.store.event('failed', self.state['error'])
                raise
            finally:
                self.store.pause_clock()


class ExternalBlock(ChangeError):
    pass
