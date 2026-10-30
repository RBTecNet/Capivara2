# Regras de trabalho neste projeto

- Após cada rodada com implementação ou correção, validar a mudança, executar
  `./scripts/install-local.sh` e criar um commit Git local. Essas etapas são
  obrigatórias e já estão autorizadas pelo usuário; não esperar novo pedido.
- A instalação deve deixar o comando `capivara2` disponível para o usuário testar
  em qualquer pasta, preservando a detecção da pasta de trabalho do terminal.
- Conferir o conteúdo do commit e o estado final do Git. Não incluir `.venv`,
  caches, credenciais ou arquivos temporários.
- Informar ao usuário como testar e o identificador do commit da rodada.
- A identidade visual do dashboard é CAPIVARA; o comando instalado continua
  `capivara2`. Cada módulo deve exibir seus próprios dados operacionais reais.
  Não copiar métricas fictícias da referência visual; indicar "não medido"
  quando uma informação ainda não for coletada.
- O mascote principal é o PNG original fornecido pelo usuário, sem redesenhar
  nem alterar o arquivo. Usar o ANSI fornecido `build-busto-40x13.ans`, com
  ajuste proporcional à área disponível, como fallback automático
  quando o terminal não oferecer suporte a imagens nativas.
- O usuário autorizou configurar e enviar commits para o remoto
  `git@github.com:RBTecNet/Capivara2.git`. Não publicar aplicações nem outros
  repositórios sem autorização.
- `init` e `plan` são documentais. O `build` executa preparação do ambiente e
  implementação do plano aprovado, com gates G0–G3 por task.
- Build pode instalar dependências e executar CLIs sem confirmações, conforme
  autorização explícita do usuário. Bloqueios de instalação interrompem o build
  com pacotes exatos e script/instruções para recuperação manual.
- Paralelismo exige isolamento, escopos de leitura/escrita sem conflitos e
  integração validada. Escopos incertos são serializados. Nunca publicar/remotar.
- G1 confere existência; G2 fidelidade à task; G3 funcionalidade mínima da task,
  sem testes estéticos ou suítes gerais desnecessárias. O critério é o resultado
  funcional pedido, não a técnica de implementação ou o formato do teste. Reutilizar
  runners existentes, aceitar testes diretos sem wrappers/citações literais e
  registrar evidências reais de execução.
- Aplicação produzida pelo build fica na raiz selecionada, com README de
  instalação/execução. Estado, logs, checks e cópias temporárias ficam em .harness.
- A prova de instalação usa runtime temporário fora da árvore do projeto para
  impedir resolução de node_modules/venv ancestrais. Seu identificador, checkpoints,
  logs e comprovantes persistentes continuam em .harness; não reutilizar runtimes
  já preparados como evidência de instalação limpa.
- Ao concluir as tasks, conferir instalação, build declarado e smoke curto de
  inicialização. Typecheck separado é diagnóstico informativo quando a aplicação
  compila e executa; registrar sua falha sem reprovar funcionamento. Não impor strict,
  instanceof, ausência de any/casts ou configurações preferidas. Aceitar receitas de
  instalação alternativas explícitas, documentadas e executadas em cópia limpa.
- Reparos operacionais em dependências/configuração/testes usam os comandos reais
  do G3 como aceite, sem aprovação textual extra. Alterações no código de negócio
  continuam com revisão de fidelidade, recebendo conteúdo base/atual, diff e hashes.
  Falta de contexto do revisor deve ser resolvida pelo controlador, sem obrigar
  o escritor a refazer código correto; distinguir falhas históricas da fonte atual.
- Se o código estiver concluído e SOMENTE a instalação de dependências externas
  estiver pendente, marcar build.status=completed com delivery_status=blocked e
  ready_to_run=false. A TUI mostra "Concluído — instalação pendente" e oferece
  validar instalação sem refazer tasks. Não usar essa conclusão para erro de código,
  configuração ou compilação ainda sem correção.
- README deve listar pacotes e versões dos manifestos, pré-requisitos de sistema,
  comandos explícitos de instalação/manual e os comandos operacionais conferidos.
  Não declarar instalação aprovada sem resultados reais; preservar pendências e
  scripts manuais quando a instalação automática não for possível.
- O plan exige init aprovado e íntegro, preserva suas fases e divide cada fase
  em tasks pequenas. Cada chamada começa sem histórico; o escritor recebe só
  uma task. Tasks e fases exigem aprovação explícita; a conclusão certifica essas
  aprovações e a cobertura completa, sem reabrir conteúdo por uma revisão livre.
- O plan só compara documentação com a origem. Não executar testes nem verificar
  ambiente, gerar código ou prescrever testes de validação ao build. A fase 0
  documenta stack e dependências de desenvolvimento e testes já definidas no init.
- O plan descreve implementação futura, sem executá-la. Preservar infraestrutura
  explicitamente pedida; não confundir isso com prescrever estratégias de testes.
- O plan não determina o que o build deve ou não testar. Atividades de QA herdadas
  ficam rastreadas semanticamente como responsabilidade do build, sem gerar tasks,
  roteiros ou limites a testes, inclusive os não citados. Validação funcional de campos e
  preparação da infraestrutura continuam nas tasks. A fase 0 somente cataloga
  tecnologias e ferramentas; suas finalidades não são ordens de executar testes.
- Tasks declaram responsabilidades por IDs e recortes literais dos requisitos.
  Revisar cada task apenas em seu escopo e conferir cobertura coletivamente.
  Remover, dividir e consolidar são operações estruturadas e validadas em lote
  antes de novas redações. Não enviar remoção como uma instrução ao escritor.
- A classificação semântica é aprovada antes da redação; referências mistas devem
  ser separadas sem omitir texto. Revisões posteriores não mudam os responsáveis.
- Devoluções exigem versão, evidência, alterações exatas e aceite verificável. Cada
  conflito tem uma aplicação pelo escritor; não reenviar o mesmo erro nem contrariar
  aceites vigentes. Reincidência/contradição é falha diagnosticada de contrato, sem
  aprovação por insistência. Erros de formato são recuperados na mesma operação,
  sem descartar aprovações, com espera cancelável e sem limite fixo de tentativas.
- A camada comum adapta JSON simples, blocos Markdown, envelopes e respostas nativas
  dos CLIs sem usar outro LLM como tradutor. Não adivinhar JSON ambíguo, aprovação ou
  evidência. Preservar saída original e resposta canônica para auditoria.
- No plan, revisores retornam findings com IDs e edições find/replace. O controlador
  preenche versões atuais, bytes before, citações e aceites verificáveis. Separar
  observações editoriais não bloqueantes de falhas funcionais; contradições do revisor
  voltam ao mesmo papel, sem refazer tasks aprovadas. Cada chamada recebe o recorte
  necessário da origem, mantendo pedido integral e histórico em disco.
- Preservar campos não alterados, decisões vigentes e aprovações por versão.
  Reavaliar estrutura apenas com operações concretas ou decisão específica,
  mantendo chamadas sem histórico e sem aprovação automática por tentativas.
- Plan e build sempre retomam o progresso compatível salvo em disco, inclusive
  no botão tentar novamente. Nunca apagar trabalho ao repetir o módulo. Init é
  a exceção de reinicialização explícita. Mudança de origem deve preservar histórico.
- Falhas de agentes/comandos são recuperáveis na TUI. Persistir checkpoints,
  respostas e operações de integração antes de avançar gates; aprovações não
  podem ser inferidas por retomada. Modelos OpenCode vêm do CLI autenticado.
- OpenCode, Claude e AGY usam os protocolos dos CLIs instalados e catálogos
  consultados sem inferência. Não inventar modelos ou efforts. Respostas estruturadas
  prevalecem sobre texto duplicado; erros nativos nunca contam como resposta válida.
- Definir cwd e PWD do processo filho para a mesma pasta do workspace. Configuração
  documental do OpenCode é efêmera e explícita no ambiente, com ferramentas negadas.
  Credenciais herdadas ficam somente no ambiente; não exportar para arquivos ou logs.
- O projeto antigo NovoHarness/capivara serve apenas de consulta de wizard/chamadas
  aos modelos, conforme autorização do usuário. Não importar seu código ou arquitetura.
- No build, erros recuperáveis de contrato, citação, comunicação e reserva não
  encerram o módulo nem dependem do botão tentar novamente. Corrigir automaticamente
  com evidência, logs e espera cancelável, sem limite de tentativas nem aprovação
  por insistência. Cancelamento explícito e bloqueios reais de instalação continuam
  preservando o progresso. Plan também recupera respostas inconsistentes automaticamente.
- Arquivos adicionais observados em uma task exigem reserva exata e execução
  exclusiva: drenar os workers, preservar/conciliar suas versões e passar G1–G3.
  Ampliar reserva não adiciona requisitos nem aprova código fora da task original.
- Escritor e revisor podem pedir scope_requests antes de alterar um arquivo.
  Falta de reserva não deve obrigar o escritor a gerar patches não aplicados nem
  repetir pedidos de autorização que só o harness pode resolver.
- Duas rejeições seguidas no mesmo gate, ausência de mudança na correção ou
  contratos repetidamente inválidos exigem diagnóstico em contexto novo, com
  histórico persistido. Diagnóstico resolve a causa e nunca aprova uma task.
- Falha G3 é diagnosticada antes de culpar o código. Checks válidos permanecem
  registrados; erro do próprio check pode ser reparado com revisão independente,
  preservando o comportamento solicitado sem exigir a técnica ou estrutura antiga,
  seguido de execução real.
  Arquivar versões, certificado e resultados; reparo de check não aprova gates.
- O brief executável deriva de TaskContent e indica donos das demais unidades.
  Citações genéricas a testes na seed não exigem suítes no G0. Usar verificações
  mínimas da unidade; não repetir toda a aplicação/fundação nem avaliar estética.
- O finish do AGY é protocolo de resposta estruturada quando seus parâmetros
  correspondem ao structured_output de um resultado SUCCESS. Não confundir esse
  retorno com uso de ferramentas; outros eventos de ferramentas continuam negados.
- Todo artefato e status de módulos fica em `.harness` no projeto selecionado:
  logs, entrevistas (`enterviews`), prompts, contexto e controle. A aplicação
  futura pertence à raiz do projeto; todo o estado do harness fica em `.harness`.
- O init somente gera e valida documentação: nunca verificar ferramentas do
  ambiente, executar testes da aplicação ou gerar código da aplicação.
- O revisor preserva o pedido original, identifica lacunas e verifica viabilidade.
  O escritor entrevista e redige. Repetir até aprovação explícita do revisor.
- Entrevistas são de múltipla escolha com exatamente uma opção livre e uma
  recomendação justificada pela finalidade do pedido. Perguntar stack e
  ferramentas do projeto quando indefinidas; inferir ferramentas internas.
- Suítes/frameworks de testes e automação de navegador são decisões internas do
  harness, inclusive quando o pedido exige testes. Não entrevistar essas escolhas;
  registrar a inferência e incorporá-la à fase 0. Erros de citação do revisor não
  podem gerar nova entrevista sobre decisões já tomadas.
- O prompt final sempre contém fase 0 de stack e ferramentas e fases posteriores
  de implementação. Só marcar init concluído após aprovação do prompt final.
- Não fixar endpoints ou tenants MCP no código. Preservar o isolamento entre
  instâncias e nunca incluir chaves de autenticação em commits ou mensagens.
- O projeto doc-center é fonte de consulta. O usuário determinou preservar suas
  listagens e seu contrato atuais, sem alterações naquele projeto.
