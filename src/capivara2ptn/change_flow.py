"""Pedido → escritor no projeto → verificação real → correção, sem fluxo prévio."""

import asyncio
import json
from dataclasses import asdict

from capivara2ptn.change_prompts import change_prompt
from capivara2ptn.change_protocol import response, text, validate_question
from capivara2ptn.change_source import ChangeError, IGNORED, git_context, project_snapshot
from capivara2ptn.change_verify import verification_spec, verify_change
from capivara2ptn.providers import ProviderError


def changes(before, after):
    return [{'arquivo': path, 'tipo': 'criado' if path not in before else 'removido' if path not in after else 'modificado'}
            for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]


class ChangeRun:
    def __init__(self, store, writer, options, secrets, update, ask):
        self.store, self.writer, self.options = store, writer, options
        self.secrets, self.update, self.ask = secrets, update, ask
        self.secrets.values.update(getattr(writer, 'secrets', []))

    @property
    def state(self):return self.store.state

    @property
    def direct(self):return self.state['direct']

    def emit(self, message):
        self.store.save()
        self.update(self.state, self.secrets.prose(message))

    async def snapshot(self):
        return await asyncio.to_thread(project_snapshot, self.store.root)

    async def checkpoint(self):
        current = await self.snapshot()
        self.direct['checkpoint'] = current
        baseline = self.store.read_json('baseline-direto.json')
        if baseline is None:raise ChangeError('Registro inicial do CHANGE ausente; histórico preservado para recuperação.')
        self.direct['changes'] = changes(baseline, current)
        self.store.write_json('alteracoes.json', {'files': self.direct['changes']})
        self.store.save()
        return current

    async def initialize(self):
        current = await self.snapshot()
        if not self.state.get('direct'):
            legacy = self.state['phase'] not in {'execution', 'done'} or bool(self.state.get('plan') or self.state.get('investigation') or self.state.get('pending_apply'))
            if legacy:
                self.store.write_json('historico-fluxo-anterior.json', self.state)
            baseline = self.store.read_json('baseline.json') if legacy else None
            baseline = current if baseline is None else baseline
            baseline = {path: value for path, value in baseline.items() if not set(path.split('/')) & IGNORED}
            self.store.write_json('baseline-direto.json', baseline)
            recovery = await asyncio.to_thread(git_context, self.store.root)
            recovery['recovery'] = ('HEAD e alterações pendentes registrados; sem rollback automático.' if recovery['available'] else
                                    'Sem ponto de restauração garantido. Alterações existentes serão preservadas; não há rollback automático.')
            self.state.update(direct={'version': 1, 'checkpoint': current, 'changes': [], 'result': None,
                                     'feedback': '', 'verified': False, 'legacy': legacy},
                              phase='execution', status='execution', question=None, recovery=recovery)
            self.state.pop('pending_apply', None)  # journal antigo continua preservado, sem reaplicar patches
            self.store.event('direct_started', 'Execução direta do pedido; histórico anterior preservado.' if legacy else 'Execução direta do pedido.')
            self.emit(recovery['recovery'])
        else:
            changed = changes(self.direct['checkpoint'], current)
            if changed:
                baseline = self.store.read_json('baseline-direto.json')
                own_paths = {item['arquivo'] for item in self.direct['changes']}
                for item in changed:
                    path = item['arquivo']
                    if path not in own_paths:
                        if path in current:baseline[path] = current[path]
                        else:baseline.pop(path, None)
                self.store.write_json('baseline-direto.json', baseline)
                self.direct.update(result=None, verified=False,
                    feedback='O projeto mudou desde o checkpoint. Leia a versão atual e preserve modificações do usuário: ' +
                             ', '.join(item['arquivo'] for item in changed))
                self.state.update(phase='execution', verification=None)
                self.store.event('source_changed', self.direct['feedback'])
            elif self.state['status'] == 'done_with_limits':
                self.direct['verified'] = False
                self.state['phase'] = 'verification'
        await self.checkpoint()

    def payload(self):
        verification = self.state.get('verification')
        if verification:
            verification = {**verification, 'comandos': [{**item, 'saida': item.get('saida', '')[-20000:]}
                             for item in verification.get('comandos', [])]}
        return self.secrets.protect({'request_id': self.store.identifier, 'request': self.state['original'],
            'project': str(self.store.root), 'history': self.state['history'], 'feedback': self.direct['feedback'],
            'verification_feedback': verification, 'previous_summary': (self.direct.get('result') or {}).get('summary', ''),
            'modified_files': self.direct['changes'], 'resuming': self.state['attempts'].get('writer', 0) > 0,
            'existing_user_changes': self.state.get('recovery', {}).get('status', []),
            'legacy_history': ({'completed': [{'title': t['title'], 'summary': t.get('writer_report', '')}
                               for t in self.state.get('tasks', []) if t['status'] == 'done']} if self.direct.get('legacy') else None),
            'forbid_automated_tests': self.options.forbid_automated_tests})

    async def clarify(self):
        self.state['status'] = 'interview'  # valor anterior preservado no estado; não há etapa de entrevista
        self.emit('Aguardando esclarecimento funcional essencial.')
        self.store.pause_clock()
        try:
            answer = await self.ask(self.state['question'])
        finally:
            self.store.start_clock()
        if answer is None:
            self.state.update(status='interrupted', error='Esclarecimento pendente; retome para responder.')
            self.store.event('interrupted', self.state['error'])
            return False
        self.state['history'].append({'question': self.state['question']['text'], 'choices': self.state['question']['choices'],
                                      'answer': self.secrets.prose(answer)})
        self.state.update(question=None, status='execution', error='')
        self.store.write_json('esclarecimentos.json', {'history': self.state['history']})
        self.store.event('clarified', 'Esclarecimento recebido; continuando o pedido diretamente.')
        return True

    async def write(self):
        self.state.update(phase='execution', status='execution', error='')
        self.state['attempts']['writer'] = self.state['attempts'].get('writer', 0) + 1
        self.emit('Escritor executando o pedido diretamente no projeto.')
        try:
            raw = await self.writer.invoke(change_prompt('change-direct.md'), self.payload(), access='write')
            raw = self.secrets.prose(raw)
            self.store.write(f'respostas/escritor-{self.state["attempts"]["writer"]}.md', raw + '\n')
            value = response(raw)
            if value.get('kind') == 'question':
                self.state['question'] = validate_question(value)
            elif value.get('kind') == 'completed':
                result = {'summary': text(value.get('summary'), 'resumo da alteração'),
                          'verification': verification_spec(value.get('verification'), self.store.root)}
                if (self.state.get('verification') or {}).get('status') == 'falha' and not result['verification']['commands']:
                    raise ChangeError('Uma verificação que falhou não pode ser omitida para declarar conclusão. Corrija a falha real.')
                self.direct.update(result=result, verified=False, feedback='')
                self.state.update(phase='verification', verification=None)
            else:
                raise ChangeError('Após executar o pedido, devolva kind completed com resumo e comandos, ou uma pergunta funcional essencial.')
        except ProviderError as error:
            if any(word in str(error) for word in ('não encontrada no PATH', 'Effort não reconhecido', 'Provedor não suportado', 'configuração inline')):
                raise
            self.direct['feedback'] = self.secrets.prose(str(error)) + '\nInspecione o trabalho parcial no disco antes de continuar.'
            self.store.event('provider_retry', self.direct['feedback'])
            self.emit('Falha do provedor; progresso preservado. Nova tentativa com o mesmo modelo.')
            await asyncio.sleep(2)
        except ChangeError as error:
            self.direct['feedback'] = self.secrets.prose(str(error)) + '\nAlterações já feitas permanecem no disco; não as repita.'
            self.emit(self.direct['feedback'])
        finally:
            await self.checkpoint()

    async def verify(self):
        self.state.update(phase='verification', status='verification', error='')
        self.emit('Executando a verificação técnica aplicável; sem auditor ou gates.')
        try:
            result = await verify_change(self.store, lambda value: self.update(self.state, self.secrets.prose(value)), self.secrets)
            self.state['verification'] = result
            if result['status'] == 'falha':
                self.direct.update(verified=False, feedback='Verificação técnica falhou. Corrija o código usando verification_feedback e preserve os requisitos.')
                self.state.update(phase='execution', status='execution')
                self.store.event('verification_failed', 'Saída real devolvida ao mesmo escritor; correção automática sem nova aprovação.')
                return
            if result['status'] not in {'sucesso', 'indisponivel', 'nao_aplicavel'}:
                raise ChangeError('A verificação não foi concluída; resultados preservados para retomada.')
            self.direct['verified'] = True
        finally:
            await self.checkpoint()

    def finish(self):
        verification = self.state['verification']
        limitation = self.direct['result']['verification']['limitation']
        self.state['limitations'] = [item for item in (limitation, verification.get('motivo') if verification['status'] != 'sucesso' else None) if item]
        status = 'done_with_limits' if self.state['limitations'] or verification['status'] != 'sucesso' else 'done'
        report = '# Conclusão CHANGE\n\n' + self.state['original'] + '\n\n' + self.direct['result']['summary']
        report += '\n\n## Arquivos modificados\n' + ('\n'.join('- ' + c['arquivo'] + ': ' + c['tipo'] for c in self.direct['changes']) or 'Nenhuma alteração de arquivo necessária.')
        report += '\n\n## Verificação técnica real\n' + json.dumps(verification, ensure_ascii=False, indent=2)
        if self.state['limitations']:report += '\n\n## Limitações\n' + '\n'.join('- ' + item for item in self.state['limitations'])
        self.store.write('conclusao.md', self.secrets.prose(report) + '\n')
        self.state.update(status=status, phase='done', error='')
        self.store.event('finished', 'Solicitação concluída.' if status == 'done' else 'Solicitação concluída com limitações explícitas de verificação.')
        self.emit(self.state['events'][-1]['message'])
        return self.store.path('conclusao.md')

    async def run(self):
        identifier = self.store.identifier
        with self.store.execution():
            self.store.select(identifier)
            if self.state['status'] in {'done', 'cancelled'}:
                return self.store.path('conclusao.md') if self.state['status'] == 'done' else None
            self.state['selection'] = asdict(self.writer.selection)
            self.store.start_clock()
            try:
                await self.initialize()
                while True:
                    if self.state.get('question'):
                        if not await self.clarify():return None
                    elif not self.direct.get('result') or self.state['phase'] == 'execution':
                        await self.write()
                    elif not self.direct['verified']:
                        await self.verify()
                    else:
                        return self.finish()
            except asyncio.CancelledError:
                self.state.update(status='interrupted', error='CHANGE interrompido; alterações, histórico e progresso preservados.')
                self.store.event('interrupted', self.state['error'])
                raise
            except Exception as error:
                self.state.update(status='failed', error=self.secrets.prose(str(error)))
                self.store.event('failed', self.state['error'])
                raise
            finally:
                self.store.pause_clock()
