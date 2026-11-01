# Retomada do INIT

O botão **Retomar INIT** continua uma execução incompleta usando os arquivos salvos.
Não há execução automática ao abrir a TUI. O pedido original permanece em
`.harness/dados/prompt-original.txt`; o arquivo externo ou MCP usado na primeira
execução não precisa estar disponível para retomar.

| Ponto de interrupção | Retomada |
| --- | --- |
| Pergunta apresentada sem resposta | Reabre a mesma pergunta, sem nova chamada ao modelo. |
| Resposta já confirmada | Continua a análise com o histórico completo, sem perguntar novamente. |
| Entrevista concluída, redação interrompida | Gera somente o PRD. |
| PRD pronto, falha na conexão com o G0 | Reutiliza o PRD e repete somente a avaliação. |
| G0 reprovado | O escritor corrige o PRD com a justificativa e submete novamente ao mesmo G0. |
| G0 aprovado antes de salvar o recibo | Finaliza a conclusão sem repetir chamadas já concluídas. |

Cada retomada é explícita. Outra reprovação preserva o novo parecer para uma próxima
retomada; não há aprovação automática ou relaxamento das regras do G0.
Os seletores atuais de escritor e auditor e a opção de gates continuam sendo usados.
O tempo anterior permanece salvo; somente Refazer INIT confirmado zera o acumulado.

## Persistência e compatibilidade

O checkpoint é `.harness/dados/init-estado.json`. Ele registra a etapa, a pergunta
pendente, a conclusão da entrevista, a situação do G0 e os hashes dos arquivos. Uma
transação curta é gravada antes de substituir cada arquivo, permitindo recuperar
interrupções sem duplicar uma resposta já confirmada.

Os seis arquivos anteriores do INIT mantêm seus nomes e conteúdos usuais. Execuções
antigas são importadas quando os arquivos permitem determinar a etapa com segurança.
PRDs potencialmente parciais são regenerados com a entrevista preservada; PRDs
confirmados por uma chamada concluída são reutilizados. Links, checkpoints inválidos,
pedidos diferentes ou alterações externas são recusados sem sobrescrever os arquivos.

Versões anteriores de PRD, G0 e erros ficam em `.harness/dados/init/historico/`.
Saídas parciais de falhas do provedor ficam em `init/parciais/`, separadas dos resultados
confirmados. O recibo final `init-concluido.json` mantém seu formato existente. PLAN
só pode consumir um novo INIT com checkpoint depois da conclusão e do recibo válido.

O novo prompt Markdown `init-correction.md` é instalado na pasta de prompts editáveis
apenas quando uma correção for necessária. Ele recebe pedido, entrevista, PRD anterior
e os motivos da reprovação. Preserva valores contratuais e distingue seus rótulos visuais.
Os prompts existentes `init.md`, `init-final.md` e `gates/g0-init.md` não são alterados.

## Alterações existentes autorizadas para esta evolução

- `init_flow.py`: reutiliza os checkpoints na execução, mantém a assinatura de `run_init`
  e a entrevista existente, reaproveita etapas concluídas e corrige PRD reprovado ao retomar.
- `app.py`: restaura os controles do INIT incompleto, exibe Retomar INIT e lê o pedido
  salvo na retomada explícita; mantém a conclusão e a transição para PLAN existentes.
- `workspace.py`: preserva o checkpoint e o histórico do INIT ao refazer PLAN ou BUILD.
- `plan_state.py`: recusa consumir um novo checkpoint de INIT sem seu recibo de conclusão;
  a compatibilidade anterior e os executores de PLAN/BUILD permanecem iguais.
- `README.md`: atualiza os trechos que antes informavam ausência de retomada do INIT.

A persistência e a restauração de controles estão em arquivos novos:
`init_resume.py`, `init_resume_ui.py` e `prompts/init-correction.md`.
Não há modificações nos adaptadores, cronômetros, scripts Bash ou regras de G0/G1/G2.

## Verificação

```bash
python -m unittest discover -s tests -v
```

`test_init_resume.py` verifica checkpoints, compatibilidade, interrupções, perguntas,
G0 reprovado, falhas de provedor e integridade dos arquivos. `test_init_resume_ui.py`
exercita a TUI reaberta, a origem salva, a retomada após rejeição, a conclusão e o tempo.

Também existe uma verificação opt-in com escritor e auditor reais, usando exclusivamente
um projeto temporário sintético com valores de contrato distintos dos rótulos:

```bash
CAPIVARA_CONTRACT_TESTS=1 CAPIVARA_CONTRACT_TEST_MODEL=<modelo> \
python -m unittest discover -s tests -p 'test_init_literal_contracts.py' \
  -k rejected_init_resumes -v
```
