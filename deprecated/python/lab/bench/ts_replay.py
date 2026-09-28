"""Export cached research inputs for the TypeScript read-path parity test. Never dispatches a provider call.

Run from the archived Python root with LAB_PRIVATE_DIR, LAB_CACHE_ONLY=1, LAB_PROCESSORS=openai.
Output MUST be a gitignored private directory. Only aggregate progress is printed.
"""
# @ref LLP 0023.001#research-protocol — labels stay outside the provider inputs and cached calls require exact prompts
import base64
import hashlib
import json
import os
import sys
import re
from datetime import datetime, timezone
from pathlib import Path

os.environ['LAB_CACHE_ONLY'] = '1'
os.environ['LAB_PROCESSORS'] = 'openai'
from lab.common import llm
from lab.memory import stores, answerers
from lab.memory.core import Context

private = Path(os.environ['LAB_PRIVATE_DIR'])
out = Path(sys.argv[1]).resolve()
if '.context' not in out.parts:
    raise SystemExit('Export must be under a gitignored .context directory')
out.mkdir(parents=True, exist_ok=True)

def digest(text): return hashlib.sha256(text.encode()).hexdigest()
def vector(v): return base64.b64encode(v.tobytes()).decode()
def stamp(value):
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
def wid(i): return f'w_{i:08d}'
def sid(i): return f's_{i:08d}'

calls = {}
original = stores.chat_json
def cached(model, prompt, schema, **kwargs):
    result, call = original(model, prompt, schema, **kwargs)
    if not call.cached: raise RuntimeError('Noncached call forbidden')
    calls[digest(prompt)] = result
    return result, call
stores.chat_json = cached

names = sorted(p.name.removeprefix('return_gold_').removesuffix('.json') for p in private.glob('return_gold_u*.json'))
summary = {'accounts': len(names), 'questions': 0, 'missing': 0, 'provider_requests': 0, 'historical': {}}
all_scores = {}
for name in names:
    calls.clear()
    ds = json.loads((private / f'scenario_{name}.json').read_text())
    run = private / 'runs' / f'scenario_{name}_v25nog'
    ctx = Context(str(run / 'memory.sqlite'), ds['user'], datetime.fromisoformat(ds['now']), cache_dir=run / 'memory')
    # Do not rebuild or modify the original stores.
    def saved(key, make):
        return json.loads((run / 'memory' / f'{key}.json').read_text())
    ctx.saved = saved
    stm = stores.LinkedStatementsListPicked(); stm.build(ctx)
    log = stores.LogListPicked(); log.build(ctx)
    dossier = stores.IncrementalDossierStore(); dossier.build(ctx)
    windows = [{'id':wid(i), 'person':name, 'session':f'log-{lid}', 'at':stamp(ts), 'text':log.texts[i], 'turns':[f'log-{lid}']} for i,(lid,ts,_) in enumerate(ctx.log)]
    statements = []
    groups = []
    for i,g in enumerate(stm.groups):
        groups.append({'id':f'g_{i}', 'person':name, 'count':g['count'], 'first':stamp(g['first']), 'last':stamp(g['last']), 'replaces':[], **({'until':stamp(g['until'])} if g.get('until') else {})})
    by_log = {lid:i for i,(lid,_,_) in enumerate(ctx.log)}
    for i,x in enumerate(stm.raw):
        statements.append({'id':sid(i), 'person':name, 'session':f'log-{x["src"][0]}', 'window':wid(by_log[x['src'][0]]), 'text':x['text'], 'kind':x['kind'], 'at':stamp(x['first']), 'group':f'g_{stm.group_of.get(i,0)}'})
    for i,g in enumerate(stm.groups):
        prior = []
        for entry in g.get('was', []):
            match = re.fullmatch(r"(.*) \(until (\d{4}-\d\d-\d\d)\)", entry)
            if not match: continue
            candidates = [j for j,x in enumerate(stm.raw) if x['text'] == match[1]]
            if not candidates: continue
            old = max(candidates)
            group_id = stm.group_of.get(old,0)
            if group_id != i and groups[group_id].get('until') == stamp(match[2]):
                prior.append(f'g_{group_id}')
        groups[i]['replaces'] = list(dict.fromkeys(prior))
    queries, labels, missing = [], [], 0
    scores = json.loads((private / 'results' / f'scenario_{name}_v25nog_runs.json').read_text())
    for config, result in scores.items():
        all_scores.setdefault(config, []).extend(r['score'] for r in result['rows'] if r['type'] == 'cue_retold')
    graded = {r['id']:r for r in scores['conv_best2']['rows']}
    for q in [q for q in ds['qa'] if q['type'] == 'cue_retold']:
        try:
            s_candidates = stm.index.top(q['question'], 40)
            w_candidates = log.semantic.top(q['question'], 20)
            s_order = stores.pick_list(q['question'], [stm.raw[i]['text'] for i in s_candidates], 15)
            s_kept = [s_candidates[i] for i in s_order]
            w_order = stores.pick_list(q['question'], [log.texts[i] for i in w_candidates], 6)
            w_kept = [w_candidates[i] for i in w_order]
            # Include the historical explicit-calendar-day rule in the Python reference.
            w_evidence = log.read(q['question'], ctx)
            s_evidence = [stm.evidence_raw(i) for i in s_kept]
            evidence = dossier.read(q['question'], ctx) + s_evidence + w_evidence
            queries.append({'id':q['id'], 'question':q['question'], 'vector':vector(llm.embed([q['question']])[0])})
            labels.append({'id':q['id'], 'statements':[sid(i) for i in s_kept], 'windows':[wid(by_log[e.source[0]]) for e in w_evidence],
                'statementCandidates':[sid(i) for i in s_candidates], 'windowCandidates':[wid(i) for i in w_candidates],
                'evidenceHash':digest(answerers.assemble(evidence,48000)), 'score':graded[q['id']]['score']})
        except llm.CacheMiss:
            missing += 1
    data = {'person':name, 'windows':windows, 'statements':statements, 'groups':groups,
        'dossier':{'person':name, 'text':dossier.text, 'at':stamp(ds['now']), 'updates':0},
        'vectors':[{'id':sid(i),'kind':'statement','vector':vector(v)} for i,v in enumerate(stm.index.m)] + [{'id':wid(i),'kind':'window','vector':vector(v)} for i,v in enumerate(log.semantic.m)],
        'queries':queries, 'calls':dict(calls)}
    (out/f'{name}.inputs.json').write_text(json.dumps(data,ensure_ascii=False))
    (out/f'{name}.labels.json').write_text(json.dumps(labels))
    summary['questions'] += len(queries); summary['missing'] += missing
    ctx.db.close()
    print(json.dumps({'accounts_exported':names.index(name)+1,'questions':summary['questions'],'missing':summary['missing']}),flush=True)
summary['historical'] = {k:{'questions':len(v),'percent':round(100*sum(v)/len(v),2)} for k,v in all_scores.items() if k in ['conv_best2','conv_best2_jev','full_context','product','product_injected']}
summary['cached_calls'] = len(llm.LEDGER.calls)
summary['provider_requests'] = sum(not c.cached for c in llm.LEDGER.calls)
(out/'manifest.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary))
