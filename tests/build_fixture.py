"""Projeto aritmético controlado; G3 executa Python de verdade."""

import asyncio
import json
from pathlib import Path
import sys

from harness.domain import AgentSelection, WizardState
from harness.init_engine import InitEngine
from harness.plan_engine import PlanEngine
from harness.plan_artifacts import load_init
from init_fixture import review


def document():
    return {'title':'Biblioteca aritmética','objective':'Fornecer funções para dobrar e triplicar números.',
        'requirements':['dobrar(3) retorna 6.','triplicar(3) retorna 9.'],
        'constraints':['Usar somente Python e sua biblioteca padrão.'],
        'stack':[{'component':'Runtime','choice':'Python 3','reason':'Biblioteca simples.'}],
        'tools':[{'name':'python3','purpose':'Executar a biblioteca.','scope':'project'}],
        'phases':[{'number':1,'title':'Funções aritméticas','goal':'Implementar as funções independentes.',
            'tasks':['Implementar a função dobrar(n) em dobro.py.','Implementar a função triplicar(n) em triplo.py.'],
            'deliverables':['dobro.py','triplo.py'],'acceptance_criteria':['As duas funções retornam os valores pedidos.'], 'depends_on':[0]}],
        'acceptance_criteria':['As funções atendem aos valores pedidos.']}


class Documents:
    async def generate(self,selection,role,prompt,schema,store,call_id):
        data=json.loads(prompt.rsplit('\n\nREQUEST:\n',1)[1])
        request=data['current_request'];task=request['task'];context=request['data']
        if task in ('initial_review','final_review'):
            result=review(approved=True,stack=True)
            result['technology']['tools_quote']='python3'
            result['requirements']=document()['requirements']
            return json.dumps(result)
        if task=='write':return json.dumps(document())
        if task=='classify_work':return json.dumps({'activities':[{'source_id':a['source_id'],'parts':[
            {'text':a['text'],'responsibility':'implementation','reason':'Função do produto.'}]} for a in context['activities']]})
        if task=='review_classification':return json.dumps({'status':'approved','summary':'Responsabilidades corretas.','corrections':[]})
        if task=='outline_phase':
            requirements=[r for r in context['requirements'] if r['execution_owner']=='tasks']
            if context['phase_source']['number']==0:
                seeds=[{'title':'Documentar stack','scope':'Documentar Python e python3.', 'source_quotes':['Python'],
                        'coverage':[{'requirement_id':r['id'],'excerpt':r['text']} for r in requirements]}]
            else:
                seeds=[{'title':r['text'],'scope':r['text'],'source_quotes':[r['text']],
                        'coverage':[{'requirement_id':r['id'],'excerpt':r['text']}]} for r in requirements]
            return json.dumps({'tasks':seeds})
        if task=='write_task':return json.dumps({'title':context['seed']['title'],'objective':context['seed']['scope'],
             'implementation_steps':[context['seed']['scope']],'deliverables':[context['seed']['title']]})
        if task in ('review_task','review_phase'):
            result={'approved':True,'matches_source':True,'coverage_complete':True,'tasks_are_small':True,
                    'no_test_strategy':True,'summary':'Documento fiel.','corrections':[]}
            result.update({'replan':None} if task=='review_task' else {'structural_changes':[],'additions':[]})
            return json.dumps(result)
        if task=='validate_completion':return json.dumps({'approved':True,'reason':'Aprovações completas.'})
        raise AssertionError(task)


async def prepared(root):
    agent=AgentSelection('codex','test','low')
    state=WizardState(root,prompt='Usar Python 3 com python3 para implementar dobrar(n) em dobro.py e triplicar(n) em triplo.py.',writer=agent,reviewer=agent)
    async def no_interview(*args):raise AssertionError('Sem entrevista.')
    await InitEngine(state,Documents(),interview=no_interview).run()
    state.module='plan';state.prompt=load_init(root)['approved_prompt']
    await PlanEngine(state,Documents()).run()
    state.module='build';state.prompt_source='plan';state.prompt=(root/'.harness/plan/final.md').read_text()
    return state


class Builders:
    def __init__(self):
        self.calls=[];self.active=0;self.peak=0;self.ranges={};self.delay=.02
        self.g1_missing=False;self.g2_reject=False;self.g3_bad=False;self.outside=False
        self.dependencies=[];self.cancel=False;self.shared=False

    async def generate(self,selection,role,prompt,schema,store,call_id,*,cwd=None,allow_tools=False):
        request=json.loads(prompt.rsplit('\n\nREQUEST:\n',1)[1]);op=request['operation'];data=request['data']
        self.calls.append((role,op,data));self.active+=1;self.peak=max(self.peak,self.active)
        try:
            if self.cancel:raise asyncio.CancelledError()
            await asyncio.sleep(self.delay)
            if op=='environment':return json.dumps({'dependencies':self.dependencies})
            if op=='delivery_plan':
                return json.dumps({'summary':'Biblioteca Python sem dependências externas.',
                    'prerequisites':[{'name':'Python 3','purpose':'Executar a biblioteca.',
                        'check':{'argv':[sys.executable,'--version'],'purpose':'Conferir Python.','timeout':10,'env':[]},
                        'install':[],'manual_instructions':'Instale Python 3 pelo gerenciador do sistema.',
                        'manual_commands':[{'argv':['sudo','apt-get','install','-y','python3'],'purpose':'Instalar Python no Linux.','timeout':60,'env':[]}]}],
                    'install':[],'checks':[],
                    'startup':[{'command':{'argv':['python3','-c','import dobro,triplo; assert dobro.dobrar(3)==6; assert triplo.triplicar(3)==9'],
                        'purpose':'Importar e usar a biblioteca instalada.','timeout':10,'env':[]},
                        'mode':'exit','path':'/','status':200,'default_port':3000,'expected_text':None,'expected_stdout':None}],
                    'readme_changes':[]})

            if op=='diagnose':
                history=data.get('recent_failures') or []
                failure=(history[-1]['feedback'] if history else data.get('feedback')) or {}
                evidence=(failure.get('functional_failures') or [{}])[0].get('observed') or (failure.get('issues') or [{}])[0].get('observed') or (data.get('failed_contract') or {}).get('error') or failure.get('required_change') or failure.get('recovery_instruction') or 'A entrega não mudou'
                action='review_again' if (data.get('failed_contract') or {}).get('operation') in ('fidelity','verification') else 'repair_code'
                return json.dumps({'action':action,'root_cause':'A unidade ainda não atende à devolução.',
                    'instruction':'Corrigir a função conforme o resultado pedido, preservando os checks válidos.',
                    'requirement_quote':data['task']['content']['objective'],'required_files':[],
                    'failure_evidence':evidence})
            if op=='blueprint':
                tasks=[]
                for phase in data['plan']['phases']:
                    for task in phase['tasks']:
                        filename='docs/stack.md' if phase['number']==0 else ('dobro.py' if 'dobrar' in task['seed']['scope'] else 'triplo.py')
                        writes=['**'] if self.shared else [filename]
                        tasks.append({'task_id':task['id'],'kind':'documentation' if phase['number']==0 else 'code',
                                      'writes':writes,'reads':[],'depends_on':[]})
                return json.dumps({'tasks':tasks})
            task=data['task'];key=task['id'];scope=data['scope'];path=Path(cwd)
            filename='README.md' if key=='BUILD-README' else 'docs/stack.md' if task['phase_number']==0 else ('dobro.py' if 'dobrar' in task['seed']['scope'] else 'triplo.py')
            if op=='write':
                if self.g1_missing and filename=='dobro.py':
                    self.g1_missing=False
                else:
                    file=path/filename;file.parent.mkdir(parents=True,exist_ok=True)
                    if filename.endswith('.md'):
                        file.write_text('# Biblioteca aritmética\n\nInstalação: Python 3, sem pacotes externos.\n\nUso: `python3 -c "import dobro; print(dobro.dobrar(3))"`.\n')
                    else:
                        name,multiple=('dobrar',2) if filename=='dobro.py' else ('triplicar',3)
                        value=0 if self.g3_bad and filename=='dobro.py' else multiple
                        file.write_text(f'def {name}(n):\n    return n * {value}\n')
                        if self.g3_bad and filename=='dobro.py':self.g3_bad=False
                if self.outside and filename=='dobro.py':(path/'unexpected.py').write_text('x=1\n')
                return json.dumps({'summary':'Entregue.','files':[filename]})
            if op=='fidelity':
                if self.g2_reject and filename=='dobro.py':
                    self.g2_reject=False
                    return json.dumps({'approved':False,'summary':'Ajuste exigido.', 'issues':[{
                        'file':filename,'observed':'Contrato ainda precisa ser aplicado.','required_change':'Preservar a função e registrar sua intenção.',
                        'acceptance':'Função corresponde ao pedido.','requirement_quote':task['content']['objective'],
                        'evidence_quote':'def dobrar(n):','missing':False}]})
                return json.dumps({'approved':True,'summary':'Código corresponde à task.','issues':[]})
            if op=='verification':
                checks=[]
                if scope['kind']!='documentation':
                    module='dobro' if filename=='dobro.py' else 'triplo';function='dobrar' if module=='dobro' else 'triplicar';value=6 if module=='dobro' else 9
                    # Pequeno script isolado congelado, executado pelo harness.
                    script=path/'.harness/checks/check.py';script.parent.mkdir(parents=True,exist_ok=True)
                    script.write_text(f'import runpy\nmodule=runpy.run_path({filename!r})\nassert module[{function!r}](3)=={value}\n')
                    checks=[{'command':{'argv':[sys.executable,'.harness/checks/check.py'],'purpose':'Verificar uma função.','timeout':10,'env':[]},
                             'target_files':[filename],'requirement_quote':task['content']['objective'],
                             'assertion_file':'.harness/checks/check.py','expected_stdout':None}]
                return json.dumps({'summary':'Verificação funcional mínima.','checks':checks,'dependencies':[]})
            raise AssertionError(op)
        finally:self.active-=1
