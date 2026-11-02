"""Instala somente as instruções REA, pelo loader de prompts já existente."""
from importlib.resources import files
from capivara2ptn.prompt_files import load_prompt,prompt_directory


def rea_prompt():
    name='rea-investigate.md';directory=prompt_directory();marker=directory.parent/'.rea-prompts-installed'
    if not marker.exists():
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            with (directory/name).open('x',encoding='utf-8') as stream:stream.write(files('capivara2ptn').joinpath('prompts',name).read_text())
        except FileExistsError:pass
        marker.touch()
    return load_prompt(name)
