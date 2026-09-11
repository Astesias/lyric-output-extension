from flask import Flask, jsonify
from flask_cors import CORS
import logging
# 创建 Flask 应用
app = Flask(__name__)
CORS(app, supports_credentials=True)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
app.config['JSON_AS_ASCII'] = False


data = {
    "AppName": "网易云音乐",
    "Title": "",
    "AllTime": "0",
    "Now": "0",
    "ChineseLryic": "",
    "Lryic": "",
    "FormattedTime": ""
}

@app.route('/BGMName', methods=['GET'])
def index():
    response = jsonify(data)
    return response

def run(port=62333):
    app.run('127.0.0.1', port, debug=False)
if __name__ == '__main__':
    # 启动 Flask 应用
    run()
