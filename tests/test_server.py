import http.client
from pathlib import Path
import json
from threading import Thread
import pytest
from investigator.server import make_server, Workbench

@pytest.fixture
def running():
    server=make_server(0)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def request(server,method,path,body=None,headers=None):
    client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
    try:
        client.request(method,path,body=body,headers=headers or {})
        response=client.getresponse()
        return response.status,dict(response.getheaders()),response.read()
    finally:client.close()


def test_server_home_and_security_headers(running):
    status,headers,body=request(running,'GET','/')
    assert status==200
    assert b'Shift Investigator' in body
    assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
    assert request(running,'GET','/../../README.md')[0]==404
    assert request(running,'GET','/',headers={'Host':'evil.example'})[0]==403
    guide_status,_,guide=request(running,'GET','/guide')
    assert guide_status==200 and b'How this agent is built' in guide and b'4-4-5' in guide


def test_post_protection_and_valid_round_trip(running):
    payload=json.dumps({'day':'2026-09-01','shift':2,'question':'Why was second shift slow?'})
    assert request(running,'POST','/api/ask',payload,{'Content-Type':'application/json'})[0]==403
    _,_,body=request(running,'GET','/api/config')
    token=json.loads(body)['token']
    headers={'Content-Type':'application/json','X-Workbench-Token':token}
    assert request(running,'POST','/api/ask',payload,{**headers,'Origin':'https://evil.example'})[0]==403
    status,_,body=request(running,'POST','/api/ask',payload,headers)
    assert status==200
    result=json.loads(body)
    assert result['status']=='complete'
    assert 'Synthetic production investigation' in result['report_markdown']
    report_request=json.dumps({'session_id':result['session_id'],'run_id':result['run_id']})
    assert request(running,'POST','/api/report',report_request,{'Content-Type':'application/json'})[0]==403
    code,report_headers,pdf=request(running,'POST','/api/report',report_request,headers)
    assert code==200 and report_headers['Content-Type']=='application/pdf' and pdf.startswith(b'%PDF-')
    assert request(running,'POST','/api/ask','x'*16001,headers)[0]==413


def test_sessions_and_day_scope():
    app=Workbench()
    try:
        first=app.ask({'day':'2026-09-01','shift':2,'question':'Show downtime','scope':'day'})
        other=app.ask({'day':'2026-09-02','shift':3,'question':'Show speed'})
        assert first['session_id']!=other['session_id']
        follow=app.ask({'day':'2026-09-01','shift':2,'question':'Compare that with first shift','scope':'day','session_id':first['session_id']})
        assert follow['selected_shifts']==[2,1]
        changed=app.ask({'day':'2026-09-03','shift':1,'question':'Show speed','session_id':first['session_id']})
        assert len(changed['context']['history'])==1
        assert changed['production_day']=='2026-09-03'
    finally:app.close()


def test_interface_control_references_exist():
    import re
    from investigator.server import WEB
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    script = (WEB / 'app.js').read_text(encoding='utf-8')
    ids = set(re.findall(r'id="([^"]+)"', html))
    references = set(re.findall(r"\$\('([^']+)'\)", script))
    assert references <= ids, references - ids
    assert '\ufffd' not in html + script


# ---- hosted public-demo mode (PUBLIC_DEMO_ORIGIN) ----
from investigator.server import PublicDemo

def test_public_demo_config_parsing():
    assert PublicDemo.from_env({}) is None
    demo=PublicDemo.from_env({'PUBLIC_DEMO_ORIGIN':'https://justfetz-shift.hf.space/, https://demo.example',
                              'PUBLIC_DEMO_EMBED_ORIGINS':'https://huggingface.co,https://justfetz.github.io','PUBLIC_DEMO_LIVE_MODEL':'0'})
    assert demo.origins==('https://justfetz-shift.hf.space','https://demo.example')
    assert demo.frame_ancestors=='https://huggingface.co https://justfetz.github.io'
    assert demo.live_model is False
    assert PublicDemo.from_env({'PUBLIC_DEMO_ORIGIN':'https://demo.example'}).frame_ancestors=="'none'"
    for bad in ('http://evil.example','https://demo.example/app','https://user@demo.example','ftp://demo.example'):
        with pytest.raises(ValueError):
            PublicDemo.from_env({'PUBLIC_DEMO_ORIGIN':bad})


@pytest.fixture
def public():
    demo=PublicDemo(origins=('https://demo.example',),embed_origins=('https://justfetz.github.io',),live_model=False)
    server=make_server(0,public=demo,host='127.0.0.1')
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()
    server.server_close()
    server.app.close()


def test_public_mode_hostname_embed_and_health(public):
    status,headers,body=request(public,'GET','/',headers={'Host':'justfetz-shift.hf.space'})
    assert status==200 and b'Shift Investigator' in body
    assert 'frame-ancestors https://justfetz.github.io;' in headers['Content-Security-Policy']
    assert request(public,'GET','/../../README.md')[0]==404
    for path in ('/api/health','/healthz'):
        code,_,health=request(public,'GET',path)
        assert code==200 and json.loads(health)=={'ok':True,'public':True}
    config=json.loads(request(public,'GET','/api/config')[2])
    assert config['public'] is True and config['live_model_available'] is False


def test_public_mode_requires_allowlisted_origin_and_ignores_visitor_keys(public,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    token=json.loads(request(public,'GET','/api/config')[2])['token']
    headers={'Content-Type':'application/json','X-Workbench-Token':token,'Host':'anything.example'}
    payload=json.dumps({'day':'2026-09-01','shift':2,'question':'Why was second shift slow?'})
    assert request(public,'POST','/api/ask',payload,headers)[0]==403                       # no Origin: refused
    assert request(public,'POST','/api/ask',payload,{**headers,'Origin':'https://evil.example'})[0]==403
    status,_,body=request(public,'POST','/api/ask',payload,{**headers,'Origin':'https://demo.example'})
    assert status==200 and json.loads(body)['status']=='complete'
    # a visitor key never turns on live mode on a hosted demo
    live=json.dumps({'day':'2026-09-01','shift':2,'question':'Why was second shift slow?','mode':'openai'})
    status,_,body=request(public,'POST','/api/ask',live,{**headers,'Origin':'https://demo.example','X-OpenAI-Key':'sk-visitor'})
    assert status==400 and 'offline planner only' in json.loads(body)['error']


def test_public_live_model_needs_flag_and_server_key(monkeypatch):
    from investigator.server import Workbench
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    assert Workbench(PublicDemo(('https://demo.example',),(),True)).live_model_available is False
    monkeypatch.setenv('OPENAI_API_KEY','sk-server')
    assert Workbench(PublicDemo(('https://demo.example',),(),False)).live_model_available is False
    assert Workbench(PublicDemo(('https://demo.example',),(),True)).live_model_available is True
    assert Workbench().live_model_available is True


def test_visitor_limiter_burst_refill_and_bound():
    from investigator.server import VisitorLimiter
    limiter=VisitorLimiter(burst=3,refill_seconds=10,size=2)
    assert [limiter.allow('a',now=0) for _ in range(4)]==[True,True,True,False]
    assert limiter.allow('a',now=9) is False      # not yet refilled
    assert limiter.allow('a',now=10) is True      # one token back
    assert limiter.allow('b',now=10) is True      # other visitors unaffected
    limiter.allow('c',now=10)
    assert 'a' not in limiter.buckets             # table bounded: oldest address evicted


def test_visitor_id_is_salted_hex_never_the_address():
    from investigator.server import visitor_id
    vid=visitor_id('203.0.113.9',salt='s')
    assert len(vid)==32 and all(c in '0123456789abcdef' for c in vid)
    assert vid==visitor_id('203.0.113.9',salt='s') and vid!=visitor_id('203.0.113.9',salt='t')
    assert '203' not in vid


def test_public_mode_limits_each_forwarded_address(public,monkeypatch):
    from investigator.server import VISITOR_BURST
    token=json.loads(request(public,'GET','/api/config')[2])['token']
    headers={'Content-Type':'application/json','X-Workbench-Token':token,'Origin':'https://demo.example'}
    bad=json.dumps({})   # rejected by validation (400) but still consumes a visitor token
    codes=[request(public,'POST','/api/ask',bad,{**headers,'X-Forwarded-For':'203.0.113.9, 10.0.0.1'})[0] for _ in range(VISITOR_BURST+1)]
    assert codes==[400]*VISITOR_BURST+[429]
    assert request(public,'POST','/api/ask',bad,{**headers,'X-Forwarded-For':'198.51.100.2'})[0]==400   # another visitor


def test_local_mode_ignores_forwarded_header(running):
    from investigator.server import VISITOR_BURST
    token=json.loads(request(running,'GET','/api/config')[2])['token']
    headers={'Content-Type':'application/json','X-Workbench-Token':token}
    bad=json.dumps({})
    codes=[request(running,'POST','/api/ask',bad,{**headers,'X-Forwarded-For':f'203.0.113.{n}'})[0] for n in range(VISITOR_BURST+1)]
    assert codes==[400]*VISITOR_BURST+[429]   # spoofed hops do not buy more requests locally


def test_budget_path_from_environment(monkeypatch,tmp_path):
    from investigator.server import budget_path
    monkeypatch.delenv('MODEL_BUDGET_PATH',raising=False)
    assert budget_path()==Path('artifacts/model-budget.sqlite3')
    monkeypatch.setenv('MODEL_BUDGET_PATH',str(tmp_path/'ledger'/'budget.sqlite3'))
    assert budget_path()==tmp_path/'ledger'/'budget.sqlite3'


def test_dataset_and_evals_are_served(public):
    code,headers,body=request(public,'GET','/api/data/2026-09-14.xml')
    assert code==200 and headers['Content-Type'].startswith('application/xml') and body.startswith(b'<?xml')
    code,headers,body=request(public,'GET','/api/data/csv/stops.csv')
    assert code==200 and body.splitlines()[0].startswith(b'production_day,id,shift,kind')
    assert request(public,'GET','/api/data/manifest.json')[0]==200
    assert request(public,'GET','/api/data/2026-10-01.xml')[0]==404          # not a demo day
    assert request(public,'GET','/api/data/../pyproject.toml')[0]==404
    assert request(public,'GET','/api/data/csv/secrets.csv')[0]==404
    code,_,body=request(public,'GET','/api/evals')
    listing=json.loads(body)
    assert code==200 and len(listing['cases'])>=26 and listing['types']==['routing','faithfulness','grounding','refusal']
    assert all(c['expectations'] for c in listing['cases'])
    code,_,page=request(public,'GET','/evals')
    assert code==200 and b'golden' in page.lower()
    config=json.loads(request(public,'GET','/api/config')[2])
    assert config['golden_cases']==len(listing['cases']) and config['repository'].startswith('https://github.com/')


def test_evals_run_in_the_app_is_offline_only(public):
    token=json.loads(request(public,'GET','/api/config')[2])['token']
    headers={'Content-Type':'application/json','X-Workbench-Token':token,'Origin':'https://demo.example'}
    code,_,body=request(public,'POST','/api/evals/run',json.dumps({'case_id':'why-slow'}),headers)
    report=json.loads(body)['reports'][0]
    assert code==200 and report['outcome']=='pass' and all(c['ok'] for c in report['checks'])
    assert report['answer']['trace'][0]['tool']=='get_shift_kpis' and report['answer']['usage']['model_calls']==0
    code,_,body=request(public,'POST','/api/evals/run',json.dumps({'case_id':'live-why-slow'}),headers)
    assert code==200 and json.loads(body)['reports'][0]['outcome']=='skip'   # no recording: never a live call
    assert request(public,'POST','/api/evals/run',json.dumps({'case_id':'nope'}),headers)[0]==400
    assert request(public,'POST','/api/evals/run',json.dumps({'case_id':'why-slow','extra':1}),headers)[0]==400
