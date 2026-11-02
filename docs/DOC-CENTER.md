# Doc-Center opcional em REVERSE e CHANGE

Base preservada: `fb2f9ea822802ec7e42604bccb1674f79c7e5338`, na branch
`feature/change`. Integração na branch `feature/doc-center`.

O contrato foi conferido no código real do
[Doc-Center](https://github.com/RBTecNet/doc-center/blob/main/src/mcp/server.ts),
incluindo a atualização disponível no commit `1ef1a0e`.
O servidor e seu banco original não foram modificados.

## Uso

1. Cadastre o servidor e a credencial pelo cadastro MCP já existente da Capivara.
2. No card REVERSE ou CHANGE, escolha **Servidor Doc-Center**. O padrão é **Nenhum**.
3. Use **Consultar**, depois selecione **Projeto Doc-Center**. Projetos vazios podem
   ser validados pela opção **Informar slug do projeto…**. Essa opção apenas lê o
   pedido para verificar a existência e a permissão; não cria projetos.
4. Inicie ou retome o módulo pelos controles habituais. O contexto remoto será
   consultado no início da execução e selecionado por atividade nas chamadas aos
   modelos. Não substitui a inspeção do código nem o escopo autorizado no CHANGE.
5. Após a conclusão local, os resultados são enfileirados e publicados. O botão
   **Sincronizar** permite publicar resultados já concluídos ou repetir uma
   publicação pendente, sem repetir a execução do módulo.
   Em CHANGE, selecione a solicitação correspondente antes de sincronizar.

Abrir a TUI restaura os campos sem consultar o servidor. Servidor sem projeto não
habilita consulta ou publicação da execução. As escolhas de REVERSE e CHANGE são
independentes, por projeto local.

Com **Nenhum**, não há chamada MCP, autenticação, fila nova ou alerta de ausência
do servidor. Pendências de uma escolha anterior permanecem guardadas e só voltam
a aparecer quando o mesmo destino for selecionado. Não são redirecionadas para
outro servidor ou projeto. Alterar a URL no cadastro exige nova seleção; a fila
anterior permanece vinculada ao endpoint original.

## Leitura e confiança

O cliente novo estende `McpClient`: transporte HTTP/SSE, autenticação, sessão,
paginação e tratamento de erros continuam sendo os existentes.

São descobertas as capacidades reais por `resources/list` e `tools/list`. A leitura
usa `resources/read`; quando anunciado, `obter_contexto` também fornece material
ativo e histórico do projeto. Somente URIs sob `capivara://<projeto-selecionado>/`
são aceitas. Documentos gerais entram apenas quando o servidor os anuncia como
vinculados ao projeto selecionado. A biblioteca `capivara://geral/` e outros
projetos não são importados. Rascunhos seguem a filtragem de permissões do servidor.

O contexto enviado ao modelo possui orçamento próprio, identificação de origem,
relevância por atividade, indicação de documentos omitidos e de registros
históricos. Documentos extensos não são cortados e apresentados como instruções
completas. Referências textuais de pacotes de skills/hooks só entram se forem
anunciadas no mesmo projeto e referenciadas pelo documento principal.

Skills orientam a atividade aplicável. Hooks são consultados como documentação:
nenhum comando, script, instalação ou alteração de permissões é executado. O MCP
examinado não oferece uma operação de execução de hooks. O cliente local de hooks
do Doc-Center não é instalado nem acionado pela integração.

Texto remoto não autoriza mudar contratos do harness, o legado REVERSE ou o escopo
CHANGE. Solicitação e decisões locais atuais prevalecem sobre snapshots históricos.
Falha de consulta é informada e permite continuar o fluxo local, sem presumir que
o conhecimento remoto foi recuperado.

## Publicação pelo contrato disponível

| Artefato | Operação real | Resultado |
| --- | --- | --- |
| Prompt final, visão geral e documentação por domínio REVERSE | `registrar_levantamento` | Documento técnico novo, ativo |
| Descobertas e limitações REVERSE | `registrar_memoria`, natureza `convencao` | Rascunho para revisão humana |
| Resumo técnico de uma solicitação CHANGE concluída | `registrar_levantamento` | Documento técnico novo, ativo |
| Decisões e conhecimento resumido CHANGE | `registrar_memoria`, natureza `decisao` ou `convencao` | Rascunho para revisão humana |

O MCP não oferece publicação genérica de documentos nem criação de documento do
tipo `prompt`. Por isso, o prompt consolidado é publicado integralmente como
`documentacao`, sem alterar o pedido do projeto. O arquivo local
`.harness/reverse/prompt-final.md` continua obrigatório e autossuficiente.
`registrar_prompt` é uma operação de histórico, não de criação desse tipo de
documento, e não é usada para substituir o pedido.

`registrar_levantamento` pode criar um projeto inexistente no servidor. Para evitar
isso, esta integração exige projeto existente e verifica sua leitura antes de cada
publicação. Nunca usa `abrir_projeto` nem uma operação de atualização do pedido.

Cada versão recebe título próprio com identificador derivado do publicador local,
módulo, artefato e conteúdo. Um envelope registra a origem e a data da observação,
para não confundir documentação anterior com o código atual. A integração não
reutiliza títulos comuns de documentos aprovados. Antes de escrever, consulta a
URI prevista: conteúdo equivalente já publicado é confirmado sem escrita;
conteúdo diferente bloqueia aquele item, sem sobrescrevê-lo.

O contrato atual não oferece escrita condicional ou compare-and-swap. Títulos
versionados e consulta prévia evitam substituições intencionais; não constituem
uma transação com outras aplicações escrevendo simultaneamente na mesma URI.
A natureza de memória `estado`, que substitui o estado central do projeto no
servidor, nunca é utilizada.

Documentação é confirmada por resposta real e releitura do conteúdo publicado.
Memória é confirmada pelo recibo real de criação do rascunho, pois rascunhos não
são legíveis via recursos MCP. Repetições usam a mesma identificação para aproveitar
o upsert existente do servidor, sem criar outra versão apenas por repetir a tentativa.
Tokens com permissão somente de leitura não publicam: os artefatos ficam pendentes
até a correção da credencial no cadastro privado existente.

## Persistência e falhas

```text
.harness/doc-center/
├── selecao.json
├── contextos/
├── publicacoes/
└── sincronizacao-<modulo>.json
```

Todos os resultados funcionais permanecem nos diretórios originais de REVERSE e
CHANGE. O armazenamento novo contém apenas a seleção, contexto sanitizado, fila,
tentativas e confirmações da integração. Não altera os formatos de estado dos módulos.
A seleção guarda o ID do servidor e o hash da URL, sem tokens ou novas credenciais.
As credenciais continuam exclusivamente no cadastro privado global existente.

Os artefatos são salvos localmente antes da tentativa de publicação. Queda de
conexão, permissão insuficiente ou operação ausente preserva os resultados e as
pendências. Uma escrita de resultado incerto não é repetida automaticamente na
mesma tentativa; **Sincronizar** permite retomá-la. Não há scheduler ou serviço
em segundo plano. O estado só é marcado como confirmado com evidência do servidor.

A publicação sanitiza segredos conhecidos, tokens e padrões de credenciais,
sem modificar os artefatos locais. CHANGE exporta decisões, resultados, componentes
e códigos de retorno das verificações; não publica stdout completo, logs extensos
ou cópias de recuperação. REVERSE não escreve no legado.

## Arquivos novos

- `src/capivara2ptn/doc_center_client.py`: operações comprovadas sobre o transporte MCP atual.
- `src/capivara2ptn/doc_center_store.py`: seleção e fila local de publicação.
- `src/capivara2ptn/doc_center_artifacts.py`: seleção e sanitização dos artefatos concluídos.
- `src/capivara2ptn/doc_center.py`: contexto opcional e sincronização recuperável.
- `src/capivara2ptn/doc_center_ui.py`: campos e ações opcionais na TUI.
- `docs/DOC-CENTER.md`: uso, contratos, limitações e validação.

## Arquivos existentes alterados

- `reverse_ui.py`: inclui os campos opcionais, prepara contexto para os adaptadores
  existentes e publica após a entrega local. Não altera o executor, a análise,
  aprovação, cronômetro ou transição para INIT.
- `change_ui.py`: inclui os campos opcionais, prepara contexto para os adaptadores
  existentes e publica apenas a solicitação concluída. Não altera entrevista,
  plano aprovado, executor, gates, realimentação ou verificação técnica.

Todo o restante está em arquivos novos `doc_center*.py` e neste documento.
INIT, PLAN, BUILD, cliente MCP original, provedores, prompts e dependências não
foram alterados. Nenhum teste automatizado novo foi criado.

## Validação

A suíte preexistente foi executada. Também foi realizada homologação contra o
servidor real do Doc-Center usando banco temporário separado: leitura vinculada,
projeto vazio, isolamento entre projetos, documentação publicada e relida, memórias
em rascunho, repetição sem duplicação, token somente leitura, queda de conexão e
retomada de publicação. O pedido original permaneceu intacto e hooks não foram
executados. O servidor e banco usados pelo usuário foram preservados.
