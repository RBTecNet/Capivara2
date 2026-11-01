"""Restaura somente os controles do INIT incompleto; não inicia execução."""

from textual.widgets import Button, Input, Select
from capivara2ptn.init_resume import pending_init_path


def restore_pending_init(app) -> None:
    path = pending_init_path(app.working_directory)
    if path is None or app._init_saved:
        return
    app.query_one('#prompt-source', Select).value = 'file'
    app.query_one('#prompt-path', Input).value = str(path)
    app._init_header = '● Interrompido'
    app._init_state_text = 'Estado: INIT incompleto'
    app._init_stage_text = 'Pedido e respostas preservados. Pressione Retomar INIT para continuar da etapa pendente.'


def refresh_init_resume_button(app) -> None:
    from textual.widgets import RadioSet
    if not app.query('#module') or not app.query('#start'):
        return
    button = app.query_one('#module', RadioSet).pressed_button
    if button and button.id == 'module-init':
        app.query_one('#start', Button).label = ('Refazer INIT' if app._init_saved else
            'Retomar INIT' if pending_init_path(app.working_directory) else 'Iniciar')
