from bottle import Bottle, run, response
import subprocess

app = Bottle()

@app.route('/initialize')
def hello():
    response.status = 204
    subprocess.call(["../../db/init.sh"])
    return ""


if __name__ == '__main__':
    run(app, host='localhost', port=8080, debug=True)
