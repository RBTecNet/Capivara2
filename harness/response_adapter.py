"""Decodificação comum às respostas de todos os provedores, sem inferir conteúdo."""
import json


def decode_response(output):
    if isinstance(output, dict):
        return output
    text = output.strip()
    for _ in range(3):
        try:
            value = json.loads(text)
        except ValueError:
            break
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            continue
        raise ValueError('A resposta deve conter um objeto JSON.')
    decoder = json.JSONDecoder()
    candidates = []
    position = 0
    while position < len(text):
        start = text.find('{', position)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            position = start + 1
            continue
        if isinstance(value, dict) and value not in candidates:
            candidates.append(value)
        position = end
    if len(candidates) != 1:
        raise ValueError('Entregue um único objeto JSON final; a resposta está ausente ou é ambígua.')
    return candidates[0]


def contract_feedback(error):
    """Não reenviar dumps de input_value, links e schemas inteiros ao modelo."""
    if hasattr(error, 'errors'):
        return '\n'.join(f"{'.'.join(map(str, item['loc'])) or 'resposta'}: {item['msg']}"
                         for item in error.errors(include_url=False, include_input=False))[:3000]
    return str(error)[:3000]
