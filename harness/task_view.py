"""Visão de revisão de uma task: entrega, base e dependências declaradas/importadas."""
import ast
from pathlib import Path
import re
import shutil
import tempfile

from .build_workspace import manifest, matches, relative_path, RUNTIMES


class TaskView:
    def __init__(self,source,data,scope):
        self.source=source
        self.root=Path(tempfile.mkdtemp(prefix='capivara2-task-review-'))
        tree=manifest(source)
        files=set(data.get('delivered_files',[]))
        files.update(data.get('changed_files',[]))
        files.update(data.get('additional_reserved_files',[]))
        files.update(entry['file'] for entry in (data.get('file_evidence') or {}).get('files',[]))
        if not files:
            files.update(name for name in tree if matches(name,[p for p in scope.writes if p not in ('*','**')]))
        files.update(name for name in tree if matches(name,[p for p in scope.reads if p not in ('*','**')]))
        files.update(name for name in tree if Path(name).name in (
            'package.json','tsconfig.json','tsconfig.app.json','tsconfig.node.json',
            'pyproject.toml','requirements.txt','Cargo.toml','go.mod'))
        pending=list(files);seen=set()
        while pending:
            name=relative_path(pending.pop())
            if name in seen or name not in tree:continue
            seen.add(name)
            path=source/name
            if path.suffix not in ('.py','.ts','.tsx','.js','.jsx','.mjs','.cjs'):continue
            text=path.read_text(errors='replace')
            imports=[]
            if path.suffix=='.py':
                try:
                    for node in ast.walk(ast.parse(text)):
                        if isinstance(node,ast.Import):imports.extend((a.name.replace('.','/'),0) for a in node.names)
                        elif isinstance(node,ast.ImportFrom):imports.append(((node.module or '').replace('.','/'),node.level))
                except SyntaxError:pass
            else:
                imports=[(value,1) for value in re.findall(r'(?:from\s*|import\s*\(?|require\s*\()\s*[\'"](\.[^\'"]+)[\'"]',text)]
            for value,level in imports:
                base=Path(name).parent if level else Path()
                if path.suffix=='.py':
                    for _ in range(max(0,level-1)):base=base.parent
                target=(source/base/value).resolve()
                if not target.is_relative_to(source.resolve()):continue
                relative=target.relative_to(source.resolve()).as_posix()
                candidates=[relative,*(relative+ext for ext in ('.py','.ts','.tsx','.js','.jsx','.json')),
                    *(relative+'/'+leaf for leaf in ('__init__.py','index.ts','index.tsx','index.js'))]
                if relative.endswith(('.js','.mjs')):candidates.extend([relative.rsplit('.',1)[0]+'.ts',relative.rsplit('.',1)[0]+'.tsx'])
                pending.extend(candidate for candidate in candidates if candidate in tree and candidate not in seen)
        for name in seen:
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
        for name in seen|files:
            name=relative_path(name)
            baseline=source/'.harness/baseline'/name
            if baseline.is_file():
                target=self.root/'.harness/baseline'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(baseline,target)
        checks=source/'.harness/checks'
        if checks.exists():shutil.copytree(checks,self.root/'.harness/checks',ignore=shutil.ignore_patterns('tmp','__pycache__'))
        (self.root/'.harness/checks').mkdir(parents=True,exist_ok=True)
        for name in RUNTIMES:
            if (source/name).exists():(self.root/name).symlink_to((source/name).resolve(),target_is_directory=True)
        self.before=manifest(self.root)

    def request(self,data):
        result=dict(data,workspace=str(self.root),baseline_directory=str(self.root/'.harness/baseline'))
        if 'available_files' in result:result['available_files']=sorted(self.before)
        return result

    def changes(self):
        after=manifest(self.root)
        return [name for name in self.before.keys()|after.keys() if self.before.get(name)!=after.get(name)]

    def archive(self,directory):
        for name in self.changes():
            if (self.root/name).is_file():
                target=directory/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(self.root/name,target)

    def checks_to_source(self):
        for path in (self.root/'.harness/checks').rglob('*'):
            if path.is_symlink():raise ValueError('Scripts de verificação devem ser arquivos locais.')
            if not path.is_file() or 'tmp' in path.relative_to(self.root/'.harness/checks').parts:continue
            target=self.source/path.relative_to(self.root);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(path.read_bytes().replace(str(self.root).encode(),str(self.source).encode()))

    def result(self,value):
        if isinstance(value,str):return value.replace(str(self.root),str(self.source))
        if isinstance(value,list):return [self.result(v) for v in value]
        if isinstance(value,dict):return {k:self.result(v) for k,v in value.items()}
        return value

    def close(self):shutil.rmtree(self.root)
