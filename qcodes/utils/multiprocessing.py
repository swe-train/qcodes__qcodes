import multiprocessing as mp
import sys
from datetime import datetime
import time
from traceback import print_exc
from queue import Empty
from uuid import uuid4
import builtins

from .helpers import in_notebook

MP_ERR = 'context has already been set'
SERVER_ERR = '~~ERR~~'


def set_mp_method(method, force=False):
    '''
    an idempotent wrapper for multiprocessing.set_start_method
    The most important use of this is to force Windows behavior
    on a Mac or Linux: set_mp_method('spawn')
    args are the same:

    method: one of:
        'fork' (default on unix/mac)
        'spawn' (default, and only option, on windows)
        'forkserver'
    force: allow changing context? default False
        in the original function, even calling the function again
        with the *same* method raises an error, but here we only
        raise the error if you *don't* force *and* the context changes
    '''
    try:
        mp.set_start_method(method, force=force)
    except RuntimeError as err:
        if err.args != (MP_ERR, ):
            raise

    mp_method = mp.get_start_method()
    if mp_method != method:
        raise RuntimeError(
            'unexpected multiprocessing method '
            '\'{}\' when trying to set \'{}\''.format(mp_method, method))


class QcodesProcess(mp.Process):
    '''
    modified multiprocessing.Process for nicer printing and automatic
    streaming of stdout and stderr to our StreamQueue singleton

    name: string to include in repr, and in the StreamQueue
        default 'QcodesProcess'
    queue_streams: should we connect stdout and stderr to the StreamQueue?
        default True
    daemon: should this process be treated as daemonic, so it gets terminated
        with the parent.
        default True, overriding the base inheritance
    any other args and kwargs are passed to multiprocessing.Process
    '''
    def __init__(self, *args, name='QcodesProcess', queue_streams=True,
                 daemon=True, **kwargs):
        # make sure the singleton StreamQueue exists
        # prior to launching a new process
        if queue_streams and in_notebook():
            self.stream_queue = get_stream_queue()
        else:
            self.stream_queue = None
        super().__init__(*args, name=name, daemon=daemon, **kwargs)

    def run(self):
        if self.stream_queue:
            self.stream_queue.connect(str(self.name))
        try:
            super().run()
        except:
            # if we let the system print the exception by itself, sometimes
            # it disconnects the stream partway through printing.
            print_exc()
        finally:
            if (self.stream_queue and
                    self.stream_queue.initial_streams is not None):
                self.stream_queue.disconnect()

    def __repr__(self):
        cname = self.__class__.__name__
        r = super().__repr__()
        r = r.replace(cname + '(', '').replace(')>', '>')
        return r.replace(', started daemon', '')


def get_stream_queue():
    '''
    convenience function to get a singleton StreamQueue
    note that this must be called from the main process before starting any
    subprocesses that will use it, otherwise the subprocess will create its
    own StreamQueue that no other processes know about
    '''
    if StreamQueue.instance is None:
        StreamQueue.instance = StreamQueue()
    return StreamQueue.instance


class StreamQueue:
    '''
    Do not instantiate this directly: use get_stream_queue so we only make one.

    Redirect child process stdout and stderr to a queue

    One StreamQueue should be created in the consumer process, and passed
    to each child process.

    In the child, we call StreamQueue.connect with a process name that will be
    unique and meaningful to the user

    The consumer then periodically calls StreamQueue.get() to read these
    messages

    inspired by http://stackoverflow.com/questions/23947281/
    '''
    instance = None

    def __init__(self, *args, **kwargs):
        self.queue = mp.Queue(*args, **kwargs)
        self.last_read_ts = mp.Value('d', time.time())
        self._last_stream = None
        self._on_new_line = True
        self.lock = mp.RLock()
        self.initial_streams = None

    def connect(self, process_name):
        if self.initial_streams is not None:
            raise RuntimeError('StreamQueue is already connected')

        self.initial_streams = (sys.stdout, sys.stderr)

        sys.stdout = _SQWriter(self, process_name)
        sys.stderr = _SQWriter(self, process_name + ' ERR')

    def disconnect(self):
        if self.initial_streams is None:
            raise RuntimeError('StreamQueue is not connected')
        sys.stdout, sys.stderr = self.initial_streams
        self.initial_streams = None

    def get(self):
        out = ''
        while not self.queue.empty():
            timestr, stream_name, msg = self.queue.get()
            line_head = '[{} {}] '.format(timestr, stream_name)

            if self._on_new_line:
                out += line_head
            elif stream_name != self._last_stream:
                out += '\n' + line_head

            out += msg[:-1].replace('\n', '\n' + line_head) + msg[-1]

            self._on_new_line = (msg[-1] == '\n')
            self._last_stream = stream_name

        self.last_read_ts.value = time.time()
        return out

    def __del__(self):
        try:
            self.disconnect()
        except:
            pass

        if hasattr(self, 'queue'):
            kill_queue(self.queue)
        if hasattr(self, 'lock'):
            del self.lock


class _SQWriter:
    MIN_READ_TIME = 3

    def __init__(self, stream_queue, stream_name):
        self.queue = stream_queue.queue
        self.last_read_ts = stream_queue.last_read_ts
        self.stream_name = stream_name

    def write(self, msg):
        try:
            if msg:
                msgtuple = (datetime.now().strftime('%H:%M:%S.%f')[:-3],
                            self.stream_name, msg)
                self.queue.put(msgtuple)

                queue_age = time.time() - self.last_read_ts.value
                if queue_age > self.MIN_READ_TIME and msg != '\n':
                    # long time since the queue was read? maybe nobody is
                    # watching it at all - send messages to the terminal too
                    # but they'll still be in the queue if someone DOES look.
                    termstr = '[{} {}] {}'.format(*msgtuple)
                    # we always want a new line this way (so I don't use
                    # end='' in the print) but we don't want an extra if the
                    # caller already included a newline.
                    if termstr[-1] == '\n':
                        termstr = termstr[:-1]
                    try:
                        print(termstr, file=sys.__stdout__)
                    except ValueError:  # pragma: no cover
                        # ValueError: underlying buffer has been detached
                        # this may just occur in testing on Windows, not sure.
                        pass
        except:
            # don't want to get an infinite loop if there's something wrong
            # with the queue - put the regular streams back before handling
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            raise

    def flush(self):
        pass


class ServerManager:
    '''
    creates and manages connections to a separate server process

    name: the name of the server. Can include .format specs to insert
        all or part of the uuid
    query_timeout: the default time to wait for responses
    kwargs: passed along to the server constructor
    '''
    def __init__(self, name, server_class, server_extras={}, query_timeout=2):
        self._query_queue = mp.Queue()
        self._response_queue = mp.Queue()
        self._error_queue = mp.Queue()
        self._server_class = server_class
        self._server_extras = server_extras

        # query_lock is only used with queries that get responses
        # to make sure the process that asked the question is the one
        # that gets the response.
        # Any query that does NOT expect a response can just dump it in
        # and move on.
        self.query_lock = mp.RLock()

        # uuid is used to pass references to this object around
        # for example, to get it after someone else has sent it to a server
        self.uuid = uuid4().hex

        self.name = name.format(self.uuid)

        self.query_timeout = query_timeout
        self._start_server()

    def _start_server(self):
        self._server = QcodesProcess(target=self._run_server, name=self.name)
        self._server.start()

    def _run_server(self):
        self._server_class(self._query_queue, self._response_queue,
                           self._error_queue, self._server_extras)

    def write(self, *query):
        '''
        Send a query to the server that does not expect a response.
        '''
        self._query_queue.put(query)
        self._check_for_errors()

    def _check_for_errors(self, expect_error=False):
        if expect_error or not self._error_queue.empty():
            # clear the response queue whenever there's an error
            while not self._response_queue.empty():
                self._response_queue.get()

            # then get the error and raise a wrapping exception
            errstr = self._error_queue.get(self.query_timeout)
            errhead = '*** error on {} ***'.format(self.name)

            # try to match the error type, if it's a built-in type
            # or available in globals or locals. Only take types that
            # end in 'Error', to be safe.
            err_type = None
            err_type_str = errstr.rstrip().rsplit('\n', 1)[-1].split(':')[0]
            if err_type_str.endswith('Error'):
                err_type = getattr(builtins, err_type_str, None)
                if err_type is None and err_type in globals():
                    err_type = globals()[err_type_str]
                if err_type is None and err_type_str in locals():
                    err_type = locals()[err_type_str]
            if err_type is None:
                err_type = RuntimeError

            raise err_type(errhead + '\n\n' + errstr)

    def _check_response(self, expect_error):
        res = self._response_queue.get()
        if res == SERVER_ERR:
            expect_error = True
        return res, expect_error

    def ask(self, *query, timeout=None):
        '''
        Send a query to the server and wait for a response
        '''
        timeout = timeout or self.query_timeout
        expect_error = False

        with self.query_lock:
            # in case a previous query errored and left something on the
            # response queue, clear it
            while not self._response_queue.empty():
                res, expect_error = self._check_response(expect_error)

            self._query_queue.put(query)

            try:
                res, expect_error = self._check_response(expect_error)

                while not self._response_queue.empty():
                    res, expect_error = self._check_response(expect_error)

            except Empty as e:
                if self._error_queue.empty():
                    # only raise if we're not about to find a deeper error
                    raise e
            self._check_for_errors(expect_error)

            return res

    def halt(self, timeout=2):
        '''
        Halt the server and end its process, but in a way that it can
        be started again
        '''
        try:
            if self._server.is_alive():
                self.write('halt')
            self._server.join(timeout)

            if self._server.is_alive():
                self._server.terminate()
                print('ServerManager did not respond to halt signal, '
                      'terminated')
        except AssertionError:
            # happens when we get here from other than the main process
            # where we shouldn't be able to kill the server anyway
            pass

    def restart(self):
        '''
        Restart the server
        '''
        self.halt()
        self._start_server()

    def close(self):
        '''
        Irreversibly stop the server and manager
        '''
        self.halt()
        for q in ['query', 'response', 'error']:
            qname = '_{}_queue'.format(q)
            if hasattr(self, qname):
                kill_queue(getattr(self, qname))
                del self.__dict__[qname]
        if hasattr(self, 'query_lock'):
            del self.query_lock


def kill_queue(queue):
    try:
        queue.close()
        queue.join_thread()
    except:
        pass
