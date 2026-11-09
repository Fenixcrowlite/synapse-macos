"""macOS operations behind explicit local control gates."""
import base64
import ctypes
import json
import os
import platform
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from . import config

MAX_FILE_BYTES = 256 * 1024

TOOLS = [
    ('ping', 'Agent heartbeat', {}),
    ('system_info', 'OS, uptime, CPU and disk information', {}),
    ('processes', 'List running processes', {'filter': 'Optional process-name substring'}),
    ('list_directory', 'List files inside configured roots', {'path': 'Absolute path'}),
    ('read_text_file', 'Read UTF-8 file inside configured roots', {'path': 'Absolute path'}),
    ('desktop_screenshot', 'Capture primary screen (macOS permission required)', {}),
    ('desktop_open_windows', 'List foreground applications (Accessibility permission may be required)', {}),
    ('clipboard_read', 'Read local clipboard', {}),
    ('write_text_file', 'Write UTF-8 file inside configured roots (control required)', {'path':'Absolute path', 'content':'Text', 'overwrite':'Boolean optional'}),
    ('run_shell', 'Execute zsh command (shell and control must be enabled)', {'command':'Shell command'}),
    ('open_application', 'Open a macOS app by name', {'name':'Application name'}),
    ('open_url', 'Open http/https URL in default browser', {'url':'URL'}),
    ('keyboard_type', 'Type text in focused app (Accessibility required)', {'text':'Text'}),
    ('keyboard_hotkey', 'Send modifier combination, e.g. ["cmd","s"]', {'keys':'Array of keys'}),
    ('mouse_click', 'Click primary-screen coordinates (Accessibility required)', {'x':'X position', 'y':'Y position', 'button':'left or right'}),
    ('clipboard_write', 'Replace clipboard text', {'text':'Text'}),
]
MUTATING = {'write_text_file','run_shell','open_application','open_url','keyboard_type','keyboard_hotkey','mouse_click','clipboard_write'}
GUI = {'keyboard_type','keyboard_hotkey','mouse_click'}


def describe():
    result=[]
    for name, description, fields in TOOLS:
        props={}
        required=[]
        for k, desc in fields.items():
            typ='boolean' if k=='overwrite' else 'array' if k=='keys' else 'integer' if k in {'x','y'} else 'string'
            item={'type':typ,'description':desc}
            if k=='keys': item['items']={'type':'string'}
            props[k]=item
            if k!='overwrite' and not (name=='processes' and k=='filter'):
                required.append(k)
        result.append({'name':name,'description':description,'inputSchema':{'type':'object','properties':props,'required':required,'additionalProperties':False}})
    return result


def inside_roots(path, allow_new=False):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('Absolute path required')
    target=Path(path).expanduser().resolve(strict=not allow_new)
    roots=[Path(p).expanduser().resolve() for p in config.settings()['allowed_roots']]
    if not any(target==root or root in target.parents for root in roots):
        raise PermissionError('Path outside configured roots')
    return target


def checked_run(args, timeout=12, input_text=None):
    try:
        p=subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)
        return {'exit_code':p.returncode,'stdout':p.stdout[:16000], 'stderr':p.stderr[:4000]}
    except subprocess.TimeoutExpired:
        return {'error':'Process timed out'}


def screenshot():
    if platform.system()!='Darwin':
        raise RuntimeError('Screen capture needs macOS')
    with tempfile.TemporaryDirectory(prefix='synapse-screen-') as td:
        path=str(Path(td)/'screen.png')
        r=checked_run(['/usr/sbin/screencapture','-x','-t','png',path],timeout=15)
        if r['exit_code']!=0 or not Path(path).exists():
            raise RuntimeError('Screen Recording permission required in System Settings')
        data=Path(path).read_bytes()
        if len(data)>8_000_000:
            raise RuntimeError('Screenshot exceeds 8MB limit')
        return {'__image__':base64.b64encode(data).decode('ascii'),'mimeType':'image/png'}


def mouse_click(x,y,button):
    if platform.system()!='Darwin': raise RuntimeError('Mouse control requires macOS')
    if type(x) is not int or type(y) is not int or abs(x)>20000 or abs(y)>20000: raise ValueError('Invalid coordinates')
    if button not in {'left','right'}: raise ValueError('Invalid mouse button')
    class CGPoint(ctypes.Structure):
        _fields_=[('x',ctypes.c_double),('y',ctypes.c_double)]
    cg=ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    cg.CGEventCreateMouseEvent.restype=ctypes.c_void_p
    cg.CGEventCreateMouseEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint32,CGPoint,ctypes.c_uint32]
    cg.CGEventPost.argtypes=[ctypes.c_uint32,ctypes.c_void_p]
    cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cf.CFRelease.argtypes=[ctypes.c_void_p]
    evt=(1,2) if button=='left' else (3,4)
    btn=0 if button=='left' else 1
    for code in evt:
        ev=cg.CGEventCreateMouseEvent(None,code,CGPoint(x,y),btn)
        if not ev: raise RuntimeError('Cannot create mouse event')
        try: cg.CGEventPost(0,ev)
        finally: cf.CFRelease(ev)
        time.sleep(0.045)
    return {'clicked':True}


def hotkey(keys):
    if not isinstance(keys,list) or not 1<=len(keys)<=5 or not all(isinstance(x,str) for x in keys): raise ValueError('Invalid keys')
    mapping={'cmd':'command down','command':'command down','ctrl':'control down','control':'control down','option':'option down','alt':'option down','shift':'shift down'}
    modifiers=[mapping[x.lower()] for x in keys[:-1] if x.lower() in mapping]
    if len(modifiers)!=len(keys)-1: raise ValueError('Unrecognized modifier')
    key=keys[-1].lower()
    special={'enter':36,'return':36,'escape':53,'esc':53,'tab':48,'space':49,'backspace':51,'delete':51,'up':126,'down':125,'left':123,'right':124}
    if key in special:
        stmt=f'key code {special[key]}'
    elif len(key)==1 and key.isascii() and key.isprintable():
        stmt=f'keystroke {json.dumps(key)}'
    else: raise ValueError('Unsupported key')
    script='tell application "System Events" to '+stmt+((' using {'+', '.join(modifiers)+'}') if modifiers else '')
    return checked_run(['/usr/bin/osascript','-e',script])


def execute(name,args):
    if name not in {x[0] for x in TOOLS}: raise ValueError('Unknown tool')
    if not isinstance(args,dict): raise ValueError('Arguments must be an object')
    opts=config.settings()
    if name in MUTATING:
        if config.stopped(): raise PermissionError('STOP flag active')
        if not opts['control_enabled']: raise PermissionError('Control disabled in local Web UI')
        if name in GUI and not opts['gui_enabled']: raise PermissionError('GUI control disabled in local Web UI')
        if name=='run_shell' and not opts['shell_enabled']: raise PermissionError('Shell disabled in local Web UI')
    if name=='ping': return {'ok':True,'platform':platform.system(),'stopped':config.stopped(),'control':opts['control_enabled']}
    if name=='system_info':
        result={'platform':platform.platform(),'python':platform.python_version(),'machine':platform.machine(),'hostname':platform.node(),'pid':os.getpid()}
        if platform.system()=='Darwin': result['sw_vers']=checked_run(['/usr/bin/sw_vers'])['stdout']
        return result
    if name=='processes':
        f=args.get('filter','')
        if not isinstance(f,str): raise ValueError('filter must be string')
        r=checked_run(['/bin/ps','-axo','pid,ppid,%cpu,rss,comm'],timeout=8)
        lines=r.get('stdout','').splitlines()
        return {'processes':[line.strip() for line in lines[:1]+[v for v in lines[1:] if f.casefold() in v.casefold()][:100]],'exit_code':r.get('exit_code')}
    if name=='list_directory':
        path=inside_roots(args.get('path'))
        if not path.is_dir(): raise ValueError('Not a directory')
        return {'path':str(path),'entries':[{'name':p.name,'is_dir':p.is_dir(),'size':p.stat().st_size if p.is_file() else None} for p in sorted(path.iterdir())[:200]]}
    if name=='read_text_file':
        path=inside_roots(args.get('path'))
        if not path.is_file(): raise ValueError('Not a file')
        if path.stat().st_size>MAX_FILE_BYTES: raise ValueError('File too large')
        return {'path':str(path),'content':path.read_text(encoding='utf-8')}
    if name=='desktop_screenshot': return screenshot()
    if name=='desktop_open_windows':
        if platform.system()!='Darwin': return {'error':'Requires macOS'}
        r=checked_run(['/usr/bin/osascript','-e','tell application "System Events" to get name of (every process whose background only is false)'])
        return r
    if name=='clipboard_read': return checked_run(['/usr/bin/pbpaste'])
    if name=='write_text_file':
        path=inside_roots(args.get('path'),allow_new=True)
        content=args.get('content')
        if not isinstance(content,str) or len(content.encode())>MAX_FILE_BYTES: raise ValueError('Invalid or too-large content')
        if path.exists() and not args.get('overwrite',False): raise FileExistsError('File exists; overwrite must be true')
        if not path.parent.is_dir(): raise ValueError('Parent directory does not exist')
        if path.is_symlink(): raise ValueError('Symlink targets are not writable')
        flags=os.O_WRONLY|os.O_CREAT| (os.O_TRUNC if args.get('overwrite') else os.O_EXCL)
        fd=os.open(path,flags,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as handle: handle.write(content)
        return {'written':True,'path':str(path),'bytes':len(content.encode())}
    if name=='run_shell':
        command=args.get('command')
        if not isinstance(command,str) or not 1<=len(command)<=4096: raise ValueError('Invalid command')
        return checked_run(['/bin/zsh','-lc',command],timeout=opts['max_command_seconds'])
    if name=='open_application':
        app=args.get('name')
        if not isinstance(app,str) or not 1<=len(app)<=160: raise ValueError('Invalid app name')
        return checked_run(['/usr/bin/open','-a',app])
    if name=='open_url':
        url=args.get('url')
        if not isinstance(url,str) or len(url)>2048 or urlparse(url).scheme not in {'https','http'} or not urlparse(url).hostname: raise ValueError('Only http(s) links supported')
        return checked_run(['/usr/bin/open',url])
    if name=='keyboard_type':
        t=args.get('text')
        if not isinstance(t,str) or len(t)>4000: raise ValueError('Invalid text')
        return checked_run(['/usr/bin/osascript','-l','JavaScript','-e','Application("System Events").keystroke('+json.dumps(t)+')'])
    if name=='keyboard_hotkey': return hotkey(args.get('keys'))
    if name=='mouse_click': return mouse_click(args.get('x'),args.get('y'),args.get('button','left'))
    if name=='clipboard_write':
        t=args.get('text')
        if not isinstance(t,str) or len(t)>100000: raise ValueError('Invalid clipboard text')
        return checked_run(['/usr/bin/pbcopy'],input_text=t)
    raise ValueError('Unimplemented tool')
