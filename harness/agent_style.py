"""Modo conciso comum aos agentes, sem cortes de conteúdo nem limites de rejeição."""
import json
from copy import deepcopy


CONCISE_STYLE = """CAPIVARA_AGENT_STYLE=concise
Responda JSON compacto. Sem introdução, narrativa ou exposição do raciocínio.
Aprovação: summary="OK" quando esse campo existir. Use somente os campos do schema.
Rejeição: só defeito funcional, local/requisito, correção exata e aceite verificável.
summary/reason/observed: uma frase direta. Não recapitule o pedido nem liste checagens aprovadas.
Ignore grafia, estilo e apresentação sem efeito funcional; não produza achados editoriais.
Nos artefatos, escreva ações objetivas sem repetir escopo/stack em cada item.
Preserve detalhes necessários, comandos, código, evidências literais e clareza ao usuário.
Concisão não autoriza omissão de requisitos nem aprovação sem análise técnica.
"""


def styled(prompt):
    return prompt if 'CAPIVARA_AGENT_STYLE=concise' in prompt else CONCISE_STYLE+'\n'+prompt


def compact_schema(value):
    # title é anotação automática do Pydantic, não um requisito do contrato.
    if isinstance(value, dict):
        return {k:(deepcopy(v) if k in ('const','enum','default','examples') else compact_schema(v))
                for k,v in value.items() if not (k=='title' and isinstance(v,str))}
    if isinstance(value, list):return [compact_schema(v) for v in value]
    return value


def render_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))
