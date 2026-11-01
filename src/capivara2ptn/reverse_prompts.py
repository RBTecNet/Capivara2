"""Prompts editáveis do REVERSE ficam exclusivamente no projeto de destino."""

from importlib.resources import files

from capivara2ptn.prompt_files import PromptError

NAMES = ('reverse-discovery.md', 'reverse-investigation.md', 'reverse-investigation-protocol.md')


def load_reverse_prompt(name: str, store) -> str:
    if name not in NAMES:
        raise PromptError('Prompt do REVERSE desconhecido.')
    # O novo complemento é aditivo: preserva prompts já instalados/editados nos projetos.
    protocol = name == 'reverse-investigation-protocol.md'
    marker = store.target('prompts/.protocol-installed' if protocol else 'prompts/.installed')
    if not marker.exists():
        for item in ((name,) if protocol else NAMES[:2]):
            if not store.target('prompts/' + item).exists():
                store.write('prompts/' + item, files('capivara2ptn').joinpath('prompts', item).read_text(encoding='utf-8'))
        store.write('prompts/.installed', '1\n')
    path = store.target('prompts/' + name)
    try:
        text = path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        raise PromptError(f'Prompt do REVERSE ausente ou não legível: {path}.') from None
    if not text.strip():
        raise PromptError(f'Prompt do REVERSE vazio: {path}.')
    return text
