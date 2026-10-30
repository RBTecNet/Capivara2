"""Flags nativas de execução sem confirmação, exclusivas do build."""

import json

from .agents import AgentError


def build_command(selection, schema, output, schema_file, cwd):
    provider = selection.provider
    if provider == 'codex':
        command = ['codex','--no-daemon','exec','--json','--ephemeral','--ignore-user-config',
            '--ignore-rules','--skip-git-repo-check','--dangerously-bypass-approvals-and-sandbox',
            '--output-schema',str(schema_file),'--output-last-message',str(output),
            '-c','project_doc_max_bytes=0','--enable','skip_host_skill_discovery']
        for feature in ('multi_agent','hooks','plugins','apps','browser_use','computer_use','image_generation'):
            command += ['--disable',feature]
        if selection.model != 'default': command += ['--model',selection.model]
        if selection.effort != 'default': command += ['-c',f'model_reasoning_effort={json.dumps(selection.effort)}']
        return command+['-'],'codex'
    if provider == 'claude':
        command = ['claude','-p','--dangerously-skip-permissions','--output-format','json',
            '--json-schema',json.dumps(schema),'--no-session-persistence','--strict-mcp-config',
            '--mcp-config','{"mcpServers":{}}','--settings','{"disableAllHooks":true}',
            '--setting-sources','','--disallowedTools','Agent']
        if selection.model != 'default': command += ['--model',selection.model]
        if selection.effort != 'default': command += ['--effort',selection.effort]
        return command,'claude'
    if provider == 'opencode':
        command = ['opencode','run','--standalone','--auto','--format','json']
        if selection.model != 'default':
            model = selection.model
            if selection.effort != 'default': model=model.split('#',1)[0]+'#'+selection.effort
            command += ['--model',model]
        elif selection.effort != 'default':
            raise AgentError('Selecione um modelo explícito do OpenCode para aplicar effort no build.')
        return command,'opencode'
    if provider == 'agy':
        command = ['agy','--input-format','stream-json','--output-format','stream-json',
                   '--dangerously-skip-permissions','--json-schema',json.dumps(schema)]
        if selection.model != 'default': command += ['--model',selection.model]
        if selection.effort != 'default': command += ['--effort',selection.effort]
        return command,'agy'
    raise AgentError(f'CLI não suportado no build: {provider}.')
