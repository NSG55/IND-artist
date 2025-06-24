from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "ok", 200

def start():
    """Run the tiny web server on port 8080 in a background thread."""
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080)).start()