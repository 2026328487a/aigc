"""Sent over the workbench SSH session; owns only the existing Qwen units."""
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

WORK = Path('/home/jnu/work/qwen38-gguf-20261111')
UNITS = ('qwen38-api.socket', 'qwen38-api.service', 'qwen38-backend.service')


def event(kind, **values):
    print('AIGC_CHAT ' + json.dumps({'type': kind, **values}), flush=True)


def units():
    result = {}
    for name in UNITS:
        raw = subprocess.check_output(['systemctl', '--user', 'show', name,
                                       '-p', 'ActiveState', '-p', 'MainPID'], text=True, timeout=10)
        result[name] = dict(line.split('=', 1) for line in raw.splitlines())
        if name == UNITS[0]:
            result[name]['MainPID'] = '0'  # Socket units have no process/PID property.
    return result


def terminate(signum, frame):
    raise SystemExit(0)


def main():
    config = json.loads(sys.stdin.readline())
    owned = False
    # Prevent two local managers from stopping each other's Qwen service.
    with (WORK / 'workbench-session.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一个工作台正在使用 Qwen，请先断开那个聊天连接') from None
        current = units()
        if any(item['ActiveState'] not in ('inactive', 'failed') for item in current.values()):
            raise RuntimeError('独立 Qwen 服务已开启，请先关闭独立入口再从工作台启动聊天')
        try:
            for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(signum, terminate)
            # Claim before starting, so partial startup failures are cleaned too.
            owned = True
            event('owned')
            subprocess.run(['python3', str(WORK / 'worker.py'), 'start',
                            '--idle-seconds', str(config['idle_seconds'])], check=True, timeout=60)
            if units()[UNITS[0]]['ActiveState'] != 'active':
                raise RuntimeError('Qwen 监听入口未启动')
            event('ready')
            heartbeat = time.monotonic()
            pending = b''
            while True:
                readable, _, _ = select.select([sys.stdin], [], [], 0.5)
                if readable:
                    chunk = os.read(sys.stdin.fileno(), 4096)
                    if not chunk:
                        break
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        if line == b'stop':
                            return
                        if line == b'ping':
                            heartbeat = time.monotonic()
                    if len(pending) > 4096:
                        raise RuntimeError('Invalid supervisor input')
                if time.monotonic() - heartbeat > config['lease_seconds']:
                    event('lease_expired')
                    break
        finally:
            if owned:
                # KillMode=control-group in the backend unit includes model children.
                subprocess.run(['systemctl', '--user', 'stop', *UNITS], check=True, timeout=65)
                subprocess.run(['python3', str(WORK / 'worker.py'), 'stop'], check=True, timeout=15)
                current = units()
                cleaned = all(item['ActiveState'] in ('inactive', 'failed') and item['MainPID'] == '0'
                              for item in current.values())
                event('cleaned', cleaned=cleaned)
                if not cleaned:
                    raise RuntimeError('Qwen 服务回收未完成')


if __name__ == '__main__':
    main()
