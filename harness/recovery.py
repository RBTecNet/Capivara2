"""Recibos duráveis de operações: repetir o controlador não repete trabalho confirmado."""

import json
import re

from .artifacts import digest


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


class OperationJournal:
    def __init__(self, store, namespace):
        self.store = store
        self.namespace = namespace
        self.root = 'control/operations'

    def path(self, key):
        return f'{self.root}/{key}.json'

    def key(self, role, operation, data, schema):
        return fingerprint({'namespace': self.namespace, 'role': role, 'operation': operation,
                            'data': data, 'schema': schema})

    def read(self, key):
        return self.store.read_json(self.path(key), {})

    def start(self, key, **metadata):
        previous = self.read(key)
        if 'operation_number' not in previous:
            sequence=self.store.read_json('control/sequence.json',{'call_id':0})
            sequence['operations']=sequence.get('operations',0)+1
            self.store.json('control/sequence.json',sequence)
            previous['operation_number']=sequence['operations']
        record = {**previous, **metadata, 'key': key, 'namespace': self.namespace, 'status': 'running'}
        self.store.json(self.path(key), record)
        return record

    def finish(self, key, value, **metadata):
        record = {**self.read(key), **metadata, 'status': 'completed', 'value': value.model_dump()}
        record['value_sha256'] = fingerprint(record['value'])
        self.store.json(self.path(key), record)

    def failed(self, key, error):
        record = {**self.read(key), 'status': 'interrupted', 'error': str(error)}
        self.store.json(self.path(key), record)

    def abandon(self,key,error):
        record={**self.read(key),'status':'invalidated','error':str(error),'response':None}
        self.store.json(self.path(key),record)

    def count(self,name,key=None):
        sequence=self.store.read_json('control/sequence.json',{'call_id':0})
        record=self.read(key) if key else {}
        if not key or not record.get('counted_'+name):
            sequence[name]=sequence.get(name,0)+1
            self.store.json('control/sequence.json',sequence)
            if key:
                record['counted_'+name]=True
                self.store.json(self.path(key),record)
        return sequence.get(name,0)

    def metrics(self):
        return self.store.read_json('control/sequence.json',{})

    def cached(self, key, model):
        record = self.read(key)
        if (record.get('status') == 'completed' and record.get('namespace') == self.namespace
                and fingerprint(record.get('value')) == record.get('value_sha256')):
            return model.model_validate(record['value'])
        return None

    def next_call(self, directory='context', prefix='call-'):
        # A sequência é gravada antes de iniciar o processo; não sobrescrever logs após falha.
        counter = self.store.read_json('control/sequence.json', {'call_id': 0})
        if not counter['call_id']:
            existing = self.store.path(directory)
            ids = [int(p.stem.removeprefix(prefix)) for p in existing.glob(prefix+'*.json')
                   if p.stem.removeprefix(prefix).isdigit()] if existing.exists() else []
            for path in self.store.path('log').glob('*.response.txt'):
                match=re.fullmatch(r'(?:writer|reviewer)-(\d+)\.response\.txt',path.name)
                if match:ids.append(int(match[1]))
            counter['call_id'] = max(ids, default=0)
        counter['call_id'] += 1
        self.store.json('control/sequence.json', counter)
        return counter['call_id']
