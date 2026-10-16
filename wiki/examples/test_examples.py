"""Run with API project dependencies available; no connection to MAX."""
import io,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
from client import MaxAPI
from bot import reply_for

class ExamplesTest(unittest.TestCase):
    def test_commands_and_self_guard(self):
        msg={'sender':'1','chat_id':'-2','text':'/ping'}
        self.assertEqual(reply_for(msg,'9',{'-2'}),'pong 👋')
        self.assertIsNone(reply_for(msg,'1',{'-2'}))
        self.assertIsNone(reply_for(msg,'9',{'-3'}))
        self.assertIsNone(reply_for({**msg,'link':{'type':'FORWARD'}},'9',{'-2'}))
        self.assertIsNone(reply_for({**msg,'sender':None},'9',{'-2'}))
        self.assertIsNone(reply_for({**msg,'text':'arbitrary'},'9',{'-2'}))
        self.assertTrue(reply_for({**msg,'text':'/time'},'9',{'-2'}).startswith('Анадырь:'))

    def test_id_preservation(self):
        api=MaxAPI('http://test','secret')
        with patch('client.urlopen',return_value=io.BytesIO(b'{"id":"42"}')) as request:
            api.send('-9223372036854775807','Привет 👋',reply_to='9223372036854775806')
        req=request.call_args.args[0];payload=json.loads(req.data)
        self.assertEqual(payload['chat_id'],'-9223372036854775807')
        self.assertEqual(payload['reply_to'],'9223372036854775806')
        self.assertEqual(req.get_header('Authorization'),'Bearer secret')

    def test_pagination(self):
        api=MaxAPI('http://test','secret')
        pages=[{'chats':[{'id':'1'}],'next_marker':'100','complete':False},{'chats':[{'id':'2'}],'next_marker':None,'complete':True}]
        with patch.object(api,'request',side_effect=pages):
            self.assertEqual([c['id'] for c in api.chats()],['1','2'])
        with patch.object(api,'request',return_value={'chats':[],'next_marker':None,'complete':False}):
            with self.assertRaises(RuntimeError):list(api.chats())

    def test_form_against_actual_api(self):
        import tempfile
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.service import Service
        with tempfile.TemporaryDirectory(dir='.') as directory:
            key='t'*40
            with TestClient(create_app(Service(directory),api_key=key,run_scheduler=False,restore_session=False)) as http:
                def bridge(req,timeout):
                    r=http.request(req.method,req.full_url.replace('http://test',''),content=req.data,headers=dict(req.header_items()))
                    self.assertLess(r.status_code,400,r.text)
                    return io.BytesIO(r.content)
                api=MaxAPI('http://test',key)
                with patch('client.urlopen',side_effect=bridge):
                    draft=api.save({'title':'Wiki','text':'Hello','chat_ids':['10'],'enabled':False})
                    self.assertEqual(draft['timezone'],'Asia/Anadyr')
                    changed=api.save({'title':'Wiki','text':'Edited','chat_ids':['10'],'enabled':False},identifier=draft['id'])
                    self.assertEqual(changed['text'],'Edited')
                    rule=api.request('POST','relays',{'title':'Wiki rule','destination':'30','source_chat_ids':['10'],'mode':'copy','enabled':False})
                    self.assertEqual(rule['mode'],'copy')
                    preview=api.request('POST','schedule/preview',{'cron':'0 9 * * 1-5','timezone':'Asia/Anadyr'})
                    self.assertEqual(len(preview['upcoming']),5)
                    self.assertEqual(api.request('GET','relays')['jobs'],[])

if __name__=='__main__':unittest.main()
