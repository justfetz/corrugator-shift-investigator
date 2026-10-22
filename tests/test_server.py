import http.client
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


def test_config_reports_the_production_calendar_and_refuses_other_days(running):
    from investigator.record_store import default_store
    from investigator.server import DAYS
    store=default_store()
    config=json.loads(request(running,'GET','/api/config')[2])
    assert config['days']==store.production_days==DAYS
    assert (config['first_day'],config['last_day'],config['production_calendar'])==('2026-08-03','2026-09-30','Mon-Fri')
    assert config['dataset_hash']==store.content_hash and len(config['dataset_hash'])==64
    assert len(config['days'])==43 and '2026-08-03' in config['days'] and '2026-09-12' not in config['days']
    assert {t['name'] for t in config['tools']}>={'get_period_kpis','compare_periods','get_downtime_pareto','get_waste_breakdown','get_stops_profile','get_trend'}
    headers={'Content-Type':'application/json','X-Workbench-Token':config['token']}
    for day,message in (('2026-09-12','Saturday'),('2026-09-13','Sunday'),('2026-07-31','outside the dataset'),('2026-10-01','outside the dataset'),('2026/09/14','YYYY-MM-DD')):
        status,_,body=request(running,'POST','/api/ask',json.dumps({'day':day,'shift':2,'question':'Show speed'}),headers)
        assert status==400 and message in json.loads(body)['error'], (day,body)
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-08-03','shift':1,'question':'Show speed'}),headers)
    assert status==200 and json.loads(body)['production_day']=='2026-08-03'
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-09-14','shift':2,'scope':'day','question':'How did September go versus August?'}),headers)
    result=json.loads(body)
    assert status==200 and result['trace'][0]['tool']=='compare_periods' and result['charts'][0]['y2']=='previous'
    assert result['context']['dataset_version']==running.app.datasets[('2026-09-14',False)].version
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-08-12','shift':2,'period':'month','question':'period'}),headers)
    result=json.loads(body)
    assert status==200 and result['period_label']=='2026-08-01 to 2026-08-31' and result['status']=='complete'
    assert any('Full comparison unavailable' in s for s in result['sections'])   # July is before the dataset


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


# ---- demo guards (DEMO_RATE_LIMIT_PER_MIN, DEMO_DAILY_CAP, DEMO_MAX_TURNS, TRUST_PROXY) and Anthropic mode ----
from investigator.server import DemoGuards, DemoLimit

ASK=json.dumps({'day':'2026-09-01','shift':2,'question':'Show speed'})


def serve(public=None,guards=None):
    server=make_server(0,public=public,host='127.0.0.1',guards=guards)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server,thread


def stop(server,thread):
    server.shutdown();thread.join();server.server_close();server.app.close()


def token_headers(server,**extra):
    token=json.loads(request(server,'GET','/api/config')[2])['token']
    return {'Content-Type':'application/json','X-Workbench-Token':token,**extra}


def test_demo_guards_from_env_defaults_and_validation(tmp_path):
    off=DemoGuards.from_env({})
    assert (off.rate_limit_per_min,off.daily_cap,off.max_turns,off.trust_proxy)==(0,0,4,False)
    on=DemoGuards.from_env({'DEMO_RATE_LIMIT_PER_MIN':'20','DEMO_DAILY_CAP':'400','DEMO_MAX_TURNS':'14','TRUST_PROXY':'1'})
    assert (on.rate_limit_per_min,on.daily_cap,on.max_turns,on.trust_proxy)==(20,400,14,True)
    assert on.summary=={'rate_limit_per_min':20,'daily_cap':400,'max_turns':14,'trust_proxy':True}
    for bad in ({'DEMO_RATE_LIMIT_PER_MIN':'-1'},{'DEMO_DAILY_CAP':'many'},{'DEMO_MAX_TURNS':'0'},{'DEMO_MAX_TURNS':'51'}):
        with pytest.raises(ValueError):
            DemoGuards.from_env(bad)


def test_rate_limit_per_ip_returns_429_with_retry_after(tmp_path):
    server,thread=serve(guards=DemoGuards(rate_limit_per_min=2,ledger=tmp_path/'ledger.sqlite3'))
    try:
        headers=token_headers(server)
        assert request(server,'POST','/api/ask',ASK,headers)[0]==200
        assert request(server,'POST','/api/ask',ASK,headers)[0]==200
        status,response_headers,body=request(server,'POST','/api/ask',ASK,headers)
        assert status==429 and json.loads(body)=={'error':'too many questions, slow down','code':'rate_limited'}
        assert 1<=int(response_headers['Retry-After'])<=60
        # the report route is not a round; and the limit is per IP, so another client is not affected
        report=json.dumps({'session_id':'none','run_id':'none'})
        assert request(server,'POST','/api/report',report,headers)[0]==400
        assert len(server.app.rate_windows)==1
        with pytest.raises(DemoLimit):
            server.app.client_round('127.0.0.1')
        server.app.client_round('198.51.100.7')
    finally:stop(server,thread)


def test_rate_limit_off_by_default_and_forwarded_for_only_when_trusted(tmp_path):
    server,thread=serve(guards=DemoGuards(rate_limit_per_min=1,ledger=tmp_path/'ledger.sqlite3'))
    try:
        headers=token_headers(server)
        assert request(server,'POST','/api/ask',ASK,headers)[0]==200
        # Without TRUST_PROXY a spoofed X-Forwarded-For cannot buy a fresh window.
        assert request(server,'POST','/api/ask',ASK,{**headers,'X-Forwarded-For':'203.0.113.9'})[0]==429
    finally:stop(server,thread)
    server,thread=serve(guards=DemoGuards(rate_limit_per_min=1,trust_proxy=True,ledger=tmp_path/'ledger2.sqlite3'))
    try:
        headers=token_headers(server)
        assert request(server,'POST','/api/ask',ASK,{**headers,'X-Forwarded-For':'203.0.113.9'})[0]==200
        # rightmost entry is the proxy-appended client; the client-supplied left entries are ignored
        assert request(server,'POST','/api/ask',ASK,{**headers,'X-Forwarded-For':'10.0.0.1, 203.0.113.9'})[0]==429
        assert request(server,'POST','/api/ask',ASK,{**headers,'X-Forwarded-For':'203.0.113.9, 198.51.100.4'})[0]==200
        assert set(server.app.rate_windows)=={'203.0.113.9','198.51.100.4'}
    finally:stop(server,thread)
    default=Workbench()
    for _ in range(5):
        default.client_round('127.0.0.1')  # off by default
    default.close()


def test_daily_cap_counts_live_rounds_persistently(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','sk-ant-server-key-for-tests-only-0001')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    calls=[]
    def stub(payload,key,timeout):
        calls.append(payload)
        return 200,json.dumps({'stop_reason':'tool_use','usage':{'input_tokens':1,'output_tokens':1},
            'content':[{'type':'tool_use','id':'toolu_1','name':'cannot_answer','input':{'reason':'test'}}]}).encode()
    monkeypatch.setattr('investigator.anthropic_provider.transport',stub)
    guards=DemoGuards(daily_cap=2,ledger=tmp_path/'ledger.sqlite3')
    server,thread=serve(guards=guards)
    try:
        headers=token_headers(server)
        live=json.dumps({'day':'2026-09-01','shift':2,'question':'Show speed','mode':'anthropic'})
        assert request(server,'POST','/api/ask',ASK,headers)[0]==200          # offline rounds are free and uncounted
        assert request(server,'POST','/api/ask',live,headers)[0]==200
        assert request(server,'POST','/api/ask',live,headers)[0]==200
        status,response_headers,body=request(server,'POST','/api/ask',live,headers)
        assert status==429 and json.loads(body)=={'error':"today's budget for the chat is used up",'code':'budget'}
        assert 1<=int(response_headers['Retry-After'])<=86400
        assert len(calls)==2 and all(p['model']=='claude-haiku-5-5' for p in calls)
        assert request(server,'POST','/api/ask',ASK,headers)[0]==200          # offline still answers
    finally:stop(server,thread)
    again=Workbench(guards=guards)  # the counter is in sqlite, so a restart does not reset it
    with pytest.raises(DemoLimit) as exc:
        again.daily_round()
    assert exc.value.code=='budget'
    again.close()
    assert Workbench(guards=DemoGuards(ledger=tmp_path/'ledger.sqlite3')).daily_round() is None  # cap unset: off


def test_anthropic_key_rules_local_and_public(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    local=Workbench()
    try:
        with pytest.raises(ValueError,match='Anthropic mode needs your key'):
            local.ask({'day':'2026-09-01','shift':2,'question':'Show speed','mode':'anthropic'})
        with pytest.raises(ValueError,match='Choose offline, OpenAI or Anthropic'):
            local.ask({'day':'2026-09-01','shift':2,'question':'Show speed','mode':'gemini'})
        assert local.live_modes==()
    finally:local.close()
    monkeypatch.setenv('ANTHROPIC_API_KEY','sk-ant-server-key-for-tests-only-0001')
    assert Workbench().live_modes==('anthropic',)
    assert Workbench(PublicDemo(('https://demo.example',),(),False)).live_modes==()
    assert Workbench(PublicDemo(('https://demo.example',),(),True)).live_modes==('anthropic',)
    monkeypatch.setenv('OPENAI_API_KEY','sk-server')
    assert Workbench(PublicDemo(('https://demo.example',),(),True)).live_modes==('openai','anthropic')
    monkeypatch.delenv('OPENAI_API_KEY')
    # public with the flag but only the Anthropic key: openai mode is refused by name, visitor keys ignored
    flagged=Workbench(PublicDemo(('https://demo.example',),(),True))
    try:
        with pytest.raises(ValueError,match='does not offer openai'):
            flagged.ask({'day':'2026-09-01','shift':2,'question':'Show speed','mode':'openai'},api_key='sk-visitor')
    finally:flagged.close()
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    unflagged=Workbench(PublicDemo(('https://demo.example',),(),False))
    try:
        with pytest.raises(ValueError,match='offline planner only'):
            unflagged.ask({'day':'2026-09-01','shift':2,'question':'Show speed','mode':'anthropic'},anthropic_key='sk-ant-visitor-key-0000000000')
    finally:unflagged.close()


def test_public_server_ignores_visitor_anthropic_key(public):
    headers=token_headers(public,Origin='https://demo.example',Host='anything.example')
    live=json.dumps({'day':'2026-09-01','shift':2,'question':'Show speed','mode':'anthropic'})
    status,_,body=request(public,'POST','/api/ask',live,{**headers,'X-Anthropic-Key':'sk-ant-visitor-key-0000000000'})
    assert status==400 and 'offline planner only' in json.loads(body)['error']


def test_config_reports_live_modes(monkeypatch,tmp_path):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setenv('ANTHROPIC_API_KEY','sk-ant-server-key-for-tests-only-0001')
    monkeypatch.setenv('ANTHROPIC_MODEL','claude-sonnet-5-5')
    server,thread=serve(guards=DemoGuards(rate_limit_per_min=20,daily_cap=400,ledger=tmp_path/'l.sqlite3'))
    try:
        config=json.loads(request(server,'GET','/api/config')[2])
        assert config['live_modes']==['anthropic'] and config['live_model_available'] is True
        assert config['live_models']=={'openai':'gpt-4.1-mini-2025-04-14','anthropic':'claude-sonnet-5-5'}
        assert config['demo_guards']=={'rate_limit_per_min':20,'daily_cap':400,'max_turns':4,'trust_proxy':False}
    finally:stop(server,thread)
    monkeypatch.setenv('ANTHROPIC_MODEL','claude-opus-5-5')
    with pytest.raises(ValueError,match='ANTHROPIC_MODEL'):
        Workbench()
    monkeypatch.delenv('ANTHROPIC_MODEL')
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    demo=PublicDemo(('https://demo.example',),(),True)
    server,thread=serve(public=demo)
    try:
        config=json.loads(request(server,'GET','/api/config')[2])
        assert config['live_modes']==[] and config['live_model_available'] is False
        assert config['live_models']['anthropic']=='claude-haiku-5-5'
    finally:stop(server,thread)


def test_max_turns_bounds_conversation_history():
    app=Workbench(guards=DemoGuards(max_turns=2))
    try:
        first=app.ask({'day':'2026-09-01','shift':2,'question':'Show downtime','scope':'day'})
        sid=first['session_id']
        for question in ('Show speed','Show rankings','Show notes'):
            last=app.ask({'day':'2026-09-01','shift':2,'question':question,'scope':'day','session_id':sid})
        assert len(last['context']['history'])==2
        assert [h['question'] for h in last['context']['history']]==['Show rankings','Show notes']
    finally:app.close()
