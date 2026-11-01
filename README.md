# CAPIVARA2PTN

Pacote Python instalável com TUI persistente em Textual, textual-image e Pillow.
INIT implementado com entrevista progressiva, geração do PRD e G0 de fidelidade.
PLAN gera fases/tasks e executa G0 por thread. Requer Python 3.11 ou superior.
BUILD executa uma task por vez, com G0, G1 e G2 opcionais e realimentação.

## Instalação

Clone este repositório e instale com pipx:

```bash
git clone git@github.com:RBTecNet/Capivara2.git capivara2ptn
cd capivara2ptn
pipx install .
```

Alternativa, dentro da pasta clonada, com ambiente virtual isolado e comando
no PATH do usuário:

```bash
python3 -m venv ~/.local/share/capivara2ptn/venv
~/.local/share/capivara2ptn/venv/bin/python -m pip install .
mkdir -p ~/.local/bin
ln -s ~/.local/share/capivara2ptn/venv/bin/capivara2ptn ~/.local/bin/capivara2ptn
```

Garanta que `~/.local/bin` esteja no `PATH`. Para atualizar essa instalação,
execute na pasta clonada:

```bash
git pull --ff-only
~/.local/share/capivara2ptn/venv/bin/python -m pip install --upgrade --force-reinstall --no-deps .
```

## Execução

Dentro do diretório de trabalho desejado, execute:

```bash
capivara2ptn
```

Também é possível executar `python -m capivara2ptn` com o Python do ambiente
em que o pacote foi instalado. A aplicação captura e exibe o cwd original;
não muda o diretório global. Caminhos de prompt digitados podem ser absolutos
ou relativos a esse diretório. O seletor de arquivos abre nele e somente
preenche o campo, sem ler ou executar o prompt.

Use Tab/Shift+Tab para navegar, setas e Espaço para selecionar o módulo,
Enter para abrir seletores e acionar botões, e Ctrl+Q para sair. O log aceita
foco e rolagem pelo teclado. Em terminais menores que 100 colunas, os cards
são empilhados e a tela pode ser rolada. Recomenda-se pelo menos 80×24.
Para ver progresso, fases e logs simultaneamente no PLAN, prefira pelo menos
80×30; em telas muito baixas, os painéis continuam acessíveis pela rolagem geral.

Os atalhos F2 (entrevista INIT), F3 (sucesso), F4 (falha) e F5 (continuação
INIT → PLAN) abrem prévias independentes dos diálogos. A entrevista exige
uma alternativa; a opção livre exige texto não vazio. Esc fecha as prévias.
Confirmar uma prévia apenas fecha o diálogo, sem iniciar ou encadear módulos.
As prévias não abrem durante uma execução real dos módulos.

Selecione INIT, provedor, modelo, effort e origem do pedido e pressione Iniciar.
BUILD fica disponível com PLAN concluído. Selecionar PLAN e pressionar Iniciar começa
o planejamento do PRD concluído do projeto atual. Não há estimativas de duração
ou porcentagens fictícias.

No INIT, o card Progresso mostra Pedido → Análise → Entrevista → Redação →
Revisão → Concluído, com etapas concluídas em verde, etapa em curso em amarelo
e etapas aguardando em tom discreto. Falhas/reprovação são marcadas em vermelho;
com gates desligados, Revisão aparece como pulada. A barra acompanha os eventos
reais do INIT e se distribui em mais linhas quando o card é estreito.

## INIT e G0

O funcionamento foi migrado de `~/Documentos/Projetos/capivarinha/init.sh`,
preservando a entrevista progressiva: uma chamada independente por pergunta,
sempre com o pedido original e todo o histórico. Não há quantidade mínima ou
máxima de perguntas nem perguntas artificiais para prolongar a entrevista.

O modelo devolve uma pergunta e alternativas em JSON, ou somente `CONCLUIDO`.
A TUI apresenta a pergunta no modal existente, com múltipla escolha e Outra
resposta. A resposta confirmada é salva antes da próxima chamada. Cancelar o
modal interrompe o INIT, preservando o histórico confirmado.

O modal da entrevista aproveita até 95% da largura do terminal (máximo de 110
colunas). Alternativas longas ou com quebras explícitas ocupam várias linhas,
sem corte por reticências. A lista e o modal permitem rolagem quando necessário,
mantendo a navegação por teclado e a opção Outra resposta.

Após `CONCLUIDO`, outra chamada gera o PRD final. Com gates habilitados, uma
chamada independente, em modo de leitura, executa apenas G0 INIT. O validador recebe
pedido original, histórico e PRD como dados separados das instruções Markdown.
A aprovação exige somente `G0 APROVADO`. A reprovação exige `G0 REPROVADO` e
justificativa. Texto contraditório, ausência de decisão ou falha da CLI nunca
aprovam o INIT. Não há correção automática, nova tentativa ou outro gate.

Quando aprovado, ou ao concluir o PRD com gates desabilitados, a TUI exibe
sucesso e oferece selecionar PLAN, preservando
provedor, modelo e effort. Aceitar apenas muda a seleção: PLAN não é executado.
Reprovação ou falha interrompe o INIT e não oferece continuação. A conclusão
também registra o hash do PRD em `.harness/dados/init-concluido.json`, para reconhecer o
resultado após reiniciar, sem alterar a entrevista ou o PRD.

Os arquivos seguem os nomes do Bash, dentro do cwd capturado na abertura:

```text
.harness/dados/prompt-original.txt   pedido original
.harness/dados/entrevista.txt        perguntas, alternativas e respostas confirmadas
.harness/dados/resposta.txt          última resposta da entrevista, inclusive CONCLUIDO
.harness/dados/prompt-final.md       PRD final
.harness/dados/g0-init.txt           resposta original do G0
.harness/dados/init-concluido.json   conclusão persistida e hash do PRD
.harness/dados/erro-init.txt         motivo de falha/cancelamento, quando houver
```

Um INIT concluído pode ser refeito após o aviso e a confirmação descritos abaixo.
Um INIT incompleto continua preservado para inspeção, sem retomada da entrevista. O código-fonte e a instalação da CAPIVARA2PTN são recusados
como destino de arquivos de projeto. Ctrl+Q encerra a TUI e interrompe a CLI.

### Arquivos ocultos e módulo inicial

Os artefatos da ferramenta ficam em `.harness/dados/`; as opções locais ficam
em `.harness/.capivara2ptn.json`. Na abertura, arquivos reconhecidos da versão
anterior são movidos automaticamente para esses destinos. Conteúdo desconhecido
em uma pasta `dados/` do produto permanece na raiz. Conflitos entre arquivos
legados e novos são informados sem sobrescrever o arquivo conflitante.

Ao reabrir um projeto com INIT concluído, PLAN é selecionado. Com PLAN concluído,
BUILD é selecionado. Se houver execução interrompida, Iniciar retoma a task pendente;
se estiver concluído, o resultado é exibido sem reiniciar automaticamente.
Nenhum módulo começa a executar apenas por abrir a TUI.

Selecionar INIT ou PLAN já concluído abre um aviso. **Apenas consultar** mantém
todos os arquivos. **Refazer ao iniciar** autoriza a exclusão, aplicada somente
ao iniciar a execução: refazer INIT exclui seus resultados e os resultados de
PLAN/BUILD; refazer PLAN preserva INIT e exclui PLAN/BUILD. Arquivos do produto
fora de `.harness/dados/`, opções locais e prompts globais são preservados.

### Opções locais de execução

O card de configuração oferece dois toggles independentes, preservados ao
alternar módulos e ao reabrir a aplicação no mesmo diretório:

- **Habilitar gates:** habilitado por padrão. No INIT, desligar essa opção pula
  o G0 e encerra após gerar o PRD. O resultado informa explicitamente que G0
  não foi executado; não declara `G0 APROVADO`. Erros reais da CLI continuam
  interrompendo a execução. PLAN aplica o mesmo valor ao G0 por thread;
  BUILD aplica a opção a G0, G1 e G2.
- **Proibir testes automatizados:** desabilitado por padrão. Quando habilitado,
  cada chamada recebe uma restrição curta de execução: não criar arquivos,
  suítes ou tarefas de testes automatizados, preservar testes existentes e
  permitir compilação, typecheck e conferências pontuais sem criar suítes.
  Não há fiscalizador adicional nem remoção de arquivos.

As escolhas são salvas no arquivo `.harness/.capivara2ptn.json` do diretório de trabalho:

```json
{
  "gates_enabled": true,
  "forbid_automated_tests": false
}
```

INIT e PLAN releem essa configuração ao iniciar. Os valores lidos valem para essa
execução; alterações posteriores valem para a próxima. A restrição de testes é
aplicada pelo mecanismo de chamada compartilhado, sem modificar os prompts
Markdown editáveis ou acrescentá-la como funcionalidade ao PRD. Alterar toggles
não modifica PRDs ou planos existentes. Não há banco de dados.

### Escritor e auditor independentes

Provedor, modelo e effort originais representam o **escritor**. Logo abaixo,
**Provedor · auditor** oferece **Mesmo do escritor** por padrão, herdando os
três valores no início da execução. Escolher um dos quatro provedores mostra
modelo e effort próprios, lado a lado. A descoberta, os IDs e a entrada manual
usam o mesmo mecanismo das seleções existentes, sem nova API.

A escolha efetiva do auditor é usada somente em G0 INIT, G0 PLAN, G0 BUILD e
G2 BUILD. Entrevista, geração do PRD, planejamento, implementação e todas as
correções continuam no escritor original. G1 permanece determinístico. Gates
desligados não chamam o auditor nem exigem selecionar seu modelo.

As escolhas ficam no mesmo `.harness/.capivara2ptn.json`, no campo `auditor`:

```json
{"provider":"writer","model":null,"effort":"Default"}
```

`writer` significa Mesmo do escritor; uma escolha independente usa `codex`,
`claude`, `agy` ou `opencode`, o ID original do modelo e o effort selecionado.
O arquivo é relido ao iniciar o módulo; mudar o auditor não reavalia tasks
concluídas nem muda uma execução já em andamento. Prompts dos gates permanecem
intactos. Os erros das CLIs mantêm o comportamento próprio de cada módulo.

### Cronômetro por módulo

A linha do módulo no card de progresso mostra **Tempo: HH:MM:SS**, usando
`time.monotonic()` durante a execução. INIT, PLAN e BUILD têm acumulados
independentes. Selecionar um módulo ou deixar a TUI aberta não conta tempo.
A contagem começa ao entrar no fluxo, depois da validação da entrada e de um
reinício confirmado, e para em sucesso, falha ou interrupção, antes dos
modais de resultado/continuação. Perguntas da entrevista e novas tentativas
fazem parte do tempo do módulo enquanto ele está executando.

Apenas os segundos acumulados são persistidos no campo `elapsed_seconds` da
configuração existente, sem relógio absoluto ou origem monotônica. Há um
checkpoint a cada segundo ativo e uma gravação ao parar; uma interrupção
forçada pode perder a fração posterior ao último checkpoint. Reabrir e retomar
acrescenta somente o tempo da nova execução, sem o intervalo parado.

Refazer INIT zera seus tempos e os de PLAN/BUILD, cujos resultados são excluídos;
refazer PLAN preserva INIT e zera PLAN/BUILD; refazer BUILD zera somente BUILD.
A confirmação isolada não zera nada: a exclusão/reset ocorre ao iniciar.
Execuções antigas sem tempo registrado mostram `--:--:--`, sem duração inventada.
O INIT conserva o fluxo anterior, sem nova retomada da entrevista; seu tempo
interrompido permanece salvo. A retomada do trabalho continua disponível onde
já existia, no PLAN e BUILD.

### Prompts externos editáveis

No primeiro uso, os modelos distribuídos com o pacote são copiados, sem
sobrescrever arquivos existentes, para a configuração global do usuário:

```text
~/.config/capivara2ptn/prompts/init.md
~/.config/capivara2ptn/prompts/init-final.md
~/.config/capivara2ptn/prompts/gates/g0-init.md
~/.config/capivara2ptn/prompts/plan.md
~/.config/capivara2ptn/prompts/gates/g0-plan.md
```

Se `XDG_CONFIG_HOME` for absoluto, ele substitui `~/.config`. **Edite essas
cópias globais ativas**: cada chamada relê o arquivo, mesmo com a TUI aberta,
sem reinstalar ou recompilar. Os arquivos em `src/capivara2ptn/prompts/` são
somente os modelos iniciais distribuídos no pacote. Atualizações não substituem
as instruções globais editadas pelo usuário.

Depois da primeira preparação, um arquivo removido não é recriado. Arquivo
ausente, ilegível ou vazio interrompe a etapa. Em particular, G0 sem instruções
nunca aprova automaticamente. Não há cópia das instruções de validação no Python.
O caminho efetivamente usado aparece no log ao iniciar o INIT.

INIT utiliza `gates/g0-init.md`; PLAN utiliza `gates/g0-plan.md` a cada thread.
BUILD usa `gates/g0-build.md`; seu escritor usa `BUILD.md` e G2 usa `G2.md`,
aproveitando os nomes dos arquivos já reservados no pacote. A preparação dos
prompts do PLAN não sobrescreve nenhum prompt existente, inclusive G0 INIT.

## PLAN e painel de fases/threads

Selecione PLAN, confira provedor/modelo/effort e pressione Iniciar. O módulo
utiliza automaticamente `.harness/dados/prompt-final.md`, sem solicitar outro pedido.
O PRD precisa pertencer a um INIT concluído. Registros novos usam o hash salvo
na conclusão; PRDs alterados são recusados. Para os INITs anteriores a esse
registro, são exigidos pedido original, histórico, resposta `CONCLUIDO`, PRD
não vazio e ausência de falha; se houver G0 INIT, ele precisa estar aprovado.

A migração preserva a chamada única de `capivarinha/plan.sh`: o planejador recebe
o PRD e devolve todo o plano em Markdown. O Python identifica fases/tasks,
salva cada task como uma thread individual e percorre essas threads na ordem
declarada, sem paralelismo. A fase inteira nunca é usada como uma única chamada
de execução ou de aprovação. BUILD executa essas definições individualmente, conforme descrito abaixo.

O formato mantém `## Fase N — Nome` e `### Task N.M — Nome`. Cada task declara
`Dependências: nenhuma` ou os IDs de tasks anteriores. Não há reordenação para
consertar dependências: referência desconhecida, própria ou posterior interrompe
o PLAN com o Markdown preservado. Cada definição individual inclui seu contexto
tecnológico, o objetivo da fase e apenas a task correspondente, para uso futuro
pelo BUILD.

Com gates habilitados, G0 avalia **cada thread**, com o PRD, sua definição e
dependências, usando o arquivo externo atualizado. Não existe avaliação ou
aprovação independente da fase inteira. Uma reprovação objetiva retorna ao mesmo
escritor para corrigir exclusivamente a task atual, com o PRD, o motivo exato
e o contexto das dependências. G0 reavalia a correção até aprovar, sem limite
de tentativas. Tasks aprovadas não são reescritas nem reavaliadas.

O escritor devolve somente a task atual, mantendo seu ID. A aplicação substitui
apenas esse trecho no plano e na definição individual. Se a correção exigir
mudar outras tasks ou fases, deve informar `ALTERACAO_AMPLA_NECESSARIA` e o
PLAN interrompe sem aplicar a proposta. Saída ambígua, erro da CLI, cancelamento
ou falha de escrita também interrompem; não há retry de infraestrutura.
Com gates desligados, a thread conclui após sua definição ter sido salva; G0
não é chamado e sua bolinha não aparece. A restrição de testes é encaminhada ao
planejador e ao G0 pelo mecanismo compartilhado, sem fiscalizador adicional.

Ao selecionar PLAN, os logs ficam menores e o espaço liberado mostra o painel
rolável de fases e threads. Cada fase tem uma bolinha; cada thread tem a bolinha
do escritor e, apenas se os gates desse PLAN estavam habilitados, a do G0.
Cinza indica pendente, amarelo pulsante indica atividade real, verde indica
conclusão/aprovação e vermelho indica falha. A fase fica verde somente quando
todas as suas threads concluem. A barra usa contagens reais de threads e fases.

A bolinha do escritor por thread registra a definição produzida e salva: não
há uma chamada adicional ao escritor para cada task aprovada na primeira avaliação.
Somente uma reprovação provoca uma chamada de correção da task correspondente. Na chamada inicial, quando
as fases ainda não são conhecidas, o painel informa que o escritor está
definindo o plano, sem inventar tasks ou porcentagens.

```text
.harness/dados/plano-original.md             resposta intacta do planejador
.harness/dados/plano.md                      plano com títulos normalizados
.harness/dados/plan-estado.json               fases, threads, estados e configuração da execução
.harness/dados/plan/threads/task-N.M.md       definição individual da thread
.harness/dados/plan/threads/task-N.M.g0.txt   resposta do G0, se habilitado
.harness/dados/plan-erro.txt                  motivo de falha/interrupção, quando houver
```

Ao reabrir a TUI e selecionar PLAN, o painel carrega o estado salvo. Nada é
executado automaticamente ao abrir. **Iniciar retoma um PLAN incompleto**,
preservando as definições salvas e as aprovações já obtidas, sem gerar outro
plano. Com gates habilitados, continua pelo G0 pendente/reprovado e pelas threads
seguintes. Uma task reprovada retoma pela correção pendente, preservando o
motivo e os contadores de tentativas. A retomada nunca reinicia o plano inteiro.

Com gates desligados na retomada, as definições já salvas contam como concluídas
e as restantes são salvas a partir do mesmo plano, sem chamar G0. Reprovações
anteriores não são convertidas em aprovação: seus arquivos são mantidos e o
motivo fica também em `previous_error` no registro de progresso. `plan-erro.txt`
pode continuar contendo o relato da interrupção anterior; o estado atual vem
de `plan-estado.json`. A configuração selecionada vale para a retomada.

O PRD, o plano e as definições concluídas são conferidos antes de reutilizar
o progresso; conteúdo alterado ou registro inconsistente é recusado, sem
sobrescrever os arquivos. Se a chamada inicial não chegou a salvar um plano,
Iniciar permite refazê-la, preservando eventual resposta parcial anterior.
Uma execução concluída exige confirmação explícita para ser refeita, conforme
o aviso de exclusão das fases subsequentes. Ao interromper a TUI, o progresso
é salvo para a próxima retomada explícita. Não há banco de dados ou scheduler.

### Normalização determinística do Markdown

Após salvar a resposta original e antes de identificar fases/tasks, o Python
normaliza somente os títulos reconhecidos. Por exemplo:

```text
**## Fase 1 — Fundação**       → ## Fase 1 — Fundação
**### Task 1.1 — Estrutura**   → ### Task 1.1 — Estrutura
```

Espaços extras nos títulos e linhas em branco são tolerados. Nomes, descrições,
requisitos, conteúdo desconhecido e ordem das tasks são preservados. Blocos de
código não são reinterpretados como títulos. Se não for possível interpretar
a estrutura com segurança, o PLAN interrompe e mantém os dois Markdown para
inspeção. Isso é tratamento de formato, não avaliação de qualidade, e não
utiliza LLM, dependências extras, gates ou mecanismos de correção.

## BUILD: uma task por vez

O fluxo segue `capivarinha/scripts_funcionais/build.sh` e os experimentos
`build-gates.sh`, `g1.sh` e `g2.sh`: extrair uma task, chamar a CLI no projeto,
verificar a entrega e avançar. O PRD e o hash do plano precisam corresponder ao
INIT/PLAN concluídos. O escritor recebe somente a task atual como autorização,
com PRD, contexto e dependências anteriores. Não há validação da fase inteira.

Com gates habilitados, a ordem é **G0 → escritor → G1 → G2**. G0 examina a
preparação; uma reprovação retorna ao mesmo escritor em modo de leitura para
um esclarecimento local, salvo no controle do BUILD sem alterar PRD/PLAN.
Uma mudança que exija outros requisitos/tasks deve informar
`ALTERACAO_AMPLA_NECESSARIA`, suspendendo a execução para decisão do usuário.

O escritor altera os arquivos reais na raiz e pastas do projeto. O G1 compara
hashes com o inventário anterior à primeira tentativa da task, incluindo
alterações acumuladas e exclusões. `.harness`, Git, dependências instaladas e
caches não contam como entrega. Não depende de Git e não avalia qualidade.
Tasks explicitamente de inspeção, sem exigência de escrever arquivos, podem
concluir sem mudanças. G2 lê os arquivos reais e avalia somente a entrega da
task, com os critérios e restrições aplicáveis, dependências e resultado G1.

Reprovação válida de G1/G2 retorna ao escritor com o motivo concreto. Após a
correção, G1 e G2 são reexecutados. Se o escritor sinalizar mudança relevante
nas premissas com `PREMISSAS_G0_ALTERADAS`, G0 também reavalia antes do avanço.
As instruções para esses marcadores ficam no prompt externo do escritor.
Não há limite de tentativas, aprovação por contagem ou descarte do código.

Erro do modelo/provedor, autenticação, conexão, timeout ou decisão inválida
repete somente a chamada pendente, com o mesmo provedor/modelo/effort. O log
distingue erro técnico de reprovação. Há uma espera assíncrona de 2 segundos
entre erros de chamada, sem bloquear a TUI ou iniciar chamadas simultâneas.
O escritor informa `ERRO_CHAMADA_MODELO` se uma falha de ferramenta impedir a
entrega, provocando nova tentativa mesmo se a CLI tiver saído com código zero.
Um projeto vazio e escolhas técnicas delegadas à task não são bloqueios de escopo.
Prompt ausente/vazio, erro de armazenamento e bloqueio de escopo suspendem a
execução com mensagem; não são tratados como reprovações corrigíveis.

**Ctrl+C interrompe o BUILD** sem fechar a TUI; Ctrl+Q encerra. Cancelamento
impede novas tentativas e preserva estado e código. Iniciar retoma a operação
pendente, sem repetir tasks concluídas. O BUILD concluído só reinicia após a
confirmação existente; reiniciar limpa seus controles, preservando INIT, PLAN
e os arquivos reais do produto. Não há rollback nem limpeza automática do código.

O painel é o mesmo do PLAN, com uma bolinha por fase e, por task, G0, Escritor,
G1 e G2. Com gates desligados há somente Escritor: nenhuma chamada a G0/G2 nem
inventário/comparação G1 é executada, e o sucesso da CLI conclui a task.
A proibição de testes vale para escritor e validadores e permite compilação,
typecheck e conferências pontuais sem criar suítes. Testes existentes permanecem.

O registro segue o mesmo formato de fases/threads, em
`.harness/dados/build-estado.json`; relatos, respostas dos gates e inventários
por task ficam em `.harness/dados/build/`. As instruções ativas são:

```text
~/.config/capivara2ptn/prompts/BUILD.md
~/.config/capivara2ptn/prompts/gates/g0-build.md
~/.config/capivara2ptn/prompts/G2.md
```

São lidas novamente a cada chamada. A instalação inicial não sobrescreve
arquivos editados. INIT e PLAN mantêm seus prompts e fluxos anteriores.

### Preparação obrigatória antes de produzir código e entrega do README

Novos PLANs começam com **Preparar ambiente de desenvolvimento** e terminam
com **Documentar instalação e uso** (penúltima task) e a verificação técnica
final. O PLAN recebe informações do host e define todas as dependências de
runtime/desenvolvimento, versões, comandos de verificação e instalações
compatíveis. Componentes opcionais como Tkinter não são presumidos disponíveis.

A primeira task contém um bloco `preparacao-ambiente`, com `verificacoes`,
`instalacoes` e `nao_aplicavel`. As listas de comandos usam `diretorio` e
`comando`, como a verificação técnica. Os manifests necessários podem ser
preparados pelo escritor; código funcional da aplicação fica fora dessa task.
O executor verifica o ambiente, instala somente o que o PLAN declarou quando
faltarem dependências e verifica novamente. Nenhuma task de produção é
liberada antes de todas as verificações passarem e dos gates aplicáveis à
preparação concluírem. Isso vale também com gates desligados.

Falha de instalação, ausência de permissão ou dependência ainda ausente
interrompem o BUILD na preparação, com saída/retorno e progresso preservados.
Não há aprovação textual que substitua as verificações nem produção de código
para contornar a falta de uma dependência. O usuário pode resolver uma
permissão/instalação bloqueada e retomar a mesma preparação. Os relatórios
`task-N.M.environment-K.antes/instalacao/depois.json` e seus logs ficam em
`.harness/dados/build/`, usando a persistência existente. Não há novo gate,
auditor, banco ou sistema de descoberta de dependências; o PLAN é a autoridade.

A task de documentação deve produzir README.md na raiz, com objetivo,
pré-requisitos, instalação, comando exato de inicialização, uso, dados e
limitações reais. Um README ausente/vazio mantém a chamada ao escritor pendente;
o BUILD não declara entrega concluída sem o arquivo. Não é criado um binário
empacotado que o projeto não previu: o README informa a forma real de iniciar.

Planos antigos não são reescritos automaticamente. Produção/correção de código
sem uma preparação declarada e verificada é bloqueada; é necessário atualizar
o planejamento. Resultados antigos continuam disponíveis para consulta. A
agenda já produzida recebeu README diretamente, sem alterar seus fontes ou PRD.

### Task final de verificação técnica

Novos planos terminam com **Verificação técnica da aplicação**, dependente das
tasks necessárias. O PLAN define tecnologias, caminhos, scripts e comandos;
não executa a verificação. A descrição da última task contém um único bloco:

````markdown
```verificacao-tecnica
{"comandos":[{"diretorio":".","comando":"python3 -m py_compile main.py"}],"nao_aplicavel":null}
```
````

Esse comando é somente exemplo para um projeto Python com `main.py`. O PLAN
escolhe o procedimento pertinente: compilação real para projetos compiláveis,
sintaxe/carregamento/inicialização apropriada para interpretados e comandos
para todos os componentes necessários. Os caminhos usados devem ser definidos
nas tasks anteriores. O formato ausente ou inválido é informado, sem inventar
um procedimento. Planos antigos não são reescritos automaticamente; refaça PLAN
com confirmação para incluir a task em um novo planejamento.

No BUILD, os comandos são executados diretamente, em sequência, com Bash
`-e -o pipefail`, no diretório relativo indicado dentro do projeto. A saída
completa de stdout/stderr e o código de retorno de cada comando ficam em
`.harness/dados/build/task-N.M.technical-K.json` e nos respectivos arquivos
`.comando-I.log`. A saída também acompanha os logs da TUI. As evidências de
execuções anteriores são mantidas. Nenhuma aprovação textual substitui o
resultado real dos comandos.

Uma falha segue para o G2 existente, com a saída original e os códigos reais.
O mesmo escritor recebe essa evidência e a orientação do G2, corrige os fontes
e o executor repete o procedimento do PLAN sem limite de tentativas. Nesta
task final, pode corrigir arquivos de tasks anteriores para resolver erros de
integração, sem reabrir/reexecutar essas tasks ou alterar suas aprovações.
PRD, PLAN e comandos não são alterados para ocultar falhas. O G0 verifica a
preparação, sem exigir que o código já passe antes dessa task de diagnóstico.

A task final permanece amarela e não conta como concluída durante correções e
reexecuções. Com gates habilitados, G1/G2 continuam no fluxo existente; G1
considera a natureza de inspeção, sem exigir alterações artificiais de arquivos.
Com gates desligados, os comandos ainda são parte obrigatória da task, e a
saída de falha volta diretamente ao escritor, sem chamar G0, G1 ou G2.
Ctrl+C interrompe o processo e preserva saída parcial e progresso. Na retomada
da verificação, os comandos são refeitos com os arquivos atuais; um parecer
pendente não permite usar evidência antiga para concluir a task.

Quando não existe verificação executável aplicável, o PLAN deve registrar
`{"comandos":[],"nao_aplicavel":"Justificativa concreta"}` no mesmo bloco.
O resultado será **não aplicável**, sem executar comandos nem simular aprovação
por compilação. Não há G3, auditoria global, validação independente da fase,
suítes de testes ou novos agentes.

### Executável indisponível na verificação técnica

O executor tenta primeiro o comando literal do PLAN. Se ele retornar 127 e
`python` estiver ausente no PATH daquele diretório, há uma substituição
restrita: `python3` para os módulos padrão `compileall` e `py_compile`, quando
o contexto tecnológico do PLAN declara Python 3. O runtime alternativo é
consultado de verdade para confirmar a versão e atender à versão mínima
indicada. Apenas o primeiro executável é trocado; módulo, flags, caminhos e
demais argumentos permanecem intactos.

Isso não usa LLM para descobrir ferramentas, não cria alias/shim, não instala
pacotes e não modifica fontes, PRD ou PLAN. Não há equivalência presumida para
outros runtimes, comandos compostos/expansões de shell ou projetos Python 2.
Um executável originalmente disponível é preservado, inclusive quando sua
verificação retorna erro real de sintaxe/compilação.

A alternativa é executada pelo próprio executor, com saída e retorno reais.
O relatório registra `comando_previsto`, `comando_executado`, a versão e o
motivo em `substituicao`, além das duas `tentativas`. Os logs originais e os
`.alternativo.log` ficam preservados. O retorno efetivo da alternativa determina
se a verificação passou; se ela falhar, a realimentação existente continua.
G2 recebe essa evidência e não exige identidade literal do nome do executável.
A tentativa inicial 127 não invalida uma alternativa equivalente bem-sucedida;
texto do escritor e sucesso de comandos arbitrariamente diferentes não são
aceitos como prova.

### Chamadas às CLIs

Nos fluxos de INIT/PLAN, cada rodada inicia um processo novo, recebe instruções/dados por stdin e envia
eventos/saída para o log sem bloquear a TUI. Nenhuma sessão é retomada. Modelo e
effort são os selecionados na interface; Default não adiciona um override.

| CLI | Mecanismo |
| --- | --- |
| Codex | `exec --json --sandbox read-only`, modelo via `-m`, effort via `model_reasoning_effort`; ferramentas de shell, apps, subagentes e perguntas desativadas. |
| Claude | `--print --output-format stream-json`, `--tools ""`, sem MCP, em modo de leitura; modelo/effort por flags próprias. |
| Agy | `--input-format stream-json --output-format stream-json`, uma mensagem por processo; `--mode plan` e regras `permissions.deny` via `--settings`. |
| OpenCode V2 | `run --standalone --format json --agent plan`; modelo `provider/model#effort`, com regras/políticas inline que negam ferramentas. |

`plan` nessas flags é o modo de permissão da CLI; não deve ser confundido com o
módulo PLAN da CAPIVARA2PTN. Nesses fluxos textuais, o Python salva as respostas e as CLIs não escrevem arquivos.
Uma tentativa de ferramenta no fluxo de eventos interrompe a chamada, em vez
de abrir perguntas internas ou criar outro executor. CLI ausente, erro, modelo
ou effort rejeitado interrompem a execução; não há troca silenciosa de modelo.

No BUILD, o mesmo adaptador usa o cwd e `$PWD` do projeto e permite ferramentas conforme a operação: escritor
com escrita e validadores/preparação em leitura. Codex usa `workspace-write`
para implementar e `read-only` para validar, com shell e code-mode host ativos.
Claude usa `acceptEdits` e ferramentas de arquivo/Bash para escrever, e somente
Read/Glob/Grep para validar. Agy usa `accept-edits`, `toolPermission=always-proceed` e
`--dangerously-skip-permissions` somente no escritor do BUILD, mantendo as
regras deny explícitas. Validadores/preparação de G0 usam `plan` e
`toolPermission=request-review`, com leitura permitida e escrita/comandos
negados. INIT/PLAN textuais continuam sem ferramentas. OpenCode usa os agentes nativos `build`/`plan`, permissões V2 por ação
e ferramentas aninhadas com `execute`. Perguntas internas e subagentes ficam
fora do fluxo. Erros de chamada no BUILD têm as novas tentativas descritas acima.

Referências de permissões: [Codex](https://learn.chatgpt.com/docs/permissions),
[Claude](https://code.claude.com/docs/en/permissions) e
[OpenCode V2](https://opencode.ai/v2/docs/permissions), além do help das CLIs locais.

## Origem do prompt (somente INIT)

O campo **Origem do prompt** aparece exclusivamente no INIT. No PLAN e no
BUILD toda a área fica oculta, preservando os dados do INIT em memória.
Nenhum pedido inicial é solicitado para esses módulos: PLAN consome o PRD final do INIT; BUILD consome as fases/tasks do PLAN concluído.

- **Manual:** caixa multilinha para digitar ou colar o pedido livremente.
- **Arquivo:** caminho e botão de seleção. Caminhos relativos usam o cwd
  capturado na abertura; selecionar um arquivo não lê nem modifica seu conteúdo.
- **Servidor MCP:** seleção de servidor, botões Adicionar/Remover/Consultar,
  seleção de projeto e uma prévia somente de leitura do pedido.

Alternar a origem preserva texto manual, caminho, servidor, item e pedido
carregado. Os demais campos de configuração continuam independentes. O card
de configuração permite rolagem quando os controles não cabem no terminal.

O modal Adicionar pede nome, URL HTTP/HTTPS e token Bearer opcional, mascarado.
Conexões são persistidas em `~/.config/capivara2ptn/mcp.json` (ou em
`$XDG_CONFIG_HOME/capivara2ptn/mcp.json` quando a variável é absoluta), com
diretório `0700`, arquivo `0600` e gravação atômica. O token é armazenado nesse
arquivo privado, fora dos projetos, e não aparece em logs, representações de
conexões ou mensagens de erro. URLs com credenciais, query ou fragmento são
recusadas. Redirecionamentos não encaminham a credencial para outro endpoint.
A configuração da Capivara original não é modificada ou importada automaticamente.

Consultar inicia uma sessão MCP e lista os recursos, com paginação.
Projetos são deduzidos dos URIs `capivara://<projeto>/…`, excluindo `geral`.
O seletor mostra somente os nomes dos projetos, sem prefixos ou entradas de
prompts separados. Selecionar um projeto carrega automaticamente seu pedido.
O pedido é lido do recurso `/pedido`, com fallback para o prompt de mesmo nome,
conforme a referência original. Conteúdo binário não é convertido em pedido.
Nada é enviado a modelos nem gravado no servidor.

As consultas HTTP/HTTPS usam JSON-RPC, inicialização, sessão, Bearer e respostas
JSON ou SSE, em tarefas de fundo. Há prazo de 20 segundos por chamada e limite
de 8 MiB por resposta. Uma consulta antiga é cancelada/descartada ao trocar de
servidor ou item; uma chamada de rede em andamento pode levar até seu timeout
para encerrar. Erros HTTP, falhas de conexão e ausência de pedido são informados
sem incluir corpos remotos ou segredos. Não há reconexão automática ou retries.

Referências consultadas no
[capivara-harness](https://github.com/RBTecNet/capivara-harness):
[cliente MCP](https://github.com/RBTecNet/capivara-harness/blob/main/packages/core/src/mcp/client.ts),
[biblioteca](https://github.com/RBTecNet/capivara-harness/blob/main/packages/core/src/mcp/library.ts),
[endpoint](https://github.com/RBTecNet/capivara-harness/blob/main/packages/core/src/mcp/endpoint.ts)
e [configuração do Doc-Center](https://github.com/RBTecNet/capivara-harness#conectar-ao-doc-center-autenticado).

## Provedores e modelos

Provedor oferece Codex CLI, Claude CLI, Agy CLI e OpenCode CLI, nessa ordem.
Ao trocar de provedor, a seleção anterior de modelo é limpa e a nova consulta
ocorre em segundo plano. Effort mantém suas opções e seleção independentes.

- **Codex:** `codex app-server`, protocolo local por stdio, com `initialize`,
  `initialized` e `model/list` paginado. Não cria conversas ou turnos.
- **Claude:** sem listagem não interativa no CLI instalado. Conforme solicitado,
  oferece Opus (`opus`), Sonnet (`sonnet`), Fable (`fable`) e Haiku (`haiku`).
  São aliases aceitos pelo CLI, não um catálogo consultado da conta.
- **Agy:** `agy models`, cuja saída tem ID e nome separados por tabulação.
- **OpenCode:** `opencode models`, preservando os IDs completos
  `provedor/modelo`, inclusive os modelos retornados para OpenRouter.

O Select Modelo apresenta apenas entradas retornadas pela CLI, além dos
aliases do Claude definidos acima. No Claude ou se a descoberta não produzir
modelos, a opção **Informar ID manualmente…** abre um campo de texto. O ID
informado passa a ser a seleção atual; cancelar preserva a seleção anterior.
IDs manuais e acesso efetivo à inferência não são validados nesta etapa.

CLI ausente, falha, formato não reconhecido, lista vazia ou consulta acima de
20 segundos produzem um aviso no log e liberam a entrada manual. A troca de
provedor cancela a consulta anterior e descarta resultados antigos. Não há
consultas diretas a APIs, autenticação nova ou lista inventada de modelos.

Mecanismos conferidos nas versões locais: Codex 0.166.0, Claude Code 2.1.314,
Agy 1.4.12 e OpenCode 2.2.2. Versões com outros formatos podem exigir ajustes
no adaptador e, enquanto isso, permitem informar o ID manualmente.
Referências: [Codex App Server](https://learn.chatgpt.com/docs/app-server),
[configuração de modelos do Claude](https://support.claude.com/en/articles/11940350-claude-code-model-configuration)
e [modelos do OpenCode](https://opencode.ai/v2/docs/models/).

## Recursos e limites desta etapa

O PNG original é copiado para `src/capivara2ptn/assets/mascote.png` e incluído
no pacote, junto de `app.tcss`. É carregado usando `importlib.resources` e
Pillow, com a abordagem `textual_image.widget.Image` do experimento `teste.py`.
O renderizador da biblioteca se adapta aos recursos do terminal; não há mascote ASCII.

`src/capivara2ptn/prompts/` distribui os modelos iniciais dos prompts do INIT
e G0 INIT, além de PLAN e G0 PLAN. BUILD e G2 usam os arquivos externos editáveis correspondentes.
G1 é determinístico e não usa prompt ou LLM.

Esta versão usa CLIs para descobrir modelos e executar INIT/G0 INIT e PLAN/G0
por thread, e MCP somente para listar/ler pedidos. BUILD implementa somente G0, G1 e G2, sem outros gates. Os dados do INIT são salvos somente no diretório de trabalho; conexões
MCP e instruções editáveis ficam na configuração global. As CLIs podem manter seus
próprios caches e serviços durante a listagem.
O diretório capturado é a referência para os arquivos de projetos em etapas
futuras; os recursos da instalação são independentes dele. Os arquivos
originais `teste.py` e `assets/mascote.png` são preservados.

## Verificação desta entrega

Instalação não editável verificada com Python 3.14, Textual 6.12.0,
textual-image 0.14.2 e Pillow 12.3.0. Foram conferidos sintaxe, abertura pelo
entry point fora do repositório, PNG e prompts instalados, cwd preservado,
navegação por teclado, modais e layouts de 120×42 e 80×24. O diretório de
trabalho permaneceu vazio na inicialização sem executar INIT. Não foram criados
testes automatizados.

Na seleção dinâmica, foram conferidos os quatro provedores, IDs originais,
modelos do OpenRouter, entrada manual com confirmação/cancelamento, ausência
de CLI, troca rápida de provedor e interação com Effort durante a consulta.

Na origem do prompt, foram conferidos os três modos, preservação do texto e do
caminho ao alternar origens/módulos, cadastro e remoção pela TUI, persistência em
outro processo e permissões `0700`/`0600`. O Doc-Center já configurado na máquina
foi cadastrado separadamente na CAPIVARA2PTN e utilizado somente para listar e
ler projetos/prompts reais; a configuração original permaneceu intacta. Também
foram verificadas falha de conexão, resposta 401 sem exposição do token e
ausência do segredo nas capturas/logs. Não foram criados testes automatizados.
A lista foi simplificada para oferecer somente projetos e buscar automaticamente
o pedido/prompt correspondente, sem expor o catálogo de prompts no seletor.

O INIT completo foi executado manualmente com Codex CLI, modelo `gpt-6-luna` e
effort Low: pergunta pelo modal, resposta persistida, `CONCLUIDO`, chamada
separada para o PRD, chamada independente para G0, aprovação, diálogo de sucesso
e seleção de PLAN sem executá-lo. Arquivo de G0 ausente/vazio e decisões ambíguas
foram conferidos sem aprovação automática. Os adaptadores de Claude, Agy e
OpenCode usam as flags/protocolos das versões locais e documentação oficial;
o fluxo completo com esses três provedores ainda não foi verificado.

As opções foram alteradas pelos toggles, preservadas entre módulos e carregadas
após reiniciar. Outro INIT real, com gates desligados e proibição de testes
ligada, gerou o PRD sem executar/criar resultado de G0 e informou esse desvio
explicitamente. Alterar as opções não modificou o PRD já gerado. Um erro real
de modelo não suportado foi propagado pela CLI. Os prompts editáveis não foram
modificados para implementar as opções e nenhum teste automatizado foi criado.

O PLAN foi executado manualmente com Codex a partir de um PRD real do INIT:
3 fases e 7 threads concluídas com 7 G0 individuais. Outra execução com gates
desligados concluiu sem campos/bolinhas/arquivos de G0. Em diretório e prompts
isolados de verificação, uma definição que contrariava o PRD foi reprovada pelo
G0. Essa conferência da versão anterior foi complementada pelo ciclo de
realimentação descrito abaixo.

Foram conferidos os estados derivados das fases, recarga do painel após
reiniciar, disposição em 130×44/110×32/80×30, preservação do PRD, os exemplos de
normalização Markdown em negrito, conteúdo desconhecido preservado e erro em
estrutura/dependências inseguras, sem reordenar tasks. G0 INIT permaneceu
inalterado. Não foram criados testes automatizados; outros provedores não foram
revalidados ponta a ponta nesta entrega.

A retomada explícita foi conferida em cópias de um PLAN real interrompido na
thread 2.1: com gates desligados, reaproveitou 6 definições, salvou as 17 restantes
e concluiu 23 threads/5 fases sem chamar planejador ou G0. PRD, plano, definições
anteriores e relatórios G0 foram preservados. Com gates habilitados, as 5
aprovações anteriores foram mantidas e a próxima validação permaneceu na thread
2.1. A pasta original não foi modificada durante essa verificação.

A realimentação foi conferida com Codex em uma cópia de projeto real: G0
reprovou a task 2.1, o escritor corrigiu apenas essa task e G0 aprovou na segunda
avaliação, avançando até concluir as 7 threads. Os hashes do PRD, das definições
e das aprovações anteriores permaneceram iguais. Um modelo inexistente provocou
falha real da CLI após uma chamada, sem correção nem repetição automática.

A migração para `.harness` foi conferida preservando arquivos desconhecidos em
`dados/` do produto. Na TUI foram verificados PLAN após INIT concluído, BUILD
após PLAN concluído, cancelamento do aviso e confirmação sem exclusão antes de
iniciar. Em cópias isoladas, refazer PLAN preservou os hashes do INIT e refazer
INIT removeu os resultados subsequentes; configuração e arquivos do produto
foram preservados. Gates desligados concluíram uma retomada sem chamadas ao
modelo, mesmo com correção pendente. Não foram criados testes automatizados.

O BUILD foi conferido manualmente com Codex CLI, `gpt-6-luna`, effort Low:
7 tasks/3 fases concluídas na TUI com gates; outra execução concluiu com 7
chamadas somente ao escritor, sem respostas de validadores ou inventários G1.
Em cópia isolada, uma preparação contrária ao PRD foi corrigida após G0 e uma
implementação com login/rede indevidos foi reprovada pelo G2, corrigida e
aprovada. O G1 também detectou ausência de mudanças e reconheceu correções
acumuladas. Cancelamento e retomada pelo G2 pendente preservaram as tentativas
e aprovações das duas tasks anteriores. Erro real de modelo indisponível repetiu
a mesma operação, sem avanço ou troca de modelo, e parou ao cancelar.
PRD, PLAN e seus artefatos mantiveram os hashes. Nenhum teste automatizado foi
criado. O fluxo completo dos outros três provedores não foi revalidado; os
adaptadores de leitura/escrita usam o help local e as permissões de cada CLI.

Arquivos desta implementação: `build_flow.py`, `build_g1.py`, `providers.py`,
`app.py`, `plan_panel.py`, `plan_state.py`, `prompt_files.py`, `workspace.py`,
`dialogs.py`, `app.tcss`, `prompts/BUILD.md`, `prompts/G2.md`,
`prompts/gates/g0-build.md` e este README. Os prompts ativos do BUILD ficam na
configuração global; prompts editáveis de INIT/PLAN foram preservados.

Na verificação técnica final, um novo PLAN real incluiu a última task com
procedimento e dependências explícitos. Em cópias de projeto já executado,
`node --check` detectou uma falha real de sintaxe: retorno 1, G2 com diagnóstico
objetivo, correção de um arquivo de task anterior pelo escritor e nova execução
com retornos zero. A TUI concluiu 8 tasks/4 fases; as aprovações anteriores,
PRD e PLAN foram preservados. O modo sem gates repetiu somente comandos e
escritor, sem validadores. Também foram conferidos compilação C real, execução
do binário, não aplicabilidade explícita e cancelamento de comando com saída
parcial e retorno -15, sem executar o comando seguinte. Não foram criados testes
automatizados. O fluxo com modelos foi verificado com Codex; os demais
provedores não foram revalidados integralmente.

Arquivos deste ajuste: `technical_verification.py`, `plan_flow.py`,
`build_flow.py`, `build_g1.py`, `plan_state.py`, `plan_panel.py`, os prompts
`plan.md`, `BUILD.md`, `G2.md`, `gates/g0-plan.md`, `gates/g0-build.md` e README.
Os trechos novos também foram acrescentados às cópias globais ativas,
preservando o conteúdo editado pelo usuário. INIT e G0 INIT não foram alterados.

Escritor/auditor e cronômetros foram conferidos na TUI com Codex `gpt-6-luna`
no escritor e Claude `sonnet` no auditor, incluindo effort Low do escritor e
Medium do auditor. INIT, PLAN e BUILD concluíram com tempos separados,
congelados antes dos diálogos de conclusão. O modo sem gates concluiu sem
modelo de auditor selecionado e sem arquivos/chamadas de validadores. A pausa,
reabertura e retomada preservaram o acumulado do BUILD, sem contar o intervalo
parado; o formato foi conferido também em 80×30. Nenhuma suíte de testes ou
dependência nova foi criada e os prompts Markdown foram preservados.

Arquivos deste ajuste: `project_config.py`, `module_clock.py`, `app.py`,
`app.tcss`, `providers.py`, `init_flow.py`, `plan_flow.py`, `build_flow.py` e README.

A realimentação com papéis independentes também foi executada: Claude/Sonnet
reprovou uma falha real de sintaxe, Codex corrigiu o arquivo e Claude aprovou
a revalidação (3 chamadas do auditor e 1 do escritor). Na TUI, confirmar
Refazer PLAN sem iniciar preservou os tempos; iniciar a reexecução zerou PLAN,
limpou BUILD e preservou exatamente o tempo do INIT.

O loop da Task 5.1 foi reproduzido a partir dos registros reais de
`teste_capivara2/desktop`: 32 verificações retornaram 127 porque `python` não
existia, e G2 recusou `python3` por exigir o nome literal. Em cópia do projeto,
a execução 33 registrou o comando original com 127, confirmou Python 3 no
runtime alternativo e executou `/usr/bin/python3 -m compileall -q main.py agenda`
com retorno zero; G2 aprovou e nenhuma nova chamada ao escritor foi necessária.
Hashes das fontes, PRD e PLAN permaneceram iguais. Sintaxe inválida continuou
retornando 1, contexto Python 2 não foi convertido e comandos originalmente
disponíveis continuaram sendo usados diretamente. O projeto original foi
somente consultado. Nenhum teste automatizado ou dependência foi acrescentado.

Arquivos desse ajuste: `technical_verification.py`, uma passagem de contexto em
`build_flow.py`, orientações externas `G2.md`/`BUILD.md` e README. Os ajustes de
auditor independente e cronômetros foram mantidos.

Na preparação/README obrigatórios, um PLAN real para a agenda declarou
Python, SQLite, Tkinter e a instalação Ubuntu de python3-tk. O suporte Tkinter
foi instalado durante a preparação, e a conferência real de imports passou.
Outra execução instalou uma dependência local de desenvolvimento somente no
projeto, repetiu a verificação com sucesso e só então criou main.py; a ordem
foi conferida pelos relatórios e horários dos arquivos. O BUILD concluiu as
4 tasks e entregou README.md. Com uma instalação indisponível, parou na
primeira task: 0/4 concluídas, nenhuma chamada de produção e nenhum main.py.
As verificações foram feitas sem criar suítes de testes automatizados.

Arquivos deste ajuste: `delivery.py`, `plan_flow.py`, `build_flow.py`,
`build_g1.py`, `plan_state.py`, `plan_panel.py`, um rótulo de progresso em
`app.py`, prompts editáveis e README. A documentação da agenda foi criada em
`teste_capivara2/desktop/README.md`; sua importação do ponto de entrada foi
conferida, sem abrir a janela nem alterar o banco de dados.

O fluxo completo foi conferido também na TUI com gates habilitados:
preparação, produção, README e verificação final concluíram 4/4 tasks. A
produção continuou condicionada à evidência real do ambiente e aos gates da
task de preparação. O cronômetro e as bolinhas existentes foram preservados.

Agy: o erro real de headless na task 1.2 mostrou que `auto` não estava disponível
sem sandbox e que `strict` exigia confirmação de read_file mesmo com allow.
O escritor do BUILD agora usa `accept-edits` com `--dangerously-skip-permissions`;
a leitura dos auditores usa request-review/allow, sem liberar escrita ou comandos.
As regras de negação de arquivos usam prefixos de diretório, inclusive .harness.
Referência: [permissões oficiais do Agy](https://antigravity.google/docs/permissions).
A correção foi conferida com Agy 1.4.12 e gemini-3.8-flash-high: leitura,
escrita e comando concluíram sem prompts; leitura do auditor funcionou sem
liberar escrita/comandos. Uma escrita direta em .harness foi negada mesmo
com a flag ativa. Não foram criados testes automatizados.
