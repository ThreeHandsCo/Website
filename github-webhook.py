from flask import Flask, request
import hmac
import hashlib
import subprocess

app = Flask(__name__)
SECRET = b'SHITFUCK'  # Use the secret you set in GitHub

def verify_signature(payload, signature):
    mac = hmac.new(SECRET, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, signature)

@app.route('/github-webhook/', methods=['POST'])
def github_webhook():
    signature = request.headers.get('X-Hub-Signature-256')
    if not signature or not verify_signature(request.data, signature):
        return "Unauthorized", 403

    payload = request.json
    if payload and payload.get('ref') == 'refs/heads/main':
        subprocess.run(['git', '-C', '/var/www/html/', 'pull', 'origin', 'main'])
        return "Updated", 200
    
    return "No update", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=443)
