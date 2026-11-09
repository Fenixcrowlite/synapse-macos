"""Launch and install the macOS Synapse agent."""
import argparse
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path
from . import config, channels, remote
from .web import serve

LABEL='dev.synapse.macos.agent'
PLIST=Path.home()/'Library'/'LaunchAgents'/f'{LABEL}.plist'


def launchd(action):
    if platform.system()!='Darwin':
        raise RuntimeError('launchd service installation requires macOS')
    domain=f'gui/{os.getuid()}'
    if action=='install':
        config.init()
        PLIST.parent.mkdir(parents=True,exist_ok=True)
        data={'Label':LABEL,'ProgramArguments':[sys.executable,'-m','synapse_mac','serve'],
              'WorkingDirectory':str(Path(__file__).resolve().parent.parent),
              'EnvironmentVariables':{'PYTHONPATH':str(Path(__file__).resolve().parent.parent),
                                      'SYNAPSE_MAC_HOME':str(config.BASE)},
              'RunAtLoad':True,'KeepAlive':True,
              'StandardOutPath':str(config.BASE/'agent.log'),
              'StandardErrorPath':str(config.BASE/'agent.err.log')}
        PLIST.write_bytes(plistlib.dumps(data))
        subprocess.run(['launchctl','bootout',domain,str(PLIST)],capture_output=True)
        subprocess.run(['launchctl','bootstrap',domain,str(PLIST)],check=True)
        subprocess.run(['launchctl','enable',f'{domain}/{LABEL}'],check=True)
        print(f'Installed per-user launchd agent: {PLIST}')
    if action=='uninstall':
        if PLIST.exists():
            subprocess.run(['launchctl','bootout',domain,str(PLIST)],capture_output=True)
            PLIST.unlink()
        print('launchd service removed; data and secrets kept in '+str(config.BASE))
    if action=='status':
        r=subprocess.run(['launchctl','print',f'{domain}/{LABEL}'],capture_output=True,text=True)
        print('Service running/registered' if r.returncode==0 else 'Service not registered')
        print('Web UI: http://127.0.0.1:8765')


def main():
    ap=argparse.ArgumentParser(description='Local-first Synapse macOS MCP agent')
    ap.add_argument('action',nargs='?',choices=['init','serve','install-service','uninstall-service','status','stop','resume','start-main','stop-main','start-backup','stop-backup','channels','remote-start','remote-stop','remote-setup','remote-status'],default='serve')
    ap.add_argument('--port',type=int,default=8765)
    args=ap.parse_args()
    if args.action=='init':
        config.init()
        print('Created config and secrets in '+str(config.BASE))
        print('Web UI login token: cat '+str(config.BASE/'admin.token'))
    elif args.action=='serve':serve(args.port)
    elif args.action=='install-service':launchd('install')
    elif args.action=='uninstall-service':launchd('uninstall')
    elif args.action=='status':launchd('status')
    elif args.action=='stop':config.stop();print('Emergency STOP active')
    elif args.action=='resume':config.resume();print('STOP flag cleared (control gate still applies)')
    elif args.action=='channels':print(__import__('json').dumps(channels.status(),indent=2))
    elif args.action=='remote-start':print(__import__('json').dumps(remote.up(),indent=2))
    elif args.action=='remote-stop':print(__import__('json').dumps(remote.down(),indent=2))
    elif args.action=='remote-setup':print(__import__('json').dumps(remote.prerequisites(),indent=2))
    elif args.action=='remote-status':print(remote.pretty_status())
    elif args.action=='start-main':print(__import__('json').dumps(channels.start('main'),indent=2))
    elif args.action=='stop-main':print(__import__('json').dumps(channels.stop('main'),indent=2))
    elif args.action=='start-backup':print(__import__('json').dumps(channels.start('backup'),indent=2))
    elif args.action=='stop-backup':print(__import__('json').dumps(channels.stop('backup'),indent=2))

if __name__=='__main__': main()
