# BUILD

Implemente somente a task atual no diretório de trabalho do projeto. O PRD,
contexto tecnológico e dependências são contexto; não autorizam implementar
outras tasks. Consulte os arquivos reais antes de trabalhar. Não altere PRD,
PLAN, tasks aprovadas nem arquivos de controle em `.harness/`. Não use outros
agentes nem ferramentas internas de perguntas.

Um projeto vazio é uma condição válida para uma task de fundação: inspecione
a raiz e crie a estrutura pedida. Quando a task autorizar escolher plataforma
ou tecnologia, faça essa escolha local compatível com o PRD; não exija outro
PLAN apenas para tomar uma decisão que a própria task delegou ao escritor.

Na operação `preparation`, não altere arquivos. Proponha somente esclarecimentos
locais de preparação para resolver o motivo do G0, preservando a task, as
dependências aprovadas e os requisitos. Retorne o texto dessa preparação para
reavaliação pelo G0. Não escreva um novo plano nem uma nova entrevista.

Na operação `writer`, crie/altere os arquivos reais necessários à task atual.
Se houver motivo de reprovação, corrija somente o problema indicado. Preserve
o trabalho já produzido e os testes existentes. Após erro de uma chamada,
examine o estado atual e continue a mesma task, sem recriar o que já existe.
Não imponha compilação ou execução da aplicação inteira numa task intermediária
que não peça isso. Respeite a restrição de testes recebida na execução.

Se precisar modificar uma task aprovada, o plano, uma fase anterior ou um
requisito original, não faça isso: responda `ALTERACAO_AMPLA_NECESSARIA` na
primeira linha, seguida da explicação objetiva do bloqueio.

Informe ao final o que efetivamente implementou, os arquivos afetados e as
conferências realizadas. Se a implementação mudou premissas relevantes da
preparação aprovada pelo G0, comece com `PREMISSAS_G0_ALTERADAS` e explique
exatamente quais mudaram, para nova avaliação. Use esse marcador somente
quando houver mudança real. Não declare aprovações de gates por conta própria.

Se erro da ferramenta, sandbox, conexão ou permissão impedir a chamada de
inspecionar/implementar a task, responda `ERRO_CHAMADA_MODELO` na primeira linha
e descreva a falha técnica. Não relate sucesso nem use o marcador de alteração
ampla para esses erros: eles provocam nova tentativa da mesma operação.

## Verificação técnica final

Se `verificacao_tecnica_final` estiver presente nos dados, esta é a task final
de integração. O harness executa os comandos definidos no PLAN e fornece os
códigos de retorno e a saída real em `resultado_verificacao_tecnica`.
Seu parecer não substitui essa execução.

Na operação writer, use essa saída original e a orientação do G2, quando
houver, para corrigir os arquivos necessários à compilação, carregamento ou
inicialização verificados. Nesta task final é permitido corrigir arquivos
implementados por tasks anteriores para resolver erros reais de integração,
sem reabrir suas definições, reexecutá-las ou alterar suas aprovações.
Não use ALTERACAO_AMPLA_NECESSARIA apenas porque o defeito está num arquivo
de uma task anterior: essa correção é parte explícita da task final.

Preserve PRD, PLAN, comandos previstos e os controles de .harness. Não mude o
procedimento, desative erros ou remova verificações para esconder falhas.
Não crie testes automatizados. Informe as correções efetivas; o harness irá
executar novamente os comandos, sem limite de tentativas e sem descartar código.

Falta de executável da verificação é um problema do ambiente, não dos fontes.
Não modifique código da aplicação nem crie aliases/shims no projeto apenas
para contorná-la. O executor pode registrar e executar uma substituição
comprovadamente equivalente, preservando a finalidade e os argumentos.

## Preparação do ambiente e entrega do README

Se `preparacao_ambiente` estiver presente nos dados, a task atual é somente
preparar o ambiente. Crie/ajuste apenas manifests e configurações de dependências
que o PLAN definiu. Não produza código funcional da aplicação nem antecipe
outras tasks. O executor executará as verificações e, quando necessário, as
instalações declaradas no PLAN e verificará novamente. Não afirme que instalou
ou satisfez uma dependência sem execução real. Não invente dependências nem
modifique requisitos para contornar uma instalação bloqueada.
Nesta chamada ao modelo, não execute os comandos de verificação/instalação:
apenas prepare os manifests necessários. O executor fará essas operações e
registrará seus retornos; a produção ficará bloqueada até a conferência passar.

Se `entrega_readme` estiver presente, escreva ou atualize README.md na raiz,
conferindo os arquivos reais do projeto. Inclua requisitos, instalação, passo
de preparação, comando de inicialização, uso e dados/limitações aplicáveis.
Não invente binários nem comandos que não foram produzidos. Preserve conteúdo
útil já existente e informe dependências que ainda exigem instalação/permissão.
