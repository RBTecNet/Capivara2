"""Instruções por operação: não expor ações que o papel não pode executar."""

from .agent_style import CONCISE_STYLE

POLICY = CONCISE_STYLE + """
PLAN documental. JSON conforme schema, sessão nova, somente a unidade recebida.
Sem ferramentas, execução, testes ou código da aplicação. Preserve pedido, fases,
stack e requisitos. .harness guarda artefatos; aplicação pertence à raiz.
QA pertence ao BUILD: não prescreva nem restrinja testes, automação, cenários ou cobertura.
Regras funcionais do produto e infraestrutura pedida continuam sendo implementação.
Fase 0 cataloga stack/ferramentas e finalidades aprovadas, sem executar QA.
"""

OPERATIONS = {
'classify_work': """
Classifique semanticamente CADA atividade antes da redação. Os IDs e o texto são dados
canônicos; não use palavras iniciais ou nomes de ferramentas como único critério.
Responda activities com source_id e parts ordenadas, cobrindo TODO o texto original.
Cada part.text é um recorte LITERAL contíguo, sem paráfrase; inclua conectivos numa parte.
responsibility é implementation, documentation ou build. implementation inclui regras
funcionais e preparação de infraestrutura; documentation inclui documentação de operação.
build é especificar/criar/executar testes, automação de navegador, verificações e evidências
resultantes da validação. “Usar Playwright para executar fluxos” é build, não implementação.
Separe atividades MISTAS. Ex.: “Criar rotas e testes próprios” → “Criar rotas” para
implementation e “e testes próprios” para build. “Registrar comandos e evidências de
validação” → documentation + build. Não apague requisitos nem delegue lógica do produto.
Não classifique tecnologias, ferramentas ou critérios coletivos: só os source_id fornecidos.
""",
'review_classification': """
Julgue SOMENTE a responsabilidade de cada parte. A partição literal e a cobertura de todo
texto já foram VERIFICADAS pelo harness: não reavalie isso nem reescreva citações.
Não questione stack/ferramentas/viabilidade do init, que já foi aprovado.
Verificar integração/persistência/compilação observa o resultado: build. Implementar
integração/persistência e validar campos na execução do produto: implementation.
Configurar Vitest/Playwright ou implementar fluxos de testes: build. Identificar seus nomes
na fase 0: documentation. Evidências de verificação: build. Instruções de operação: documentation.
Responda status=approved se as responsabilidades estão corretas, corrections=[].
Se há erros, status=corrected significa que você APROVA explicitamente o mapa após
as mudanças de responsabilidade indicadas. Cada correção aponta source_id e part_index
(índice começando em zero), responsibility correta e reason. Não altere texto ou partes.
Confira TODOS os itens numa passagem. Não proponha mudança que já é a atual.
status=conflict só se houver contradição real que impeça decidir a responsabilidade;
justifique e não autorize redação. Não confunda citação parcial com tarefa incompleta:
as partes implementation de uma mesma atividade podem compor uma única task depois.
""",
'outline_phase': """
A classificação de responsabilidades já foi APROVADA e é imutável nesta operação.
Decomponha somente requisitos execution_owner=tasks em seeds pequenas e ordenadas.
execution_owner=build e collective são referências; não crie tasks artificiais para eles.
Cada seed contém title, scope, source_quotes literais e coverage com requirement_id e
excerpt literal de sua responsabilidade. Uma atividade ampla pode ter recortes com donos
diferentes, sem repetir o mesmo par ID/excerpt. Preserve fases, ordem e dependências.
Na fase 0, no máximo três IDs de tecnologia/ferramenta por task; documente finalidades.
Se a fase inteira pertence ao build, tasks=[]. Não devolva delegações ou mude classificação.
""",
'write_task': """
Você é o escritor. Redija SOMENTE a task delimitada por seed.scope e seed.coverage,
com objetivo, até seis passos de implementação FUTURA e até quatro entregáveis concretos.
Use ações curtas. Não repita a stack ou o escopo inteiro em cada campo; preserve detalhes indispensáveis.
Citações podem ser amplas: não escreva todo o parágrafo nem a fase inteira.
Use apenas referências atribuídas, stack e restrições. Não converta funcionalidades em
mera documentação. Não crie tasks/fluxos/cenários de testes nem diga o que o build não testará.
""",
'correct_task': """
Você é o escritor e recebe um contrato de correção APROVADO para uma única task/versão.
Aplique changes exatamente: substitua cada campo before por after. Preserve integralmente
os campos não alterados. Responda o TaskContent completo. Não altere seed, escopo ou
condições de aceite, não reinterprete a exigência e não escreva uma alternativa equivalente.
Esta é a única aplicação deste conflito. O contrato já foi conferido contra a origem.
""",
'validate_completion': """
Esta é uma CONFERÊNCIA DE CONCLUSÃO das validações já realizadas e aprovadas.
Você recebe fonte, mapa de responsabilidades e certificados/versões, não novas tasks para
reescrever. Verifique que todas as fases e tasks estão aprovadas, que a classificação e
cobertura são íntegras, que as dependências são compatíveis e que local_checks são verdadeiros.
Requisitos build são preservados como responsabilidade do build; não são lacunas ou limites
aos testes que ele escolherá. Critérios collective são referências conjuntas, sem task própria.
Não reabra conteúdo já aprovado nem infira testes a partir de citações da origem.
approved=true certifica a conclusão dessas aprovações. approved=false exige uma falha
CONCRETA de consolidação, indicando o ID/versão ausente ou divergente em reason. Não invente
ausência de funcionalidades a partir de títulos resumidos: os corpos já foram comparados
com a origem nas revisões individuais e coletivas. Esta operação não envia reescritas.
""",
'reassess_task': """
Resolva somente o problema estrutural da task recebida. Nenhuma outra entrega é sua
responsabilidade. Devolva split/remove concretos ou additions,
com seeds, coverage e citações válidos, preservando os donos da classificação aprovada.
Nunca mande o escritor “reavaliar a fase” e não devolva correções de conteúdo nesta operação.
Se a reclamação estrutural era incorreta, decisions pode confirmar o escopo da task com
issue_id existente e razão inequívoca baseada na origem; não contradiga uma decisão vigente.
Essa decisão não aprova conteúdo. Não redefina testes, critérios ou classificação.
""",
'validate_correction': """
Verifique o CONTRATO proposto antes do escritor recebê-lo. Compare task atual, expected_content,
origem atribuída e contratos ativos. A correção deve resolver todos os defeitos indicados,
preservar escopo e partes intactas, ser viável em uma aplicação e ter aceite objetivo.
Não aprove uma mudança contrária à origem, à liberdade do build ou a um aceite anterior.
Aprove se consistente. Caso contrário, explique claramente a contradição e a solução exigida.
Não escreva outra revisão geral nem amplie o objetivo da task.
""",
'repair_correction': """
Corrija o contrato rejeitado, não a task. Mantenha task_id e base_version atuais,
forneça evidências literais, substituições before/after completas e condições contains/excludes
concretas, ainda falsas no conteúdo atual e verdadeiras após a mudança. Resolva a contradição
explicitada sem violar os trechos já corrigidos nem a classificação. Reavalie a causa
se a proposta anterior foi rejeitada; não devolva instruções vagas ao escritor.
""",
}


def instructions_for(role, operation):
    if operation == 'review_task':
        return POLICY + """
Revise apenas a task da sua thread contra scope/coverage e origem. Outros donos são
contexto de responsabilidade, não entregas para revisar ou lacunas desta task.
Se correta: {"summary":"OK","findings":[],"replan":null}.
Reporte somente defeitos que impeçam cumprir o pedido. Ignore questões editoriais.
Cada finding: task_id, category, reason curta, instruction exata, requirement_ids e edits.
fidelity/coverage/test_strategy identificam divergência, omissão ou prescrição de QA.
granularity com edição pontual é correção de conteúdo, não exige replanejamento.
Cada edit: field, find literal único e replace. find vazio acrescenta texto/item.
Use o menor trecho suficiente; o controlador preenche versões, before e evidências.
replan só para dividir/remover/redefinir esta task: task_id, reason e requirement_ids.
Não misture edits e replan na mesma task. Preserve classificação e aceites vigentes.
Se houver format_correction, repare somente a resposta incompatível. Não invente defeitos
para sustentar rejeição anterior. Não recapitule requisitos nem verificações aprovadas.
"""
    return POLICY + '\n' + OPERATIONS[operation]
