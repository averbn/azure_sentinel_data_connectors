import logging
import azure.functions as func
import azure.durable_functions as df
import boto3
import json
import datetime
from botocore.config import Config as BotoCoreConfig
import os
import tempfile
import gzip
import time

AWS_KEY = os.environ['AWS_KEY']
AWS_SECRET = os.environ['AWS_SECRET']
AWS_REGION_NAME = os.environ['AWS_REGION_NAME']
QUEUE_URL = os.environ['QUEUE_URL']
VISIBILITY_TIMEOUT = 60
temp_dir = tempfile.TemporaryDirectory()
os.environ['temp_directory'] = '/var/folders/24/w2xsm9ps45scpl5vt9bfp6y40000gn/T/tmpibqny59r'
data="TEST"

def get_sqs_messages():
        #logging.info("Creating SQS connection")
        sqs = boto3.resource('sqs', region_name=AWS_REGION_NAME, aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET)
        queue = sqs.Queue(url=QUEUE_URL)
        #logging.info("Queue connected")
        for msg in queue.receive_messages(VisibilityTimeout=VISIBILITY_TIMEOUT):
            msg_body = json.loads(msg.body)
            ts = datetime.datetime.utcfromtimestamp(msg_body['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            logging.info("Start processing bucket {0}: {1} files with total size {2}, bucket timestamp: {3}".format(msg_body['bucket'],msg_body['fileCount'],msg_body['totalSize'],ts))
            if "files" in msg_body:
                if download_message_files(msg_body) is True:
                    msg.delete()

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
    chunksize = 1024*1024  # 10 Mbytes
    with gzip.open(file_path, 'rb') as f:
        try:
            while f.read(chunksize) != '':
                return True
        except:
            return False

def create_s3_client():
    try:
        boto_config = BotoCoreConfig(region_name=AWS_REGION_NAME)
        return boto3.client('s3', region_name=AWS_REGION_NAME, aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET, config=boto_config)
    except Exception as ex:
        logging.error("Connect to S3 exception. Msg: {0}".format(str(ex)))
        return None

s3_client = create_s3_client()

def main(mytimer: func.TimerRequest)  -> None:
    logging.info('Starting program')
    max_time = 9 * 60 # 9 minutes
    start_time = time.time()  # remember when we started
    global files_for_handling
    # while (time.time() - start_time) < max_time:
    #     get_sqs_messages()


