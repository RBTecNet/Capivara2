# Regras de trabalho neste projeto

- Após cada rodada com implementação ou correção, validar a mudança, executar
  `./scripts/install-local.sh` e criar um commit Git local. Essas etapas são
  obrigatórias e já estão autorizadas pelo usuário; não esperar novo pedido.
- A instalação deve deixar o comando `capivara2` disponível para o usuário testar
  em qualquer pasta, preservando a detecção da pasta de trabalho do terminal.
- Conferir o conteúdo do commit e o estado final do Git. Não incluir `.venv`,
  caches, credenciais ou arquivos temporários.
- Informar ao usuário como testar e o identificador do commit da rodada.
- Manter o repositório somente local. Não adicionar remotos nem publicar ou
  enviar commits até que o usuário autorize explicitamente.
- Desenvolver uma etapa por vez. Implementar somente o `init` documental agora;
  `plan` e `build` ficam para etapas posteriores.
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
