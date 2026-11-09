import http.client
import json
from pathlib import Path
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
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-08-12','shift':2,'period':'month','question':'Build month report','report':True}),headers)
    result=json.loads(body)
    assert status==200 and result['period_label']=='2026-08-01 to 2026-08-31' and result['status']=='complete'
    assert any('Full comparison unavailable' in s for s in result['sections'])   # July is before the dataset
    assert result['trace'][0]['tool']=='get_period_report'
    assert result['tables'] and all(tbl['source_result_id']==result['trace'][0]['result_id'] for tbl in result['tables'])   # #40: period tables name their step
    report=result['trace'][0]['data'];assert report['daily_sql'].count('?')==len(report['daily_parameters'])>0 and set(report['daily_parameters'])=={2}   # #40: the per-day SQL with its bound values
    # A question under Week is investigated over that week by the period tools (no longer the canned report).
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-09-14','shift':2,'period':'week','question':'Why was this shift slow? Chart downtime by reason.'}),headers)
    result=json.loads(body)
    assert status==200 and result['status']=='complete' and result['period_label']=='Week 2026-09-14 to 2026-09-18 (Mon-Fri)', body[:300]
    assert [t['tool'] for t in result['trace'] if t['tool']!='render_chart']==['get_period_kpis','get_downtime_pareto','get_waste_breakdown']
    assert all(t['arguments']['window']=={'period':'week','anchor':'2026-09-14'} and t['arguments']['shift']==2 for t in result['trace'] if t['tool']!='render_chart')
    assert result['context']['period']=='week' and result['charts']
    # 4-4-5 is 'accounting' in the interface and 'fiscal' in the tools; the report flag needs a period.
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-09-14','shift':2,'period':'accounting','scope':'day','question':'Which downtime reason cost the most minutes?'}),headers)
    result=json.loads(body)
    assert status==200 and result['trace'][0]['arguments']['window']=={'period':'fiscal','anchor':'2026-09-14'} and result['trace'][0]['arguments']['shift'] is None
    assert result['period_label'].startswith('4-4-5 P')
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-09-14','shift':2,'period':'week','question':'Show setup matrix'}),headers)
    assert status==400 and 'Switch Period to Day' in json.loads(body)['error']
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-09-14','shift':2,'period':'day','question':'x','report':True}),headers)
    assert status==400 and 'needs Week, Month or 4-4-5' in json.loads(body)['error']


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
        health=json.loads(health)
        assert code==200 and health['ok'] is True and health['public'] is True
        assert health['store']['source'] in ('prebuilt','generated') and health['store']['boot_ms']>=0 and len(health['store']['dataset_hash'])==12   # issue #19
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


# ---- issue #17: the token and the sessions survive a second instance and a restart ----

def test_public_token_is_shared_across_instances_and_restarts(public,monkeypatch):
    """Cloud Run runs up to two instances and scales to zero. A visitor whose page loaded
    on one instance must be accepted by the other, and by the next restart."""
    from investigator.server import PublicDemo
    monkeypatch.delenv('WORKBENCH_SECRET',raising=False)
    demo=PublicDemo(('https://demo.example',),(),False)
    assert Workbench._workbench_token(demo)==Workbench._workbench_token(demo)        # two instances agree
    assert public.app.token==Workbench._workbench_token(public.app.public)           # and so does a live server
    assert Workbench._workbench_token(PublicDemo(('https://other.example',),(),False))!=Workbench._workbench_token(demo)
    monkeypatch.setenv('WORKBENCH_SECRET','rotate-me')
    rotated=Workbench._workbench_token(demo)
    assert rotated!=public.app.token and rotated==Workbench._workbench_token(demo)   # the secret rotates it, deterministically
    assert Workbench._workbench_token(None)!=Workbench._workbench_token(None)        # the loopback workbench stays random per process
    # over HTTP: a token computed by "another instance" is accepted, a wrong one is still refused
    headers={'Content-Type':'application/json','Host':'anything.example','Origin':'https://demo.example'}
    payload=json.dumps({'day':'2026-09-01','shift':2,'question':'Show speed'})
    monkeypatch.delenv('WORKBENCH_SECRET',raising=False)
    other_instance=Workbench._workbench_token(public.app.public)
    assert request(public,'POST','/api/ask',payload,{**headers,'X-Workbench-Token':other_instance})[0]==200
    assert request(public,'POST','/api/ask',payload,{**headers,'X-Workbench-Token':'x'*64})[0]==403
    assert request(public,'POST','/api/ask',payload,headers)[0]==403


def test_unknown_session_starts_a_fresh_conversation_and_says_so():
    """A session id this process has never seen (the other instance answered the earlier
    question, or the process restarted) is not an error: the request carries day, shift
    and period, so a fresh conversation answers and the result flags the reset."""
    app=Workbench()
    try:
        first=app.ask({'day':'2026-09-01','shift':2,'question':'Show downtime','scope':'day'})
        assert first['session_reset'] is False
        again=app.ask({'day':'2026-09-01','shift':2,'question':'Show speed','scope':'day','session_id':'issued-by-another-instance'})
        assert again['status']=='complete' and again['session_reset'] is True
        assert again['session_id']!='issued-by-another-instance' and again['session_id'] in app.sessions
        follow=app.ask({'day':'2026-09-01','shift':2,'question':'Show downtime','scope':'day','session_id':again['session_id']})
        assert follow['session_reset'] is False and follow['session_id']==again['session_id']
        with pytest.raises(ValueError,match='Invalid session id'):
            app.ask({'day':'2026-09-01','shift':2,'question':'Show downtime','session_id':42})
    finally:app.close()


# ---- issue #33: compressed answers, revalidated static files, trace after first paint ----

def test_json_responses_are_gzipped_when_accepted(running):
    import gzip
    plain_status,plain_headers,plain=request(running,'GET','/api/config')
    assert plain_status==200 and 'Content-Encoding' not in plain_headers and plain_headers['Vary']=='Accept-Encoding'
    status,headers,body=request(running,'GET','/api/config',headers={'Accept-Encoding':'gzip, deflate, br'})
    assert status==200 and headers['Content-Encoding']=='gzip' and int(headers['Content-Length'])==len(body)
    assert gzip.decompress(body)==plain                      # the same bytes, smaller on the wire
    assert len(body)<len(plain)/3                            # TOOL_SCHEMAS compresses well
    assert headers['Cache-Control']=='no-store'
    # an answer: the biggest payloads shrink the most
    token=json.loads(plain)['token']
    payload=json.dumps({'day':'2026-09-14','shift':2,'question':'Show the full setup matrix','scope':'day'})
    base={'Content-Type':'application/json','X-Workbench-Token':token}
    _,_,raw=request(running,'POST','/api/ask',payload,base)
    status,headers,small=request(running,'POST','/api/ask',payload,{**base,'Accept-Encoding':'gzip'})
    assert status==200 and headers['Content-Encoding']=='gzip'
    assert json.loads(gzip.decompress(small))['status']==json.loads(raw)['status']
    assert len(raw)>100_000 and len(small)<=25_600          # #33 acceptance: setup matrix <= 25 KB on the wire (145 KB raw, 21 KB measured)
    payload=json.dumps({'day':'2026-09-14','shift':2,'question':'Show the downtime breakdown','scope':'day'})
    status,headers,small=request(running,'POST','/api/ask',payload,{**base,'Accept-Encoding':'gzip'})
    assert status==200 and headers['Content-Encoding']=='gzip' and len(small)<=6_144   # #33 acceptance: downtime breakdown <= 6 KB (3.9 KB measured)
    # q=0 means no gzip; x-gzip is gzip
    status,headers,body=request(running,'GET','/api/config',headers={'Accept-Encoding':'gzip;q=0, identity'})
    assert status==200 and 'Content-Encoding' not in headers
    status,headers,body=request(running,'GET','/api/config',headers={'Accept-Encoding':'x-gzip'})
    assert status==200 and headers['Content-Encoding']=='gzip'
    # small bodies are sent as they are
    status,headers,body=request(running,'GET','/api/health',headers={'Accept-Encoding':'gzip'})
    assert status==200 and 'Content-Encoding' not in headers and json.loads(body)['ok'] is True


def test_static_files_revalidate_with_an_etag(running):
    status,headers,body=request(running,'GET','/app.js')
    assert status==200 and headers['ETag'].startswith('"') and headers['Cache-Control']=='no-cache'
    assert body==(Path(__file__).resolve().parent.parent/'src'/'investigator'/'web'/'app.js').read_bytes()
    etag=headers['ETag']
    status,headers,body=request(running,'GET','/app.js',headers={'If-None-Match':etag})
    assert status==304 and body==b'' and 'Content-Length' not in headers and 'Content-Type' not in headers   # unchanged: no download; RFC 9110 8.6
    # the gzip representation carries its own validator, and either one revalidates
    status,headers,body=request(running,'GET','/app.js',headers={'Accept-Encoding':'gzip'})
    assert status==200 and headers['Content-Encoding']=='gzip' and headers['ETag']==etag[:-1]+'-gzip"'
    status,headers,body=request(running,'GET','/app.js',headers={'If-None-Match':f'"other", {headers["ETag"]}'})
    assert status==304
    status,_,body=request(running,'GET','/app.js',headers={'If-None-Match':'"stale"'})
    assert status==200 and len(body)>1000
    status,headers,body=request(running,'GET','/style.css',headers={'Accept-Encoding':'gzip'})
    assert status==200 and headers['Content-Encoding']=='gzip'  # static files compress too


# ---- issue #20: URL-addressable questions (the Datasette model) ----

def test_a_typed_question_redirects_to_its_canonical_url_and_the_url_answers(running):
    """GET /q resolves the question with the offline router and redirects; the canonical URL serves the page,
    the answer as .json (or with Accept: application/json), and the PDF with format=pdf. No token, no session."""
    sessions_before,reports_before=len(running.app.sessions),len(running.app.reports)
    status,headers,body=request(running,'GET','/q?day=2026-09-14&shift=2&scope=shift&period=day&q=Why+was+this+shift+slow%3F+Chart+downtime+by+reason.')
    assert status==302 and headers['Location']=='/corr/1/day/2026-09-14/shift/2?measure=overview' and json.loads(body)['url']==headers['Location']
    assert headers['Cache-Control']=='no-store'
    canonical=headers['Location']
    status,headers,body=request(running,'GET',canonical,headers={'Accept':'application/json'})
    result=json.loads(body)
    assert status==200 and headers['Content-Type'].startswith('application/json') and result['status']=='complete'
    assert result['url']==canonical and 'session_id' not in result and result['usage']['model_calls']==0
    assert [t['tool'] for t in result['trace'] if t['tool']!='render_chart']==['get_shift_kpis','get_downtime_breakdown','get_wet_end_performance','get_shift_notes']
    assert result['context']['history']==[{'question':canonical,'shifts':[2],'intent':'overview'}]
    served=float(headers['Server-Timing'].split('dur=')[1])
    assert 0<served<250, headers['Server-Timing']   # acceptance: server time under 250 ms
    assert 'Synthetic production investigation' in result['report_markdown']
    # the same answer three ways: .json suffix, format=json, Accept header
    status,_,again=request(running,'GET','/corr/1/day/2026-09-14/shift/2.json?measure=overview')
    assert status==200 and json.loads(again)['sections']==result['sections'] and json.loads(again)['url']==canonical
    status,_,again=request(running,'GET',canonical+'&format=json')
    assert status==200 and json.loads(again)['sections']==result['sections']
    assert (len(running.app.sessions),len(running.app.reports))==(sessions_before,reports_before)   # no session, no retained report
    # the POST answer to the same question names the same address
    token=json.loads(request(running,'GET','/api/config')[2])['token']
    post_headers={'Content-Type':'application/json','X-Workbench-Token':token}
    status,_,posted=request(running,'POST','/api/ask',json.dumps({'day':'2026-09-14','shift':2,'question':'Why was this shift slow? Chart downtime by reason.'}),post_headers)
    assert status==200 and json.loads(posted)['url']==canonical and json.loads(posted)['sections']==result['sections']
    # a browser navigation gets the app page (ETag-revalidated like /), which reads its own address
    status,headers,page=request(running,'GET',canonical,headers={'Accept':'text/html,application/xhtml+xml,*/*;q=0.8'})
    assert status==200 and headers['Content-Type'].startswith('text/html') and b'Shift Investigator' in page and headers['ETag']
    assert request(running,'GET',canonical,headers={'Accept':'text/html','If-None-Match':headers['ETag']})[0]==304
    assert request(running,'GET','/corr/1/day/2026-09-14/shift/2',headers={'Accept':'text/html'})[0]==200   # the bare scope is a page
    assert request(running,'GET','/corr/1/day/2026-09-14/shift/4',headers={'Accept':'text/html'})[0]==404   # not an address
    # the PDF is the same address with format=pdf: no report cache, so any instance can serve it
    status,headers,pdf=request(running,'GET',canonical+'&format=pdf')
    assert status==200 and headers['Content-Type']=='application/pdf' and pdf.startswith(b'%PDF-') and 'synthetic-report-2026-09-14.pdf' in headers['Content-Disposition']
    assert running.app.lock.locked() is False and running.app.read_lock.locked() is False


def test_the_url_path_refuses_like_the_post_path(running):
    for path,status,message in (
        ('/q?day=2026-09-14&shift=2&q=What+is+the+weather',400,'Try an example question'),
        ('/q?day=2026-09-13&shift=2&q=Show+speed',400,'Sunday'),
        ('/q?day=2026-09-14&shift=9&q=Show+speed',400,'supported shift'),
        ('/q?day=2026-09-14&shift=2&period=year&q=Show+speed',400,'accounting period'),
        ('/q?day=2026-09-14&shift=2&mode=openai&q=Show+speed',400,'Invalid request fields'),
        ('/q?day=2026-09-14&shift=2&scope=shift&q=Show+shift+1',400,'Selected shift only'),
        ('/q?day=2026-09-14&shift=2&period=week&q=Show+setup+matrix',400,'Switch Period to Day'),
        ('/corr/1/day/2026-09-14/shift/2.json',400,'Add measure='),
        ('/corr/1/day/2026-09-14/shift/2.json?measure=overview&bogus=1',400,'Unknown query field'),
        ('/corr/1/day/2026-09-12/shift/2.json?measure=overview',400,'Saturday'),
        ('/corr/1/week/2026-09-14/shift/2.json?measure=setups',400,'Switch Period to Day'),
        ('/corr/1/day/2026-09-14/shift/2.json?measure=report',400,'needs Week, Month or 4-4-5'),
        ('/corr/2/day/2026-09-14/shift/2.json?measure=overview',404,'No machine'),
    ):
        got,_,body=request(running,'GET',path)
        assert got==status and message in json.loads(body)['error'], (path,got,body)
    assert request(running,'GET','/q?day=2026-09-14&shift=2&q=Show+speed',headers={'Host':'evil.example'})[0]==403


def test_follow_ups_reports_and_the_bad_record_ride_in_the_url(running):
    """after= carries the conversation (no session), measure=report is the deterministic period report, and
    corrupt=1 is the Advanced toggle; the redirect keeps all three on the canonical address."""
    status,headers,_=request(running,'GET','/q?day=2026-09-14&shift=2&scope=day&q=Why+was+second+shift+slow%3F')
    first=headers['Location']
    assert status==302 and first=='/corr/1/day/2026-09-14?measure=overview&shifts=2'
    status,headers,_=request(running,'GET','/q?day=2026-09-14&shift=2&scope=day&q=Compare+that+with+first+shift&after='+first.replace('&','%26'))
    second=headers['Location']
    assert second=='/corr/1/day/2026-09-14?measure=compare&shifts=2,1&after=/corr/1/day/2026-09-14?measure=overview%26shifts=2'
    status,_,body=request(running,'GET',second+'&format=json')
    result=json.loads(body)
    assert status==200 and result['selected_shifts']==[2,1] and result['url']==second
    assert [h['question'] for h in result['context']['history']]==[first,'/corr/1/day/2026-09-14?measure=compare&shifts=2,1']
    status,_,body=request(running,'GET','/corr/1/month/2026-08-12/shift/2.json?measure=report')
    result=json.loads(body)
    assert status==200 and result['trace'][0]['tool']=='get_period_report' and result['period_label']=='2026-08-01 to 2026-08-31'
    assert result['url']=='/corr/1/month/2026-08-12/shift/2?measure=report'
    status,_,body=request(running,'GET','/corr/1/day/2026-09-14/shift/2.json?measure=setups&corrupt=1')
    result=json.loads(body)
    assert status==200 and result['trace'][0]['data']['coverage']=='incomplete' and result['url'].endswith('?measure=setups&corrupt=1')
    status,headers,_=request(running,'GET','/q?day=2026-09-14&shift=2&corrupt=1&q=Show+setup+matrix')
    assert headers['Location']=='/corr/1/day/2026-09-14/shift/2?measure=setups&corrupt=1'
    token=json.loads(request(running,'GET','/api/config')[2])['token']
    status,_,body=request(running,'POST','/api/ask',json.dumps({'day':'2026-08-12','shift':2,'period':'month','question':'Build month report','report':True}),
                          {'Content-Type':'application/json','X-Workbench-Token':token})
    assert status==200 and json.loads(body)['url']=='/corr/1/month/2026-08-12/shift/2?measure=report'
    assert json.loads(request(running,'GET','/api/config')[2])['machine']=='1'


def test_url_answers_count_against_the_request_window_and_the_per_ip_limit(tmp_path):
    from investigator.server import DemoGuards
    app=Workbench(guards=DemoGuards(rate_limit_per_min=2,ledger=tmp_path/'ledger.sqlite3'))
    try:
        app.client_round('198.51.100.7'); app.client_round('198.51.100.7')
        with pytest.raises(Exception,match='slow down'):
            app.client_round('198.51.100.7')
        result,fmt=app.answer_url('/corr/1/day/2026-09-14/shift/2?measure=fpm')
        assert fmt is None and result['status']=='complete' and app.datasets=={} and set(app.read_datasets)=={('2026-09-14',False)}
        result,fmt=app.answer_url('/corr/1/day/2026-09-14/shift/2?measure=fpm&format=pdf')
        assert fmt=='pdf'
        assert app.question_url('day=2026-09-14&shift=2&q=Show+speed+to+target')=='/corr/1/day/2026-09-14/shift/2?measure=fpm'
    finally:app.close()
    server=make_server(0)
    try:
        server.app.request_limit=1
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        assert request(server,'GET','/corr/1/day/2026-09-14/shift/2.json?measure=fpm')[0]==200
        status,_,body=request(server,'GET','/corr/1/day/2026-09-14/shift/2.json?measure=fpm')
        assert status==429 and 'request limit' in json.loads(body)['error']
        assert request(server,'GET','/corr/1/day/2026-09-14/shift/2',headers={'Accept':'text/html'})[0]==200   # the page itself is not a round
    finally:
        server.shutdown();thread.join();server.server_close();server.app.close()
