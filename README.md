# Capivara2 · wizard de inicialização

Harness com wizard em tela cheia, feito em Python com
[Textual](https://textual.textualize.io/). O `init` recebe o pedido, entrevista o
usuário e produz um prompt profissional por fases, aprovado por um revisor.
O `plan` decompõe esse documento em tasks pequenas e revisadas, sem gerar código.

## Executar

O comando local pode ser aberto em qualquer pasta:

```bash
capivara2
```

Para instalar ou atualizar a versão local, com Python 3.11 ou superior:

```bash
./scripts/install-local.sh
```

O instalador usa um ambiente virtual no projeto e disponibiliza o comando em
`~/.local/bin/capivara2`. Essa pasta deve estar no `PATH`. A instalação é editável,
portanto usa os arquivos desta pasta do projeto. Também é possível executar
`.venv/bin/capivara2` ou `.venv/bin/python -m harness` diretamente no projeto.

O caminho inicial é sempre a pasta de trabalho do terminal ao abrir o programa.
Para usar outro projeto, execute `capivara2` a partir daquela pasta, ou edite o
caminho na primeira tela.

Após cada rodada com implementação ou correção, a instalação local é atualizada
e um commit local é criado, conforme [AGENTS.md](AGENTS.md). O envio para o remoto
exige autorização do usuário.

## Fluxo

1. Conferir ou editar a pasta do projeto; Enter confirma.
2. Escolher `init` ou `plan`. O plan exige init aprovado e íntegro e começa
   selecionado após sua conclusão. Depois do plan aprovado, `build` é destacado,
   e pode ser executado usando os artefatos aprovados.
3. No init, escolher entre prompt manual, arquivo e base MCP. No plan, essa
   etapa é pulada: a origem é sempre `.harness/prompts/final.md`.
4. Digitar o prompt com múltiplas linhas, informar um arquivo UTF-8 ou selecionar
   a instância MCP e o projeto remoto.
5. Selecionar IA escritora, modelo e effort.
6. Reutilizar a configuração do escritor como revisor ou selecionar outra IA,
   modelo e effort.
7. Conferir o resumo e responder “Executar agora?”.
8. Responder às entrevistas e acompanhar o ciclo de revisão até a aprovação.

No prompt manual, **Enter insere uma linha** e **Ctrl+D confirma**. Menus usam
as setas e Enter; Tab alterna entre os controles, Esc volta e Ctrl+Q sai.
Os controles também aceitam mouse. O rascunho do prompt permanece ao voltar.

Arquivos de prompt podem usar caminhos absolutos, `~` ou caminhos relativos à
pasta do projeto selecionada. O conteúdo é lido e validado antes de avançar.

“Sim” inicia o módulo documental selecionado. “Não” encerra o wizard sem executá-lo.

## Init documental

O revisor analisa o prompt original e toda a documentação textual recebida,
preserva seus requisitos e aponta lacunas. O escritor transforma essas lacunas
em entrevistas e depois redige o prompt completo. O contexto de cada papel é
salvo separadamente e recuperado a cada chamada, inclusive quando escritor e
revisor usam o mesmo modelo.

Cada pergunta tem opções concretas, uma recomendação justificada pela finalidade
do pedido e exatamente uma opção **Outra opção — descrever livremente**. A escolha
recomendada começa destacada, mas exige confirmação. No texto livre, Enter cria
uma linha e Ctrl+D confirma. Esc retorna às opções; Esc nas opções interrompe o init.

Stack e ferramentas do projeto são obrigatoriamente levantadas quando não estão
explícitas no material original. Ferramentas exclusivamente internas do harness
são inferidas pelos agentes. Isso inclui suítes, frameworks, runners de testes e
automação de navegador, mesmo quando o pedido exige testes automatizados e fluxos
reais no navegador. Essas exigências são preservadas como critérios de qualidade;
escolher suas ferramentas não vira uma pergunta para o usuário.

Antes de exibir a entrevista, o harness devolve perguntas de escolha de ferramentas
de QA ao escritor para convertê-las em decisões internas. As inferências ficam em
`.harness/control/inferred-decisions.json` e são entregues ao escritor e ao revisor
para constar na fase 0, separadas das respostas do usuário. Decisões funcionais do
produto continuam sendo entrevistadas normalmente.

Uma citação de stack/ferramentas que não corresponde ao texto é devolvida ao revisor
para corrigir a evidência, sem reabrir automaticamente uma entrevista. Se faltar
clareza na fase 0, o escritor corrige a documentação usando as decisões existentes.
Respostas mais recentes que alteram a mesma decisão prevalecem sobre as antigas,
mantendo o histórico para auditoria.

O init não verifica instalação de ferramentas, não
inspeciona o ambiente, não executa testes da aplicação e não gera seu código.
Os CLIs são chamados para produzir respostas documentais, com ferramentas
desabilitadas ou negadas pelos adaptadores.

O escritor devolve requisitos, decisões, stack, ferramentas e fases com atividades,
entregáveis, dependências e critérios de aceite. O harness gera a **fase 0 — Stack
e ferramentas**, seguida por fases consecutivas de implementação. O revisor
confere fidelidade ao pedido original e às respostas, ausência de lacunas, fases
coerentes e viabilidade documental. Novas lacunas geram outra entrevista; desvios
e impossibilidades explicitamente justificados retornam para correção automática.
Esse ciclo não tem limite fixo de rodadas nem aprovação por esgotamento de tentativas.

Somente uma aprovação explícita e consistente do revisor cria `prompts/final.md`
e marca o init como concluído. Contratos JSON inválidos são devolvidos ao agente
com o motivo para correção, sem limite fixo. Uma falha do CLI deixa o init incompleto,
com logs para diagnóstico. A tela permite tentar novamente. Esc interrompe o
processo; Ctrl+Q encerra a TUI e cancela a execução em andamento.

Todos os artefatos ficam na pasta `.harness` do projeto selecionado:

```text
.harness/
  status.json                    # status de init, plan e build
  owner.json                     # identificação da pasta gerenciada
  prompts/initial.md             # pedido original, preservado
  prompts/round-0001.md           # candidatos de cada rodada
  prompts/final.md               # somente após aprovação
  prompts/final.json             # representação estruturada para o plan
  enterviews/round-0001.json      # perguntas, recomendações e respostas
  context/                       # documentos recebidos e anexos preservados
  control/reviewer-context.json  # contexto recuperável do revisor
  control/writer-context.json    # contexto do escritor
  control/approval.json          # revisão e hash do prompt aprovado
  log/                           # progresso, entradas, saídas e erros dos CLIs
```

Reexecutar o init substitui os artefatos anteriores dessa pasta gerenciada e
reinicia os status de plan/build. Os arquivos da aplicação na raiz são preservados.
Uma pasta `.harness` não reconhecida é preservada e gera uma mensagem, em vez de
ser apagada. Ao reabrir um projeto cujo prompt final aprovado continua íntegro,
`plan` é destacado como próximo módulo. O build usa o plano aprovado para preparar o ambiente e gerar a aplicação.

## Plan documental

O plan lê o prompt final e sua representação estruturada, confere o hash e a
aprovação do init e preserva a ordem, as dependências e a identidade de suas fases.
A **fase 0 — Stack de implantação e dependências de desenvolvimento e testes**
documenta as escolhas já aprovadas. As demais fases são divididas em tasks pequenas,
com IDs estáveis, objetivo delimitado, passos de implementação, entregáveis e
referências literais da fase e do contexto global aprovado. Citações de requisitos
globais, stack e ferramentas não ampliam a responsabilidade da task; seus IDs de
cobertura continuam limitados ao recorte atribuído na fase.

Antes da redação, o revisor classifica semanticamente as atividades do init em
implementação, documentação e responsabilidades do build. Itens mistos são divididos em
recortes literais que cobrem todo o texto original. O harness verifica a partição; o
revisor decide somente a responsabilidade de cada parte, sem reescrever a origem.
O mapa resultante recebe aprovação explícita e fica estável durante o plan. Não há
classificador por início de frase: “Usar Playwright para executar fluxos” é avaliado pelo
seu significado. Critérios e entregáveis coletivos permanecem referências de conjunto.

O escritor recebe uma task, stack, restrições e requisitos globais do produto, sem a fase
inteira ou histórico conversacional. O revisor verifica apenas o recorte atribuído, usando
os detalhes globais quando necessários (por exemplo, os valores de um status).
Cada thread é responsável apenas por sua task. Quando todas as threads estão aprovadas,
o controlador conclui a fase. Nenhum agente recebe a fase, nem um agrupamento de
resumos ou entregas das outras threads.

Uma devolução de conteúdo exige um **contrato de correção**: task e versão de origem,
evidência literal, problema concreto, substituições exatas por campo e condições de aceite
`contains`/`excludes`. O harness confere que o defeito existe antes e que a mudança satisfaz
o aceite. Outro exame documental valida a coerência desse contrato antes de enviá-lo ao
escritor. Uma proposta rejeitada volta ao revisor dessa mesma thread para correção
automática, preservando as outras aprovações.

O escritor aplica cada contrato uma única vez e preserva os campos restantes. Se sua
resposta desviar das substituições autorizadas, o harness aplica exatamente o patch
aprovado e registra a recuperação; não pede outra tentativa. A task corrigida ainda
precisa de aprovação explícita do revisor. Os aceites já estabelecidos permanecem ativos:
uma nova devolução não pode exigir o contrário ou reabrir o mesmo conflito, mesmo com
outra redação. Uma incompatibilidade retorna ao revisor dessa thread, sem reabrir
entregas das outras threads.

Remoção, divisão e consolidação são operações estruturadas, aplicadas em lote após validar
cobertura e conflitos. Elas não são instruções vagas para o escritor. A revisão individual
não possui esses campos; pode solicitar uma reavaliação estrutural específica. IDs removidos
não são reutilizados e as versões retiradas ficam no histórico. Uma decisão vigente não é
silenciosamente substituída. Repetir o mesmo problema estrutural sem mudar o mapa é falha
de contrato, sem aprovação automática.

Cada aprovação individual recebe um certificado ligado à versão exata da task.
O controlador consolida esses certificados e marca a fase como concluída quando todas
as suas threads estão aprovadas. A conclusão do plan também é local, sem chamada global
a um LLM. O build só é habilitado após todas as aprovações e certificados íntegros.
A retomada recupera o checkpoint e essas aprovações, sem reproduzir revisões coletivas antigas.

Erros de JSON/schema e respostas inconsistentes são recuperados automaticamente na mesma
operação, com espera cancelável, sem limite fixo de tentativas e sem refazer tasks aprovadas.
O diagnóstico de recuperação fica em `control/response-recovery.json`; contradições reais
da documentação continuam exigindo correção, nunca aprovação por insistência.
O dashboard contabiliza correções documentais e erros de contrato separadamente.

Os agentes usam modo conciso por padrão, em todos os provedores: JSON compacto,
aprovação com `summary: "OK"` e rejeição limitada ao defeito funcional e à correção
necessária. Questões editoriais são ignoradas. Os prompts removem anotações automáticas
de schema e metadados duplicados; a task, os requisitos e as evidências permanecem
integrais. Essa orientação não cria limites de tamanho nem novas rejeições por
verbosidade. Código, comandos e documentação conservam os detalhes necessários.

Todos os provedores usam uma camada comum de decodificação. JSON simples, blocos Markdown,
texto ao redor do JSON e envelopes nativos convergem para o mesmo contrato interno;
respostas ambíguas voltam ao agente. Saídas originais e arquivos `.normalized.json` ficam
nos logs. Não é necessário outro modelo como tradutor.

O revisor do plan informa `findings` com categoria, IDs dos requisitos e edições
`find/replace`. Versões, valores anteriores, citações e condições de aceite são preenchidos
pelo controlador. Grafia e preferências de apresentação sem mudança de sentido são
observações não bloqueantes em `control/observations`; perdas de funcionalidade, cobertura
ou fidelidade continuam exigindo correção e nova revisão. A resposta não precisa repetir
booleanos de aprovação que podem contradizer os próprios problemas descritos.

O encaminhamento depende da ação proposta em qualquer fase/task. Edições que ajustam
responsabilidades dentro do escopo aprovado são correções de conteúdo, inclusive quando
classificadas como `granularity`. Divisão, consolidação e alteração de seeds seguem o
fluxo estrutural. Findings sem edições podem explicar um `replan` ou uma operação
estrutural já fornecida; não são tratados como patches ausentes. O replanejamento
informa IDs de requisitos e recebe suas citações canônicas do controlador.

Correções independentes podem afetar o mesmo campo e requisito. Os aceites registram
os trechos corrigidos, sem congelar o campo inteiro. Uma proposta repetida ou contraditória
volta ao revisor para diagnóstico; não encerra o plan nem reaplica a mesma correção.
Propostas de correção rejeitadas também são refeitas antes de chegar ao escritor.

Uma alteração estrutural atualiza referências a uma task retirada quando a cobertura
aponta um único sucessor. Os contratos originais ficam no histórico e os documentos
afetados voltam à revisão. Havendo vários sucessores, o harness preserva a ambiguidade
para o revisor resolver, sem manter um literal de ID retirado como regra imutável.

Cada chamada recebe apenas o recorte necessário: finalidade, restrições, requisitos e
conteúdo da própria task. O pedido integral e as versões aprovadas permanecem em disco,
sem reenviar textos completos de outras fases ou cópias redundantes dos prompts.

O plan não entrevista o usuário, gera código, verifica dependências instaladas ou
executa testes. Suas verificações se limitam à correspondência entre os documentos.
Também não cria cenários, comandos, estratégias ou roteiros de testes para o build;
o build definirá suas validações. Identificar as dependências de desenvolvimento e
testes já aprovadas na fase 0 não constitui um plano de testes.

O plan **não determina o que o build deve ou não testar**. O build escolhe livremente os
testes, inclusive os não citados na origem. Menções a QA no init são preservadas no mapa
como referências de responsabilidade, sem virar tasks de testes, cenários ou limites.
Documentar nomes de dependências já aprovadas na fase 0 é permitido. Regras funcionais
de campos e infraestrutura de produção continuam nas tasks de implementação.

```text
.harness/plan/
  source.json                    # cópia da origem aprovada e seu hash
  final.md                       # plano somente após aprovação completa
  final.json                     # fases e tasks estruturadas para o futuro build
  phases/00/outline.json          # recortes da fase 0
  phases/00/tasks/P0-T001.json    # documentação de uma única task
  phases/00/approved.json         # fase consolidada pelo controlador
  context/call-0001.json         # entrada isolada, sem histórico de outras chamadas
  control/settings.json          # escritor, revisor e política de contexto
  control/checkpoint.json        # progresso e versões aprovadas
  control/requirements.json      # requisitos e partes literais com responsáveis
  control/classification.json    # classificação semântica aprovada
  control/classification-approval.json # certificado do mapa e hash da origem
  control/corrections.json       # contratos, aceites e versões corrigidas
  control/completion-certificate.json # conferência explícita das aprovações
  control/contract-failure.json  # diagnóstico, somente se houver falha de contrato
  control/coverage.json          # mapa de responsabilidades por recorte
  control/issues.json            # pendências e decisões de escopo vigentes
  control/repairs/                # lotes de correções e operações estruturais
  control/retired/                # histórico de tasks removidas ou substituídas
  control/reviews/               # pareceres de cada comparação documental
  control/approval.json          # aprovação global e hash do plano
  log/                           # entradas, saídas, erros e eventos deste módulo
```

O status do plan fica no arquivo compartilhado `.harness/status.json`. Reexecutá-lo
substitui somente `.harness/plan` e reinicia o status do build, preservando o init
e os arquivos da aplicação. Init e plan não podem executar ao mesmo tempo no mesmo
projeto. Uma interrupção ou falha mantém os registros e não libera o build.

Só depois de aprovar todas as tasks, todas as fases e o plano completo, o plan é
marcado como concluído. Ao reabrir a TUI, o build fica habilitado e selecionado se
a origem e o plano aprovado continuarem íntegros. O plan continua disponível para reexecução.

## Build

O build exige init e plan aprovados e íntegros. A origem é sempre `.harness/plan`;
a aplicação produzida fica na **raiz do projeto selecionado**. O wizard seleciona
escritor/modelo/effort, modo **serial** ou **paralelo**, máximo de agentes (1 a 32,
quando paralelo) e revisor compartilhado ou independente. Não há escolha de prompt.

Primeiro, o builder levanta runtime, pacotes de desenvolvimento e ferramentas de
verificação necessárias à stack. Executa check, tenta a instalação sem interação e
repete o check. Comandos recebem stdin fechado e configurações não interativas;
`sudo` automático usa `-n`. Uma dependência continua pendente se o check real falhar.

Um bloqueio interrompe o build antes da geração de código: o painel informa os pacotes,
`.harness/build/manual-install.md` contém instruções e erros concretos, e
`install-manual.sh` é criado quando existem comandos utilizáveis. O script manual
pode pedir a autenticação do próprio usuário. Quando não há comandos automáticos,
a orientação é específica e não há script vazio fingindo instalar. Após resolver,
execute build novamente. Dependências adicionais de um check G3 são instaladas numa
área de ferramentas interna, sem alterar o manifesto da aplicação durante o teste.
CLIs ausentes precisam ser instalados/autenticados ou configurados no catálogo.

O builder solicita um escopo por chamada, enviando apenas uma task do plan. O controlador
reúne os escopos e agenda as threads; nenhum modelo recebe o plano completo para isso.
Cada worker usa uma cópia
isolada da aplicação e das dependências locais. Reservas incluem escritas e leituras;
uma escrita que conflita com leitura/escrita de outra task impede paralelismo.
Escopos desconhecidos (`**`) são serializados. Fases respeitam suas dependências e
a fase 0. O máximo configurado é um teto; conflitos podem reduzir o número efetivo.

Os validadores G2/G3 de cada worker recebem uma visão temporária com sua entrega,
a base correspondente e dependências declaradas ou importadas. Fontes não relacionadas
não são copiadas para essa visão. Os scripts funcionais retornam ao workspace da task
para execução pelo harness; outras threads mantêm suas próprias devoluções e aprovações.

Antes da integração, o harness confere os arquivos realmente alterados contra a reserva
e compara a base com a raiz atual. Mudanças fora da reserva ou alterações externas
não são sobrescritas. As integrações são serializadas e têm restauração em caso de erro.
Cancelamento aguarda a integração em andamento terminar seu checkpoint.

Cada task passa pelos gates:

| Gate | Responsabilidade | Evidência |
|---|---|---|
| G0 | Escritor produz a unidade | Arquivos reais na cópia isolada |
| G1 | Auditor confere existência | Arquivos declarados existem; código não é substituído por uma descrição |
| G2 | Validador compara à task | Rejeições citam requisito e trecho real do código, com mudança e aceite específicos |
| G3 | Verificador comprova funcionamento mínimo | Até três checks focados, executados pelo harness, com assertivas ou saída esperada |

G1 não julga qualidade. Ausência de entrega retorna a G0. G2/G3 rejeitados ficam
vermelhos; ao devolver para escrita, os gates seguintes voltam a aguardar e G0 fica
amarelo. Aprovação de G3 só fica verde quando a entrega também foi integrada.
Tasks documentais entregam documentos reais e podem dispensar comandos G3;
a existência e a fidelidade do documento continuam obrigatórias.

Checks G3 exercitam **a unidade da task**, com dados isolados, sem suíte do sistema,
matriz extensa ou critérios estéticos. O verificador pode escolher Playwright ou outra
ferramenta adequada e reutilizar runners/testes existentes. O critério é exercitar o
comportamento pedido: não há obrigação de criar wrappers, repetir citações literais
ou fornecer `assertion_file`/`expected_stdout`. O harness executa os comandos e confere
o resultado real. Fontes não podem ser alteradas pelo validador durante a medição.
Os checks são registrados e podem ser corrigidos quando o problema é o próprio teste;
a forma do teste pode mudar, preservando o resultado funcional esperado. Recorrência do mesmo conflito ou correção que não muda a entrega
gera diagnóstico, sem aprovação por insistência. Caches Python da cópia são limpos antes
dos checks para evitar validar bytecode de uma versão anterior gravada no mesmo segundo.

Uma task adicional **Documentação de instalação e execução** garante README com
pré-requisitos, comandos reais, configuração e exemplos de uso aplicáveis. Variáveis
necessárias usam `.env.example` sem credenciais. Cada fase tem somente uma bolinha:
vazia antes de iniciar, amarela piscando durante o trabalho e verde após suas tasks.
Tasks têm quatro bolinhas G0–G3 e uma barra mostra o progresso das entregas concluídas.

Execuções seguintes reutilizam tasks já integradas quando a fonte e os arquivos
continuam válidos. Alterações externas invalidam as reservas afetadas e suas dependentes.
Tasks incompletas também são retomadas: a cópia de trabalho, o último gate confirmado,
as devoluções e os checks funcionais congelados ficam salvos em disco. Um check já
concluído não é repetido na retomada se a fonte permanece idêntica. Uma chamada de
agente ou comando interrompido antes de confirmar sua conclusão pode ser repetido.
O build preserva o init, o plan e arquivos existentes da aplicação. Histórico de comandos,
saídas de agentes, resultados e cópias ficam em `.harness/build`, com logs separados por
execução. Nenhum push ou publicação é realizado.

### Recuperação do plan e do build

Antes de encerrar o build, o Capivara2 confere uma entrega operacional: instalação
sem runtimes herdados, build declarado e até dois smoke checks curtos
dos comandos reais de início/importação. Isso não acrescenta uma suíte funcional
global nem avaliação estética. Projetos npm com lock usam `npm ci` por padrão;
receitas alternativas explícitas são documentadas e executadas em cópia limpa.
Uma checagem de tipos separada é informativa quando o build e a inicialização passam;
falhas desse diagnóstico ficam no README e nos comprovantes, sem gerar reparos infinitos.
Erros que impedem compilar ou executar continuam em correção. Não há preferência
obrigatória por strict, instanceof, ausência de any/casts ou organização de testes.

Reparos de dependências, configuração e testes são decididos pela execução real do G3,
sem uma aprovação textual adicional sobre a técnica adotada. Se o reparo altera código
de negócio, a revisão recebe os conteúdos base/atuais, diff e hashes. Pedidos de contexto
são atendidos pelo controlador sem repetir a escrita. Erros de uma versão anterior
são identificados como históricos; não provam que a candidata corrigida continua errada.

O runtime de conferência fica em um diretório temporário externo para que Node não
encontre `node_modules` no projeto pai; Python sem ambiente declarado é conferido
sem pacotes globais. Logs, comandos, resultados e checkpoints são registrados em
`.harness/build/delivery`. Uma interrupção retoma os comandos já confirmados quando
fonte, receita e ambiente continuam compatíveis, preservando todas as tasks prontas.

O README recebe uma seção controlada com os pacotes/versões dos manifestos,
pré-requisitos de sistema, comandos explícitos para instalação e manutenção e os
procedimentos operacionais conferidos. Os comandos manuais acompanham o gerenciador
de pacotes da plataforma analisada; quando necessário há também `install-manual.sh`.

**Código concluído com instalação pendente:** se somente um pré-requisito externo
não puder ser instalado automaticamente, o status principal é **Concluído — instalação
pendente**, com `delivery_status=blocked` e `ready_to_run=false`. O relatório e o README
explicam os pacotes faltantes. **Validar instalação** repete apenas a conferência
pendente; não começa o projeto de novo. Isso não equivale a afirmar que a aplicação
já está pronta para executar. Erros que impedem compilar ou executar continuam em
correção automática e não recebem essa conclusão por pendência externa.

O **build se recupera automaticamente durante a execução**, sem exigir um clique em
Tentar novamente para erros recuperáveis do agente. Respostas JSON inválidas, citações
que não correspondem à origem/ao código, falhas de comunicação do CLI e falhas recuperáveis
da task são devolvidas com diagnóstico para correção, com espera progressiva e cancelável.
Não há encerramento após uma quantidade fixa de tentativas. Cada retorno precisa passar
pelos mesmos gates; repetir uma falha nunca equivale a aprovar a entrega.

Ao repetir duas rejeições no mesmo gate, receber correção sem mudança de código ou
contratos repetidamente inválidos, o harness chama um diagnóstico em contexto novo.
Ele recebe o histórico, a reserva, a origem e os checks e decide entre corrigir código,
ampliar reserva, reavaliar uma acusação ou reparar o instrumento de teste. O ciclo de
recuperação tem uma identificação nova para evitar reutilizar uma revisão negativa
antiga do cache. Esse diagnóstico não aprova a task e fica persistido para retomada.

Escritor e auditor podem solicitar `scope_requests` com caminhos exatos e evidência
da necessidade antes de editar os arquivos. O scheduler reserva esses caminhos com
execução exclusiva e devolve a task ao escritor. Isso evita o impasse em que o auditor
exige montar uma rota, mas o escritor só pode entregar um patch porque o arquivo de
entrada está fora de `writes`.

Cada task recebe um `brief.json` derivado de seu conteúdo aprovado, com os donos das
demais unidades. Citações e requisitos globais continuam rastreáveis, sem transformar
uma task de estrutura na implementação de todo o CRUD ou em geração de suítes.
G3 prefere uma verificação mínima da funcionalidade entregue; browser e integração
ampla só quando necessários à própria unidade.

Uma falha G3 passa por diagnóstico: código incorreto volta ao escritor, enquanto bug
do check/fixture/runner pode ser corrigido pelo verificador. O reparo precisa preservar
o comportamento solicitado e receber uma revisão independente contra a origem e
a falha observada, sem obrigação de manter a técnica ou a estrutura do teste antigo. A aprovação do reparo é seguida de execução real; nunca
vale como aprovação da task. Versões anteriores, propostas, certificados e resultados
ficam em `tasks/<id>/verification-repairs`. Uma transação interrompida é retomada sem
regenerar a aplicação nem perder a versão de check aprovada.

O auditor recebe um catálogo de referências aos requisitos e trechos reais dos arquivos.
Ao citar esses IDs, o harness preenche as citações literais, evitando erros de transcrição.
Uma avaliação inválida é corrigida pelo auditor e não vira uma ordem de refazer código
correto. Se o auditor editar a fonte, a versão recebida é restaurada, a alteração fica
arquivada e a avaliação é refeita. Auditorias interrompidas também são recuperadas.

Quando uma task escreve um arquivo adicional, como `src/app.css` ou `package-lock.json`,
o scheduler aguarda os outros workers terminarem e reserva os caminhos exatos para execução
exclusiva. As versões de base, integradas e candidatas são preservadas; alterações concorrentes
são conciliadas e conflitos voltam ao escritor. Todos os arquivos adicionais aparecem para
o G2: ampliar a reserva não autoriza funcionalidades novas nem dispensa os gates G1–G3.
As tarefas já concluídas são preservadas. Bloqueios reais de instalação continuam fornecendo
instruções para intervenção manual, conforme a etapa de dependências.

**Tentar novamente** e abrir o mesmo módulo em uma nova execução retomam o trabalho
salvo. Isso também vale após cancelamento, falha do CLI, encerramento do processo
ou queda de energia. Uma falha inesperada da execução aparece no dashboard e mantém
o botão de recuperação disponível. O init continua sendo a exceção: executá-lo
novamente reinicializa os artefatos, conforme seu fluxo original.

Após atualizar a instalação, reabra `capivara2` no mesmo projeto e selecione `build`
para carregar o código atualizado e retomar o checkpoint. Um processo que já estava
aberto continua usando os módulos Python carregados no início da sua execução.

No plan, recibos em `plan/control/operations` preservam as respostas validadas.
O controlador reconstrói o estado localmente, sem chamar novamente os agentes para
operações confirmadas, e segue da primeira operação pendente. Logs antigos de
contexto/resposta também podem ser recuperados quando compatíveis com o contrato
atual. Correções exatas já autorizadas são idempotentes e continuam exigindo revisão.
Executar um plan já concluído preserva também o status do build.

No build, `state.json` registra o gate pendente e a cópia isolada de cada task.
O diretório `.harness` dessa cópia preserva a base, os scripts congelados e o diário
de integração. Esse diário é gravado antes de substituir arquivos na raiz; se a
integração for interrompida, a próxima execução pode concluí-la sem regerar código.
Edições externas conflitantes são preservadas e indicadas para resolução.
Quando a origem aprovada muda, o controle anterior é arquivado em
`.harness/plan-history` ou `.harness/build-history`, conforme o módulo.

### CLIs sem confirmação

As permissões de execução são exclusivas do build; init e plan continuam documentais.
Cada chamada inicia processo novo e não usa `--continue` ou `--session`.

| CLI | Configuração usada |
|---|---|
| Codex | `--dangerously-bypass-approvals-and-sandbox` ([documentação oficial](https://learn.chatgpt.com/docs/developer-commands?surface=cli)) |
| Claude | `--dangerously-skip-permissions` ([referência oficial](https://code.claude.com/docs/en/cli-reference)) |
| OpenCode | `run --auto --standalone`; bloqueios explícitos continuam valendo ([permissões](https://opencode.ai/docs/permissions/)) |
| AGY | `--dangerously-skip-permissions`, entrada/saída stream-json e schema ([modo headless](https://antigravity.google/docs/cli/headless/)) |

Codex e OpenCode foram conferidos também pela ajuda dos executáveis locais. A validação
real de geração e comandos desta etapa usa Codex; Claude e AGY exigem suas próprias
instalações/autenticações. O OpenCode preserva regras explícitas de deny.

Adaptadores personalizados de execução ficam separados dos adaptadores documentais:

```toml
[build_agents.agy]
command = ["/caminho/para/seu-adaptador-build-agy", "--sua-flag-sem-confirmacao"]
```

Recebem JSON/schema no prompt em stdin, devem operar no cwd fornecido e devolver o JSON
final. `CAPIVARA2_EXECUTION=build`, `CAPIVARA2_WORKSPACE`, `CAPIVARA2_ROLE`, modelo, effort,
schema e caminho de saída identificam a chamada. O wrapper precisa configurar suas próprias
permissões não interativas. Credenciais permanecem na configuração privada de cada CLI.

```text
.harness/build/
  source.json                   # origem aprovada, imutável durante a execução
  state.json                    # mapa, progresso, árvore integrada e retomada
  report.md                     # relatório após conclusão
  manual-install.md             # pacotes e instruções se houver bloqueio
  install-manual.sh             # recuperação manual, quando viável
  control/                      # settings por execução, dependências e blueprint
  tasks/<id>/                   # devoluções, checks e aprovação de cada task
  checks/<id>/                  # scripts de verificação preservados
  tools/                        # ferramentas internas adicionais do G3
  work/<id>-<uuid>/             # cópias isoladas usadas pelos workers
  log/                          # comandos, resultados, entradas e saídas reais
```

## Painel de execução

O dashboard de cada módulo usa a marca **CAPIVARA**, mascote em PNG, fundo
escuro e painéis ciano. A faixa superior mostra projeto, módulo, etapa, status e
duração. Abaixo ficam o pipeline documental, o provedor atual com papel/modelo/effort,
a telemetria e os eventos recentes.

A imagem fornecida pelo usuário fica em `harness/assets/capivara-engineer.png`,
preservada sem redesenho nem alterações no arquivo. O cabeçalho usa a imagem
proporcionalmente em terminais com gráficos Sixel ou Kitty. A negociação acontece
antes de abrir a TUI; sem suporte gráfico, o mascote muda automaticamente para a
arte ANSI `harness/assets/build-busto-40x13.ans`, fornecida pelo usuário.
O arquivo original de 40 × 13 é preservado; sua exibição é ajustada proporcionalmente
para 31 × 10 no cabeçalho atual, mantendo as cores e o enquadramento completo.
O funcionamento do harness não depende de suporte a imagens.

No plan, o painel mostra fases aprovadas/total, tasks aprovadas/identificadas até o
momento, fase atual, rodadas e revisões. Os totais de tasks podem aumentar enquanto
as fases são decompostas ou quando o revisor identifica uma omissão. Não há métricas
de entrevistas ou inferências nesse módulo.

No lugar do pipeline, o plan apresenta uma árvore rolável de fases e tasks, com
**duas bolinhas por linha: criação (C) e validação (V)**. O círculo vazio aguarda;
amarelo indica trabalho em andamento, verde indica conclusão e vermelho indica
devolução do revisor. Uma nova redação põe a criação em amarelo, mantendo a validação
vermelha até a nova revisão. A barra acima da árvore mostra a criação dos itens
identificados; seu total acompanha a descoberta de tasks. A lista acompanha o item
ativo e permite rolagem manual, sem perder a posição escolhida a cada atualização.

As informações vêm da execução real: perguntas/respostas no init, rodada, operações lógicas,
tentativas de chamada aos CLIs, devoluções para correção e decisões internas inferidas.
O provedor muda entre escritor e revisor conforme cada chamada. Tokens aparecem como
“não medido”, pois o harness ainda não contabiliza esse consumo. O painel apresenta
somente metadados operacionais; prompts, respostas e justificativas completas dos
agentes continuam nos artefatos, sem serem copiados para a lista de eventos da tela.

Em terminais largos, pipeline e provedor aparecem lado a lado. Em janelas menores,
os painéis se reorganizam em uma coluna, com rolagem vertical e controles acessíveis
por Tab. O cabeçalho se reduz quando necessário. As entrevistas continuam usando
as telas de múltipla escolha durante o andamento do init.

## Modelos e efforts

O catálogo fica em [harness/harness.toml](harness/harness.toml). Codex, Claude, OpenCode e agy
podem ser escolhidos para escritor e revisor. O CLI selecionado é iniciado
diretamente ao executar o módulo; falhas de execução ou autenticação são registradas.

Por padrão, Codex aproveita os modelos e efforts de
`$CODEX_HOME/models_cache.json` ou `~/.codex/models_cache.json`, quando esse
arquivo existe e é válido. Modelos ocultos no cache são ignorados. O cache é
somente lido; se estiver ausente ou inválido, a TUI usa o catálogo TOML.

Ao selecionar OpenCode, o wizard consulta os modelos habilitados e variantes de
effort do CLI autenticado no contexto da pasta escolhida, usando
`opencode api --standalone model.list`. CLIs anteriores usam a listagem
`opencode models` como alternativa. IDs incluem o provedor (`provedor/modelo`)
para preservar a distinção entre catálogos. A consulta não gera código nem inspeciona
dependências da aplicação. Um cache breve guarda somente IDs, nomes e efforts;
credenciais, headers e configuração da API não são copiados. Se a consulta falhar,
o último catálogo válido pode ser usado; sem catálogo válido, a tela informa a falha
e permite tentar novamente. Para usar apenas uma lista manual, configure
`discover_cli = false` em `[providers.opencode]` e preencha `models`.

Claude consulta o catálogo do próprio CLI pelo handshake `initialize`, sem enviar um
prompt de inferência. O wizard usa os IDs resolvidos e os `supportedEffortLevels`
informados pelo processo. AGY consulta `agy models`, agrupa as variantes da mesma
família e apresenta apenas os efforts encontrados nessa listagem. Não há uma lista
fixa de aliases/modelos. Essas consultas mantêm cache breve somente de ID, nome e efforts.
Uma consulta ao catálogo não prova que a conta está autenticada ou tem saldo para gerar.

Para uma lista manual, use `discover_cli = false` no provedor e cadastre os modelos.
O identificador `default` deixa o CLI escolher o padrão, por exemplo:

```toml
[providers.claude]
discover_cli = false
models = [
  { id = "seu-modelo", name = "Nome exibido", efforts = ["low", "high"] },
]
```

Os IDs e efforts desse exemplo são ilustrativos; use os valores suportados pelo
seu CLI. Para substituir o cache do Codex por uma lista própria, defina
`read_local_cache = false` em `[providers.codex]` e preencha `models`.

Para usar outro arquivo de configuração:

```bash
capivara2 --config /caminho/harness.toml
```

Há adaptadores nativos para Codex, Claude, OpenCode V2 e AGY nos módulos documentais
e no build. OpenCode recebe o agente documental por configuração efêmera explícita,
com ferramentas negadas; o build seleciona o agente nativo `build` e usa `--auto`.
Isso evita depender da descoberta de um agente em uma pasta ignorada pelo Git.
Consulte a [configuração do OpenCode](https://opencode.ai/v2/docs/config/).

Claude usa modo print e `--json-schema`; AGY usa o protocolo `stream-json` com uma
mensagem por chamada e `--json-schema`. Ambos priorizam `structured_output` quando
presente. Isso evita tentar interpretar texto repetido como dois objetos JSON.
O evento interno `finish` do AGY é aceito como protocolo de resposta quando seus
parâmetros correspondem ao `structured_output` de um resultado `SUCCESS`. Ele não é
uma ação sobre arquivos ou ambiente; eventos de ferramentas reais seguem bloqueados
nos módulos documentais.
No documental, ferramentas são desabilitadas/negadas. No build, Claude e AGY recebem
`--dangerously-skip-permissions`. Cada chamada abre uma sessão nova, sem continue/resume.
Referências: [CLI do Claude](https://code.claude.com/docs/en/cli-reference),
[headless do AGY](https://antigravity.google/docs/cli/headless/).

Todos os processos recebem `cwd` e `PWD` apontando para a pasta escolhida da operação,
inclusive consultas de modelos e comandos do build. Erros estruturados do CLI são
diagnosticados mesmo com exit code zero e nunca são aceitos como resposta documental.
Falhas de transporte dos CLIs são tentadas novamente também no init/plan, com espera
cancelável; no plan, essas falhas não consomem as oportunidades de reparo documental.

É necessário instalar e autenticar os CLIs que serão usados. Para Claude, execute
`claude auth login` quando o CLI informar que não há sessão autenticada. A autenticação
continua no armazenamento privado do provedor. Também é possível substituir qualquer
adaptador com um comando explícito, sem shell:

```toml
[agents.agy]
command = ["/caminho/para/seu-adaptador-agy"]
```

Esse adaptador recebe o pedido documental completo em stdin e precisa devolver
somente o JSON correspondente em stdout, sem executar ferramentas ou criar código.
As variáveis `CAPIVARA2_ROLE`, `CAPIVARA2_MODEL`, `CAPIVARA2_EFFORT` e
`CAPIVARA2_SCHEMA` informam papel, modelo, effort e caminho do schema. O contexto
anterior já acompanha a entrada; o adaptador não precisa implementar sessões.

## Base MCP

Em **Base MCP**, a TUI apresenta as instâncias cadastradas pelo nome do recurso
e a opção **Adicionar MCP**. Sem instâncias registradas, aparece somente a opção
de adicionar. Cada cadastro é independente: duas instâncias podem usar o mesmo
endereço e chaves de tenants diferentes.

O formulário solicita três campos:

- **Nome do recurso:** nome que identifica a instância na lista.
- **Endereço do MCP:** URL completa, incluindo protocolo, porta e caminho.
  O endpoint informado para o doc-center atual é
  `http://doc-center.rbtecnologia.net.br:7777/mcp`; esse endereço não é fixado
  pelo código.
- **Chave API de autenticação:** token Bearer, digitado em campo oculto.
  Também aceita `env:NOME_DA_VARIAVEL` ou `@/caminho/arquivo-da-chave`.
  Referências de arquivo relativas partem da pasta em que o Capivara2 foi aberto
  e são salvas como caminhos absolutos.

Enter avança entre os campos. No terceiro campo, Enter valida e salva; o botão
**Validar e salvar** e o atalho **Ctrl+S** fazem a mesma operação. O cadastro só
é gravado após inicializar a conexão MCP e consultar as listagens autenticadas.
Se houver falha, os campos permanecem disponíveis para correção e nova tentativa.
Uma instância válida sem projetos também pode ser cadastrada.

As instâncias ficam em `~/.config/capivara2/mcps.json`, respeitando
`XDG_CONFIG_HOME`. O arquivo é gravado de forma atômica com permissão `0600`;
uma pasta de configuração nova é criada com `0700`. Chaves digitadas diretamente
são guardadas nesse arquivo local. Para manter o segredo fora dele, use uma
referência de ambiente ou de arquivo. Essa configuração não faz parte do Git.

É possível escolher outro arquivo com `CAPIVARA2_MCP_CONFIG` ou com a flag,
que tem precedência:

```bash
capivara2 --mcp-config /caminho/privado/mcps.json
```

Ao selecionar uma instância, a TUI consulta os projetos expostos por
`resources/list` e `prompts/list`, seguindo a paginação quando houver. O contrato
atual do doc-center foi preservado: projetos sem recursos ou prompts publicados
não aparecem. As permissões da chave determinam o tenant e os projetos visíveis.
O menu permite atualizar a listagem e alterar os dados de uma conexão existente.

Ao escolher o projeto, o Capivara2 carrega o pedido completo, os documentos
selecionados para aquele projeto e seus arquivos de apoio, incluindo conteúdo
binário. Skills, hooks e memórias permanecem separados no contexto da sessão.
O acervo geral que o projeto não selecionou e o histórico de conversas não são
importados. Um prompt vazio ou uma falha ao ler qualquer recurso interrompe o
avanço, permitindo corrigir ou tentar novamente. Após carregar tudo, o fluxo
segue para a escolha do escritor e do revisor.

A integração usa o [SDK oficial MCP para Python](https://github.com/modelcontextprotocol/python-sdk)
com transporte HTTP, incluindo respostas JSON e SSE. O transporte e o cadastro
de instâncias são separados do adaptador do contrato documental do doc-center.
Os hooks são carregados como conteúdo documental; o init não os executa.
Ao iniciar o módulo, textos e anexos são preservados em `.harness/context`, sem
copiar a chave API para os artefatos do projeto. Anexos binários são guardados
como referências, sem presumir um conteúdo que o agente não observou.

## Verificação

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Os testes percorrem a TUI por teclado, verificando prompt com múltiplas linhas,
Ctrl+D, arquivos, edição da pasta, retorno entre telas, os quatro provedores,
revisor compartilhado ou independente e a execução documental do `init`.
Também verificam cadastro e edição de MCPs, persistência e permissões do arquivo,
falhas de autenticação, isolamento entre tenants no mesmo endereço, paginação,
respostas SSE, arquivos binários e importação completa sem material não selecionado.
Os testes do init focam no documento: preservação do pedido original, fase 0 e
fases executáveis, decisões obrigatórias sobre stack/ferramentas, lacunas resolvidas,
devolução de desvios/inviabilidade e aprovação explícita. Não testam a aplicação
solicitada nem verificam ferramentas do ambiente.

Os testes do plan verificam pré-requisito e integridade do init, preservação das
fases, contexto vazio por chamada, envio de uma única task ao escritor, devolução
pontual de divergências, inclusão e revisão de omissões, cancelamento, reexecução
e liberação do build somente depois de todas as aprovações documentais.

As regressões de revisão incluem remoções enviadas incorretamente ao escritor,
atribuições duplicadas, divisão/consolidação com IDs estáveis, reparos em lote,
revisão limitada ao recorte, rejeições contraditórias, reavaliação sem aprovação
automática e preservação de aprovações de versões intactas.

As regressões de contratos cobrem devolução do mesmo erro com outra redação,
exigências contrárias a um aceite vigente, mudanças sobre versão antiga, correções
vagas, falha de JSON repetida, escritor que ignora o patch, classificação de QA com
redações diferentes, partições mistas sem perda de texto e certificados adulterados.
Nenhuma dessas falhas libera o build ou produz aprovação por tentativas.

Os testes do build exercitam instalação automática e bloqueada, recuperação manual,
G1 sem entrega, rejeições específicas de G2, falhas reais de G3, comparação de stdout,
congelamento de verificações, validação sem alteração de fonte, reservas conflitantes,
execução serial/paralela, retomada, cancelamento e wizard/dashboard responsivos.
