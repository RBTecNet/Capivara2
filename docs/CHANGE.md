# CHANGE — manutenção e evolução localizada

Referência funcional: `ca5a337709487a4d61acbc4f2cce57e85dabceb6`, preservada na
branch `feature/reverse`. Implementação adicional na branch `feature/change`.
O commit de referência inclui as evoluções anteriores já verificadas; o arquivo
preexistente `assets/chatgpt_amd64.deb` não foi incluído no snapshot.

## Uso

1. Execute `capivara2ptn` no diretório da aplicação existente e selecione CHANGE.
2. Use **Nova solicitação**, descreva a correção, modificação ou recurso e cadastre.
3. Selecione escritor, modelo, effort e auditor nos controles existentes e pressione
   **Iniciar CHANGE**. O investigador identifica o tipo do pedido pelo conteúdo.
4. Responda às perguntas necessárias. Uma pergunta pendente fica salva; a entrevista
   usa o histórico e pode consultar código antes de perguntar algo ao usuário.
5. Revise o plano, os arquivos autorizados e os comandos de verificação. Escolha
   **Aprovar execução**, **Solicitar ajustes** ou **Cancelar solicitação**.
6. Acompanhe as tasks, G0/G1/G2 e os logs. Use **Interromper CHANGE** para preservar
   o progresso. Reabrir a TUI não inicia investigação ou escrita automaticamente.
7. Selecione a mesma solicitação e use **Retomar CHANGE**. Solicitações concluídas
   permanecem consultáveis em **Histórico**; uma nova solicitação recebe outro ID.

CHANGE não exige histórico de INIT, PLAN, BUILD ou REVERSE. Não reescreve esses
artefatos. Os padrões são descobertos no código atual; campos ou regras específicos
sem definição são tratados na entrevista, sem assumir requisitos de outros cadastros.

O seletor de módulos ocupa duas linhas para acomodar CHANGE sem cortar seu rótulo.
Os cards, cabeçalho, mascote, seletores de modelos, logs e modais anteriores são mantidos.
O relógio reutiliza o indicador e o tick da TUI; seu acumulado é salvo por solicitação,
sem alterar `ModuleClock` ou os formatos de configuração dos módulos existentes.

## Investigação e escrita

A investigação usa um catálogo de caminhos e leituras/buscas paginadas. Somente trechos
necessários entram no contexto; não há engenharia reversa completa do aplicativo.
Conclusões, evidências, padrões e incertezas são persistidos. Os modelos podem pedir
outros arquivos, linhas ou saídas de verificação quando precisarem de contexto.

Todos os adaptadores atuais são usados em modo textual. A CLI recebe um diretório
neutro temporário e dados sanitizados, sem ferramentas de escrita ou instruções
operacionais herdadas do projeto. Comentários, Markdown e arquivos como AGENTS são
dados da investigação, nunca comandos autorizados ao harness.

O escritor propõe uma lista de edições. O executor próprio do CHANGE aplica somente:

- Criação de arquivo novo explicitamente autorizado na task.
- Substituição de trechos exatos e únicos, com o fingerprint da versão atual.
- Remoção de um arquivo individual autorizado, preservando sua cópia anterior.

Não há aplicação difusa de patch, reescrita indiscriminada de arquivos existentes,
`git reset --hard`, `git clean` ou limpeza ampla. Codificação, modo e quebras de linha
existentes são preservados. Arquivos sensíveis, binários, links e controles de `.harness`
não são destinos de escrita. Uma solicitação que dependa deles precisa de tratamento
externo explícito; o executor não enfraquece essas restrições ao receber texto do modelo.

Uma ampliação de arquivos exige autorização explícita e atualiza o plano aprovado.
Uma ampliação funcional prepara um plano revisado para aprovação antes de nova escrita.
Reprovação técnica dentro do escopo volta à task responsável sem nova aprovação do usuário.
G0 prepara a task; G1 compara arquivos reais; G2 examina defeitos concretos da entrega
com conteúdo anterior e atual sanitizado, sem depender apenas do relato do escritor.
Não há auditoria independente de fase. Com os gates de modelo desligados, o controle
de aprovação, caminhos, conflitos, alterações reais e verificação técnica permanece.
A proibição de criar testes é respeitada sem apagar testes existentes.

## Recuperação, Git e concorrência

Antes da escrita, registra-se o inventário determinístico e, quando disponível, HEAD e
estado do Git. Não é criado commit automático nem prometido rollback completo. Sem
referência Git válida, a falta de um ponto de restauração completo garantido é informada
na aprovação. Modificações não relacionadas são preservadas; arquivos tocados têm
cópias privadas anteriores às alterações.

Cada conjunto de edições recebe um registro de recuperação antes de tocar a aplicação.
Se a execução parar entre arquivos, a retomada reconhece o que já foi aplicado e
continua sem duplicar edições. Uma versão inesperada bloqueia a aplicação e apresenta
os caminhos em conflito. Atualizar a investigação após esse conflito exige uma decisão
do usuário, conserva as respostas e as tasks ainda válidas e submete novo plano.

Se houver conflito durante uma aplicação parcial, nenhuma reversão automática é feita:
as edições já aplicadas e as modificações do usuário permanecem. O restante da transação
é suspenso para reexame, e as cópias anteriores ficam disponíveis no histórico privado.

Um bloqueio de arquivo exclusivo impede dois executores CHANGE no mesmo projeto,
inclusive em duas TUIs/processos. O cadastro de outra solicitação continua independente.
Não existe scheduler, execução paralela ou escritor adicional.

## Verificação técnica segura

Os comandos constam do plano aprovado e são executados pelo mecanismo determinístico
existente em uma cópia descartável da aplicação. O isolamento usa Bubblewrap quando
já está disponível: sem rede, ambiente/credenciais do host, bancos reais ou a árvore
original montada como projeto. Dependências instaladas na aplicação são montadas em
leitura, quando compatíveis; a cópia é a área gravável.

Os resultados preservam comandos, códigos de retorno e saída. Falhas reais voltam ao
validador e ao escritor da task responsável, sem limite fixo de tentativas. Tasks válidas
não são reexecutadas por causa da falha de outra task. Um parecer do modelo não torna
um comando que falhou em uma verificação aprovada.

Se o isolamento ou uma ferramenta necessária não estiver disponível com segurança,
não há execução alternativa desprotegida no host. A conclusão registra **limitações de
verificação**, e uma retomada explícita pode verificar novamente sem refazer as tasks.
Não há instalação automática de ferramentas, downloads ou acesso a serviços de produção.
Checks que dependam de infraestrutura externa/credenciais não ficam comprovados por
uma inspeção estática; suas limitações ou bloqueios precisam permanecer explícitos.

## Persistência

```text
.harness/change/
  execucao.lock
  catalogo.lock
  selecao.json
  solicitacoes/
    change-001/
      estado.json
      pedido.md
      investigacao.json
      entrevista.json
      plano.md
      baseline.json    # inventário anterior à escrita
      modelos/
      tasks/
      transacoes/       # cópias privadas e registros de aplicação
      verificacoes/    # comandos, saídas e relatórios reais
      alteracoes.json
      conclusao.md
      execucao.log
    change-002/
```

Não há banco de dados. Cada solicitação mantém seus estados, respostas, plano aprovado,
contadores de tentativas, alterações, evidências, limitações e tempo. Resultados de
solicitações anteriores não são sobrescritos ao criar outra solicitação.

## Arquivos de implementação e integração

Arquivos novos:

- `change_source.py`: leitura localizada, catálogo/busca, sanitização, fingerprints e Git.
- `change_state.py`: solicitações, checkpoints, relógio por solicitação e bloqueio exclusivo.
- `change_protocol.py`: validação dos dados do CHANGE e apresentação do plano.
- `change_prompts.py`: instalação aditiva dos cinco prompts pelo diretório e loader existentes.
- `change_patch.py`: edições restritas e recuperação de aplicações interrompidas.
- `change_verify.py`: cópia isolada e uso do executor técnico existente.
- `change_flow.py`: sequência de investigação, entrevista, aprovação, tasks e realimentação.
- `change_ui.py`: controles, modais, painel e integração visual do novo módulo.
- `prompts/change-{investigate,interview,plan,write,review}.md`: instruções externas editáveis.
- `tests/test_change.py`, `tests/test_change_ui.py`, `tests/test_change_models.py`: aceite,
  integração/regressão e verificação opcional com modelos reais.

Único arquivo anterior modificado: `src/capivara2ptn/app.py`, com 15 linhas adicionadas:
importação/criação do controlador; registro no seletor; seus controles e painel;
carregamento da lista sem execução; e desvios exclusivos para vista, início e indicador
de tempo do CHANGE. O ajuste do seletor em duas linhas fica no controlador novo.
Nenhuma linha dos módulos, prompts, adaptadores, gates, relógios, CSS ou persistências
anteriores foi alterada em relação ao commit de referência.

## Validação

```bash
python -m unittest discover -s tests -v
```

Os testes cobrem aprovação, entrevista, cancelamento, retomada, escopo, hashes,
transações interrompidas, modificações concorrentes, Git/arquivos pendentes, segredos,
realimentação, evidências reais e isolamento. A suíte anterior continua sendo executada.

O teste opcional abaixo usa escritor e auditor reais em projeto sintético temporário,
com verificação funcional efetiva em cópia isolada; requer CLI autenticada e Bubblewrap:

```bash
CAPIVARA_CHANGE_TESTS=1 CAPIVARA_CHANGE_TEST_MODEL=<modelo> \
python -m unittest discover -s tests -p 'test_change_models.py' -v
```
