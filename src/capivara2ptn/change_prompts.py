"""Instalação aditiva dos prompts CHANGE pelo mecanismo de prompts existente."""

from importlib.resources import files

from capivara2ptn.prompt_files import PromptError, load_prompt, prompt_directory

NAMES = ('change-investigate.md', 'change-interview.md', 'change-plan.md', 'change-write.md', 'change-review.md')


def change_prompt(name):
    if name not in NAMES:
        raise PromptError('Prompt CHANGE desconhecido.')
    directory = prompt_directory()
    marker = directory.parent / '.change-prompts-installed'
    if not marker.exists():
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            for item in NAMES:
                try:
                    with (directory / item).open('x', encoding='utf-8') as stream:
                        stream.write(files('capivara2ptn').joinpath('prompts', item).read_text(encoding='utf-8'))
                except FileExistsError:
                    pass
            marker.touch()
        except OSError:
            raise PromptError('Não foi possível instalar os prompts exclusivos do CHANGE.') from None
    return load_prompt(name)
