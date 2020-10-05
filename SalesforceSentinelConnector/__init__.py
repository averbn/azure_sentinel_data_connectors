import datetime
import logging
import json 
import requests
import hashlib
import hmac
import base64
import csv
import os
import sys
import azure.functions as func


customer_id = os.environ['WorkspaceID'] 
shared_key = os.environ['WorkspaceKey']
log_type = 'salesforce_service_cloud'
user = os.environ['SalesforceUser']
password = os.environ['SalesforcePass']
security_token = os.environ['SalesforceSecurityToken']
consumer_key = os.environ['SalesforceConsumerKey']
consumer_secret = os.environ['SalesforceConsumerSecret']
object =  "EventLogFile"
interval = "hourly"
hours_interval = 1
days_interval = 1
url = "https://login.salesforce.com/services/oauth2/token"


def _get_token():
    params = {
        "grant_type": "password",
        "client_id": consumer_key,
        "client_secret": consumer_secret,
        "username": user,
        "password": f'{password}{security_token}'
    }
    try:
        r = requests.post(url, params=params)
        _token = json.loads(r.text)['access_token']
        _instance_url = json.loads(r.text)['instance_url']
        return _token,_instance_url
    except Exception as err:
        logging.error(f'Token getting failed. Exiting program. {err}')
        exit()
token = _get_token()


def generate_date():
    if interval == 'hourly':
        current_time = datetime.datetime.utcnow().replace(second=0, microsecond=0)
        past_time = current_time - datetime.timedelta(hours=hours_interval)
    elif interval == 'daily':
        current_time = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        past_time = current_time - datetime.timedelta(days=days_interval, hours=1)
    return past_time.strftime("%Y-%m-%dT%H:%M:%SZ")


def pull_log_files():
    past_time = generate_date()

    if interval == 'hourly':
        query = "/services/data/v44.0/query?q=SELECT+Id+,+EventType+,+Interval+,+LogDate+,+LogFile+,+LogFileLength" + \
        "+FROM+EventLogFile" + \
        f"+WHERE+Interval+=+'Hourly'+and+CreatedDate+>+{past_time}"

    elif interval == 'daily':
        query = "/services/data/v44.0/query?q=SELECT+Id+,+CreatedDate+,+EventType+,+LogDate+,+LogFile+,+LogFileLength" + \
                "+FROM+EventLogFile" + \
                f"+WHERE+LogDate+>+{past_time}"
    try:
        r = requests.get(f'{instance_url}{query}', headers=headers)
    except Exception as err:
        logging.error(f'File list getting failed. Exiting program. {err}')
        exit()

    if r.status_code == 200:
        files = json.loads(r.text)['records']
        return files
    else:
        logging.error(f'File list getting failed. Exiting program. {r.status_code} {r.text}')
        exit()


def get_file_raw_lines(file_url):
    url = f'{instance_url}{file_url}'

    r = requests.get(url, headers=headers)
    return r.text.strip().split('\n')


def csv_to_json(csv_file_body):
    field_names = [name.lower() for name in list(csv.reader(csv_file_body))[0]]
    field_names = [x if x != 'type' else 'type_' for x in field_names]
    reader = csv.DictReader(csv_file_body[1:],fieldnames=field_names)
    obj_array = []
    for row in reader:
        row = enrich_event_with_user_email(row)
        obj_array.append(row)
    return(obj_array)


def get_users(url=None):
    if url is None:
        query = "/services/data/v44.0/query?q=SELECT+Id+,+Email+FROM+User"
    else:
        query = url
    try:
        r = requests.get(f'{instance_url}{query}', headers=headers)

        for x in r.json()['records']:
            users.update({x['Id']: x['Email']})

        if not r.json()['done']:
            next_url = r.json()['nextRecordsUrl']
            get_users(url=next_url)
    except Exception as err:
        logging.error(f'Users getting failed. Exiting program. {err}')
        exit()


def enrich_event_with_user_email(event):
    user_id = event.get('user_id_derived')
    if user_id:
        user_email = users.get(user_id)
        if user_email:
            event.update({'user_email': user_email})
    return event


def build_signature(customer_id, shared_key, date, content_length, method, content_type, resource):
    x_headers = 'x-ms-date:' + date
    string_to_hash = method + "\n" + str(content_length) + "\n" + content_type + "\n" + x_headers + "\n" + resource
    bytes_to_hash = bytes(string_to_hash, encoding="utf-8")
    decoded_key = base64.b64decode(shared_key)
    encoded_hash = base64.b64encode(hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()).decode()
    authorization = "SharedKey {}:{}".format(customer_id,encoded_hash)
    return authorization


def post_data(customer_id, shared_key, body, log_type):
    method = 'POST'
    content_type = 'application/json'
    resource = '/api/logs'
    rfc1123date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    content_length = len(body)
    signature = build_signature(customer_id, shared_key, rfc1123date, content_length, method, content_type, resource)
    uri = 'https://' + customer_id + '.ods.opinsights.azure.com' + resource + '?api-version=2016-04-01'
    headers = {
        'content-type': content_type,
        'Authorization': signature,
        'Log-Type': log_type,
        'x-ms-date': rfc1123date
    }
    response = requests.post(uri,data=body, headers=headers)
    if (response.status_code >= 200 and response.status_code <= 299):
        print('Accepted')
    else:
        print("Response code: {}".format(response.status_code))
        logging.info("Response code: {}".format(response.status_code))
        print(sys.getsizeof(body))


def to_chunks_and_process_message(obj_array):
    n = 4000
    x = list(divide_chunks(obj_array, n))
    for line in x:
        body = json.dumps(line)
        post_data(customer_id, shared_key, body, log_type)


def divide_chunks(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]


def main(mytimer: func.TimerRequest) -> None:
    logging.info(f'Script started')
    global instance_url, token, headers,customer_id, shared_key,log_type, users
    users = dict()
    token = _get_token()[0]
    instance_url = _get_token()[1]
    headers = {
        'Authorization': f'Bearer {token}'
    }
    get_users()
    for line in pull_log_files():
        csv_file_body = get_file_raw_lines(line["LogFile"])
        obj_array = csv_to_json(csv_file_body)
        to_chunks_and_process_message(obj_array)
    utc_timestamp = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
    if mytimer.past_due:
        logging.info('The timer is past due!')
    logging.info('Python timer trigger function ran at %s', utc_timestamp)