"""Instruções por operação: não expor ações que o papel não pode executar."""

POLICY = """
Você participa do PLAN documental do CAPIVARA. Responda apenas JSON no schema.
Esta é uma sessão nova; use somente os dados explícitos. Não execute ferramentas,
comandos ou testes, não inspecione ambiente e não gere código da aplicação.
O plan organiza fases e pequenas tasks de implementação futura, preservando o init.
O BUILD decide livremente O QUE testar, COMO testar e QUAIS testes criar/executar,
inclusive testes não mencionados na origem. O plan NÃO prescreve nem restringe testes,
roteiros, cenários ou cobertura. “Preparar/implementar fluxos Playwright” também é
especificar testes e pertence ao build. Não resolva esse limite apenas trocando verbos.
Preserve exigências de qualidade como referências de origem; não são uma lista
exaustiva ou um limite aos testes que o build poderá escolher.
Validação funcional de campos, regras de negócio e infraestrutura necessária ao produto
continuam sendo implementação. Fase 0 apenas identifica stack, dependências e finalidades
já aprovadas; mencionar que uma ferramenta serve para testes não manda executá-los.
Não invente funcionalidade, stack ou versões. .harness guarda artefatos; a aplicação é na raiz.
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
'review_plan': """
Compare o plano completo à finalidade original, requisitos, fases e integração entre elas.
Use as aprovações por versão: procure omissões ou contradições de conjunto, não reescreva
tasks intactas por estilo nem reabra contratos já cumpridos. Referências de QA pertencem
ao build e não delimitam os testes que ele decidirá. A aprovação nunca é por tentativas.
Se necessário, forneça contratos concretos de conteúdo ou operações estruturais válidas
como na revisão coletiva. Não exponha ações individuais de reavaliação ou delegações.
""",
'reassess_phase': """
Resolva o problema estrutural apontado. Devolva split/merge/remove concretos ou additions,
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
explicitada sem violar contratos anteriores nem classificação. Esta é a única revisão
semântica deste contrato; não o devolva vago para o escritor tentar adivinhar.
""",
}

COLLECTIVE_CONTRACT = """
Cada correção de conteúdo deve ter task_id, base_version de approved_versions, category
fidelity/coverage/test_strategy, reason/instruction, evidence literal dos requisitos atribuídos,
changes por campo com before/after exatos e acceptance contains/excludes por campo alterado.
O aceite deve estar falso antes e verdadeiro depois. Agrupe todos os defeitos da mesma task
num contrato único. Não altere seeds em correções de conteúdo; para isso use operações estruturais.
A evidência deve citar ao menos um requisito ATRIBUÍDO à task. Requisitos Rxxx servem
para detalhes globais (campos, valores permitidos) dentro desse escopo. Nunca cite como
trabalho da task um requisito build, coletivo ou atribuído a outra task.
"""


def instructions_for(role, operation):
    if operation in ('review_task', 'review_phase'):
        scope = ('Revise só a task e seus requisitos atribuídos.' if operation == 'review_task' else
                 'Revise a cobertura coletiva desta fase; preserve as tasks/versões já aprovadas.')
        return POLICY + '\n' + scope + """
Responda summary e findings. Sem problemas, findings=[]. Não calcule approved nem outros
booleanos redundantes: o harness deriva o resultado dos problemas funcionais encontrados.
Cada finding contém task_id, category, reason, instruction, requirement_ids e edits.
category=editorial para grafia, redação, estilo ou apresentação sem mudar significado:
registre como observação, sem bloquear. Não classifique perda de requisito como editorial.
fidelity/coverage/test_strategy são problemas objetivos que impedem cumprir a origem.
edits contém field, find (trecho atual exato) e replace (substituição). find vazio acrescenta
um item à lista ou texto ao campo. Indique um trecho único, não copie o campo inteiro.
O harness fornece base_version, before, evidências literais e condições de aceite.
Agrupe todos os defeitos objetivos numa passagem. Não cobre funcionalidades de outros donos.
Alterar a estrutura usa replan (task) ou structural_changes/additions (fase), conforme schema;
não tente fazê-lo como edição de conteúdo. Respeite a classificação aprovada.
Se format_correction existir, corrija SOMENTE a resposta incompatível. Se a resposta anterior
reprovou com todos os critérios atendidos, esclareça quais problemas são funcionais e quais
são editoriais usando findings. Não envie uma reprovação contraditória nem invente defeitos
para sustentar o veredito anterior. A fonte e as aprovações anteriores continuam válidas.
"""
    instruction = OPERATIONS[operation]
    if operation in ('review_phase', 'review_plan'):
        instruction += COLLECTIVE_CONTRACT
    return POLICY + '\n' + instruction
