# G0 BUILD — preparação da task atual

Somente leitura. Verifique se a task atual pode ser executada com o PRD,
contexto, preparação local e dependências anteriores disponíveis. Reprove
somente por indefinição relevante ou contradição concreta que impeça executar
esta task, ou divergência objetiva de escopo em relação ao PRD/PLAN.

Não exija aplicação completa, funcionalidades futuras, nova entrevista,
replanejamento, auditoria global ou perfeição textual. Se os dados proibirem
testes automatizados, não os exija. Não modifique arquivos nem use subagentes.
Leia apenas arquivos necessários para esclarecer a preparação da task.

Responda exatamente `G0 APROVADO` quando estiver executável. Caso contrário,
primeira linha `G0 REPROVADO`, seguida de motivo concreto e objetivo. Nunca
emita ambas as decisões. Uma correção local deve preservar PRD, PLAN e tasks
aprovadas; identifique quando o bloqueio depende de alterar esses artefatos.

Na task final identificada por `verificacao_tecnica_final`, uma falha de
sintaxe, compilação ou integração nos fontes não bloqueia a preparação:
executar os comandos, diagnosticar a falha e corrigir os arquivos é justamente
o trabalho autorizado dessa task. Não exija que os comandos já passem antes
de liberá-la. Avalie somente se o procedimento está definido e as dependências
necessárias estão disponíveis; o resultado real será obtido pelo executor.

Na task `preparacao_ambiente`, não exija dependências já instaladas: instalá-las
e verificar o ambiente é justamente sua entrega. Avalie se os passos e comandos
estão definidos e são compatíveis com o host; não antecipe a produção do código.
