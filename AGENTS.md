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
- Manter o repositório somente local. Não adicionar remotos nem publicar ou
  enviar commits até que o usuário autorize explicitamente.
- Desenvolver uma etapa por vez. Implementar `init` e `plan` documentais;
  `build` fica para uma etapa posterior.
- O plan exige init aprovado e íntegro, preserva suas fases e divide cada fase
  em tasks pequenas. Cada chamada começa sem histórico; o escritor recebe só
  uma task. Revisar tasks, fases e plano completo até aprovação explícita.
- O plan só compara documentação com a origem. Não executar testes nem verificar
  ambiente, gerar código ou prescrever testes de validação ao build. A fase 0
  documenta stack e dependências de desenvolvimento e testes já definidas no init.
- O plan descreve implementação futura, sem executá-la. Preservar infraestrutura
  explicitamente pedida; não confundir isso com prescrever estratégias de testes.
- Atividades explícitas de QA herdadas do init ficam rastreadas como responsabilidade
  do build, sem gerar roteiros de testes no plan. Validação funcional de campos e
  preparação da infraestrutura continuam nas tasks. A fase 0 somente cataloga
  tecnologias e ferramentas; suas finalidades não são ordens de executar testes.
- Tasks declaram responsabilidades por IDs e recortes literais dos requisitos.
  Revisar cada task apenas em seu escopo e conferir cobertura coletivamente.
  Remover, dividir e consolidar são operações estruturadas e validadas em lote
  antes de novas redações. Não enviar remoção como uma instrução ao escritor.
- Repetições sem avanço acionam reavaliação documental da fase. Registrar pendências,
  versões e decisões de escopo explícitas, mantendo chamadas sem histórico e sem
  aprovação automática por número de tentativas.
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
