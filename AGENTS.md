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
- Desenvolver uma etapa por vez. Nesta etapa, o wizard coleta a configuração;
  a execução do `init`, `plan`, `build` e a integração MCP continuam fora do escopo.
