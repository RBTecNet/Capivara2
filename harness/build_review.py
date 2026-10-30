"""Referências verificáveis para o auditor citar a origem e o código sem transcrever."""

from .build_workspace import relative_path


def requirement_quotes(data):
    task=data['task']
    return list(dict.fromkeys(q for q in [task['seed']['scope'],task['content']['objective'],
        *task['content']['implementation_steps'],*task['content']['deliverables'],
        *task['seed'].get('source_quotes',[]),*data.get('requirements',[]),
        data.get('objective',''),*data.get('constraints',[])] if q))


def evidence_catalog(data,cwd):
    requirements={f'Q{i:03d}':q for i,q in enumerate(requirement_quotes(data),1)}
    evidence={}
    # Arquivos grandes (como lockfiles) continuam acessíveis ao CLI; o catálogo é um índice breve.
    budget=32000
    for name in data.get('delivered_files',[]):
        path=cwd/relative_path(name)
        if not path.is_file():continue
        for line_number,line in enumerate(path.read_text(encoding='utf-8',errors='replace').splitlines(),1):
            if not line.strip():continue
            if line_number>80 or budget<len(line) or len(line)>1200:break
            evidence[f'E{len(evidence)+1:04d}']={'file':name,'line':line_number,'quote':line}
            budget-=len(line)
    return {'requirements':requirements,'evidence':evidence}


def fidelity_schema(schema,catalog):
    props=schema['$defs']['CodeIssue']['properties']
    for field,ids in (('requirement_ref',catalog['requirements']),('evidence_ref',catalog['evidence'])):
        props[field]={'anyOf':[{'type':'string','enum':list(ids)} ,{'type':'null'}]} if ids else {'type':'null'}
        schema['$defs']['CodeIssue']['required'].append(field)
    props['requirement_quote']={'type':'string'}
    return schema


def resolve_references(value,catalog):
    if not isinstance(value,dict) or not isinstance(value.get('issues',[]),list):
        raise ValueError('A avaliação deve ser um objeto JSON com issues em uma lista.')
    for issue in value.get('issues',[]):
        if not isinstance(issue,dict):raise ValueError('Cada issue deve ser um objeto JSON.')
        ref=issue.pop('requirement_ref',None)
        if ref is not None:
            if not isinstance(ref,str) or ref not in catalog['requirements']:raise ValueError('Escolha requirement_ref do catálogo fornecido.')
            issue['requirement_quote']=catalog['requirements'][ref]
        ref=issue.pop('evidence_ref',None)
        if ref is not None:
            if not isinstance(ref,str) or ref not in catalog['evidence']:raise ValueError('Escolha evidence_ref do catálogo fornecido.')
            evidence=catalog['evidence'][ref]
            if issue.get('file')!=evidence['file'] or issue.get('missing'):
                raise ValueError('evidence_ref deve corresponder ao arquivo existente apontado no issue.')
            issue['evidence_quote']=evidence['quote']
    return value
