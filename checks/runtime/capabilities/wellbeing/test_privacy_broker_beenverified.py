import json
import socketserver
import threading
from contextlib import closing

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_broker_beenverified import register
from gideon.workspace.capabilities.communications import PeopleStore, mirrors
from gideon.workspace.capabilities.communications.outbound_email import OutboundEmail, SMTPTransport
from gideon.workspace.capabilities.wellbeing.privacy import PrivacyStore
from gideon.workspace.capabilities.wellbeing.privacy_broker_beenverified import RECIPIENT, BeenVerifiedCaseAdapter
from gideon.workspace.capabilities.wellbeing.privacy_brokers import BrokerAdapterResult, PrivacyBrokerStore
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


class SMTPContract:
    def __init__(self, drop=False): self.messages=[]; self.drop=drop
    def start(self):
        owner=self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b'220 local\r\n'); sender=''; recipients=[]
                while True:
                    line=self.rfile.readline()
                    if not line: return
                    value=line.decode(errors='replace').rstrip('\r\n'); upper=value.upper()
                    if upper.startswith('EHLO '): self.wfile.write(b'250-local\r\n250 SIZE 20971520\r\n')
                    elif upper.startswith('MAIL FROM:'): sender=value.split(':',1)[1].split()[0].strip('<>'); self.wfile.write(b'250 ok\r\n')
                    elif upper.startswith('RCPT TO:'): recipients.append(value.split(':',1)[1].strip('<>')); self.wfile.write(b'250 ok\r\n')
                    elif upper=='DATA':
                        self.wfile.write(b'354 data\r\n'); raw=bytearray()
                        while True:
                            part=self.rfile.readline()
                            if part==b'.\r\n': break
                            raw.extend(part[1:] if part.startswith(b'..') else part)
                        owner.messages.append({'sender':sender,'recipients':recipients,'data':bytes(raw)})
                        if owner.drop: return
                        self.wfile.write(b'250 accepted\r\n')
                    elif upper=='QUIT': self.wfile.write(b'221 bye\r\n'); return
                    else: self.wfile.write(b'250 ok\r\n')
        self.server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start(); return self
    @property
    def port(self): return self.server.server_address[1]
    def close(self): self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def setup(tmp_path, smtp):
    privacy=PrivacyStore(tmp_path/'privacy')
    subject=privacy.create_subject({'request_id':'subject','alias':'Owner','relationship':'self','source':'owner'})
    for scope in ('broker_scan','broker_submit'):
        privacy.consent(subject['id'],{'request_id':scope,'revision':0,'scope':scope,'granted':True,'method':'owner choice'})
    store=PrivacyBrokerStore(tmp_path/'privacy')
    broker=store.create_broker({'request_id':'broker','name':'BeenVerified','website':'https://www.beenverified.com','optout_url':'https://www.beenverified.com/svc/optout/search/optouts','source':'curated donor protocol'})
    case=store.create_case(subject['id'],{'request_id':'case','broker_id':broker['id']})
    found=BrokerAdapterResult('owned-observation-adapter','found','2026-09-25T12:00:00+00:00','sha256:'+'a'*64)
    case=store.apply_adapter_scan(case['id'],{'request_id':'scan','revision':case['revision']},found)
    communications=PeopleStore(tmp_path/'communications')
    account=mirrors.save_account(communications,{'name':'Owner email','kind':'imap','owner_email':'owner@example.com','alias':'custom','host':'imap.example.com','username':'owner@example.com','credential_ref':'OWNER_EMAIL','auth_mode':'password','inbox_folder':'INBOX','sent_folder':'Sent'})
    transport=SMTPTransport(host='127.0.0.1',port=smtp.port,starttls=False,authenticate=False)
    outbound=OutboundEmail(communications,transport=transport)
    return store,communications,account,case,BeenVerifiedCaseAdapter(store,communications,outbound)


def prepare(service, account, case, request='prepare'):
    return service.prepare(case['id'],{'request_id':request,'revision':case['revision'],'account_id':account['id'],'full_name':'Jane Doe','contact_email':'owner@example.com','profile_url':'https://www.beenverified.com/name/Jane-Doe/Oakland-CA/abc','jurisdiction':'US-CA'})


def approve(service, result):
    return service.approve(result['case']['id'],{'revision':result['case']['revision'],'draft_revision':result['draft']['revision'],'content_sha256':result['draft']['content_sha256'],'confirm_exact':True})


def send(service, result, request='send'):
    return service.send(result['case']['id'],{'request_id':request,'revision':result['case']['revision'],'draft_revision':result['draft']['revision'],'content_sha256':result['draft']['content_sha256'],'confirm_send':True})


@pytest.fixture
def smtp():
    value=SMTPContract().start(); yield value; value.close()


def test_prepare_creates_exact_canonical_draft_without_network_and_binds_case(tmp_path,smtp):
    store,_,account,case,service=setup(tmp_path,smtp)
    result=prepare(service,account,case)
    assert result['case']['state']=='optout_in_progress' and result['case']['revision']==3
    draft=result['draft']
    assert draft['state']=='draft' and draft['sender']=='owner@example.com'
    assert draft['to']==[RECIPIENT] and draft['provider_acceptance']=='not_submitted'
    assert 'Jane Doe' in draft['body'] and 'https://www.beenverified.com/name/Jane-Doe/Oakland-CA/abc' in draft['body']
    assert 'CCPA section 1798.105' in draft['body']
    assert result['plan']=={'recipient':RECIPIENT,'requires_exact_content_approval':True,'message_sent':False,'external_verification_required':True,'primary_suppression_form_completed':False,'affiliate_rescans_required':True}
    assert smtp.messages==[]
    persisted=store.get_case(case['id'])
    assert persisted['evidence'].startswith('outbound-email:'+draft['id']+':sha256:')
    assert 'owner@example.com' not in json.dumps(store.events(case['id']))


def test_exact_approval_and_separate_send_use_real_smtp_and_keep_delivery_uncertain(tmp_path,smtp):
    _,_,account,case,service=setup(tmp_path,smtp)
    result=prepare(service,account,case)
    with pytest.raises(MeasurementError,match='Exact recipient'):
        service.approve(case['id'],{'revision':result['case']['revision'],'draft_revision':result['draft']['revision'],'content_sha256':result['draft']['content_sha256'],'confirm_exact':False})
    assert smtp.messages==[]
    approved=approve(service,result)
    assert approved['draft']['state']=='approved' and smtp.messages==[]
    sent=send(service,approved)
    assert sent['case']['state']=='submitted'
    assert sent['draft']['state']=='accepted' and sent['draft']['provider_acceptance']=='accepted'
    assert sent['draft']['delivery']=='uncertain'
    assert len(smtp.messages)==1 and smtp.messages[0]['sender']=='owner@example.com'
    assert smtp.messages[0]['recipients']==[RECIPIENT]
    encoded=smtp.messages[0]['data'].decode()
    assert 'BeenVerified deletion request for Jane Doe' in encoded
    assert 'opt that information out of sale or sharing' in encoded
    assert 'NeighborWho, Ownerly, NumberGuru, and Bumper' in encoded
    replay=send(service,sent,'send')
    assert replay['case']['state']=='submitted' and len(smtp.messages)==1


def test_account_identity_profile_and_case_provider_fail_closed_before_email(tmp_path,smtp):
    store,communications,account,case,service=setup(tmp_path,smtp)
    for changes,match in [({'contact_email':'other@example.com'},'match the selected account'),({'profile_url':'https://attacker.example/jane'},'BeenVerified listing')]:
        payload={'request_id':'bad-'+match[:3],'revision':case['revision'],'account_id':account['id'],'full_name':'Jane Doe','contact_email':'owner@example.com','profile_url':'https://www.beenverified.com/name/jane','jurisdiction':'US-OTHER'}; payload.update(changes)
        with pytest.raises(MeasurementError,match=match): service.prepare(case['id'],payload)
    local=mirrors.save_account(communications,{'name':'Local','kind':'maildir','owner_email':'owner@example.com','alias':'custom','inbox_folder':'INBOX','sent_folder':'Sent'})
    with pytest.raises(MeasurementError,match='credential-bound'):
        service.prepare(case['id'],{'request_id':'local','revision':case['revision'],'account_id':local['id'],'full_name':'Jane Doe','contact_email':'owner@example.com','profile_url':'https://www.beenverified.com/name/jane','jurisdiction':'US-OTHER'})
    other=store.create_broker({'request_id':'other','name':'Other','website':'https://example.com','source':'owner'})
    other_case=store.create_case(store.get_case(case['id'])['subject_id'],{'request_id':'other-case','broker_id':other['id']})
    with pytest.raises(MeasurementError,match='BeenVerified cases only'):
        service.prepare(other_case['id'],{'request_id':'wrong','revision':other_case['revision'],'account_id':account['id'],'full_name':'Jane Doe','contact_email':'owner@example.com','profile_url':'https://www.beenverified.com/name/jane','jurisdiction':'US-OTHER'})
    assert smtp.messages==[]


def test_uncertain_transport_is_preserved_and_never_claims_delivery_or_removal(tmp_path):
    smtp=SMTPContract(drop=True).start()
    try:
        _,_,account,case,service=setup(tmp_path,smtp)
        result=approve(service,prepare(service,account,case)); sent=send(service,result)
        assert sent['draft']['state']=='uncertain' and sent['draft']['provider_acceptance']=='uncertain'
        assert sent['draft']['delivery']=='uncertain' and sent['case']['state']=='submitted'
        assert sent['case']['state']!='confirmed_removed'
        replay=send(service,sent); assert replay['draft']['state']=='uncertain' and len(smtp.messages)==1
    finally: smtp.close()


def ingest_reply(communications,account,draft,body):
    raw=(f'From: {RECIPIENT}\r\nTo: owner@example.com\r\nMessage-ID: <beenverified-reply@example.com>\r\nIn-Reply-To: {draft["message_id"]}\r\nSubject: Verify request\r\n\r\n{body}\r\n').encode()
    row=mirrors.normalize_message(raw,account['owner_email'],'fallback')
    with closing(communications.connect()) as db,db:
        mirrors.schema(db); db.execute('INSERT INTO mirror_messages VALUES (?,?,?)',(account['id'],row['external_id'],json.dumps(row)))
    return row


def test_correlation_matches_manual_code_from_canonical_reply_and_never_opens_link(tmp_path,smtp,monkeypatch):
    _,communications,account,case,service=setup(tmp_path,smtp)
    sent=send(service,approve(service,prepare(service,account,case)))
    inbound=ingest_reply(communications,account,sent['draft'],'Your verification code is 482913. Review https://untrusted.example/continue')
    opened=[]; monkeypatch.setattr('urllib.request.urlopen',lambda *args,**kwargs: opened.append(args))
    with pytest.raises(MeasurementError,match='does not match'):
        service.correlate(case['id'],{'revision':sent['case']['revision'],'manual_code':'1111'})
    result=service.correlate(case['id'],{'revision':sent['case']['revision'],'manual_code':'482913'})
    assert result['verification']=={'reply_correlated':True,'manual_code_matched':True,'removal_confirmed':False,'links_opened':False}
    assert result['draft']['verification']['message_external_id']==inbound['external_id']
    assert result['draft']['verification']['links']==[{'url':'https://untrusted.example/continue','opened':False}]
    assert result['case']['state']=='submitted' and opened==[]


@pytest.mark.asyncio
async def test_real_http_handler_drives_prepare_approve_send_and_correlation(tmp_path,smtp):
    _,communications,account,case,service=setup(tmp_path,smtp)
    app=web.Application(); register(app,adapter=service)
    base=f'/api/capabilities/wellbeing/privacy/broker-cases/{case["id"]}/providers/beenverified'
    async with TestClient(TestServer(app)) as client:
        response=await client.post(base+'/prepare',json={'request_id':'http-prepare','revision':case['revision'],'account_id':account['id'],'full_name':'Jane Doe','contact_email':'owner@example.com','profile_url':'https://www.beenverified.com/name/jane','jurisdiction':'US-OTHER'})
        assert response.status==200 and response.headers['Cache-Control']=='no-store'; prepared=await response.json()
        response=await client.post(base+'/send',json={'request_id':'premature','revision':prepared['case']['revision'],'draft_revision':prepared['draft']['revision'],'content_sha256':prepared['draft']['content_sha256'],'confirm_send':True})
        assert response.status==409 and smtp.messages==[]
        response=await client.post(base+'/approve',json={'revision':prepared['case']['revision'],'draft_revision':prepared['draft']['revision'],'content_sha256':prepared['draft']['content_sha256'],'confirm_exact':True}); approved=await response.json()
        response=await client.post(base+'/send',json={'request_id':'http-send','revision':approved['case']['revision'],'draft_revision':approved['draft']['revision'],'content_sha256':approved['draft']['content_sha256'],'confirm_send':True}); sent=await response.json()
        assert response.status==200 and sent['draft']['delivery']=='uncertain' and len(smtp.messages)==1
        ingest_reply(communications,account,sent['draft'],'Code 7654. https://untrusted.example/x')
        response=await client.post(base+'/correlate',json={'revision':sent['case']['revision'],'manual_code':'7654'}); correlated=await response.json()
        assert response.status==200 and correlated['verification']['manual_code_matched'] is True
        assert correlated['verification']['removal_confirmed'] is False
