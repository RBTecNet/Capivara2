# REA — cliente do servidor MCP oficial

Base funcional preservada: `0bf74fb11e482425b7438178a27f28470c4c0125`.
Contrato examinado em [RBTecNet/rea](https://github.com/RBTecNet/rea), commit
`4838f444939677107902549c59b73439e14d3ae9`, versão 6.5.0.

REA faz a análise técnica. A Capivara seleciona o alvo, prepara a instalação,
encaminha chamadas MCP e utiliza o escritor selecionado para conduzir a investigação
e consolidar a documentação. Não incorpora nem reimplementa descompiladores,
parsers de aplicações, analisadores binários ou motores do REA.

## Uso

1. Abra `capivara2ptn` no projeto que receberá os resultados e selecione **REA**.
2. Selecione um arquivo ou diretório e informe o objetivo, se desejar delimitá-lo.
3. Escolha provedor, modelo e effort nos seletores existentes e pressione **Iniciar REA**.
4. Revise e autorize a preparação quando algum componente necessário estiver ausente.
5. Acompanhe os logs e mensagens reais do servidor. **Interromper REA** preserva os
   checkpoints; reabra a TUI, selecione REA e pressione **Retomar REA** para continuar.
6. Use **Documentação** para consultar o resultado. **Nova investigação** arquiva a
   anterior após confirmação, sem apagar seus resultados ou afetar outros módulos.

O escritor usa o adaptador já existente em modo textual. Ele solicita uma operação
por vez em JSON; a Capivara encaminha essa operação ao MCP local. As ferramentas do
REA não precisam ser registradas no Codex, Claude, Agy ou OpenCode. O auditor e os
gates dos outros módulos não participam dessa execução.

A entrada pode ser um binário, pacote ou diretório aceito pelas ferramentas que o
REA anunciar. A disponibilidade por formato e plataforma vem do servidor. Um
formato sem suporte permanece uma limitação, sem uma análise substituta inventada
pelo harness. Diretórios JavaScript/Electron usam o fluxo estático oficial.

## Preparação compartilhada

A instalação gerenciada fica em `~/.local/share/capivara2ptn/rea/`, compartilhada
entre os projetos. Uma instalação existente acessível pelo comando `rea` ou
registrada nesse diretório é verificada antes de qualquer instalação. Os critérios
incluem o diagnóstico oficial `rea mcp doctor --json`, handshake real, descoberta
de ferramentas e contratos de persistência necessários à retomada.

Na ausência de instalação compatível, a TUI apresenta o plano e pede autorização.
O preparador obtém um commit identificado de **RBTecNet/rea**, instala as dependências
pelo lockfile e executa os procedimentos documentados:

```text
npm ci --no-audit --no-fund
npm run build:cached
rea mcp doctor --json
```

O `install.sh` do fork examinado aponta o pacote e as releases para outro upstream.
Ele não é executado: a instalação usa o código do repositório indicado pelo usuário,
sem substituir a referência por outro repositório ou pela publicação npm upstream.
As dependências npm são as declaradas pelo próprio REA.

Node.js/npm compatíveis são reutilizados. Se faltarem, é instalado um runtime
privado da linha 24.x, obtido de `nodejs.org` e validado pelo checksum publicado.
Isso não atualiza o Node global nem altera os adaptadores dos modelos. Na preparação
Linux, `procps` é solicitado quando necessário aos processos supervisionados.
Dependências de compilação ausentes podem ser preparadas quando o erro real indicar
a ferramenta faltante; não há repetição infinita do mesmo comando sem correção.

Cada etapa concluída fica registrada. Falha de rede, download, build ou diagnóstico
preserva as etapas anteriores e o erro real. Uma nova tentativa exige início/retomada
explícitos. `ready` só é registrado após confirmação real do MCP.

## Ferramentas do alvo

As dependências adicionais só são propostas após uma operação necessária falhar e
o diagnóstico justificar a instalação. A lista e os destinos são mostrados antes
da autorização. Não há instalação indiscriminada dos motores opcionais do catálogo.

- **Ghidra:** usa uma distribuição oficial da NSA com checksum, em diretório privado.
  Instalações existentes encontradas nos locais usuais são verificadas e reutilizadas.
- **JDK:** reutiliza um JDK compatível ou obtém JDK 21 do Eclipse Adoptium, validando
  o checksum; `JAVA_HOME` fica no ambiente privado do servidor REA.
- **JADX:** utiliza os metadados de release e o JAR exato indicados pelo REA instalado,
  incluindo a verificação SHA-256. Não mantém uma versão própria do analisador.
- **Hopper:** chama o `runSetup` e os hosts de instalação do próprio REA. A ponte
  restringe o plano a `install_hopper`, usa escopo vazio para clientes/skills e bloqueia
  as APIs que os modificariam. O plano real é mostrado antes de aplicar. A instalação
  não substitui exigências comerciais de licença, ativação ou autenticação do SO.
- **IDA e outras instalações existentes:** podem ser utilizadas conforme a disponibilidade
  e os contratos anunciados pelo REA; caminhos de provedores suportados podem ser
  configurados e verificados após confirmação. Licenças não são contornadas.

Pacotes Linux necessários usam o gerenciador reconhecido do sistema. Se ele exigir
privilégio, a autenticação é solicitada na TUI. A senha é entregue somente ao stdin
do sudo de validação, nunca ao modelo, aos argumentos ou a registros persistidos.
Os comandos de instalação recebem stdin fechado. Restrições de autenticação do SO
não são desativadas para contornar uma instalação recusada.

Preparação automática e análise nativa foram homologadas em Linux x86_64. Em outros
hosts, a compatibilidade depende das distribuições e controles disponíveis no REA.
Ferramentas sem receita de instalação suportada ou que exijam ativação humana são
reportadas objetivamente; não há download alternativo presumido ou falso sucesso.

## MCP, efeitos e ciclo de vida

A Capivara inicia `rea mcp` por stdio com o protocolo negociado oficialmente, descobre
`tools/list` e consulta `binary_session`. Os schemas de entrada são obtidos do servidor;
o modelo não ganha endpoints ou ferramentas ausentes. O cliente stdio é novo porque
o cliente MCP anterior suporta HTTP/SSE. O transporte e as integrações anteriores
permanecem intactos.

O servidor recebe tokens MCP de progresso. As mensagens de fases e espera são
exibidas sem convertê-las em porcentagens fictícias. Durante análise sem total
conhecido, a barra fica indeterminada. O cronômetro registra somente a execução,
excluindo a espera por autorização ou esclarecimento.

REA fornece os efeitos das operações. Chamadas com execução de código estrangeiro,
mutações relevantes ou rede exigem autorização específica. Caminhos de entrada ficam
restritos ao alvo e os destinos de escrita explícitos à área REA. Texto do alvo,
strings, skills, logs e comentários não são ordens confiáveis. Em caso de informação
indispensável ausente, o escritor pode solicitar um esclarecimento persistente.

O servidor pertence à execução atual. Após conclusão, interrupção ou falha, o cliente
solicita o encerramento das sessões e fecha seu processo. Não instala um serviço nem
mantém um servidor MCP permanentemente ativo. O REA continua responsável pelos
processos e caches especializados dos provedores.

## Persistência e retomada

```text
.harness/rea/
  estado.json
  catalogo-mcp.json
  diagnostico.json
  sessao.json
  evidencias/
    chamada-NNNNN.json
    bundle-NNNNN.json
    snapshot-*.json
  documentacao.json
  documentacao.md
  execucao.log
  historico/
```

Bundles e snapshots são produzidos pelo servidor oficial. A Capivara registra as
confirmações e seus checksums, valida os arquivos antes de reutilizá-los e importa
os bundles pela operação real `import_evidence_bundle`. Operações confirmadas não
são repetidas para recriar o histórico. O provedor nativo selecionado é preservado,
e snapshots confirmados são utilizados ao reabrir o alvo.

Uma operação sem checkpoint confirmado continua pendente. Se a publicação do bundle
terminou antes da interrupção, sua importação validada recupera o checkpoint. Se não
terminou, o resultado observado permanece preservado e pode exigir reexame daquela
operação. Evidências alteradas ou bundles recusados não são tratados como válidos.

Quando o diretório escolhido contém a área de saída, é preparada uma cópia de entrada
sem os controles `.harness` e `.git` do projeto. Isso evita incluir logs e bundles que
mudam durante a própria análise. O caminho original fica registrado e o modelo recebe
a correspondência. Essa cópia é do alvo, nunca de motores incorporados ao harness.
As identidades de arquivos servem somente ao controle de retomada; toda interpretação
técnica continua no REA. Mudanças no alvo invalidam o reaproveitamento daquela versão,
mas seus resultados anteriores são arquivados e preservados.

As evidências canônicas ficam privadas, sem alterações que invalidariam suas IDs e
assinaturas. Modelos, logs e documentação recebem projeções sanitizadas. Não há envio
automático ao Doc-Center, execução dos módulos anteriores ou gravação de controle na
raiz do projeto.

## Escopo e validação

Arquivos novos:

- `rea_mcp.py`: cliente stdio, catálogo, progresso e cancelamento MCP.
- `rea_setup.py` e `rea_vendor_setup.py`: instalação compartilhada e ponte para o setup oficial.
- `rea_state.py` e `rea_input.py`: checkpoints, identidade e preparação da entrada.
- `rea_flow.py`: encaminhamento sequencial das operações e consolidação.
- `rea_ui.py`: seleção, autorizações, painel e retomada na TUI.
- `rea_common.py`: arquivos privados, sanitização e processos locais.
- `rea_prompts.py` e `prompts/rea-investigate.md`: instruções externas editáveis.
- `tests/test_rea.py` e `tests/test_rea_ui.py`: regressões da integração.

O único arquivo funcional anterior alterado é `app.py`: registro do sexto módulo,
seus controles/painel e desvios exclusivos para seleção, início e indicação de tempo.
INIT, PLAN, BUILD, REVERSE, CHANGE, prompts, gates, clientes MCP anteriores e adaptadores
dos provedores permanecem com seus códigos e contratos anteriores.

A validação inclui a suíte preexistente, testes específicos de UI/protocolo/retomada,
instalação real do código oficial em pasta vazia, bootstrap privado do Node,
análise estática JavaScript pelo MCP real e instalação de Ghidra/JDK seguida de
análise de um ELF sem executá-lo. A interrupção e a retomada nativas foram verificadas
com bundles e snapshots oficiais. O plano Hopper foi examinado sem aplicar instalação
comercial ou ativação nesta homologação.
