import logging
import hashlib
import hmac
import os
import azure.functions as func
import json
import re
import base64
import requests
import datetime

AppSecret = os.environ['WorkplaceAppSecret']
VerifyToken = os.environ['WorkplaceVerifyToken']
customer_id = os.environ['WorkspaceID']
shared_key = os.environ['WorkspaceKey']
logAnalyticsUri = os.environ.get('logAnalyticsUri')
log_type = 'Workplace_Facebook'

if ((logAnalyticsUri in (None, '') or str(logAnalyticsUri).isspace())):
    logAnalyticsUri = 'https://' + customer_id + '.ods.opinsights.azure.com'
pattern = r'https:\/\/([\w\-]+)\.ods\.opinsights\.azure.([a-zA-Z\.]+)$'
match = re.match(pattern,str(logAnalyticsUri))
if(not match):
    raise Exception("Workplace_Facebook: Invalid Log Analytics Uri.")

def hmac_sha1(message, secret):
    message = bytes(message, 'utf-8')
    secret = bytes(secret, 'utf-8')
    hash = hmac.new(secret, message, hashlib.sha1)
    return hash.hexdigest()


def parse_signature(value):
    parts = value.split('&')
    ret = {}
    for kv in parts:
        (k, v) = kv.split('=')
        ret[k] = v
    return ret


def build_signature(customer_id, shared_key, date, content_length, method, content_type, resource):
    x_headers = 'x-ms-date:' + date
    string_to_hash = method + "\n" + str(content_length) + "\n" + content_type + "\n" + x_headers + "\n" + resource
    bytes_to_hash = bytes(string_to_hash, encoding="utf-8")
    decoded_key = base64.b64decode(shared_key)
    encoded_hash = base64.b64encode(hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()).decode()
    authorization = "SharedKey {}:{}".format(customer_id,encoded_hash)
    return authorization


def post_data(body):
    method = 'POST'
    content_type = 'application/json'
    resource = '/api/logs'
    rfc1123date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    content_length = len(body)
    signature = build_signature(customer_id, shared_key, rfc1123date, content_length, method, content_type, resource)
    uri = logAnalyticsUri + resource + "?api-version=2016-04-01"
    headers = {
        'content-type': content_type,
        'Authorization': signature,
        'Log-Type': log_type,
        'x-ms-date': rfc1123date
    }
    response = requests.post(uri,data=body, headers=headers)
    if (response.status_code >= 200 and response.status_code <= 299):
        logging.info("Message successfully processed into Azure.")
        return response.status_code
    else:
        logging.warn("Message is not processed into Azure. Response code: {}".format(response.status_code))
        return None


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request. Start of processing.')

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
            logging.info(signature)
            hmac = signature['sha1']
            logging.info(hmac)
            message = post_data.decode('utf-8')
            logging.info(message)
            computed_hmac = hmac_sha1(message, AppSecret)
            logging.info(computed_hmac)
            if hmac != computed_hmac:
                logging.error("Request signature invalid. Error code: 400.")
                return func.HttpResponse("Request signature invalid!", status_code=400)
            else:
                result = []
                message = {"object": "workplace_security", "entry": [{"id": "504942447160640", "time": 1617019222, "changes": [{"value": {"actor_community_id": "504942443827307", "actor_scim_company_id": "504942447160640", "actor_id": "100064094363063", "actor_email": "rm@socprime.com", "target_id": "100064094363063", "target_email": "rm@socprime.com", "ip": "165.225.207.52", "timestamp": "2021-03-29T12:00:20+0000", "event": "CUSTOM_INTEGRATION_EDIT", "browser_name": "Firefox", "browser_os": "Mac OS X 10.15", "ip_country": "PL", "useragent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:86.0) Gecko/20100101 Firefox/86.0", "target_community_id": "504942443827307", "target_company_id": "504942447160640", "integration_data": {"custom_integration_name": "1", "custom_integration_status": "INSTALLED"}}, "field": "integrations"}]}]}
                result.append(json.loads(message))
                post_data(json.dumps(result))
                logging.info("200 OK HTTPS")
                return func.HttpResponse("200 OK HTTPS", status_code=200)
    logging.error("HTTP method not supported. Error code: 405.")
    return func.HttpResponse("HTTP method not supported", status_code=405)