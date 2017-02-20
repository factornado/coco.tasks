import os
import time
from tornado import ioloop, web, httpserver, escape
import logging
import pandas as pd

from utils import Config

config = Config('config.yml')

logging.basicConfig(
    level=20,  # Set to 10 for debug
    filename=config.conf['log']['file'],
    format='%(asctime)s (%(filename)s:%(lineno)s)- %(levelname)s - %(message)s',
    )
logging.Formatter.converter = time.gmtime
logging.getLogger('tornado').setLevel(logging.WARNING)
logging.info('='*80)


class Info(web.RequestHandler):
    def get(self):
        self.write(config.conf)


class Heartbeat(web.RequestHandler):
    def get(self):
        config.register()
        self.write("ok")


class Tasks(web.RequestHandler):
    def initialize(self, config=None):
        assert config is not None
        self.config = config
        self.msg = config.mongo.local.db_test.tasks
        self.tasks = config.conf['allowedTasks']

    def get(self, uri):
        if len(uri.split('/')) != 1:
            raise web.HTTPError(
                409,
                reason="uri `{}` shall have the form 'task'.".format(uri))
        task = uri
        if task not in self.tasks:
            logging.warning("Task `{}` not covered by this service.".format(task))
        todo = self.msg.find_one(
            {'status': 'todo', 'task': task},
            sort=[('try', 1), ('ldt', 1)])
        if todo is None:
            self.set_status(204, reason='No task to do')
        else:
            self.msg.update_one({'_id': todo['_id']},
                                {'$set': {
                                    'status': 'doing',
                                    'statusSince': pd.Timestamp.utcnow().value,
                                    }})
            self.write(pd.json.dumps(todo))

    def post(self, uri):
        if len(uri.split('/')) != 2:
            raise web.HTTPError(
                409,
                reason="uri {} shall have the form 'task/key'.".format(uri))
        task, key = uri.split('/')
        if task not in self.tasks:
            logging.warning("Task `{}` not covered by this service.".format(task))
        try:
            data = escape.json_decode(self.request.body)
        except:
            raise web.HTTPError(
                501,
                reason="Bytes `{}...` are not JSON serializable".format(self.request.body[:30]))
        todo = {
            '_id': '/'.join([task, key]),
            'task': task,
            'status': 'todo',
            'lastPost': pd.Timestamp.utcnow().value,
            'statusSince': pd.Timestamp.utcnow().value,
            'lastTry': None,
            'try': 0,
            'data': data,
            }
        replace = self.msg.replace_one({'_id': todo['_id']}, todo, upsert=True)
        out = replace.raw_result
        if 'electionId' in out:
            out['electionId'] = out['electionId'].binary.hex()
        self.write(pd.json.dumps(out))

    def put(self, uri):
        if len(uri.split('/')) != 4:
            raise web.HTTPError(
                409,
                reason="uri {} shall have the form 'task/key/status/lastPost'.".format(uri))
        task, key, status, last_post = uri.split('/')
        if task not in self.tasks:
            logging.warning("Task `{}` not covered by this service.".format(task))
        if status not in ['done', 'fail', 'todo']:
            raise web.HTTPError(
                411,
                reason="Status {} not understood. Expect done|fail|todo.".format(status))
        try:
            last_post = int(last_post)
        except:
            raise web.HTTPError(
                413,
                reason="LastPost `{}` is not an int.".format(last_post))

        _id = '/'.join([task, key])
        todo = self.msg.find_one({'_id': _id})
        if todo is None:
            raise web.HTTPError(
                412,
                reason="Task id {} not found.".format(_id))
        if todo['lastPost'] <= last_post:
            update = self.msg.update_one(
                {'_id': _id, 'lastPost': {'$lte': last_post}},
                {'$set': {
                    'status': 'done' if status == 'done' else 'todo',
                    'statusSince': pd.Timestamp.utcnow().value,
                    },
                 '$inc': {'try': 1 if status == 'fail' else 0}})
            assert update.raw_result['n']
            out = {"n": 1, "nModified": update.raw_result["nModified"], "reason": "ok"}
        else:
            out = {"n": 1, "nModified": 0, "reason": "task outdated"}
        self.write(pd.json.dumps(out))


class SomeHandler(web.RequestHandler):
    def get(self, param=''):
        self.write(
            "Hello from service {}. "
            "You've asked for uri {}\n".format(
                config.conf['name'], param))

app = web.Application([
    ("/(swagger.json)", web.StaticFileHandler, {'path': os.path.dirname(__file__)}),
    ("/swagger", web.RedirectHandler, {'url': '/swagger.json'}),
    ("/heartbeat", Heartbeat),
    ("/info", Info),
    ("/tasks/(.*)", Tasks, {'config': config}),
    ])

if __name__ == '__main__':
    port = config.get_port()  # We need to have a fixed port in both forks.
    logging.info('Listening on port {}'.format(port))
    time.sleep(2)  # We sleep for a few seconds to let the registry start.
    # config.register()
    if os.fork():
        config.register()
        # print('Listening on port', port)
        server = httpserver.HTTPServer(app)
        server.bind(config.get_port(), address='0.0.0.0')
        server.start(config.conf['threads_nb'])
        ioloop.IOLoop.current().start()
    else:
        ioloop.PeriodicCallback(config.heartbeat,
                                config.conf['heartbeat']['period']).start()
        ioloop.IOLoop.instance().start()
