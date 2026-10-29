# Capivara2 · wizard de inicialização

Harness com wizard em tela cheia, feito em Python com
[Textual](https://textual.textualize.io/). O `init` recebe o pedido, entrevista o
usuário e produz um prompt profissional por fases, aprovado por um revisor.

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
e um commit local é criado, conforme [AGENTS.md](AGENTS.md). O repositório
permanece sem remoto até autorização do usuário.

## Fluxo

1. Conferir ou editar a pasta do projeto; Enter confirma.
2. Escolher a função `init`. Depois de um init concluído, `plan` começa selecionado;
   seu menu informa que a implementação será feita na próxima etapa.
3. Escolher entre prompt manual, arquivo e base MCP.
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

“Sim” inicia o init documental. “Não” encerra o wizard sem executar o módulo.

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
`plan` é destacado como próximo módulo. `plan` e `build` ainda não executam trabalho.

## Painel de execução

O dashboard do init usa a marca **CAPIVARA**, mascote em blocos de terminal, fundo
escuro e painéis ciano. A faixa superior mostra projeto, módulo, etapa, status e
duração. Abaixo ficam o pipeline documental, o provedor atual com papel/modelo/effort,
a telemetria e os eventos recentes.

As informações vêm da execução real: perguntas/respostas, rodada, operações lógicas,
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

O catálogo fica em [harness/harness.toml](harness/harness.toml). A seleção não
consulta os CLIs nem faz testes de disponibilidade. Codex, Claude, OpenCode e agy
podem ser escolhidos para escritor e revisor. O CLI selecionado é iniciado
diretamente ao executar o módulo; falhas de execução ou autenticação são registradas.

Por padrão, Codex aproveita os modelos e efforts de
`$CODEX_HOME/models_cache.json` ou `~/.codex/models_cache.json`, quando esse
arquivo existe e é válido. Modelos ocultos no cache são ignorados. O cache é
somente lido; se estiver ausente ou inválido, a TUI usa o catálogo TOML.

Os demais CLIs começam com “Padrão do CLI”. O identificador `default` representa
deixar que o CLI escolha o padrão, sem presumir um ID de modelo ou effort.
Cadastre os modelos reais que deseja listar, por exemplo:

```toml
[providers.claude]
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

Há adaptadores nativos para Codex, Claude e OpenCode V2. A validação real desta
etapa usa Codex; os demais exigem suas próprias configurações e autenticações.
O comando não interativo específico do agy precisa ser configurado. Também é
possível substituir qualquer adaptador com um comando explícito, sem shell:

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
