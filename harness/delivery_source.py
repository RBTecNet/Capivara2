"""Fonte portátil, inventário e comandos de instalação derivados dos manifestos."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import re
import tomllib

from .artifacts import atomic_write
from .build_artifacts import BuildError
from .build_models import CommandSpec
from .build_workspace import manifest
from .recovery import fingerprint

START='<!-- capivara2:installation:start -->'
END='<!-- capivara2:installation:end -->'
MANIFESTS=('package.json','package-lock.json','pnpm-lock.yaml','yarn.lock','tsconfig.json','tsconfig.app.json','tsconfig.node.json','requirements.txt','pyproject.toml','uv.lock','Cargo.toml','Cargo.lock','go.mod','go.sum','composer.json','composer.lock','Gemfile','Gemfile.lock')


def source_tree(root):
    # Dados locais e saídas de testes não são pré-requisitos da instalação de outro usuário.
    return {name:value for name,value in manifest(root).items() if name!='README.md' and Path(name).name not in (':memory:',':memory:-wal',':memory:-shm')
        and not any(p in ('results','report','.cache','.egg-info') or p.endswith('.egg-info') for p in Path(name).parts)
        and not name.endswith(('.sqlite','.sqlite3','.db','.sqlite-wal','.sqlite-shm','.db-wal','.db-shm','.tsbuildinfo'))}


def source_name(name):
    return (name=='README.md' or Path(name).name not in (':memory:',':memory:-wal',':memory:-shm') and not any(p in ('results','report','.cache') or p.endswith('.egg-info') for p in Path(name).parts)
        and not name.endswith(('.sqlite','.sqlite3','.db','.sqlite-wal','.sqlite-shm','.db-wal','.db-shm','.tsbuildinfo')))


def source_key(root):
    return fingerprint(source_tree(root))


def copy_source(root,target):
    target.mkdir(parents=True,mode=0o700,exist_ok=True)
    for name in [*source_tree(root),*(['README.md'] if (root/'README.md').is_file() else [])]:
        destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/name,destination)
    return target


def manifests(root):
    result={}
    for name in MANIFESTS:
        path=root/name
        if not path.is_file():continue
        if name.endswith(('lock.json','lock.yaml','.lock')) or name in ('go.sum',):
            result[name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        elif name.endswith('.json'):
            from .cli_protocols import json_config
            try:result[name]=json_config(path.read_text())
            except ValueError as exc:result[name]={'parse_error':str(exc),'source':path.read_text()[:24000]}
        else:result[name]=path.read_text()[:24000]
    return result


def command(argv,purpose):
    return CommandSpec(argv=argv,purpose=purpose,timeout=600)


def installation(root,plan):
    package=root/'package.json'
    if plan.install:return plan.install
    if not package.exists():return []
    try:info=json.loads(package.read_text())
    except ValueError as exc:raise BuildError('package.json inválido: '+str(exc)) from exc
    manager=info.get('packageManager','').split('@',1)[0]
    if manager=='pnpm' or not (root/'package-lock.json').exists() and (root/'pnpm-lock.yaml').exists():
        if not (root/'pnpm-lock.yaml').exists():raise BuildError('A entrega pnpm exige pnpm-lock.yaml para instalação reprodutível.')
        return [command(['pnpm','install','--frozen-lockfile'],'Instalar exatamente as dependências do lockfile.')]
    if manager=='yarn' or not (root/'package-lock.json').exists() and (root/'yarn.lock').exists():
        if not (root/'yarn.lock').exists():raise BuildError('A entrega Yarn exige yarn.lock para instalação reprodutível.')
        version=info.get('packageManager','yarn@1').split('@')[-1]
        return [command(['yarn','install','--frozen-lockfile' if version.startswith('1.') else '--immutable'],'Instalar exatamente as dependências do lockfile.')]
    if not (root/'package-lock.json').exists():raise BuildError('A entrega npm exige package-lock.json. Gere um lockfile válido antes de tentar npm ci.')
    return [command(['npm','ci','--no-audit','--no-fund'],'Instalação limpa com scripts de instalação habilitados.')]


def compilation(root,plan):
    required=[];package=root/'package.json'
    if package.is_file():
        info=json.loads(package.read_text());scripts=info.get('scripts',{})
        manager=info.get('packageManager','npm').split('@',1)[0]
        manager=manager if manager in ('npm','yarn','pnpm') else 'npm'
        if 'typecheck' in scripts:required.append(command([manager,'run','typecheck'],'Conferir a tipagem declarada pelo projeto.'))
        elif (root/'tsconfig.json').exists() and 'typescript' in {**info.get('dependencies',{}),**info.get('devDependencies',{})}:
            required.append(command(['node','node_modules/typescript/bin/tsc','--noEmit'],'Conferir os tipos TypeScript da entrega.'))
        if 'build' in scripts:required.append(command([manager,'run','build'],'Compilar a entrega.'))
    for check in plan.checks:
        if not any(c.argv==check.command.argv and c.env==check.command.env for c in required):required.append(check.command)
    return required


def package_rows(root):
    package=root/'package.json'
    if package.exists():
        info=json.loads(package.read_text());manager=info.get('packageManager','npm').split('@',1)[0]
        manager=manager if manager in ('npm','yarn','pnpm') else 'npm'
        result=[]
        for group,label in (('dependencies','Funcionamento'),('devDependencies','Desenvolvimento/compilação')):
            for name,version in sorted(info.get(group,{}).items()):
                argv=[manager,'install' if manager=='npm' else 'add']
                if group=='devDependencies':argv+=['--save-dev' if manager=='npm' else '-D']
                argv+=[f'{name}@{version}']
                result.append((name,str(version),label,shlex.join(argv)))
        return result
    result=[]
    def python_row(spec,group):
        match=re.match(r'([A-Za-z0-9_.-]+)(.*)',spec.strip())
        if match:
            name,version=match.groups()
            result.append((name,version.strip() or 'Conforme manifesto',group,shlex.join(['python3','-m','pip','install',spec])))
    requirements=root/'requirements.txt'
    if requirements.exists():
        for line in requirements.read_text().splitlines():
            line=line.strip()
            if line and not line.startswith(('#','-')):python_row(line,'Dependência Python')
    project=root/'pyproject.toml'
    if project.exists():
        config=tomllib.loads(project.read_text())
        for spec in config.get('project',{}).get('dependencies',[]):python_row(spec,'Funcionamento')
        for group,items in config.get('project',{}).get('optional-dependencies',{}).items():
            for spec in items:python_row(spec,'Opcional: '+group)
        for spec in config.get('build-system',{}).get('requires',[]):python_row(spec,'Compilação/empacotamento')
    if result:return result
    for name in ('requirements.txt','pyproject.toml','Cargo.toml','go.mod','composer.json','Gemfile'):
        if (root/name).exists():result.append((name,'Conforme manifesto','Dependências do projeto','Ver comando de instalação validado abaixo.'))
    return result


def readable(command):
    return ('env '+shlex.join([f'{v.name}={v.value}' for v in command.env])+' ' if command.env else '')+shlex.join(command.argv)


def readme_body(root):
    path=root/'README.md';text=path.read_text() if path.exists() else ''
    if START in text and END in text:
        first,rest=text.split(START,1);_,last=rest.split(END,1);return (first+last).rstrip()
    return text.rstrip()


def render_readme(root,plan,installs,checks,status,applied,diagnostics=()):
    body=readme_body(root)
    for patch in plan.readme_changes:
        key=fingerprint(patch.model_dump())
        if key in applied:continue
        if patch.before in body:
            body=body.replace(patch.before,patch.after,1);applied.append(key)
        elif patch.after not in body:raise BuildError('A correção do README não corresponde mais ao documento; reanalisar a entrega.')
    lines=[START,'## Instalação e dependências verificadas pelo Capivara2','',f'**Situação da entrega:** {status}.','',
        '### Pré-requisitos do sistema','',
        '| Pacote/ferramenta | Necessidade | Instalação ou resolução |','| --- | --- | --- |']
    for dep in plan.prerequisites:
        commands='; '.join(readable(c).replace('sudo -n ','sudo ') for c in dep.manual_commands)
        instructions=(commands+' — ' if commands else '')+dep.manual_instructions
        values=[dep.name,dep.purpose,instructions]
        lines.append('| '+' | '.join(v.replace('|','\\|').replace('\n',' ') for v in values)+' |')
    rows=package_rows(root)
    if rows:
        lines+=['','### Pacotes do projeto','','| Pacote | Versão declarada | Uso | Instalação específica |','| --- | --- | --- | --- |']
        for row in rows:lines.append('| '+' | '.join(str(v).replace('|','\\|').replace('\n',' ') for v in row)+' |')
        lines+=['','Os comandos específicos são para manutenção e podem atualizar o lockfile. Para reproduzir a entrega, use o procedimento completo a seguir.']
    lines+=['','### Procedimento de instalação','','```sh',*(readable(c) for c in installs),'```',
        '','### Compilação e verificação de tipos','','```sh',*(readable(c) for c in checks),'```',
        '','### Inicialização conferida','']
    for probe in plan.startup:
        lines+=['```sh',readable(probe.command).replace('{port}',str(probe.default_port)),'```']
    if diagnostics:
        lines+=['','### Diagnósticos adicionais (não bloqueantes)','',*(f"- `{readable(d['command'] if hasattr(d['command'],'argv') else CommandSpec.model_validate(d['command']))}` retornou {d['exit_code']}: {d.get('observed',d.get('stderr',''))[:1500]}" for d in diagnostics),'']
    lines+=['','Os comandos de pré-requisitos referem-se à plataforma usada na validação. Mensagens e saídas reais ficam nos logs do build.',END,'']
    atomic_write(root/'README.md',body+'\n\n'+'\n'.join(lines))
