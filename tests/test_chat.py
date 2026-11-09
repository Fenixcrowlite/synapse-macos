"""Chat separation and confirmation gates. No external network or model required."""
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('SYNAPSE_MAC_HOME', tempfile.mkdtemp(prefix='synapse-chat-test-'))
from synapse_mac import chat, config, web


class FakeResponse:
    def __init__(self, body):
        self.body = io.BytesIO(json.dumps({'message': {'content': json.dumps(body)}}).encode())
    def __enter__(self): return self.body
    def __exit__(self, *args): pass


class ModelTests(unittest.TestCase):
    def test_input_validation(self):
        for bad in (None, [], [{'role':'system','content':'hack'}], [{'role':'user','content':'x'*8001}]):
            with self.assertRaises(ValueError): chat.check_history(bad)

    def test_free_local_model_no_tool_execution(self):
        def fake_urlopen(req, timeout):
            self.assertEqual(req.full_url, 'http://127.0.0.1:11434/api/chat')
            return FakeResponse({'message':'I can inspect running processes. Approve the proposed action.',
                                 'tool':'processes','arguments':{'filter':'chrome'}})
        with patch.object(chat.tools, 'execute', side_effect=AssertionError('Tool executed before approval')):
            result=chat.ask([{'role':'user','content':'Show chrome processes'}],transport=fake_urlopen)
        self.assertEqual(result['proposal']['name'],'processes')

    def test_unsupported_model_tool_is_rejected(self):
        def mock(req,timeout): return FakeResponse({'message':'Attempt','tool':'delete_home','arguments':{}})
        self.assertIsNone(chat.ask([{'role':'user','content':'Hello'}],transport=mock)['proposal'])


class ChatHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=web.ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
        cls.server.daemon_threads=True
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        config.init()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)

    def request(self, path, value, cookie=''):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4)
        conn.request('POST',path,body=json.dumps(value),headers={'Content-Type':'application/json','Cookie':cookie})
        res=conn.getresponse()
        content=res.read()
        out=(res.status,json.loads(content) if content else {},res.getheader('Set-Cookie'))
        conn.close()
        return out

    def test_unauthenticated_chat_denied(self):
        self.assertEqual(self.request('/api/chat',{'messages':[{'role':'user','content':'Hi'}]})[0],401)

    def test_confirm_once_with_server_side_stored_tool(self):
        config.resume()
        config.update_settings({'control_enabled':False,'shell_enabled':False})
        status,body,cookie=self.request('/api/login',{'token':config.token('admin')})
        self.assertEqual(status,200)
        cookie=cookie.split(';')[0]
        with patch.object(web.chat,'ask',return_value={'message':'I propose a safe diagnostic','proposal':{'name':'ping','arguments':{}}}), \
             patch.object(web.tools,'execute',return_value={'ok':True}) as executor:
            status,body,_=self.request('/api/chat',{'messages':[{'role':'user','content':'check'}]},cookie)
            self.assertEqual(status,200)
            self.assertFalse(executor.called)
            proposal=body['confirmation']
            status,result,_=self.request('/api/chat/execute',{'id':proposal['id']},cookie)
            self.assertEqual(status,200)
            self.assertTrue(result['ok'])
            self.assertEqual(executor.call_count,1)
            self.assertEqual(executor.call_args.args,('ping',{}))
            self.assertEqual(self.request('/api/chat/execute',{'id':proposal['id']},cookie)[0],400)

    def test_rejected_action_cannot_be_replayed(self):
        status,body,cookie=self.request('/api/login',{'token':config.token('admin')})
        cookie=cookie.split(';')[0]
        with patch.object(web.chat,'ask',return_value={'message':'Proposal','proposal':{'name':'ping','arguments':{}}}):
            _,body,_=self.request('/api/chat',{'messages':[{'role':'user','content':'ping'}]},cookie)
        nonce=body['confirmation']['id']
        self.assertEqual(self.request('/api/chat/reject',{},cookie)[0],200)
        self.assertEqual(self.request('/api/chat/execute',{'id':nonce},cookie)[0],400)

    def test_chat_shell_permission_rechecked(self):
        status,body,cookie=self.request('/api/login',{'token':config.token('admin')})
        cookie=cookie.split(';')[0]
        config.update_settings({'control_enabled':False,'shell_enabled':False})
        with patch.object(web.chat,'ask',return_value={'message':'Proposal','proposal':{'name':'run_shell','arguments':{'command':'echo ok'}}}):
            status,body,_=self.request('/api/chat',{'messages':[{'role':'user','content':'run'}]},cookie)
        status,result,_=self.request('/api/chat/execute',{'id':body['confirmation']['id']},cookie)
        self.assertFalse(result['ok'])
        self.assertIn('Control disabled',result['error'])

if __name__=='__main__': unittest.main()
