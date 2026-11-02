"""Preparação compartilhada do REA oficial; nenhum motor integra o pacote Capivara."""

import asyncio
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import time
import threading
import urllib.request
import zipfile
from pathlib import Path

from capivara2ptn.rea_common import ReaError, checked, read_json, run, write_json, server_environment,locked
from capivara2ptn.mcp import McpError

REPOSITORY='https://github.com/RBTecNet/rea'
API='https://api.github.com/repos/RBTecNet/rea'
ENV_PATHS={'GHIDRA_INSTALL_DIR','JAVA_HOME','REA_JADX_MCP_JAR','HOPPER_LAUNCHER_PATH','REA_IDA_MCP_CONFIG'}


def node_supported(version):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',version.strip())
    if not match:return False
    major,minor,_=map(int,match.groups())
    return (major==22 and minor>=19) or (major==24 and minor>=11) or major>=26


def fetch_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'CAPIVARA2PTN-REA','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=40) as response:return json.load(response)


def download(url,path,checksum=None,cancel=None):
    if not url.startswith('https://'):raise ReaError('Downloads REA exigem HTTPS.')
    path=checked(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists() and checksum:
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()==checksum:return path
    temporary=path.with_name(path.name+'.download')
    req=urllib.request.Request(url,headers={'User-Agent':'CAPIVARA2PTN-REA'})
    with urllib.request.urlopen(req,timeout=60) as response,temporary.open('wb') as stream:
        os.chmod(temporary,0o600)
        if not response.url.startswith('https://'):raise ReaError('Download redirecionado para transporte não seguro.')
        while True:
            if cancel and cancel.is_set():raise ReaError('Download REA interrompido.')
            chunk=response.read(512*1024)
            if not chunk:break
            stream.write(chunk)
    with temporary.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if checksum and actual!=checksum:raise ReaError('Checksum não corresponde ao publicado; arquivo não instalado.')
    if cancel and cancel.is_set():raise ReaError('Download REA interrompido.')
    os.replace(temporary,path)
    return path


def _unpack(archive,destination,cancel=None):
    """Extrair somente conteúdo regular, sem escapes ou links de arquivos externos."""
    destination=checked(destination);destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    def target(name):
        path=destination/name
        if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(destination.resolve()):
            raise ReaError('Arquivo de instalação contém um caminho fora do destino.')
        return checked(path)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as stream:
            for item in stream.infolist():
                if cancel and cancel.is_set():raise ReaError('Extração REA interrompida.')
                path=target(item.filename)
                if ((item.external_attr>>16)&0o170000)==0o120000:raise ReaError('Link inesperado no pacote de instalação.')
                if item.is_dir():path.mkdir(parents=True,exist_ok=True);continue
                path.parent.mkdir(parents=True,exist_ok=True)
                with stream.open(item) as source,path.open('wb') as out:shutil.copyfileobj(source,out)
                path.chmod(((item.external_attr>>16)&0o777) or 0o600)
    else:
        with tarfile.open(archive) as stream:
            for item in stream:
                if cancel and cancel.is_set():raise ReaError('Extração REA interrompida.')
                path=target(item.name)
                if item.isdir():path.mkdir(parents=True,exist_ok=True);continue
                if item.issym():
                    link=Path(item.linkname)
                    if link.is_absolute() or not (path.parent/link).resolve().is_relative_to(destination.resolve()):raise ReaError('Link externo no pacote de instalação.')
                    if not path.exists():path.symlink_to(item.linkname)
                    continue
                if not item.isfile():raise ReaError('Tipo de arquivo inesperado no pacote de instalação.')
                path.parent.mkdir(parents=True,exist_ok=True)
                with stream.extractfile(item) as source,path.open('wb') as out:shutil.copyfileobj(source,out)
                path.chmod(item.mode&0o777)


def unpack(archive,destination,cancel=None):
    destination=checked(destination)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.rea-extraindo-',dir=destination.parent) as temporary:
        stage=Path(temporary);_unpack(archive,stage,cancel)
        if cancel and cancel.is_set():raise ReaError('Extração REA interrompida.')
        destination.mkdir(parents=True,exist_ok=True,mode=0o700)
        for path in stage.iterdir():
            target=checked(destination/path.name)
            if target.exists():target.rename(target.with_name(target.name+'.anterior-'+str(time.time_ns())))
            path.rename(target)


class ReaSetup:
    def __init__(self,log,safe,approve,privilege=None,base=None):
        self.base=Path(base or Path.home()/'.local/share/capivara2ptn/rea').absolute()
        self.log,self.safe,self.approve,self.privilege=log,safe,approve,privilege
        self.cancelled=threading.Event()
        checked(self.base).mkdir(parents=True,exist_ok=True,mode=0o700)
        self.config=read_json(self.base/'instalacao.json',{'version':1,'environment':{},'steps':{}})
        if self.config.get('version')!=1:raise ReaError('Configuração da instalação REA incompatível.')
        if set(self.config.get('environment',{}))-ENV_PATHS:raise ReaError('Configuração REA contém variáveis não autorizadas.')
        self.command=self.config.get('command',[])

    def save(self):write_json(self.base/'instalacao.json',self.config)
    def cancel(self):self.cancelled.set()
    def reload(self):
        self.config=read_json(self.base/'instalacao.json',{'version':1,'environment':{},'steps':{}})
        if self.config.get('version')!=1 or set(self.config.get('environment',{}))-ENV_PATHS:raise ReaError('Configuração compartilhada REA inválida.')
        self.command=self.config.get('command',[])

    def environment(self):
        env=server_environment(self.config.get('environment',{}))
        if self.config.get('node_bin'):env['PATH']=self.config['node_bin']+os.pathsep+env.get('PATH','')
        env['PATH']=env.get('PATH','')+os.pathsep+os.defpath
        return env

    async def execute(self,argv,*,cwd=None,input_text=None,allow_failure=False,json_output=False,label=None):
        self.log('Preparação REA · '+(label or self.safe(' '.join(map(str,argv)))))
        transcript=[]
        def log(text):
            transcript.append(text)
            if not json_output:self.log(text)
        code,output=await run(argv,cwd or self.base,self.environment(),log,self.safe,input_text=input_text,separate_stderr=json_output)
        with (self.base/'preparacao.log').open('a',encoding='utf-8') as stream:
            os.chmod(self.base/'preparacao.log',0o600);stream.write('\n'.join(transcript)+'\n')
        if code:
            reason='\n'.join(transcript)[-16000:]
            self.config['last_error']=reason;self.save()
            if not allow_failure:raise ReaError(f'Preparação encerrou com código {code}.\n'+reason)
        return code,output

    async def cli_json(self,args,*,allow_failure=False):
        code,output=await self.execute([*self.command,*args],allow_failure=True,json_output=True)
        try:value=json.loads(output)
        except ValueError:raise ReaError('REA não devolveu diagnóstico JSON válido.\n'+self.safe(output[-8000:] or self.config.get('last_error',''))) from None
        if not isinstance(value,dict):raise ReaError('Diagnóstico REA não é um objeto JSON.')
        if code and not allow_failure:raise ReaError('Diagnóstico REA falhou.\n'+self.safe(json.dumps(value,ensure_ascii=False))[-12000:])
        return value

    async def node(self,force=False):
        env=self.environment();node=shutil.which('node',path=env.get('PATH'))
        if node and not force:
            code,out=await self.execute([node,'--version'],allow_failure=True)
            if code==0 and node_supported(out) and shutil.which('npm',path=env.get('PATH')):return node
        if platform.system() not in {'Linux','Darwin'}:raise ReaError('Preparação automática do Node disponível em Linux/macOS; nenhuma ferramenta foi instalada.')
        arch={'x86_64':'x64','aarch64':'arm64','arm64':'arm64'}.get(platform.machine())
        if arch is None:raise ReaError('Arquitetura sem pacote oficial Node configurado.')
        system='linux' if platform.system()=='Linux' else 'darwin'
        release=await asyncio.to_thread(fetch_json,'https://nodejs.org/dist/index.json')
        version=next(r['version'] for r in release if r['version'].startswith('v24.') and node_supported(r['version']))
        filename=f'node-{version}-{system}-{arch}.tar.gz';url=f'https://nodejs.org/dist/{version}/'
        def sums():
            with urllib.request.urlopen(url+'SHASUMS256.txt',timeout=40) as response:return response.read().decode()
        checksum=next(line.split()[0] for line in (await asyncio.to_thread(sums)).splitlines() if line.split()[-1]==filename)
        archive=await asyncio.to_thread(download,url+filename,self.base/'downloads'/filename,checksum,self.cancelled)
        directory=self.base/'node'
        if not (directory/f'node-{version}-{system}-{arch}'/'bin/node').exists():await asyncio.to_thread(unpack,archive,directory,self.cancelled)
        self.config['node_bin']=str(directory/f'node-{version}-{system}-{arch}'/'bin');self.save()
        node=shutil.which('node',path=self.environment()['PATH'])
        code,out=await self.execute([node,'--version'])
        if not node_supported(out):raise ReaError('Node instalado não atende ao contrato do REA.')
        return node

    async def healthy(self,command):
        previous=self.command;self.command=command
        try:
            report=await self.cli_json(['mcp','doctor','--json'])
            if report.get('healthy') and report.get('server',{}).get('name')=='rea' and await self.compatible():
                metadata={'healthy':True,'server':report['server'],'identity':report.get('identity',{}),'inventory':report.get('inventory',{})}
                self.config.update(command=command,ready=True,mcp_doctor=metadata,last_error='');self.save();return True
        except (ReaError,McpError,OSError,asyncio.TimeoutError):pass
        self.command=previous;return False

    async def compatible(self):
        from capivara2ptn.rea_mcp import ReaMcp
        client=ReaMcp([*self.command,'mcp'],self.base,self.environment(),lambda text:None,self.safe)
        try:
            await asyncio.wait_for(client.start(),60)
            result=await asyncio.wait_for(client.call('binary_session',{}),60)
            return not result.get('isError',False)
        finally:await client.close()

    async def ensure(self,force=False):
        with locked(self.base/'preparacao.lock'):
            self.reload()
            candidates=[self.command] if self.command else []
            existing=shutil.which('rea')
            if existing and [existing] not in candidates:candidates.append([existing])
            for command in ([] if force else candidates):
                if await self.healthy(command):
                    self.command=command;self.log('REA existente verificado pelo diagnóstico MCP oficial.');return self
            if self.config.get('source'):self.config.setdefault('steps',{}).pop('build',None)
            plan={'title':'Preparar REA','items':['REA de '+REPOSITORY,'Dependências npm e build oficiais',
                'Node.js/npm compatíveis em diretório privado, somente se necessários'],
                'destination':str(self.base),'commands':['npm ci --no-audit --no-fund','npm run build:cached'],
                'note':'Não modifica os registros MCP, autenticação ou configurações de Codex, Claude, Agy ou OpenCode.'}
            if not await self.approve(plan):raise ReaError('Preparação REA não autorizada; investigação não iniciada.')
            node=await self.node()
            if platform.system()=='Linux':
                ps=shutil.which('ps',path=self.environment()['PATH'])
                code,out=await self.execute([ps,'--version'],allow_failure=True) if ps else (1,'')
                if code or 'procps' not in out.casefold():await self.system_packages(['procps'])
            self.config['ready']=False;self.save()
            revision=self.config.get('revision')
            if revision is None:
                commit=await asyncio.to_thread(fetch_json,API+'/commits/main');revision=commit.get('sha','')
                if not re.fullmatch(r'[a-f0-9]{40}',revision):raise ReaError('GitHub não confirmou o commit oficial do REA.')
                self.config['revision']=revision;self.save()
            source=self.base/'fontes'/('rea-'+revision)
            if not (source/'package.json').exists():
                archive=await asyncio.to_thread(download,f'https://codeload.github.com/RBTecNet/rea/tar.gz/{revision}',self.base/'downloads'/f'{revision}.tar.gz',None,self.cancelled)
                await asyncio.to_thread(unpack,archive,self.base/'fontes',self.cancelled)
            manifest=json.loads((source/'package.json').read_text())
            if manifest.get('name')!='rea-agents':raise ReaError('O código obtido não é o pacote REA esperado.')
            steps=self.config.setdefault('steps',{})
            if steps.get('npm_ci')!=revision or not (source/'node_modules').is_dir():
                # HUSKY=0 evita criar hooks Git na cópia compartilhada de instalação.
                while True:
                    code,out=await run([str(Path(node).parent/'npm'),'ci','--no-audit','--no-fund'],source,
                        {**self.environment(),'HUSKY':'0'},self.log,self.safe)
                    if not code:break
                    if await self.repair_build(out):continue
                    self.config['last_error']=self.safe(out[-16000:]);self.save();raise ReaError('npm ci do REA falhou.\n'+self.safe(out[-16000:]))
                steps['npm_ci']=revision;self.save()
            if steps.get('build')!=revision or not (source/'dist/mcp/main.js').exists():
                while True:
                    try:
                        await self.execute([str(Path(node).parent/'npm'),'run','build:cached'],cwd=source);break
                    except ReaError as error:
                        if not await self.repair_build(str(error)):raise
                steps['build']=revision;self.save()
            self.command=[node,str(source/'scripts/rea.mjs')]
            self.config.update(command=self.command,package_version=manifest['version'],source=str(source));self.save()
            if not await self.healthy(self.command):raise ReaError('O REA foi preparado, mas seu diagnóstico MCP não confirmou funcionamento. Etapas concluídas preservadas.')
            return self

    async def jdk(self):
        env=self.environment();home=env.get('JAVA_HOME');java=str(Path(home)/'bin/java') if home else shutil.which('java',path=env.get('PATH'))
        javac=str(Path(home)/'bin/javac') if home else shutil.which('javac',path=env.get('PATH'))
        private=list((self.base/'jdk').glob('*/bin/javac'))+list((self.base/'jdk').glob('*/Contents/Home/bin/javac'))
        if not home and private:
            home=private[0].parent.parent;java=str(home/'bin/java');javac=str(home/'bin/javac')
        if java and javac and Path(java).is_file() and Path(javac).is_file():
            code,out=await self.execute([java,'-version'],allow_failure=True)
            match=re.search(r'version "(\d+)',out)
            if not code and match and int(match[1])>=21:
                if home:self.config['environment']['JAVA_HOME']=str(home);self.save()
                return
        arch={'x86_64':'x64','aarch64':'aarch64','arm64':'aarch64'}.get(platform.machine())
        system={'Linux':'linux','Darwin':'mac'}.get(platform.system())
        if not arch or not system:raise ReaError('Instalação automática do JDK indisponível nesta plataforma.')
        values=await asyncio.to_thread(fetch_json,f'https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture={arch}&image_type=jdk&os={system}&vendor=eclipse')
        package=values[0]['binary']['package']
        if not package['link'].startswith('https://github.com/adoptium/'):raise ReaError('Origem do JDK não corresponde ao fornecedor oficial.')
        archive=await asyncio.to_thread(download,package['link'],self.base/'downloads'/package['name'],package['checksum'],self.cancelled)
        directory=self.base/'jdk'
        await asyncio.to_thread(unpack,archive,directory,self.cancelled)
        candidates=list(directory.glob('*/bin/javac'))+list(directory.glob('*/Contents/Home/bin/javac'))
        if not candidates:raise ReaError('JDK obtido sem compilador Java.')
        self.config['environment']['JAVA_HOME']=str(candidates[0].parent.parent);self.save()
        await self.execute([str(candidates[0]),'-version'])

    async def system_packages(self,packages):
        if platform.system()!='Linux':raise ReaError('Instalação de pacotes do sistema não disponível neste host.')
        installer=next((tool for tool in ('apt-get','dnf','apk','pacman','zypper') if shutil.which(tool)),None)
        if installer is None:raise ReaError('Gerenciador de pacotes Linux não reconhecido; nenhuma instalação foi executada.')
        allowed={'procps':{'apt-get':['procps'],'dnf':['procps-ng'],'apk':['procps'],'pacman':['procps-ng'],'zypper':['procps']},
                 'build':{'apt-get':['build-essential'],'dnf':['gcc-c++','make'],'apk':['build-base'],'pacman':['base-devel'],'zypper':['gcc-c++','make']},
                 'python':{'apt-get':['python3'],'dnf':['python3'],'apk':['python3'],'pacman':['python'],'zypper':['python3']}}
        names=[package for name in packages for package in allowed[name][installer]]
        commands={'apt-get':[['apt-get','update'],['apt-get','install','-y',*names]],'dnf':[['dnf','install','-y',*names]],
                  'apk':[['apk','add',*names]],'pacman':[['pacman','-S','--needed','--noconfirm',*names]],'zypper':[['zypper','--non-interactive','install',*names]]}[installer]
        if not await self.approve({'title':'Instalar dependências do sistema','items':names,'commands':[' '.join(c) for c in commands],
                                  'destination':'sistema operacional','note':'Somente os pacotes solicitados pelo diagnóstico atual.'}):
            raise ReaError('Dependência indispensável não autorizada.')
        prefix=[];password=None
        if os.geteuid()!=0:
            if not shutil.which('sudo'):raise ReaError('Elevação administrativa indisponível: sudo não está instalado.')
            code,_=await self.execute(['sudo','-n','true'],allow_failure=True)
            if code:
                if self.privilege:password=await self.privilege()
                if not password:raise ReaError('Autenticação administrativa não fornecida; resultados preservados.')
                # A senha vai só ao sudo de validação, nunca ao stdin do gerenciador.
                await self.execute(['sudo','-S','-p','','-v'],input_text=password+'\n')
                password=None
            prefix=['sudo','-n','--']
        for command in commands:await self.execute([*prefix,*command])

    async def repair_build(self,output):
        if platform.system()!='Linux':return False
        missing=[];path=self.environment()['PATH'];low=output.casefold()
        if ('python' in low and any(text in low for text in ('not found','could not find','cannot find'))) and not shutil.which('python3',path=path):missing.append('python')
        if any(text in low for text in ('make: not found',"not found: make",'g++: not found','c++: not found','c compiler cannot')):
            if any(not shutil.which(name,path=path) for name in ('make','g++')):missing.append('build')
        if not missing:return False
        self.log('O erro real identificou dependências de build ausentes: '+', '.join(missing))
        await self.system_packages(missing)
        expected={'python':['python3'],'build':['make','g++']}
        if any(not shutil.which(name,path=self.environment()['PATH']) for item in missing for name in expected[item]):
            raise ReaError('A instalação terminou, mas os executáveis necessários continuam ausentes; o comando não será repetido indefinidamente.')
        return True

    async def dependency(self,name):
        if name not in {'ghidra','hopper','jdk','jadx','node','procps','build'}:raise ReaError('Instalação não suportada: '+name)
        if name=='hopper':await self.hopper();return
        if name in {'procps','build'}:
            await self.system_packages([name]);return
        if name in {'ghidra','jadx'}:
            report=await self.cli_json(['doctor','--provider',name,'--json'],allow_failure=True)
            if report.get('healthy'):
                self.log(name.upper()+' já funcional; instalação reutilizada.');return
        plan={'title':'Preparar '+name.upper(),'items':[name]+(['JDK 21 se necessário'] if name in {'ghidra','jadx'} else []),
              'destination':str(self.base),'note':'Download oficial com checksum. Instalação privada compartilhada; sem alteração dos demais módulos.'}
        if not await self.approve(plan):raise ReaError('Instalação não autorizada: '+name)
        with locked(self.base/'preparacao.lock'):
            self.reload()
            if name=='node':
                node=await self.node(force=True)
                if len(self.command)>1 and self.command[1].endswith('/scripts/rea.mjs'):
                    self.command[0]=node;self.config['command']=self.command;self.save()
            if name in {'jdk','ghidra','jadx'}:await self.jdk()
            if name=='ghidra':
                if await self.reuse_ghidra():return
                release=await asyncio.to_thread(fetch_json,'https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/tags/Ghidra_12.1.4_build')
                asset=next(a for a in release['assets'] if a['name'].endswith('.zip') and 'PUBLIC' in a['name'])
                digest=asset.get('digest') or ''
                if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):raise ReaError('A distribuição oficial Ghidra não forneceu checksum verificável.')
                archive=await asyncio.to_thread(download,asset['browser_download_url'],self.base/'downloads'/asset['name'],digest.split(':')[1],self.cancelled)
                directory=self.base/'ghidra';await asyncio.to_thread(unpack,archive,directory,self.cancelled)
                installation=next((p.parent.parent for p in directory.glob('*/support/analyzeHeadless')),None)
                if not installation:raise ReaError('Distribuição Ghidra sem analyzeHeadless.')
                self.config['environment']['GHIDRA_INSTALL_DIR']=str(installation);self.save()
            if name=='jadx':
                source=Path(self.config.get('source',''))/'src/android/JadxRelease.ts'
                if source.is_file():text=source.read_text()
                else:
                    commit=await asyncio.to_thread(fetch_json,API+'/commits/main');revision=commit['sha']
                    manifest=await asyncio.to_thread(fetch_json,API+'/contents/package.json?ref='+revision)
                    manifest=json.loads(base64.b64decode(manifest['content']))
                    version=self.config.get('mcp_doctor',{}).get('server',{}).get('version')
                    if manifest['version']!=version:raise ReaError('Metadados JADX da versão instalada não foram confirmados; download presumido recusado.')
                    remote=await asyncio.to_thread(fetch_json,API+'/contents/src/android/JadxRelease.ts?ref='+revision)
                    text=base64.b64decode(remote['content']).decode()
                url=re.search(r'url:\s*"([^"]+)"',text);checksum=re.search(r'sha256:\s*"([a-f0-9]{64})"',text)
                if not url or not checksum or not url[1].startswith('https://github.com/1013503897/jadx-headless-mcp/releases/download/'):
                    raise ReaError('Metadados oficiais JADX não reconhecidos.')
                file=await asyncio.to_thread(download,url[1],self.base/'downloads'/Path(url[1]).name,checksum[1],self.cancelled)
                self.config['environment']['REA_JADX_MCP_JAR']=str(file);self.save()
            if name=='ghidra':await self.cli_json(['doctor','--provider','ghidra','--json'])
            elif name=='jadx':await self.cli_json(['doctor','--provider','jadx','--json'])
            elif name=='node':
                if not await self.healthy(self.command):raise ReaError('REA não iniciou com o Node preparado.')
            self.config['last_error']='';self.save()

    async def reuse_ghidra(self):
        candidates=[]
        launcher=shutil.which('analyzeHeadless',path=self.environment()['PATH'])
        if launcher:candidates.append(Path(launcher).resolve().parent.parent)
        for parent in (self.base/'ghidra',Path('/opt'),Path('/usr/share'),Path('/usr/local/share'),Path.home()/'.local/share'):
            if parent.exists():candidates.extend(p for p in parent.glob('*') if 'ghidra' in p.name.casefold() and (p/'support/analyzeHeadless').is_file())
        old=dict(self.config['environment'])
        for candidate in candidates:
            self.config['environment']['GHIDRA_INSTALL_DIR']=str(candidate.resolve())
            report=await self.cli_json(['doctor','--provider','ghidra','--json'],allow_failure=True)
            if report.get('healthy'):
                self.save();self.log('Ghidra existente verificado e reutilizado: '+str(candidate));return True
            self.config['environment']=dict(old)
        return False

    async def configure(self,provider,value):
        keys={'ghidra':'GHIDRA_INSTALL_DIR','hopper':'HOPPER_LAUNCHER_PATH','ida':'REA_IDA_MCP_CONFIG','jadx':'REA_JADX_MCP_JAR','jdk':'JAVA_HOME'}
        if provider not in keys or not isinstance(value,str):raise ReaError('Configuração de ferramenta não reconhecida.')
        path=Path(value).expanduser().resolve(strict=True)
        if provider in {'ghidra','jdk'} and not path.is_dir():raise ReaError('Selecione o diretório da instalação.')
        if provider not in {'ghidra','jdk'} and not path.is_file():raise ReaError('Selecione o arquivo da instalação/configuração existente.')
        if not await self.approve({'title':'Usar instalação '+provider.upper(),'items':[str(path)],
            'note':'A ferramenta será verificada pelo diagnóstico oficial; configurações das CLIs dos modelos não serão alteradas.'}):
            raise ReaError('Configuração não autorizada.')
        with locked(self.base/'preparacao.lock'):
            self.reload();previous=dict(self.config['environment']);self.config['environment'][keys[provider]]=str(path)
            try:
                if provider=='jdk':
                    code,out=await self.execute([str(path/'bin/javac'),'-version'])
                    match=re.search(r'javac (\d+)',out)
                    if not match or int(match[1])<21:raise ReaError('Selecione JDK 21 ou superior.')
                else:await self.cli_json(['doctor','--provider',provider,'--json'])
            except BaseException:
                self.config['environment']=previous;raise
            self.save()

    async def hopper(self):
        from capivara2ptn.rea_vendor_setup import HOPPER_SETUP
        def source_root():
            values=[Path(self.config['source'])] if self.config.get('source') else []
            values.extend(Path(arg).resolve().parent.parent for arg in self.command if Path(arg).is_file() and Path(arg).resolve().name=='rea.mjs')
            return next((p for p in values if (p/'dist/application/Setup.js').is_file() and (p/'dist/doctorRuntime.js').is_file()),None)
        root=source_root()
        if root is None:
            await self.ensure(force=True);root=source_root()
        if root is None:raise ReaError('Esta instalação não expõe o setup oficial necessário; nenhum instalador alternativo será utilizado.')
        with locked(self.base/'preparacao.lock'):
            self.reload()
            node=shutil.which('node',path=self.environment()['PATH'])
            async def invoke(mode):
                code,out=await self.execute([node,'--input-type=module','--eval',HOPPER_SETUP,str(root),mode],
                    allow_failure=True,json_output=True,label='Setup oficial REA · Hopper · '+mode)
                try:report=json.loads(out)
                except ValueError:raise ReaError('O setup oficial do Hopper não devolveu um plano válido.\n'+self.safe(out[-8000:] or self.config.get('last_error',''))) from None
                if code or report.get('status') not in {'planned','ready'}:raise ReaError(self.safe(str(report.get('remediation','O setup oficial requer ação humana.'))))
                return report
            report=await invoke('plan')
            actions=report.get('plannedActions',[])
            if any(action.get('kind')!='install_hopper' for action in actions):raise ReaError('Plano recusado: inclui alterações em clientes ou skills.')
            if report.get('status')=='planned':
                plan={'title':'Instalar Hopper pelo setup oficial REA','items':[a.get('label','Hopper')+' · '+a.get('target','') for a in actions],
                      'commands':[c for a in actions for c in a.get('commands',[])],
                      'note':'Ferramenta comercial: instalação não concede licença nem substitui ativação. O setup REA verifica a integridade; clientes e skills dos modelos estão bloqueados nesta chamada.'}
                if not await self.approve(plan):raise ReaError('Instalação Hopper não autorizada.')
                report=await invoke('apply')
            path=report.get('doctor',{}).get('hopperPath')
            if not path:raise ReaError('O setup oficial não confirmou um Hopper funcional/licenciado. '+str(report.get('remediation','')))
            self.config['environment']['HOPPER_LAUNCHER_PATH']=path;self.save()
            await self.cli_json(['doctor','--provider','hopper','--json'])
