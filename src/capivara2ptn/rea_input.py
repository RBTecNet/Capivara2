"""Cópia de entrada quando o destino está dentro do diretório selecionado."""

import asyncio
import os
import shutil
import tempfile
import threading
from pathlib import Path

from capivara2ptn.rea_common import ReaError
from capivara2ptn.rea_state import fingerprint


async def prepare_input(store,log):
    original=Path(store.state['target'])
    existing=store.state.get('analysis_target')
    if existing and Path(existing).exists():return Path(existing)
    if not original.is_dir() or not store.base.is_relative_to(original):
        store.state['analysis_target']=str(original);store.save();return original
    # Evita analisar os próprios logs/bundles que mudam durante a investigação.
    stop=threading.Event();destination=store.path('entrada')
    if destination.exists():
        if await asyncio.to_thread(fingerprint,destination,store.project)==store.state['fingerprint']:
            store.state['analysis_target']=str(destination);store.save();return destination
        raise ReaError('Uma cópia de entrada anterior está inconsistente; use Nova investigação para preservá-la e preparar outra.')
    log('Preparando cópia de entrada, sem os controles .harness do projeto.')
    def copy():
        def ignore(parent,names):return ([name for name in names if name=='.git']+
            (['.harness'] if Path(parent)==store.project and '.harness' in names else []))
        def regular(source,target):
            if stop.is_set():raise ReaError('Preparação da entrada interrompida.')
            fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as reader,open(target,'wb') as writer:
                while chunk:=reader.read(512*1024):
                    if stop.is_set():raise ReaError('Preparação da entrada interrompida.')
                    writer.write(chunk)
            shutil.copystat(source,target,follow_symlinks=False)
            return target
        with tempfile.TemporaryDirectory(prefix='.entrada-',dir=store.base) as temporary:
            target=Path(temporary)/'alvo'
            shutil.copytree(original,target,symlinks=True,ignore=ignore,copy_function=regular)
            if stop.is_set():raise ReaError('Preparação da entrada interrompida.')
            target.rename(destination)
    task=asyncio.create_task(asyncio.to_thread(copy))
    try:await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        try:await task
        except Exception:pass
        raise
    store.state['analysis_target']=str(destination);store.save();return destination
