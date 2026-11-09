"""Portable safety, configuration, MCP and HTTP smoke tests."""
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

os.environ['SYNAPSE_MAC_HOME']=tempfile.mkdtemp(prefix='synapse-test-home-')
from synapse_mac import config, tools, mcp, web, remote


class ConfigTests(unittest.TestCase):
    def setUp(self):
        config.init()
        config.resume()
        config.update_settings({'remote_access_enabled':False,'control_enabled':False,'shell_enabled':False,'gui_enabled':False,'allowed_roots':[str(Path(os.environ['SYNAPSE_MAC_HOME']))]})

    def test_separate_secrets(self):
        self.assertNotEqual(config.token('admin'),config.token('mcp'))
        self.assertGreaterEqual(len(config.token('admin')),40)
        self.assertEqual((config.BASE/'admin.token').stat().st_mode & 0o777,0o600)

    def test_read_root_enforced(self):
        with self.assertRaises(PermissionError):
            tools.inside_roots('/etc/passwd')

    def test_mutations_disabled(self):
        with self.assertRaises(PermissionError):
            tools.execute('open_application',{'name':'Finder'})
        with self.assertRaises(PermissionError):
            tools.execute('run_shell',{'command':'true'})

    def test_control_needs_separate_gates(self):
        config.update_settings({'control_enabled':True})
        with self.assertRaises(PermissionError):
            tools.execute('run_shell',{'command':'true'})
        with self.assertRaises(PermissionError):
            tools.execute('keyboard_type',{'text':'A'})

    def test_stop_blocks_all_mutations(self):
        config.update_settings({'control_enabled':True,'shell_enabled':True,'gui_enabled':True})
        config.stop()
        with self.assertRaises(PermissionError):
            tools.execute('run_shell',{'command':'true'})
        self.assertTrue(tools.execute('ping',{})['stopped'])

    def test_file_read_write_safe(self):
        f=config.BASE/'test.txt'
        config.update_settings({'control_enabled':True})
        self.assertTrue(tools.execute('write_text_file',{'path':str(f),'content':'test'})['written'])
        self.assertEqual(tools.execute('read_text_file',{'path':str(f)})['content'],'test')
        with self.assertRaises(FileExistsError):
            tools.execute('write_text_file',{'path':str(f),'content':'overwrite'})

    def test_invalid_settings_rejected(self):
        with self.assertRaises(ValueError):
            config.update_settings({'shell_enabled':'yes'})
        with self.assertRaises(ValueError):
            config.update_settings({'max_command_seconds':0})

    def test_mcp_initialize_and_tools(self):
        init=mcp.rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})
        self.assertEqual(init['result']['protocolVersion'],'2025-06-18')
        resp=mcp.rpc({'jsonrpc':'2.0','id':2,'method':'tools/list'})
        self.assertGreaterEqual(len(resp['result']['tools']),10)
        denied=mcp.rpc({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'run_shell','arguments':{'command':'true'}}})
        self.assertTrue(denied['result']['isError'])
        self.assertIsNone(mcp.rpc({'jsonrpc':'2.0','method':'notifications/initialized'}))

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=web.ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
        cls.server.daemon_threads=True
        cls.t=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.t.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.t.join(timeout=2)

    def request(self,method,path,data=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4)
        h=headers or {}
        if data is not None:h={**h,'Content-Type':'application/json'}
        c.request(method,path,body=json.dumps(data) if data is not None else None,headers=h)
        r=c.getresponse();body=r.read();hs=dict(r.getheaders());status=r.status;c.close()
        return status,json.loads(body) if body else {},hs

    def test_health_and_mcp_auth(self):
        self.assertEqual(self.request('GET','/health')[0],200)
        self.assertEqual(self.request('POST','/mcp',{'jsonrpc':'2.0','id':1,'method':'ping'})[0],401)
        status,obj,_=self.request('POST','/mcp',{'jsonrpc':'2.0','id':1,'method':'ping'},headers={'Authorization':'Bearer '+config.token('mcp')})
        self.assertEqual(status,200)
        self.assertEqual(obj['result'],{})

    def test_admin_login(self):
        self.assertEqual(self.request('GET','/api/status')[0],401)
        status,obj,headers=self.request('POST','/api/login',{'token':config.token('admin')})
        self.assertEqual(status,200)
        cookie=headers['Set-Cookie'].split(';')[0]
        status,obj,_=self.request('GET','/api/status',headers={'Cookie':cookie})
        self.assertEqual(status,200)
        self.assertEqual(obj['settings']['port'],8765)


# Dual-channel tests deliberately avoid starting public tunnels or using network.
from unittest.mock import patch
from synapse_mac import channels

class ChannelTests(unittest.TestCase):
    def setUp(self):
        config.init()
        config.update_settings({'remote_access_enabled': True, 'channels': {'main': '', 'backup': ''}})

    def test_https_domains_are_strict(self):
        self.assertEqual(channels.clean_url('https://mac.example.ts.net', 'main'), 'https://mac.example.ts.net')
        self.assertEqual(channels.clean_url('https://example.ngrok-free.app', 'backup'), 'https://example.ngrok-free.app')
        for url in ('http://mac.example.ts.net', 'https://user:pass@mac.example.ts.net',
                    'https://mac.example.ts.net/path', 'https://evil.example.org',
                    'https://mac.example.ts.net?token=abc'):
            with self.assertRaises(ValueError):
                channels.clean_url(url, 'main')
        with self.assertRaises(ValueError):
            channels.clean_url('https://mac.example.ts.net', 'backup')

    def test_configured_host_only(self):
        channels.set_public_url('main', 'https://mac.tailnet.ts.net')
        self.assertTrue(channels.remote_origin_host('mac.tailnet.ts.net'))
        self.assertFalse(channels.remote_origin_host('unrelated.ts.net'))
        self.assertFalse(channels.remote_origin_host('mac.tailnet.ts.net.evil.org'))
        channels.set_public_url('main', '')
        self.assertFalse(channels.remote_origin_host('mac.tailnet.ts.net'))

    def test_http_remote_login_requires_exact_host_origin(self):
        server=web.ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
        server.daemon_threads=True
        t=threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        def get(host, origin=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Host':host}
            if origin: headers['Origin']=origin
            c.request('GET','/health',headers=headers)
            r=c.getresponse();s=r.status;r.read();c.close()
            return s
        try:
            channels.set_public_url('backup','https://reserve.ngrok-free.app')
            self.assertEqual(get('reserve.ngrok-free.app'),200)
            self.assertEqual(get('reserve.ngrok-free.app','https://reserve.ngrok-free.app'),200)
            self.assertEqual(get('reserve.ngrok-free.app','https://attack.example'),403)
            self.assertEqual(get('other.ngrok-free.app'),403)
        finally:
            server.shutdown();server.server_close();t.join(timeout=2)

    def test_main_start_uses_background_funnel(self):
        with patch.object(channels.platform, 'system', return_value='Darwin'), \
             patch.object(channels.shutil, 'which', return_value='/bin/tailscale'), \
             patch.object(channels, '_run') as run, \
             patch.object(channels, 'status', return_value={'ok':True}):
            self.assertEqual(channels.start('main'), {'ok':True})
            self.assertEqual(run.call_args.args[0], ['/bin/tailscale','funnel','--bg','--yes','8765'])

    def test_backup_requires_ngrok_cli(self):
        with patch.object(channels.platform,'system',return_value='Darwin'), \
             patch.object(channels.shutil,'which',return_value=None):
            with self.assertRaises(RuntimeError):
                channels.start('backup')


from synapse_mac import failover

class FailoverTests(unittest.TestCase):
    def test_selects_main_when_available(self):
        cfg={'main':'https://first.ts.net','backup':'https://second.ngrok-free.app'}
        self.assertEqual(failover.select_base(cfg,health=lambda u: True)[1], 'main')

    def test_switches_to_backup_before_request(self):
        cfg={'main':'https://first.ts.net','backup':'https://second.ngrok-free.app'}
        selected=failover.select_base(cfg,health=lambda u:u.endswith('ngrok-free.app'))
        self.assertEqual(selected[1], 'backup')

    def test_unreachable_both_report_error(self):
        cfg={'main':'https://first.ts.net','backup':'https://second.ngrok-free.app'}
        with self.assertRaises(ConnectionError):
            failover.select_base(cfg,health=lambda u:False)


class RemoteStopTests(unittest.TestCase):
    def test_remote_cannot_clear_emergency_stop(self):
        server=web.ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
        server.daemon_threads=True
        t=threading.Thread(target=server.serve_forever,daemon=True)
        t.start()
        config.update_settings({'remote_access_enabled':True})
        channels.set_public_url('main','https://a-example.ts.net')
        config.stop()
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
            c.request('POST','/api/login',body=json.dumps({'token':config.token('admin')}),headers={'Content-Type':'application/json','Host':'a-example.ts.net','Origin':'https://a-example.ts.net'})
            r=c.getresponse();header=r.getheader('Set-Cookie');r.read()
            self.assertEqual(r.status,200)
            self.assertIn('Secure',header)
            c.close()
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
            c.request('POST','/api/resume',body='{}',headers={'Content-Type':'application/json','Host':'a-example.ts.net','Origin':'https://a-example.ts.net','Cookie':header.split(';')[0]})
            r=c.getresponse();r.read();c.close()
            self.assertEqual(r.status,400)
            self.assertTrue(config.stopped())
        finally:
            config.resume()
            channels.set_public_url('main','')
            server.shutdown();server.server_close();t.join(timeout=2)



class RemoteAccessTests(unittest.TestCase):
    def setUp(self):
        config.init()
        config.update_settings({'remote_access_enabled':False,'channels':{'main':'','backup':''}})

    def test_remote_access_denied_until_enabled(self):
        channels.set_public_url('backup','https://test-remote.ngrok-free.app')
        self.assertFalse(channels.remote_origin_host('test-remote.ngrok-free.app'))
        config.update_settings({'remote_access_enabled':True})
        self.assertTrue(channels.remote_origin_host('test-remote.ngrok-free.app'))

    def test_remote_option_type_checked(self):
        with self.assertRaises(ValueError):
            config.update_settings({'remote_access_enabled':'yes'})

    def test_remote_up_tries_both_channels_and_enables_access(self):
        with patch.object(remote.channels, 'start', return_value={'ok':True}) as starter, \
             patch.object(remote.channels,'status',return_value={
                 'main':{'running':True,'url':'https://a.ts.net'},
                 'backup':{'running':True,'url':'https://b.ngrok-free.app'}}):
            outcome=remote.up(wait_seconds=0)
        self.assertEqual(starter.call_count,2)
        self.assertTrue(config.settings()['remote_access_enabled'])
        self.assertTrue(outcome['both_online'])

    def test_remote_down_is_fail_closed_even_if_cli_errors(self):
        config.update_settings({'remote_access_enabled':True})
        with patch.object(remote.channels,'stop',side_effect=RuntimeError('CLI failure')):
            result=remote.down()
        self.assertFalse(result['remote_access_enabled'])
        self.assertFalse(config.settings()['remote_access_enabled'])

    def test_http_public_blocked_after_remote_kill_switch(self):
        config.update_settings({'remote_access_enabled':True})
        channels.set_public_url('backup','https://test-remote.ngrok-free.app')
        server=web.ThreadingHTTPServer(('127.0.0.1',0),web.Handler)
        server.daemon_threads=True
        th=threading.Thread(target=server.serve_forever,daemon=True);th.start()
        try:
            def get():
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                conn.request('GET','/health',headers={'Host':'test-remote.ngrok-free.app'})
                r=conn.getresponse();ret=r.status;r.read();conn.close();return ret
            self.assertEqual(get(),200)
            config.update_settings({'remote_access_enabled':False})
            self.assertEqual(get(),403)
        finally:
            server.shutdown();server.server_close();th.join(timeout=2)

if __name__=="__main__": unittest.main()
