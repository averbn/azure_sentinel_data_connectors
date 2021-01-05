import boto3
import json
import datetime
from botocore.config import Config as BotoCoreConfig
import tempfile
import os
import gzip
import time
from filesplit import FileSplit
import base64
import hashlib
import hmac
import requests
import threading
import azure.functions as func
import logging

customer_id = os.environ['WorkspaceID'] 
shared_key = os.environ['WorkspaceKey']
log_type = "CrowdstrikeReplicatorLogs"
AWS_KEY = os.environ['AWS_KEY']
AWS_SECRET = os.environ['AWS_SECRET']
AWS_REGION_NAME = os.environ['AWS_REGION_NAME']
QUEUE_URL = os.environ['QUEUE_URL']
VISIBILITY_TIMEOUT = 60
temp_dir = tempfile.TemporaryDirectory()

def get_sqs_messages():
    logging.info("Creating SQS connection")
    sqs = boto3.resource('sqs', region_name=AWS_REGION_NAME, aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET)
    queue = sqs.Queue(url=QUEUE_URL)
    logging.info("Queue connected")
    for msg in queue.receive_messages(VisibilityTimeout=VISIBILITY_TIMEOUT):
        msg_body = json.loads(msg.body)
        ts = datetime.datetime.utcfromtimestamp(msg_body['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        logging.info("Start processing bucket {0}: {1} files with total size {2}, bucket timestamp: {3}".format(msg_body['bucket'],msg_body['fileCount'],msg_body['totalSize'],ts))
        if "files" in msg_body:
            if download_message_files(msg_body) is True:
                msg.delete()

def process_message_files():
    for file in files_for_handling:
        process_file(file)

def download_message_files(msg):
    try:
        msg_output_path = os.path.join(temp_dir.name, msg['pathPrefix'])
        if not os.path.exists(msg_output_path):
            os.makedirs(msg_output_path)
        for s3_file in msg['files']:
            s3_path = s3_file['path']
            local_path = os.path.join(temp_dir.name, s3_path)
            logging.info("Start downloading file {}".format(s3_path))
            s3_client.download_file(msg['bucket'], s3_path, local_path)
            if check_damaged_archive(local_path) is True:
                logging.info("File {} successfully downloaded.".format(s3_path))
                files_for_handling.append(local_path)
            else:
                logging.warn("File {} damaged. Unpack ERROR.".format(s3_path))
        return True
    except Exception as ex:
        logging.error("Exception in downloading file from S3. Msg: {0}".format(str(ex)))
        return False

def check_damaged_archive(file_path):
    chunksize = 10000000  # 10 Mbytes
    with gzip.open(file_path, 'rb') as f:
        try:
            while f.read(chunksize) != '':
                return True
        except:
            return False

def process_file(file_path):
    size = 1024*1024
    out_tmp_file_path = file_path.replace(".gz", ".tmp")
    with gzip.open(file_path, 'rb') as f_in:
        with open(out_tmp_file_path, 'wb') as f_out:
            while True:
                data = f_in.read(size)
                if not data:
                    break
                f_out.write(data)
    out_path = os.path.dirname(file_path)
    fs = FileSplit(file=out_tmp_file_path, splitsize=20*1024*1024, output_dir=out_path) ## 20 Mb for 1 split chunk
    fs.split(include_header=False, callback=cb_rename_tmp_to_json)
    chunk_result = process_chunks(out_path)
    logging.info("File {} processed. {} events - successfully, {} events - failed.".format(file_path,chunk_result[0],chunk_result[1]))
    os.remove(file_path)

def process_chunks(path):
    global processed_messages_success, processed_messages_failed
    processed_messages_success = 0
    processed_messages_failed = 0
    threads = []
    for file in os.listdir(path):
        if file.endswith(".json"):
            data = []
            with open(os.path.join(path, file)) as json_file:
                logging.info("Processing chunk file: {}.".format(json_file.name))
                for line in json_file:
                    data.append(json.loads(line))
            os.remove(json_file.name)
            chunk_count = len(data)
            data = json.dumps(data)
            t = threading.Thread(target=post_data,args=(data,chunk_count))
            threads.append(t)
            t.start()
    for t in threads:
        t.join()
    return processed_messages_success,processed_messages_failed

def build_signature(customer_id, shared_key, date, content_length, method, content_type, resource):
    x_headers = 'x-ms-date:' + date
    string_to_hash = method + "\n" + str(content_length) + "\n" + content_type + "\n" + x_headers + "\n" + resource
    bytes_to_hash = bytes(string_to_hash, encoding="utf-8")
    decoded_key = base64.b64decode(shared_key)
    encoded_hash = base64.b64encode(hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()).decode()
    authorization = "SharedKey {}:{}".format(customer_id,encoded_hash)
    return authorization

def post_data(body,chunk_count):
    global processed_messages_success, processed_messages_failed
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
        processed_messages_success = processed_messages_success + chunk_count
        logging.info("Chunk with {} events was processed and uploaded to Azure".format(chunk_count))
    else:
        processed_messages_failed = processed_messages_failed + chunk_count
        logging.warn("Problem with uploading to Azure. Response code: {}".format(response.status_code))

def cb_rename_tmp_to_json(file_path, file_size, lines_count):
    out_file_name = file_path.replace(".tmp", ".json")
    os.rename(file_path, out_file_name)

def create_s3_client():
    try:
        boto_config = BotoCoreConfig(region_name=AWS_REGION_NAME)
        return boto3.client('s3', region_name=AWS_REGION_NAME, aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET, config=boto_config)
    except Exception as ex:
        logging.error("Connect to S3 exception. Msg: {0}".format(str(ex)))
        return None

s3_client = create_s3_client()

def main(mytimer: func.TimerRequest)  -> None:
    if mytimer.past_due:
        logging.info('The timer is past due!')
    logging.info('Starting program')
    while True:
        global files_for_handling
        files_for_handling = []
        get_sqs_messages()
        process_message_files()
        time.sleep(1)