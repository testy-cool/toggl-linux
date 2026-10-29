"""Record thread stacks when GTK stalls; never restart timers automatically."""
import sys
import threading
import time
import traceback
import toggl_debug


class UIWatchdog:
    def __init__(self, threshold=30, interval=5, clock=time.monotonic):
        self.threshold, self.interval, self.clock = threshold, interval, clock
        self.last_beat = clock()
        self.reported = False
        self.stopped = threading.Event()
        self.source = None
        self.thread = None

    def pulse(self):
        if self.stopped.is_set():
            return False
        if self.reported:
            toggl_debug.LOGGER.info('GTK heartbeat recovered after %.1f seconds', self.clock() - self.last_beat)
        self.last_beat = self.clock()
        self.reported = False
        return True

    def check(self):
        age = self.clock() - self.last_beat
        if self.stopped.is_set() or self.reported or age < self.threshold:
            return
        self.reported = True
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        stacks = []
        for ident, frame in sys._current_frames().items():
            stacks.append(f'\nThread {names.get(ident, ident)}:\n' + ''.join(traceback.format_stack(frame)))
        toggl_debug.LOGGER.error('GTK heartbeat stalled for %.1f seconds. Thread stacks:%s', age, ''.join(stacks))

    def start(self, glib):
        self.glib = glib
        self.source = glib.timeout_add_seconds(1, self.pulse)
        def monitor():
            while not self.stopped.wait(self.interval):
                self.check()
        self.thread = threading.Thread(target=monitor, name='gtk-watchdog', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.source is not None:
            self.glib.source_remove(self.source)
            self.source = None
        if self.thread is not None:
            self.thread.join(timeout=1)
