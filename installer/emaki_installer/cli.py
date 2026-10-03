import argparse
import json
from pathlib import Path
import socket
import sys
import uuid

from .constants import MAX_FRAME, SOCKET
from .errors import InstallError
from .protocol import encode_frame


def main(argv=None):
    parser = argparse.ArgumentParser(description='Emaki installer protocol client (live ISO).')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--probe', action='store_true')
    action.add_argument('--plan', type=Path, metavar='FILE.json')
    parser.add_argument('--follow', action='store_true')
    action.add_argument('--save-log', metavar='DEST')
    parser.add_argument('--yes', action='store_true', help='Confirm the reviewed disk plan and install.')
    parser.add_argument('--job-id', help='Resume a known job, including a completed job.')
    parser.add_argument('--since-seq', type=int, default=0)
    parser.add_argument('--socket', type=Path, default=SOCKET)
    args = parser.parse_args(argv)
    if not (args.probe or args.plan or args.save_log or args.follow):
        parser.error('one of --probe, --plan, --follow, --save-log is required')
    if args.follow and (args.probe or args.save_log):
        parser.error('--follow combines only with --plan --yes')
    if args.follow and args.plan and not args.yes:
        parser.error('--plan with --follow requires --yes')
    if args.yes and not args.plan:
        parser.error('--yes requires --plan')
    config = None
    try:
        if args.plan:
            config = json.loads(args.plan.read_text())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(args.socket))
            stream = client.makefile('rb')

            def send(kind, **fields):
                ident = uuid.uuid4().hex
                client.sendall(encode_frame({'type': kind, 'id': ident, **fields}))
                return ident

            send('hello', proto=1)
            job_id, last_log = None, args.since_seq
            while True:
                frame = stream.readline(MAX_FRAME + 1)
                if not frame or len(frame) > MAX_FRAME or not frame.endswith(b'\n'):
                    print('Installer connection closed or sent an invalid frame.', file=sys.stderr)
                    return 2
                msg = json.loads(frame)
                kind = msg['type']
                if kind == 'hello':
                    if args.probe:
                        send('probe')
                    elif args.plan:
                        if msg.get('busy_job'):
                            print(json.dumps({'type': 'reply', 'ok': False, 'code': 'busy'}))
                            return 4
                        send('plan', config=config)
                        config = None
                    elif args.save_log:
                        send('save_log', dest=args.save_log)
                    else:
                        job_id = args.job_id or msg.get('busy_job')
                        if not job_id:
                            print('No active job. Pass --job-id to replay the last completed job.', file=sys.stderr)
                            return 2
                        send('resume', job_id=job_id, since_seq=args.since_seq)
                    continue
                if job_id and msg.get('job_id') and msg['job_id'] != job_id:
                    continue
                if kind == 'log':
                    if msg['seq'] <= last_log:
                        continue
                    last_log = msg['seq']
                # Do not print the capability token, even in script output.
                public = {k: v for k, v in msg.items() if k != 'token'}
                print(json.dumps(public, ensure_ascii=False), flush=True)
                if kind == 'inventory':
                    return 0
                if kind == 'plan_ack':
                    if msg['errors']:
                        return 3
                    if not args.yes:
                        return 0
                    send('confirm', plan_id=msg['plan_id'], token=msg['token'])
                elif kind == 'reply':
                    if not msg['ok']:
                        return 4 if msg.get('code') == 'busy' else 2
                    if args.save_log:
                        return 0
                    job_id = msg.get('job_id', job_id)
                elif kind == 'done':
                    return 0
                elif kind == 'error':
                    return 2
    except (OSError, ValueError, InstallError) as exc:
        # Never echo plan input or a JSON parsing exception (may contain secrets).
        print('emaki-install-cli: ' + type(exc).__name__ + '; could not complete request.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
