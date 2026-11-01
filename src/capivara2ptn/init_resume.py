"""Checkpoints locais do INIT e importação conservadora de execuções anteriores."""

import hashlib
import json
import os
import tempfile
import time
from importlib.resources import files
from pathlib import Path

from capivara2ptn.prompt_files import PromptError, load_prompt, prompt_directory
from capivara2ptn.workspace import data_directory

RUN_FILES = ('prompt-original.txt', 'entrevista.txt', 'resposta.txt', 'prompt-final.md', 'g0-init.txt', 'erro-init.txt')
PHASES = {'interview', 'writing', 'reviewing', 'correcting', 'done'}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def pending_init_path(cwd: Path) -> Path | None:
    directory = data_directory(cwd)
    path = directory / 'prompt-original.txt'
    if (cwd / '.harness').is_symlink() or directory.is_symlink() or path.is_symlink():
        return None
    if (directory / 'init-concluido.json').exists() or (directory / 'init-concluido.json').is_symlink():
        return None
    return path if path.is_file() else None


def load_correction_prompt() -> str:
    directory = prompt_directory()
    marker = directory.parent / '.init-resume-prompts-installed'
    if not marker.exists():
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = directory / 'init-correction.md'
            if not target.exists():
                with target.open('x', encoding='utf-8') as stream:
                    stream.write(files('capivara2ptn').joinpath('prompts/init-correction.md').read_text(encoding='utf-8'))
            marker.touch()
        except OSError:
            raise PromptError(f'Não foi possível preparar o prompt de correção do INIT em {directory}.') from None
    return load_prompt('init-correction.md')


class InitSession:
    def __init__(self, cwd, error, parse_question, g0_decision):
        self.directory = data_directory(cwd)
        self.error = error
        self.parse_question = parse_question
        self.g0_decision = g0_decision
        self.state = None
        self.owned = set()
        self.resuming = False

    def target(self, name):
        path = self.directory / name
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise self.error('Caminho de checkpoint inválido no INIT.')
        for candidate in (self.directory.parent, self.directory, path, *path.parents):
            if candidate.is_symlink():
                raise self.error('A retomada do INIT não pode atravessar links. Arquivos preservados.')
        return path

    def text(self, name):
        path = self.target(name)
        try:
            if not path.exists():
                return None
            with path.open('r', encoding='utf-8', newline='') as stream:
                return stream.read()
        except (OSError, UnicodeError):
            raise self.error(f'Arquivo do INIT inválido ou ilegível: {name}. Arquivos preservados.') from None

    def atomic(self, name, text):
        path = self.target(name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.init-', dir=path.parent)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.target(name))
            temporary = None
        except OSError:
            raise self.error(f'Não foi possível salvar {name}. Checkpoints anteriores preservados.') from None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def save(self):
        self.atomic('init-estado.json', json.dumps(self.state, ensure_ascii=False, indent=2) + '\n')

    def check(self):
        transaction = self.state.get('transaction')
        for name in RUN_FILES:
            current = self.text(name)
            actual = digest(current) if current is not None else None
            expected = self.state['files'].get(name)
            allowed = {expected}
            if transaction and transaction['name'] == name:
                allowed.add(transaction['after'])
            if actual not in allowed:
                raise self.error(f'{name} mudou fora desta execução. Retomada interrompida para preservar os dados existentes.')
        if self.state['g0_status'] == 'approved':
            try:
                approved, _ = self.g0_decision(self.text('g0-init.txt') or '')
            except self.error:
                approved = False
            if not approved:
                raise self.error('O checkpoint não corresponde a um parecer G0 aprovado. Arquivos preservados.')

    def recover(self):
        transaction = self.state.get('transaction')
        if not transaction:
            return
        name, text = transaction['name'], transaction['text']
        if name not in RUN_FILES or (digest(text) if text is not None else None) != transaction['after']:
            raise self.error('Transação de INIT inválida. Nenhum resultado foi substituído.')
        self.check()
        if transaction.get('archive'):
            previous = self.text(name)
            archive = transaction['archive']
            if not archive.startswith('init/historico/'):
                raise self.error('Caminho de histórico inválido no INIT.')
            if previous is not None and digest(previous) == transaction['before'] and self.text(archive) is None:
                self.atomic(archive, previous)
        if text is None:
            self.target(name).unlink(missing_ok=True)
            self.state['files'].pop(name, None)
        else:
            self.atomic(name, text)
            self.state['files'][name] = transaction['after']
        self.state.update(transaction['changes'])
        self.state.pop('transaction')
        self.save()

    def commit(self, name, text, changes=None):
        if self.state.get('transaction'):
            self.recover()
        self.check()
        before = self.state['files'].get(name)
        after = digest(text) if text is not None else None
        transaction = {'name': name, 'text': text, 'before': before, 'after': after, 'changes': changes or {}}
        if before is not None and before != after and name in {'prompt-final.md', 'g0-init.txt', 'erro-init.txt'}:
            transaction['archive'] = f'init/historico/{time.time_ns()}-{name}'
        self.state['transaction'] = transaction
        self.save()
        self.recover()
        self.owned.add(name)

    def begin(self, original):
        if self.target('init-concluido.json').exists():
            raise self.error('INIT já concluído. Use a confirmação de Refazer INIT para iniciar outra entrevista.')
        if any(self.target(name).exists() for name in ('plan-estado.json', 'build-estado.json')):
            raise self.error('Existem resultados posteriores de PLAN/BUILD. A retomada não pode substituir seu PRD sem um reinício confirmado.')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        saved = self.text('init-estado.json')
        if saved is not None:
            try:
                state = json.loads(saved)
                if (state['version'] != 1 or state['phase'] not in PHASES or not isinstance(state['files'], dict)
                        or any(name not in RUN_FILES for name in state['files'])
                        or any(not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)
                               for value in [state['original_sha256'], *state['files'].values()])
                        or type(state['response_consumed']) is not bool or type(state['interview_complete']) is not bool
                        or type(state['questions_answered']) is not int or state['questions_answered'] < 0
                        or state['g0_status'] not in {'pending', 'approved', 'rejected', 'inconclusive'}
                        or not isinstance(state['g0_issues'], str)
                        or (state['phase'] != 'interview' and not state['interview_complete'])
                        or (state['questions_answered'] > 0 and 'entrevista.txt' not in state['files'])
                        or (state['phase'] != 'interview' and not {'prompt-original.txt', 'entrevista.txt', 'resposta.txt'} <= state['files'].keys())):
                    raise ValueError
                question = state['question']
                if question is not None and (not isinstance(question, dict) or not isinstance(question.get('text'), str)
                        or not question['text'].strip() or not isinstance(question.get('choices'), list)
                        or len(question['choices']) < 2 or any(not isinstance(choice, str) or not choice.strip() for choice in question['choices'])):
                    raise ValueError
                if state.get('transaction'):
                    transaction = state['transaction']
                    if (transaction['name'] not in RUN_FILES or not isinstance(transaction['changes'], dict)
                            or not set(transaction['changes']) <= {'phase', 'response_consumed', 'question', 'interview_complete',
                                                                  'questions_answered', 'g0_status', 'g0_issues'}
                            or transaction['before'] != state['files'].get(transaction['name'])
                            or (transaction['text'] is not None and not isinstance(transaction['text'], str))):
                        raise ValueError
            except (ValueError, KeyError, TypeError):
                raise self.error('Checkpoint do INIT inválido. Arquivos existentes preservados.') from None
            if state['original_sha256'] != digest(original):
                raise self.error('O pedido mudou desde este INIT. Retome com o pedido salvo em .harness/dados/prompt-original.txt.')
            self.state = state
            self.resuming = True
            self.recover()
            self.check()
        else:
            existing = {name: self.text(name) for name in RUN_FILES}
            self.state = {'version': 1, 'original_sha256': digest(original), 'files': {},
                          'phase': 'interview', 'response_consumed': True, 'interview_complete': False,
                          'question': None, 'questions_answered': 0, 'g0_status': 'pending', 'g0_issues': ''}
            if any(value is not None for value in existing.values()):
                if existing['prompt-original.txt'] != original or existing['entrevista.txt'] is None or existing['resposta.txt'] is None:
                    raise self.error('Arquivos anteriores não permitem retomar este pedido com segurança. Originais preservados.')
                self.resuming = True
                self.state['files'] = {name: digest(text) for name, text in existing.items() if text is not None}
                history = existing['entrevista.txt']
                self.state['questions_answered'] = history.count('\nResposta: ')
                if existing['resposta.txt'].strip().upper() == 'CONCLUIDO':
                    self.state.update(interview_complete=True, phase='writing')
                    if existing['prompt-final.md'] and existing['g0-init.txt']:
                        self.state['phase'] = 'reviewing'
                        try:
                            approved, issues = self.g0_decision(existing['g0-init.txt'])
                            if not approved:
                                self.state.update(phase='correcting', g0_status='rejected', g0_issues=issues)
                        except self.error:
                            pass
                    elif existing['prompt-final.md'] and not existing['erro-init.txt']:
                        self.state['phase'] = 'reviewing'
                elif existing['prompt-final.md'] or existing['g0-init.txt']:
                    raise self.error('O PRD anterior não possui confirmação de entrevista concluída. Arquivos preservados.')
                else:
                    try:
                        question = self.parse_question(existing['resposta.txt'])
                    except self.error:
                        question = None
                    if question and f'Pergunta: {question.transcript()}\nResposta: ' not in history:
                        self.state.update(question={'text': question.text, 'choices': list(question.choices)}, response_consumed=False)
                self.save()
            else:
                self.commit('prompt-original.txt', original)
        for name in ('entrevista.txt', 'resposta.txt'):
            if name not in self.state['files']:
                if self.state['interview_complete'] or self.state['questions_answered']:
                    raise self.error('Histórico confirmado do INIT está ausente. Arquivos preservados.')
                self.commit(name, '')
        self.owned.update(self.state['files'])
        # Recupera a resposta já gravada se o processo parou antes de abrir o modal.
        if self.state['phase'] == 'interview' and not self.state['response_consumed'] and not self.state['question']:
            try:
                self.parse_question(self.text('resposta.txt'))
            except self.error:
                pass
            else:
                self.accept_interview_response(self.text('resposta.txt'))
        self.state['last_start_ns'] = time.time_ns()
        self.save()

    def transition(self, **changes):
        if self.state.get('transaction'):
            self.recover()
        self.check()
        self.state.update(changes)
        self.save()

    def write(self, name, text, append=False):
        if name not in RUN_FILES:
            raise self.error('Arquivo de execução não reconhecido.')
        if append:
            text = (self.text(name) or '') + text
        changes = {}
        if name == 'resposta.txt':
            changes.update(response_consumed=False, question=None)
        elif name == 'prompt-final.md':
            if not text.strip():
                raise self.error('O escritor devolveu um PRD vazio. Progresso anterior preservado.')
            changes.update(phase='reviewing', g0_status='pending', g0_issues='')
        elif name == 'g0-init.txt':
            try:
                approved, issues = self.g0_decision(text)
                changes.update(phase='done' if approved else 'correcting', g0_status='approved' if approved else 'rejected', g0_issues=issues)
            except self.error:
                changes.update(phase='reviewing', g0_status='inconclusive')
        self.commit(name, text, changes)

    def accept_interview_response(self, response):
        question = self.parse_question(response)
        if question is None:
            self.transition(interview_complete=True, response_consumed=True, question=None, phase='writing')
        else:
            self.transition(question={'text': question.text, 'choices': list(question.choices)})
        return question

    def answer(self, question, answer):
        history = self.text('entrevista.txt') or ''
        history += f'Pergunta: {question.transcript()}\nResposta: {answer}\n\n'
        self.commit('entrevista.txt', history, {'question': None, 'response_consumed': True,
                    'questions_answered': self.state['questions_answered'] + 1})

    def partial(self, output, text):
        self.atomic(f'init/parciais/{time.time_ns()}-{output}', text)

    def complete(self):
        if 'erro-init.txt' in self.state['files']:
            self.commit('erro-init.txt', None)
        self.transition(phase='done')
