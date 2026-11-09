"""Local admin UI and authenticated MCP HTTP endpoint; binds to loopback only."""
import hmac
import http.cookies
import json
import mimetypes
import secrets
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from . import config, mcp, tools, channels, remote, chat

STATIC = Path(__file__).with_name('static')
SESSIONS = {}
SESSION_LOCK = threading.Lock()
PENDING = {}  # session -> (nonce, time-to-live, immutable tool proposal)


def audit(event, outcome):
    try:
        config.init()
        with (config.BASE/'audit.log').open('a',encoding='utf-8') as fp:
            fp.write(json.dumps({'timestamp':int(time.time()),'event':event[:100],'result':outcome[:80]})+'\n')
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version='SynapseMac/1.2'

    def log_message(self, fmt, *args):
        # Avoid logging token-bearing URLs, headers or request bodies.
        return

    def reply(self, status, obj, headers=None):
        raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        self.wfile.write(raw)

    def fail(self,status,reason):
        self.reply(status,{'error':reason})

    def safe_request(self):
        raw_host=self.headers.get('Host','').lower()
        local_hosts={'localhost','127.0.0.1','[::1]', f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
        local=raw_host in local_hosts
        remote=channels.remote_origin_host(raw_host)
        if not local and not remote:
            self.fail(403,'Unrecognized hostname; register public endpoint locally first')
            return False
        origin=self.headers.get('Origin','')
        allowed_origins={f'http://localhost:{self.server.server_port}', f'http://127.0.0.1:{self.server.server_port}'}
        if local: allowed_origins.update({'http://localhost','http://127.0.0.1'})
        if remote: allowed_origins.add('https://' + raw_host.removesuffix(':443'))
        if origin and origin not in allowed_origins:
            self.fail(403,'Origin not allowed')
            return False
        return True

    def session_id(self):
        cookie=http.cookies.SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except http.cookies.CookieError: return False
        item=cookie.get('synapse_session')
        if not item: return None
        sid=item.value
        with SESSION_LOCK:
            expiry=SESSIONS.get(sid,0)
            if expiry<=time.monotonic():
                SESSIONS.pop(sid,None)
                PENDING.pop(sid,None)
                return None
        return sid

    def logged_in(self):
        return self.session_id() is not None

    def bearer(self):
        hdr=self.headers.get('Authorization','')
        prefix='Bearer '
        return hdr.startswith(prefix) and hmac.compare_digest(hdr[len(prefix):],config.token('mcp'))

    def body(self):
        if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
            raise ValueError('Content-Type application/json required')
        length=int(self.headers.get('Content-Length','0'))
        if not 0<=length<=512*1024: raise ValueError('Request too large')
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        if not self.safe_request(): return
        if self.path=='/health': return self.reply(200,{'alive':True})
        if self.path in ('/','/index.html','/app.js','/style.css'):
            filename={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css'}[self.path]
            data=(STATIC/filename).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type',{'index.html':'text/html; charset=utf-8','app.js':'application/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[filename])
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path=='/mcp':
            if not self.bearer(): return self.fail(401,'MCP bearer token required')
            self.send_response(405)
            self.send_header('Allow','POST')
            self.send_header('Content-Length','0')
            self.end_headers()
            return
        if not self.logged_in(): return self.fail(401,'Login required')
        if self.path=='/api/status':
            return self.reply(200,{'settings':config.settings(),'stopped':config.stopped(),'version':'1.3.0','tools':tools.describe(),'mcp_url':f'http://127.0.0.1:{self.server.server_port}/mcp'})
        if self.path=='/api/mcp-token': return self.reply(200,{'token':config.token('mcp')})
        if self.path=='/api/channels': return self.reply(200,channels.status())
        if self.path=='/api/remote/setup': return self.reply(200,remote.prerequisites())
        if self.path=='/api/audit':
            f=config.BASE/'audit.log'
            return self.reply(200,{'events':f.read_text(encoding='utf-8').splitlines()[-50:] if f.exists() else []})
        return self.fail(404,'Not found')

    def do_POST(self):
        if not self.safe_request(): return
        try: data=self.body()
        except (ValueError,json.JSONDecodeError) as e: return self.fail(400,str(e))
        if self.path=='/api/login':
            if not isinstance(data,dict) or not isinstance(data.get('token'),str): return self.fail(400,'Token required')
            if not hmac.compare_digest(data['token'],config.token('admin')):
                audit('login','denied')
                return self.fail(401,'Invalid admin token')
            sid=secrets.token_urlsafe(40)
            with SESSION_LOCK: SESSIONS[sid]=time.monotonic()+12*3600
            audit('login','ok')
            secure = '; Secure' if channels.remote_origin_host(self.headers.get('Host','').lower()) else ''
            return self.reply(200,{'ok':True},{'Set-Cookie':f'synapse_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}'})
        if self.path=='/mcp':
            if not self.bearer(): return self.fail(401,'MCP bearer token required')
            if not isinstance(data,dict) or (data.get('method')=='tools/call' and not isinstance(data.get('params'),dict)):
                return self.fail(400,'Invalid JSON-RPC object')
            out=mcp.rpc(data)
            if out is None:
                self.send_response(202);self.send_header('Content-Length','0');self.end_headers();return
            if data.get('method')=='tools/call':
                name=data.get('params',{}).get('name','unknown')
                audit('mcp:'+str(name), 'error' if out.get('result',{}).get('isError') else 'ok')
            return self.reply(200,out)
        if not self.logged_in(): return self.fail(401,'Login required')
        if self.path=='/api/logout':
            cookie=http.cookies.SimpleCookie()
            try:cookie.load(self.headers.get('Cookie',''))
            except http.cookies.CookieError: pass
            if cookie.get('synapse_session'):
                with SESSION_LOCK:
                    SESSIONS.pop(cookie['synapse_session'].value,None)
                    PENDING.pop(cookie['synapse_session'].value,None)
            return self.reply(200,{'ok':True},{'Set-Cookie':'synapse_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
        try:
            if self.path=='/api/chat':
                if not isinstance(data,dict): raise ValueError('Invalid chat payload')
                answer=chat.ask(data.get('messages'))
                proposal=answer.pop('proposal')
                sid=self.session_id()
                with SESSION_LOCK:
                    PENDING.pop(sid,None)
                    if proposal:
                        nonce=secrets.token_urlsafe(24)
                        PENDING[sid]=(nonce,time.monotonic()+180,proposal)
                        answer['confirmation']={'id':nonce,**proposal}
                audit('chat:plan','tool proposed' if proposal else 'reply')
                return self.reply(200,answer)
            if self.path=='/api/chat/reject':
                sid=self.session_id()
                with SESSION_LOCK: PENDING.pop(sid,None)
                audit('chat:reject','ok')
                return self.reply(200,{'ok':True})
            if self.path=='/api/chat/execute':
                if not isinstance(data,dict) or not isinstance(data.get('id'),str):
                    raise ValueError('Confirmation ID required')
                sid=self.session_id()
                with SESSION_LOCK:
                    pending=PENDING.pop(sid,None)
                if not pending or pending[0]!=data['id'] or pending[1]<time.monotonic():
                    raise PermissionError('Confirmation expired or already used')
                proposed=pending[2]
                # Tool permissions and STOP are rechecked at execution time.
                try:
                    output=tools.execute(proposed['name'],proposed['arguments'])
                except (ValueError, PermissionError, RuntimeError, OSError, FileExistsError) as exc:
                    audit('chat:execute:'+proposed['name'],'denied')
                    return self.reply(200,{'ok':False,'error':str(exc)[:400]})
                audit('chat:execute:'+proposed['name'],'ok')
                return self.reply(200,{'ok':True,'name':proposed['name'],'result':output})
            if self.path=='/api/remote/start':
                result=remote.up()
                audit('remote:start','ok' if result['at_least_one_online'] else 'unavailable')
                return self.reply(200,result)
            if self.path=='/api/remote/stop':
                result=remote.down()
                audit('remote:stop','ok')
                return self.reply(200,result)
            if self.path.startswith('/api/channel/'):
                bits=self.path.strip('/').split('/')
                if len(bits)!=4 or bits[2] not in ('main','backup') or bits[3] not in ('start','stop','refresh','set-url'):
                    return self.fail(404,'Invalid channel action')
                channel,action=bits[2:]
                if action == 'refresh': result=channels.status()
                elif action == 'set-url':
                    if not isinstance(data,dict): raise ValueError('Invalid payload')
                    channels.set_public_url(channel,data.get('url',''))
                    result=channels.status()
                elif action == 'start': result=channels.start(channel)
                else: result=channels.stop(channel)
                audit('channel:'+channel+':'+action,'ok')
                return self.reply(200,result)
            if self.path=='/api/config':
                updated=config.update_settings(data)
                audit('update-settings','ok')
                return self.reply(200,{'settings':updated})
            if self.path=='/api/stop':
                config.stop();audit('emergency-stop','ok');return self.reply(200,{'stopped':True})
            if self.path=='/api/resume':
                if channels.remote_origin_host(self.headers.get('Host','').lower()):
                    raise PermissionError('For safety, emergency STOP can be cleared only from local Web UI or Terminal')
                config.resume();audit('resume','ok');return self.reply(200,{'stopped':False})
            if self.path=='/api/rotate-mcp':
                new=config.rotate_mcp();audit('rotate-mcp-token','ok');return self.reply(200,{'token':new})
            if self.path=='/api/test-tool':
                if not isinstance(data,dict): raise ValueError('Invalid test input')
                name=data.get('name')
                if name not in {'ping','system_info','processes','desktop_open_windows'}: raise PermissionError('Only safe diagnostic tools available here')
                result=tools.execute(name,data.get('arguments',{}))
                audit('ui-test:'+name,'ok')
                return self.reply(200,{'result':result})
        except (ValueError,PermissionError,OSError,RuntimeError,subprocess.TimeoutExpired) as ex:
            audit('ui-action','error')
            return self.fail(400,str(ex))
        return self.fail(404,'Not found')


def serve(port=8765):
    config.init()
    if not 1024<=port<=65535: raise ValueError('port range: 1024..65535')
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    print(f'Synapse macOS Web UI: http://127.0.0.1:{port}',flush=True)
    print(f'Admin token is in: {config.BASE}/admin.token',flush=True)
    print('Public HTTPS reachable only when authenticated tunnels are active. Ctrl+C stops local server.',flush=True)
    try: server.serve_forever(poll_interval=.4)
    except KeyboardInterrupt: pass
    finally:server.server_close()
