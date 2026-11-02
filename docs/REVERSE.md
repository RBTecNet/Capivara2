# REVERSE

Módulo adicional de investigação: **REVERSE → INIT → PLAN → BUILD**.
O REVERSE documenta o legado e gera uma especificação para reconstrução; não implementa
nem refatora a aplicação.

## Uso

1. Crie o projeto novo e execute `capivara2ptn` dentro dele.
2. Selecione **REVERSE**, o escritor e, se desejado, um auditor independente pelos seletores existentes.
3. Use **Selecionar projeto legado**. O seletor permite navegar para cima ou informar um caminho.
4. Pressione **Iniciar**. Revise os domínios propostos no editor e use **Aprovar domínios**.
5. Acompanhe etapas, domínios, unidades investigadas, logs e tempo efetivo. **Interromper** preserva os checkpoints.
6. Após reabrir a TUI, selecione REVERSE e **Retomar**. Nenhuma investigação inicia ao abrir o projeto.
7. Ao concluir, **Continuar para INIT** seleciona a origem Arquivo e preenche o caminho do prompt. A entrevista exige pressionar Iniciar normalmente.

O cronômetro pertence ao REVERSE e fica no seu estado; não altera os tempos ou o formato
de persistência dos módulos anteriores. Espera por aprovação não conta como execução.

**Verificar alterações** compara os hashes do legado com a investigação concluída.
Antes de invalidar resultados, apresenta arquivos e domínios afetados para confirmação.
Dependentes declarados, referências de análise e infraestrutura compartilhada propagam
invalidações. Arquivos novos voltam à classificação. Resultados válidos são reutilizados.
As referências cruzadas dependem dos indícios identificados no código; alterações de
configurações sensíveis ou recursos sem domínio conhecido invalidam conservadoramente
todos os domínios. A saída anterior é arquivada quando há atualização.

**Preparar reexecução** informa quais resultados serão substituídos e pede confirmação.
O arquivamento só ocorre ao pressionar **Iniciar reexecução**, dentro de
`.harness/reverse/historico/`. Artefatos de INIT, PLAN e BUILD não são modificados. Se o
INIT já utilizou o resultado anterior, executar REVERSE novamente não atualiza os módulos posteriores.

## Artefatos

Tudo é salvo no projeto novo, inclusive os prompts Markdown editáveis do REVERSE.
Esses prompts são lidos novamente em cada chamada e preservados ao preparar uma reexecução:

```text
.harness/reverse/
  estado.json
  inventario.json
  prompts/reverse-discovery.md
  prompts/reverse-discovery-protocol.md
  prompts/reverse-investigation.md
  prompts/reverse-investigation-protocol.md
  dominios/<id>.md
  evidencias/<unidade>.json
  evidencias/descoberta/<lote>.json  # classificação recusada e realimentação
  evidencias/pendentes/<unidade>.json  # diagnóstico e reexame, quando necessário
  evidencias/verificacao-<n>.txt  # quando solicitada
  documentacao/visao-geral.md
  documentacao/cobertura.json
  prompt-final.md
  historico/                    # reexecuções e prompts anteriores
```

A especificação final incorpora os resultados de todos os domínios, rotinas, regras,
fluxos, interfaces, entidades, relações, incertezas e limitações documentadas. As
referências de código são evidências, não instruções para carregar outros documentos.
Não há uma síntese final pelo modelo que possa eliminar requisitos de domínios anteriores.

## Segurança e investigação incremental

- Resolve caminhos reais e recusa diretórios iguais, sobrepostos ou do próprio harness.
- Lê arquivos regulares com `openat`/`O_NOFOLLOW`; não segue links no legado.
- Não usa o legado como diretório de trabalho das CLIs. Os adaptadores existentes
  recebem apenas trechos sanitizados em modo textual, sem ferramentas ou agentes adicionais.
- Configurações sensíveis são registradas sem seus valores. Dependências instaladas,
  caches e compilados têm exclusões justificadas por árvore. Recursos binários são
  inventariados e registrados como limitação da análise estática.
- Markdown, comentários e outros conteúdos do legado são dados não confiáveis.
- Há sanitização de credenciais reconhecíveis e dos valores sensíveis de arquivos `.env`.
  Valores públicos de configuração, como ambiente, nível de log e porta, não são
  tratados automaticamente como senhas. Caminhos e hashes verificados do inventário
  mantêm sua identidade nas referências, sem substituir fragmentos por `[OCULTO]`.
  Valores opacos sem identificação semântica podem exigir classificação humana;
  revise os documentos antes de compartilhá-los fora do projeto.
- O inventário precisa terminar antes da descoberta. A descoberta usa lotes de metadados
  e amostras; a investigação aprovada lê **todos** os trechos textuais relevantes.
- Na descoberta, `allowed_paths` informa os caminhos exatos do lote. Arquivos fora
  desse conjunto e domínios sem arquivos continuam inválidos. O mesmo investigador
  recebe a resposta recusada e o motivo para corrigir a classificação, sem limite
  fixo de tentativas. Diagnóstico e progresso ficam salvos em `evidencias/descoberta/`;
  a retomada conserva os lotes já válidos e o reexame pendente.
- Respostas precisam justificar cobertura e evidências com arquivo e intervalo de linhas.
  Rotinas não compreendidas exigem limitação explícita. Inferências ficam separadas dos fatos.
- O intervalo investigado define a cobertura da unidade; continuidade de rotinas e
  dependências recebem trechos adicionais limitados ao contexto disponível. O modelo
  pode solicitar arquivos e linhas pelo campo `context_requests`. Evidências adicionais
  só são aceitas quando os respectivos trechos foram fornecidos.
- Respostas com cobertura incompleta preservam diagnóstico e achados parciais em
  `evidencias/pendentes/`, recebem a justificativa anterior e são reexaminadas pelo mesmo
  investigador. A unidade permanece pendente até uma resposta válida. Cancelamentos
  preservam o reexame para retomada. Impedimentos externos reais exibem o motivo e a
  ação necessária; erros de formato e evidências inválidas também recebem realimentação para correção, sem contabilizar cobertura indevida.
- O protocolo complementar é instalado de forma aditiva, preservando prompts já
  editados no projeto, inclusive em investigações iniciadas em versões anteriores.
- Há checkpoints atômicos por unidade. Falhas de provedor/formato permitem novas tentativas
  por Retomar, sem limite fixo e sem repetir unidades concluídas e válidas.
- Alterações no legado durante a análise impedem a entrega de um prompt desatualizado.

O auditor selecionado pode reexaminar unidades com rotinas não compreendidas; não há
gates novos. A opção global de proibição de testes não é aplicada às verificações
investigativas do REVERSE e mantém seu comportamento nos módulos anteriores.

## Verificações práticas opcionais

Informe um comando no campo de verificação antes de iniciar/retomar, se desejar confirmar
um comportamento. O comando é executado a cada início/retomada em que o campo estiver preenchido; não há
limite de verificações ou tentativas. Exemplos: `python3 main.py --help` ou um comando de testes
já presente no legado.

A execução requer **Bubblewrap** (`bwrap`) já instalado. Não há instalação automática.
O REVERSE copia somente os arquivos textuais sanitizados para uma pasta temporária sob
seu próprio diretório, sem `.env`, links, bancos ou dependências instaladas. A origem não
é montada no sandbox. O processo não recebe o ambiente do usuário, não tem rede e não
acessa diretórios pessoais ou sockets do host. Apenas `/usr`, binários e bibliotecas são
montados em leitura; a cópia descartável é o único projeto gravável. A cópia é removida
ao terminar ou interromper. Nenhum serviço de produção deve ser apontado pelo comando.

Ferramentas, navegadores, dependências ou serviços ausentes nessa cópia podem impedir
uma verificação. A saída e a falha ficam documentadas; a investigação estática continua.
Fluxos web interativos com Playwright e integrações que exigem rede/serviços não são
provisionados automaticamente nesta versão. A indisponibilidade de confirmação prática
fica explícita no prompt para o INIT.

## Preservação da versão funcional

Referência: `cd772d2374b96ef29e0288d83366efa0bb045772`, branch original `master`.
Implementação: branch `feature/reverse`.

Arquivos novos:

- `src/capivara2ptn/reverse_source.py`: validação de diretórios, inventário, leitura e sanitização.
- `src/capivara2ptn/reverse_state.py`: checkpoints, histórico e cronômetro exclusivo.
- `src/capivara2ptn/reverse_prompts.py`: instalação e leitura dos prompts novos.
- `src/capivara2ptn/reverse_flow.py`: descoberta, investigação, cobertura e consolidação.
- `src/capivara2ptn/reverse_verify.py`: verificações em cópia isolada.
- `src/capivara2ptn/reverse_ui.py`: controles, painéis, modais e executor independente.
- `src/capivara2ptn/prompts/reverse-discovery.md`: instruções editáveis de descoberta.
- `src/capivara2ptn/prompts/reverse-investigation.md`: instruções editáveis de investigação.
- `src/capivara2ptn/prompts/reverse-investigation-protocol.md`: escopo da unidade, continuidade e reexame incremental.
- `tests/test_reverse.py`: aceite do inventário, estado e executor.
- `tests/test_reverse_ui.py`: integração e regressão da TUI.
- `docs/REVERSE.md`: instruções de uso, segurança e relatório de integração.

Na entrega inicial do REVERSE, o único arquivo existente alterado foi: `src/capivara2ptn/app.py`, com inserções localizadas:

| Integração | Motivo |
| --- | --- |
| Importação e criação de `ReverseController` | Manter estado e execução do novo módulo separados. |
| RadioButton REVERSE | Registrar o módulo no seletor existente. |
| `ReverseControls` e `ReversePanel` | Exibir os controles/painel próprios; os cards anteriores não são reescritos. |
| `reverse.mount()` | Carregar estado do REVERSE sem iniciar execução. |
| Desvio de `update_elapsed` quando REVERSE está selecionado | Exibir seu cronômetro sem mudar `ModuleClock` ou configuração existente. |
| Desvio de `apply_module_view` para REVERSE | Selecionar exclusivamente a sua vista; as vistas anteriores seguem o código original. |
| Desvio de `start_pressed` para REVERSE | Acionar seu executor independente; INIT, PLAN e BUILD seguem o despacho original. |

Na entrega inicial do REVERSE, nenhum adaptador, gate, prompt anterior, executor existente, arquivo de configuração,
script Bash, dependência ou mecanismo de entrevista foi alterado. O preenchimento do
arquivo do INIT usa os controles existentes e não inicia execução.

A evolução posterior solicitada para retomada do INIT está documentada em
[INIT-RETOMADA.md](INIT-RETOMADA.md), com seus pontos de integração específicos.

## Validação

```bash
python -m unittest discover -s tests -v
python -m compileall -q src/capivara2ptn
```

Os testes usam projetos temporários, respostas de modelo simuladas e a TUI real em modo
headless. Exercitam isolamento, sanitização, evidências, cobertura, falhas, cancelamento,
retomada, atualização seletiva, preservação dos módulos anteriores e transição para INIT.
A verificação em Bubblewrap é exercitada quando disponível; sua ausência/falha é
registrada, sem executar o comando no host. Não são necessárias chamadas pagas a modelos.
