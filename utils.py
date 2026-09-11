"""
工具函数：从 pysl 库提取的 chained_request 和 mute_all
"""
import os
import sys
import json
import requests
from contextlib import contextmanager


class response_wrapper():
    def __init__(self, response):
        self.r = response

    def response(self):
        return self.r

    def byte(self):
        return self.r.content

    def str(self, encoding='utf8'):
        return str(self.byte(), encoding=encoding)

    def json(self, encoding='utf8'):
        return json.loads(self.str(encoding=encoding))

    def __repr__(self):
        return self.r.__repr__()


class chained_request():
    def __init__(self, url):
        self._url = url
        self._headers = None
        self._payload = None
        self._ajax = False

    def payload(self, data):    
        self._payload = data
        return self

    def ajax(self):
        """以 form 表单方式发送（默认）"""
        self._ajax = True
        return self

    def headers(self, path_or_dict):
        if isinstance(path_or_dict, str):
            self._headers = json.load(open(path_or_dict))
        else:
            self._headers = path_or_dict
        return self

    def post(self, payload=None, stream=False):
        if payload:
            self._payload = payload
        return self._request('POST', stream=stream)

    def _request(self, method, stream=False):
        if method == 'POST':
            response = requests.post(
                self._url,
                headers=self._headers,
                data=self._payload if self._ajax else json.dumps(self._payload),
                stream=stream
            )
        elif method == 'GET':
            response = requests.get(
                self._url,
                headers=self._headers,
                stream=stream
            )
        return response_wrapper(response)


@contextmanager
def mute_all():
    """静音所有 stdout 输出"""
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout