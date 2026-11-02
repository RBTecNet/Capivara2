# CHANGE — solicitação direta no projeto

Base funcional preservada: `518d4112a69dc76ad370537a593a56f26f33b3e4`, na branch
`feature/doc-center`. Simplificação na branch `feature/change-direto`.

## Uso

1. Execute `capivara2ptn` no diretório da aplicação e selecione CHANGE.
2. Cadastre a descrição em **Nova solicitação**.
3. Selecione provedor, modelo e effort e pressione **Iniciar CHANGE**.
4. O escritor recebe o pedido e usa suas ferramentas para ler e modificar os
   arquivos diretamente no diretório atual, seguindo os padrões da aplicação.
5. O harness executa os comandos técnicos pertinentes informados pelo escritor.
   Uma falha devolve a saída real ao mesmo escritor para correção, sem limite de
   tentativas e sem nova aprovação. A conclusão registra os arquivos modificados.

Não há investigação prévia, descoberta de domínios, entrevista, plano, fases,
tasks, aprovação intermediária, auditor ou gates no CHANGE. A consulta ao código
acontece durante a própria execução. O pedido original é a autorização para a
alteração; o escritor deve preservar funcionalidades e modificações não relacionadas.
Os controles globais de auditor e gates continuam funcionando nos outros módulos,
mas não criam chamadas a auditores no CHANGE.

Somente uma decisão funcional essencial ausente pode abrir **Esclarecimento CHANGE**.
A pergunta e a resposta ficam salvas. Não existe quantidade predefinida de perguntas
nem um processo de entrevista antes da escrita.

## Acompanhamento, cancelamento e retomada

O painel mostra a solicitação, execução do escritor, arquivos modificados e os
resultados reais de verificação. Durante a execução, a barra é indeterminada: não
há porcentagem fictícia, fases ou tasks artificiais. Logs e botão **Interromper
CHANGE** permanecem acessíveis. O cronômetro mantém o acumulado por solicitação e
pausa enquanto aguarda esclarecimento ou após interrupção/conclusão.

Abrir a TUI nunca inicia uma solicitação automaticamente. Selecione a solicitação
anterior e pressione **Retomar CHANGE**. Alterações parciais permanecem no projeto;
o escritor recebe a indicação de retomada para conferir o código atual antes de
continuar. Se já terminou a escrita e parou na verificação, a retomada executa a
verificação sem chamar o escritor novamente. Uma conclusão cujo salvamento falhou
pode ser salva sem repetir escrita ou verificação ainda válida.

Mudanças no projeto desde o checkpoint são identificadas e comunicadas ao escritor,
que deve preservar as edições atuais. Alterações externas em arquivos não tocados
pelo CHANGE não são atribuídas à solicitação. Não há rollback automático, limpeza
Git, reinvestigação completa ou novo plano de aprovação.

Solicitações antigas continuam consultáveis. Na primeira retomada explícita pelo
fluxo novo, seu estado anterior é preservado em `historico-fluxo-anterior.json`.
Planos, cópias e journals antigos não são apagados. Patches pendentes do motor antigo
não são reaplicados automaticamente; o escritor examina os arquivos já existentes
e continua o pedido original. Solicitações concluídas não são reexecutadas.

## Escritor e verificação técnica

A implementação usa `CliRunner` e seu modo `access="write"`, já existente, com o
próprio projeto como cwd/PWD. Seleção, autenticação, effort e adaptadores Codex,
Claude, Agy e OpenCode permanecem os mesmos. Não há diretório neutro para o escritor,
protocolo de leituras intermediárias ou aplicação de patches pelo harness.

O prompt externo editável `change-direct.md` orienta escopo, padrões da aplicação,
preservação do trabalho do usuário e segurança. É instalado de forma aditiva pelo
mecanismo atual de prompts, inclusive em instalações que já têm os prompts antigos.
Não sobrescreve prompts personalizados ou de outros módulos.

Após a escrita, os comandos são executados no projeto com o executor técnico
existente, sem alterar a verificação do BUILD. São registrados comando, diretório,
saída real e código de retorno. Falha exige correção; um parecer do modelo não é
aprovação técnica. Não se exige Bubblewrap, cópia integral ou infraestrutura nova.
Comandos e ferramentas devem ser pertinentes e seguros, sem testes em produção,
migrações destrutivas, credenciais em argumentos ou exclusões amplas.

Se não houver verificação segura aplicável, a limitação precisa ser explícita:
o estado será **Concluído com limitações**, sem simular sucesso. A opção de proibir
criação de testes continua sendo entregue ao escritor. Nenhuma dependência é
instalada automaticamente pelo verificador.

Antes da primeira escrita, hashes dos arquivos autorais e, quando disponível,
HEAD/estado Git são registrados. Dependências, caches, controles e saídas de build
não entram no registro de entrega. Git não é obrigatório e não há promessa de
restauração completa. A sanitização cobre segredos locais e credenciais conhecidas
nos logs, respostas, relatórios e realimentação.

## Histórico e Doc-Center

Cada solicitação continua independente em `.harness/change/solicitacoes/change-NNN/`:

```text
estado.json                       # checkpoint, tentativas e tempo
pedido.md
baseline-direto.json               # hashes antes da escrita
respostas/escritor-N.md
esclarecimentos.json               # somente se necessários
verificacoes/run-*/                # saídas e códigos reais
ultima-verificacao.json
alteracoes.json
conclusao.md
execucao.log
```

O formato anterior é lido sem excluir seu histórico. Um bloqueio exclusivo mantém
apenas uma solicitação CHANGE em execução por projeto, inclusive entre processos.
O cadastro de novas solicitações continua independente.

A configuração opcional do [Doc-Center](DOC-CENTER.md) permanece. **Nenhum** não faz
comunicação externa. Com projeto selecionado, o conhecimento entra como contexto
auxiliar; depois da conclusão, resumo, decisões, arquivos afetados e resultados
são publicados pelo fluxo de sincronização existente. A exportação entende o
resultado direto e os históricos anteriores sem exigir tasks. Logs extensos e
segredos não são publicados. Hooks remotos não são executados.

## Escopo da alteração

A mudança se limita aos arquivos próprios do CHANGE, seu novo prompt, testes e
esta documentação. No coletor `doc_center_artifacts.py`, somente a exportação CHANGE
foi adaptada para resultados sem tasks. A parte REVERSE permaneceu intacta.

INIT, PLAN, BUILD, REVERSE, adaptadores, seletores globais, cliente MCP, cronômetros
compartilhados, gates e prompts dos outros módulos não foram alterados.

## Validação

```bash
python -m unittest discover -s tests -v
```

A cobertura do CHANGE verifica execução direta, verificação funcional real,
realimentação sem limite fixo, cancelamento de processos, retomada de trabalho
parcial, perguntas essenciais persistentes, histórico anterior, arquivos externos,
credenciais e exportação Doc-Center. As verificações de journals antigos permanecem
para preservar seus artefatos de recuperação. A suíte dos módulos protegidos é
executada sem modificar seus testes.

Aceite opcional com escritor real em aplicação temporária:

```bash
CAPIVARA_CHANGE_TESTS=1 CAPIVARA_CHANGE_TEST_MODEL=<modelo> \
python -m unittest discover -s tests -p 'test_change_models.py' -v
```
