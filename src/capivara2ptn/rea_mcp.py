"""Transporte stdio MCP do REA; não executa nenhum analisador internamente."""

import asyncio
import json

from capivara2ptn.mcp import McpError, McpClient
from capivara2ptn.rea_common import ReaError
from capivara2ptn.technical_verification import _stop


class ReaMcp:
    def __init__(self,argv,cwd,env,log,safe):
        self.argv,self.cwd,self.env=argv,cwd,env
        self.log,self.safe=log,safe
        self.process=None;self.sequence=0;self.stderr_task=None
        self.tools={};self.identity={};self.current=None;self.stderr=[]

    async def start(self):
        self.process=await asyncio.create_subprocess_exec(*map(str,self.argv),cwd=self.cwd,env=self.env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            start_new_session=True,limit=128*1024*1024)
        self.stderr_task=asyncio.create_task(self.read_stderr())
        result=await asyncio.wait_for(self.request('initialize',{'protocolVersion':'2025-11-25','capabilities':{},
            'clientInfo':{'name':'capivara2ptn','version':'0.1.0'}}),60)
        self.identity=result.get('serverInfo',{})
        if self.identity.get('name')!='rea':raise ReaError('O processo não se identificou como o servidor MCP REA.')
        if result.get('protocolVersion') not in {'2025-03-26','2025-06-18','2025-11-25'}:
            raise ReaError('Versão MCP do REA incompatível.')
        await self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        cursor=None;seen=set()
        while True:
            result=await self.request('tools/list',{'cursor':cursor} if cursor else {})
            for tool in result.get('tools',[]):
                if not isinstance(tool,dict) or not isinstance(tool.get('name'),str):raise ReaError('Catálogo MCP inválido.')
                self.tools[tool['name']]=tool
            cursor=result.get('nextCursor')
            if cursor is None:break
            if not isinstance(cursor,str) or cursor in seen:raise ReaError('Paginação MCP inválida.')
            seen.add(cursor)
        required={'binary_session','get_evidence_bundle','export_evidence_bundle','import_evidence_bundle'}
        if not required<=self.tools.keys():raise ReaError('REA incompatível; ferramentas de persistência ausentes: '+', '.join(sorted(required-self.tools.keys())))
        return self.tools

    async def send(self,value):
        self.process.stdin.write((json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n').encode());await self.process.stdin.drain()

    async def read_stderr(self):
        async for raw in self.process.stderr:
            text=self.safe(raw.decode('utf-8',errors='replace')).rstrip()
            self.stderr.append(text);self.stderr=self.stderr[-20:]
            if text:self.log('REA · '+text)

    async def request(self,method,params):
        self.sequence+=1;identifier=self.sequence;self.current=identifier
        await self.send({'jsonrpc':'2.0','id':identifier,'method':method,'params':params})
        try:
            while raw:=await self.process.stdout.readline():
                try:message=json.loads(raw)
                except ValueError:raise ReaError('O servidor REA enviou JSON inválido no stdout.') from None
                if not isinstance(message,dict):raise ReaError('O servidor REA enviou uma mensagem MCP inválida.')
                if message.get('method')=='notifications/progress':
                    self.log(self.safe(str(message.get('params',{}).get('message','REA em execução.'))));continue
                if message.get('method')=='notifications/message':
                    self.log(self.safe(str(message.get('params',{}).get('data',''))));continue
                if 'method' in message and 'id' in message:
                    if message['method']=='ping':await self.send({'jsonrpc':'2.0','id':message['id'],'result':{}})
                    else:await self.send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'Client capability not supported'}})
                    continue
                if message.get('id')!=identifier:continue
                if 'error' in message:
                    error=message['error'];raise McpError(self.safe(str(error.get('message','Falha MCP REA.'))),error.get('code',0))
                if not isinstance(message.get('result'),dict):raise ReaError('Resposta MCP REA sem resultado válido.')
                return message['result']
            raise ReaError('O servidor REA encerrou a conexão. '+self.safe('\n'.join(self.stderr[-5:])))
        except asyncio.CancelledError:
            try:await self.send({'jsonrpc':'2.0','method':'notifications/cancelled','params':{'requestId':identifier,'reason':'Interrupção solicitada pelo usuário'}})
            except (OSError,RuntimeError):pass
            raise
        finally:self.current=None

    async def call(self,name,arguments):
        if name not in self.tools:raise ReaError('Ferramenta não anunciada pelo REA: '+name)
        return await self.request('tools/call',{'name':name,'arguments':arguments,'_meta':{'progressToken':str(self.sequence+1)}})

    async def close(self):
        if self.process:
            try:
                if self.process.returncode is None:
                    try:
                        if 'close_binary' in self.tools:
                            result=await asyncio.wait_for(self.call('close_binary',{}),10)
                            if result.get('isError'):self.log(self.safe('REA reportou pendência de encerramento: '+str(normalized(result))))
                    except Exception as error:self.log(self.safe('Encerramento REA: '+str(error)))
                    try:self.process.stdin.close()
                    except OSError:pass
                    try:await asyncio.wait_for(self.process.wait(),3)
                    except asyncio.TimeoutError:pass
            finally:
                if self.process.returncode is None:await _stop(self.process)
                if self.stderr_task:
                    self.stderr_task.cancel();await asyncio.gather(self.stderr_task,return_exceptions=True)
                self.process=None


def normalized(result):
    structured=result.get('structuredContent')
    if isinstance(structured,dict):
        return structured.get('normalized_result',structured.get('result',structured))
    text=McpClient._text(result.get('content',[]))
    try:return json.loads(text)
    except ValueError:return {'text':text}
