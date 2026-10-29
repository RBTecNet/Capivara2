# Capivara2 · wizard de inicialização

Primeira etapa do harness: uma TUI em tela cheia, feita em Python com
[Textual](https://textual.textualize.io/). O wizard coleta a configuração do
`init` e termina na confirmação. A função `init` ainda não está implementada.

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
2. Escolher a função `init`. `plan` e `build` aparecem desabilitados nesta etapa.
3. Escolher entre prompt manual e arquivo. A opção MCP/doc-center aparece
   desabilitada, reservada para a segunda parte.
4. Digitar o prompt com múltiplas linhas ou informar um arquivo UTF-8.
5. Selecionar IA escritora, modelo e effort.
6. Reutilizar a configuração do escritor como revisor ou selecionar outra IA,
   modelo e effort.
7. Conferir o resumo e responder “Executar agora?”.

No prompt manual, **Enter insere uma linha** e **Ctrl+D confirma**. Menus usam
as setas e Enter; Tab alterna entre os controles, Esc volta e Ctrl+Q sai.
Os controles também aceitam mouse. O rascunho do prompt permanece ao voltar.

Arquivos de prompt podem usar caminhos absolutos, `~` ou caminhos relativos à
pasta do projeto selecionada. O conteúdo é lido e validado antes de avançar.

Ao responder “Sim” na confirmação, a TUI registra a intenção de executar e
mostra que o `init` será implementado depois. “Não” encerra a configuração sem
solicitar execução. Os dados ficam em memória; o wizard não inicia agentes,
não grava o prompt e não gera arquivos no projeto escolhido.

## Modelos e efforts

O catálogo fica em [harness/harness.toml](harness/harness.toml). Nesta primeira
etapa, a TUI não consulta os CLIs nem verifica instalação, autenticação ou acesso
a modelos. Codex, Claude, OpenCode e agy estão disponíveis para configurar os
papéis de escritor e revisor.

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

A configuração e a listagem de MCPs serão implementadas na segunda parte.

## Verificação

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Os testes percorrem a TUI por teclado, verificando prompt com múltiplas linhas,
Ctrl+D, arquivos, edição da pasta, retorno entre telas, os quatro provedores,
revisor compartilhado ou independente e a confirmação sem execução do `init`.
