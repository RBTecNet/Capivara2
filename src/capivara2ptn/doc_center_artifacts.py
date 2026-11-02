"""Publicações derivadas dos resultados confirmados; não publica logs ou pedidos remotos."""

import json
import re
from pathlib import Path
from datetime import datetime, timezone

from capivara2ptn.change_source import Secrets, sha
from capivara2ptn.mcp import McpError
from capivara2ptn.reverse_source import redact


class Cleaner:
    def __init__(self, root, tokens=(), source=None):
        self.filters = [Secrets(root)]
        if source and source != root:
            self.filters.append(Secrets(source))
        self.tokens = [token for token in tokens if token]

    def text(self, value):
        for token in sorted(self.tokens, key=len, reverse=True):
            value = value.replace(token, '[OCULTO]')
        value = re.sub(r'\bdct_[A-Za-z0-9_-]{16,}\b', '[OCULTO]', value)
        for filter in self.filters:
            value = filter.prose(value)
        return redact(value)

    def value(self, value):
        if isinstance(value, str):return self.text(value)
        if isinstance(value, list):return [self.value(item) for item in value]
        if isinstance(value, dict):return {key: self.value(item) for key, item in value.items()}
        return value


def local_text(root, path):
    path = Path(path)
    if not path.is_relative_to(root / '.harness'):
        raise McpError('Artefato fora da área interna do projeto.')
    for candidate in (path, *path.parents):
        if candidate == root.parent:break
        if candidate.is_symlink():raise McpError('Artefatos para publicação não podem ser links.')
    return path.read_text(encoding='utf-8')


def observed_at(state, path):
    for item in reversed(state.get('events', state.get('history', []))):
        if item.get('kind') == 'finished' or item.get('event') == 'done':
            stamp = item.get('at')
            if isinstance(stamp, str):
                try:return datetime.fromisoformat(stamp.replace('Z', '+00:00')).isoformat()
                except ValueError:pass
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def reverse_artifacts(root, cleaner):
    base = root / '.harness/reverse'
    if not (base / 'estado.json').exists():return []
    state = json.loads(local_text(root, base / 'estado.json'))
    if state.get('status') != 'done':return []
    final = local_text(root, base / 'prompt-final.md')
    if sha(final) != state.get('prompt_sha256'):
        raise McpError('Prompt consolidado REVERSE ausente ou alterado; publicação não iniciada.')
    result = [{'key': 'reverse/prompt-final', 'label': 'prompt-final', 'kind': 'document', 'content': cleaner.text(final)}]
    overview = base / 'documentacao/visao-geral.md'
    if overview.exists():
        result.append({'key': 'reverse/visao-geral', 'label': 'visao-geral', 'kind': 'document', 'content': cleaner.text(local_text(root, overview))})
    for domain in state.get('domains', []):
        identifier = domain['id']
        if not re.fullmatch(r'[a-f0-9]+', identifier):raise McpError('Identificador de domínio inválido.')
        path = base / 'dominios' / (identifier + '.md')
        result.append({'key': 'reverse/dominio/' + identifier, 'label': 'dominio-' + identifier[:8],
                       'kind': 'document', 'content': cleaner.text(local_text(root, path))})
    coverage = state.get('coverage', {})
    limitations = coverage.get('limitations', state.get('limitations', []))
    summary = '# Descobertas e limitações do REVERSE\n\n'
    summary += '\n'.join('- ' + str(d.get('name', '')) for d in state.get('domains', []))
    summary += '\n\nLimitações explícitas:\n' + ('\n'.join('- ' + str(item) for item in limitations) or 'Nenhuma registrada.')
    summary += '\n\nO prompt consolidado continua sendo uma especificação para entrevista no INIT; inferências não são requisitos confirmados.\n'
    result.append({'key': 'reverse/descobertas', 'label': 'descobertas', 'kind': 'memory', 'nature': 'convencao', 'content': cleaner.text(summary)})
    for artifact in result:artifact['observed_at'] = observed_at(state, base / 'prompt-final.md')
    return result


def change_artifacts(root, cleaner, identifier=None):
    base = root / '.harness/change/solicitacoes'
    if not base.exists():return []
    requests = [base / identifier] if identifier else sorted(base.glob('change-*'))
    result = []
    for directory in requests:
        if not re.fullmatch(r'change-\d+', directory.name):raise McpError('Identificador de solicitação inválido.')
        state = json.loads(local_text(root, directory / 'estado.json'))
        if state.get('status') not in {'done', 'done_with_limits'}:continue
        summary = '# CHANGE ' + directory.name + '\n\n## Solicitação\n' + state['original']
        investigation = state.get('investigation') or {}
        summary += '\n\n## Contexto observado\n' + investigation.get('summary', '')
        summary += '\n\n## Alterações concluídas\n'
        for task in state.get('tasks', []):
            summary += f"\n### {task['id']} — {task['title']}\n" + task.get('writer_report', '')[:8000]
            summary += '\n' + '\n'.join(f"- {change['arquivo']}: {change['tipo']}" for change in task.get('changes', []))
        decisions = '\n'.join(f"- {item['question']}\n  Decisão: {item['answer']}" for item in state.get('history', []))
        summary += '\n\n## Decisões confirmadas pelo usuário\n' + (decisions or 'Nenhuma pergunta adicional foi necessária.')
        verification = state.get('verification') or {}
        checks = {'status': verification.get('status'), 'motivo': verification.get('motivo'),
                  'comandos': [{'comando': c.get('comando'), 'diretorio': c.get('diretorio'), 'codigo_retorno': c.get('codigo_retorno')}
                               for c in verification.get('comandos', [])]}
        summary += '\n\n## Verificações reais\n' + json.dumps(checks, ensure_ascii=False, indent=2)
        summary += '\n\n## Limitações\n' + ('\n'.join('- ' + x for x in state.get('limitations', [])) or 'Nenhuma registrada.')
        result.append({'key': directory.name + '/documentacao', 'label': directory.name, 'kind': 'document', 'content': cleaner.text(summary)})
        memory = '# Resultado ' + directory.name + '\n\n' + (state.get('plan') or {}).get('objective', state['original'])
        memory += '\n\nComponentes afetados:\n' + '\n'.join('- ' + p for p in sorted({c['arquivo'] for t in state.get('tasks', []) for c in t.get('changes', [])}))
        memory += '\n\nDecisões do usuário:\n' + (decisions[:6000] or 'Sem decisões adicionais.')
        memory += '\n\nVerificação: ' + str(verification.get('status', 'não identificada'))
        if state.get('limitations'):memory += '\nLimitações: ' + '; '.join(state['limitations'])[:2500]
        result.append({'key': directory.name + '/memoria', 'label': directory.name, 'kind': 'memory',
                       'nature': 'decisao' if decisions else 'convencao', 'content': cleaner.text(memory)})
        for artifact in result[-2:]:artifact['observed_at'] = observed_at(state, directory / 'estado.json')
    return result
