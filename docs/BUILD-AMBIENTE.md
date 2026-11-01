# Evidências de preparação do ambiente no BUILD

O contexto das tasks agora inclui os resultados reais das verificações da task de
preparação, com sua origem e seus logs. Antes, uma dependência era apresentada aos
modelos somente com identificador, descrição e relato do escritor. Esse relato é
anterior à execução das verificações e não demonstra seu resultado.

Isso permitia ao G0 repetir uma verificação dentro do ambiente isolado da CLI e
confundir uma restrição desse ambiente com falha do host onde o harness verificou
as dependências. No caso investigado, o executor havia registrado retorno zero para
`firefox --version`, Python e Node; a CLI relatava uma falha de confinamento do Snap.
O BUILD já possuía as evidências corretas, mas não as encaminhava ao G0 da task seguinte.

A correção acrescenta:

- Configuração, resultado e origem da verificação nas dependências diretas de preparação.
- O campo `evidencias_ambiente_execucao`, com preparações concluídas disponíveis mesmo
  quando a dependência é indireta, dados do host e a distinção entre seus ambientes.
- Cópias dos resultados, para que montar o contexto não modifique o estado persistido.

Os comandos, códigos de retorno, saídas e caminhos de logs são preservados. O contexto
não executa comandos, não instala ferramentas, não aprova gates e não transforma falha
em sucesso. A verificação determinística que bloqueia produção sem ambiente preparado
permanece igual. Nenhum PRD, PLAN ou task aprovada é reescrito.

## Arquivos

- Novo `src/capivara2ptn/build_environment.py`: monta o contexto e sua proveniência.
- Alteração localizada em `build_flow.py`: encaminha esse contexto aos modelos.
- Novo `tests/test_build_environment.py`: verifica dependências diretas e indiretas,
  preservação de resultados e bloqueio real mesmo quando um modelo responde aprovado.
- `README.md`: explica a origem e o encaminhamento das evidências.

As operações do executor, os adaptadores, os prompts e os critérios de G0/G1/G2 não
foram alterados. Não há tratamento especial para Firefox, Snap ou uma tecnologia
específica: a evidência é encaminhada para qualquer preparação declarada no PLAN.

A validação incluiu uma chamada real de leitura ao mesmo auditor, com os dados da
task bloqueada e o contexto corrigido: retornou G0 APROVADO. Esse parecer de diagnóstico
não foi aplicado ao projeto; a retomada normal executará seus próprios passos e gates.

```bash
python -m unittest discover -s tests -p 'test_build_environment.py' -v
```
