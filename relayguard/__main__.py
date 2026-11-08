import argparse
import json
import os
import time
from .core import HTTPDestination, Queue, work_once
from .server import serve


def main():
    parser = argparse.ArgumentParser(description='RelayGuard durable webhook-to-CRM relay')
    parser.add_argument('--db', default='data/relayguard.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    ingress = sub.add_parser('serve')
    ingress.add_argument('--port', type=int, default=8080)
    worker = sub.add_parser('worker')
    worker.add_argument('--once', action='store_true')
    worker.add_argument('--max-attempts', type=int, default=5)
    sub.add_parser('status')
    sub.add_parser('dead')
    replay = sub.add_parser('replay')
    replay.add_argument('event_id')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'serve':
        secret = os.environ.get('WEBHOOK_SECRET', '')
        if len(secret) < 32:
            parser.error('WEBHOOK_SECRET must be at least 32 characters')
        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
        serve(args.db, secret, args.port)
        return
    destination = None
    if args.command == 'worker':
        if args.max_attempts < 1:
            parser.error('--max-attempts must be positive')
        try:
            destination = HTTPDestination(os.environ.get('CRM_URL', ''), os.environ.get('CRM_TOKEN', ''))
        except ValueError as error:
            parser.error(str(error))
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    queue = Queue(args.db)
    try:
        if args.command == 'status':
            print(json.dumps(queue.stats()))
        elif args.command == 'dead':
            print(json.dumps(queue.dead()))
        elif args.command == 'replay':
            print(json.dumps({'requeued': queue.replay(args.event_id)}))
        else:
            while True:
                worked = work_once(queue, destination, args.max_attempts)
                if args.once:
                    break
                if not worked:
                    time.sleep(1)
    finally:
        queue.close()

if __name__ == '__main__':
    main()
