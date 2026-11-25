"""Install/status/remove this user-owned runtime's macOS launch agent.

This checkout was created because the original account's checkout is read-only.
Only this isolated runtime database is opened. No .env is copied or written.
"""
import argparse
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LABEL='com.harshagrawal.slbm-tracker'
PLIST=Path.home()/'Library'/'LaunchAgents'/(LABEL+'.plist')
DOMAIN=f'gui/{os.getuid()}'


def configuration():
    logs=ROOT/'runtime'/'logs'
    return {
        'Label':LABEL,'ProgramArguments':[sys.executable,str(ROOT/'scripts'/'run_local.py')],
        'WorkingDirectory':str(ROOT),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
        'StandardOutPath':str(logs/'app.out.log'),'StandardErrorPath':str(logs/'app.err.log'),
        'EnvironmentVariables':{'PYTHONUNBUFFERED':'1','PYTHONPYCACHEPREFIX':str(ROOT/'runtime'/'pycache'),
                                'PATH':'/usr/bin:/bin:/usr/sbin:/sbin'},
    }


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['install','status','remove','print'])
    args=parser.parse_args()
    if args.action=='print':
        print(plistlib.dumps(configuration()).decode());return
    if args.action=='status':
        subprocess.run(['/bin/launchctl','print',DOMAIN+'/'+LABEL]);return
    if args.action=='remove':
        subprocess.run(['/bin/launchctl','bootout',DOMAIN+'/'+LABEL],check=False)
        if PLIST.exists():PLIST.unlink()
        print('Removed only this runtime launch agent. Data is retained.');return
    if sys.platform!='darwin':raise SystemExit('This launcher requires macOS.')
    if not (ROOT/'frontend'/'dist'/'index.html').exists():raise SystemExit('Build the frontend first.')
    if not (ROOT/'runtime'/'slbm_tracker.db').exists():raise SystemExit('Prepare the isolated runtime database first.')
    (ROOT/'runtime'/'logs').mkdir(parents=True,exist_ok=True)
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    # Replacing only our own label makes installation repeatable.
    subprocess.run(['/bin/launchctl','bootout',DOMAIN+'/'+LABEL],capture_output=True)
    PLIST.write_bytes(plistlib.dumps(configuration()))
    subprocess.run(['/usr/bin/plutil','-lint',str(PLIST)],check=True)
    # bootout may return while launchd is still unloading the old job.
    for attempt in range(4):
        result=subprocess.run(['/bin/launchctl','bootstrap',DOMAIN,str(PLIST)],capture_output=True,text=True)
        if result.returncode==0:break
        if attempt==3:raise SystemExit('Could not start launch agent: '+result.stderr.strip())
        time.sleep(1)
    print('Installed',PLIST)
    print('Open http://127.0.0.1:8010/live; logs:',ROOT/'runtime'/'logs')


if __name__=='__main__':main()
