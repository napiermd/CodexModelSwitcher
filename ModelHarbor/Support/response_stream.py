"""Single-writer SSE relay ticks without changing upstream socket deadlines."""
import copy
import json
import queue
import threading
import time

HEARTBEAT_SECONDS = 10


def lines_with_ticks(upstream, budget=None, interval=HEARTBEAT_SECONDS):
    items = queue.Queue(maxsize=1)
    stop = threading.Event()
    consumed = threading.Event()

    def put(item):
        while not stop.is_set():
            try:
                items.put(item, timeout=.1)
                return
            except queue.Full:
                pass

    def read():
        try:
            iterator = iter(upstream)
            while not stop.is_set():
                try:
                    line = next(iterator) if budget is None else budget.io(next, iterator)
                except StopIteration:
                    break
                consumed.clear()
                put((line, None))
                while not stop.is_set() and not consumed.wait(.1):
                    pass
        except Exception as error:
            put((None, error))
        finally:
            put((None, None))

    worker = threading.Thread(target=read, name='harbor-stream-reader', daemon=True)
    worker.start()
    try:
        while True:
            if budget:
                budget.check()
            try:
                line, error = items.get(timeout=min(interval, .1) if budget else interval)
            except queue.Empty:
                yield None
                continue
            if error:
                raise error
            if line is None:
                return
            yield line
            consumed.set()
    finally:
        stop.set()
        consumed.set()
        # The caller closes the upstream response on exit. Its original read
        # timeout or Azure budget bounds the reader; ticks never reset either.


class Lifecycle:
    def __init__(self, interval=HEARTBEAT_SECONDS, clock=time.monotonic):
        self.response = None
        self.terminal = False
        self.sequence = -1
        self.injected = False
        self.interval = interval
        self.clock = clock
        self.last_sent = clock()

    def observe(self, event):
        kind = event.get('type', '')
        response = event.get('response')
        if kind in ('response.created', 'response.in_progress') and isinstance(response, dict) and response.get('id'):
            self.response = {key: copy.deepcopy(response[key]) for key in
                             ('id', 'object', 'created_at', 'model') if key in response}
        self.terminal |= kind in ('response.completed', 'response.failed', 'response.incomplete', 'error')
        if 'sequence_number' in event:
            if self.injected:
                self.sequence = max(self.sequence + 1, event['sequence_number'])
                event['sequence_number'] = self.sequence
            else:
                self.sequence = event['sequence_number']
        self.last_sent = self.clock()
        return event

    def heartbeat(self):
        if not self.response or self.terminal or self.clock() - self.last_sent < self.interval:
            return b''
        self.injected = True
        self.sequence += 1
        self.last_sent = self.clock()
        response = dict(self.response, status='in_progress')
        event = {'type': 'response.in_progress', 'response': response, 'sequence_number': self.sequence}
        return ('event: response.in_progress\ndata: ' + json.dumps(event) + '\n\n').encode()
