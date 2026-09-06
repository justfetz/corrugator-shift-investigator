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
