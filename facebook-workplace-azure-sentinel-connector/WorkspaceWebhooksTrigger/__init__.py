import logging
import hashlib
import hmac
import os
import azure.functions as func

AppSecret = os.environ['WorkplaceAppSecret']
VerifyToken = os.environ['WorkplaceVerifyToken']

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    method = req.method
    params = req.params
    if method == 'GET':
        hub_mode = params.get("hub.mode")
        hub_challenge = params.get("hub.challenge")
        hub_verify_token = params.get("hub.verify_token")
        if hub_mode == "subscribe" and hub_verify_token == VerifyToken:
            return func.HttpResponse(hub_challenge, status_code=200)
        else:
            return func.HttpResponse("Auth failed", status_code=401)
    elif method == 'POST':
        post_data = req.get_body()
        signature_header = req.headers.get('X-Hub-Signature')
        if signature_header:
            signature = parse_signature(signature_header)
            hmac = signature['sha1']
            message = '%s.%s' % (post_data.decode('utf-8'), signature['t'])
            print(message)
            logging.info(message)
            computed_hmac = hmac_sha1(message, AppSecret)
            if hmac != computed_hmac:
               return func.HttpResponse("Request signature invalid!", status_code=400)
        return func.HttpResponse("200 OK HTTPS", status_code=200)
    return func.HttpResponse(
            "HTTP method not supported",
            status_code=405
    )

def parse_signature(value):
    parts = value.split('&')
    ret = {}
    for kv in parts:
        (k, v) = kv.split('=')
        ret[k] = v
    return ret

def hmac_sha1(message, secret):
    message = bytes(message, 'utf-8')
    secret = bytes(secret, 'utf-8')
    hash = hmac.new(secret, message, hashlib.sha1)
    return hash.hexdigest()