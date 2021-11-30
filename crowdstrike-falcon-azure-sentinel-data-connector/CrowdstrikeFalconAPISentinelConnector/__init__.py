import asyncio
import os
import sys
import asyncio
import json
from botocore.config import Config as BotoCoreConfig
from aiobotocore.session import get_session
from gzip_stream import AsyncGZIPDecompressedStream
import re
from .sentinel_connector_async import AzureSentinelConnectorAsync
import time
import aiohttp
import logging
import azure.functions as func

MAX_SCRIPT_EXEC_TIME_MINUTES = 5

WORKSPACE_ID = os.environ['WorkspaceID']
SHARED_KEY = os.environ['WorkspaceKey']
LOG_TYPE = "CrowdstrikeReplicatorLogs"
AWS_KEY = os.environ['AWS_KEY']
AWS_SECRET = os.environ['AWS_SECRET']
AWS_REGION_NAME = os.environ['AWS_REGION_NAME']
QUEUE_URL = os.environ['QUEUE_URL']
VISIBILITY_TIMEOUT = 60
LINE_SEPARATOR = os.environ.get('lineSeparator',  '[\n\r\x0b\v\x0c\f\x1c\x1d\x85\x1e\u2028\u2029]+')

# Defines how many files can be processed simultaneously
MAX_CONCURRENT_PROCESSING_FILES = int(os.environ.get('SimultaneouslyProcessingFiles', 5))

# Defines max number of events that can be sent in one request to Azure Sentinel
MAX_BUCKET_SIZE = int(os.environ.get('EventsBucketSize', 1000))

LOG_ANALYTICS_URI = os.environ.get('logAnalyticsUri')
if not LOG_ANALYTICS_URI or str(LOG_ANALYTICS_URI).isspace():
    LOG_ANALYTICS_URI = 'https://' + WORKSPACE_ID + '.ods.opinsights.azure.com'
pattern = r'https:\/\/([\w\-]+)\.ods\.opinsights\.azure.([a-zA-Z\.]+)$'
match = re.match(pattern, str(LOG_ANALYTICS_URI))
if not match:
    raise Exception("Invalid Log Analytics Uri.")

script_start_time = int(time.time())

def _create_sqs_client():
    sqs_session = get_session()
    return sqs_session.create_client(
                                    'sqs', 
                                    region_name=AWS_REGION_NAME,
                                    aws_access_key_id=AWS_KEY, 
                                    aws_secret_access_key=AWS_SECRET
                                    )

def _create_s3_client():
    s3_session = get_session()
    boto_config = BotoCoreConfig(region_name=AWS_REGION_NAME)
    return s3_session.create_client(
                                    's3',
                                    region_name=AWS_REGION_NAME,
                                    aws_access_key_id=AWS_KEY,
                                    aws_secret_access_key=AWS_SECRET,
                                    config=boto_config
                                    )

def check_if_script_runs_too_long():
    now = int(time.time())
    duration = now - script_start_time
    max_duration = int(MAX_SCRIPT_EXEC_TIME_MINUTES * 60 * 0.85)
    return duration > max_duration

async def main(mytimer: func.TimerRequest):
    logging.info("Creating SQS connection")
    async with _create_sqs_client() as client:
        async with aiohttp.ClientSession() as session:
            logging.info('Trying to check messages off the queue...')
            while True:
                try:
                    response = await client.receive_message(
                        QueueUrl=QUEUE_URL,
                        WaitTimeSeconds=2,
                        VisibilityTimeout=VISIBILITY_TIMEOUT
                    )
                    if 'Messages' in response:
                        for msg in response['Messages']:
                            body_obj = json.loads(msg["Body"])
                            logging.info("Got message with MessageId {}. Start processing {} files from Bucket: {}. Path prefix: {}".format(msg["MessageId"], body_obj["fileCount"], body_obj["bucket"], body_obj["pathPrefix"]))
                            await download_message_files(body_obj, session)
                            logging.info("Finished processing {} files from MessageId {}. Bucket: {}. Path prefix: {}".format(body_obj["fileCount"], msg["MessageId"], body_obj["bucket"], body_obj["pathPrefix"]))
                            try:
                                await client.delete_message(
                                    QueueUrl=QUEUE_URL,
                                    ReceiptHandle=msg['ReceiptHandle']
                                )
                            except Exception as e:
                                logging.error("Error during deleting message with MessageId {} from queue. Bucket: {}. Path prefix: {}. Error: {}".format(msg["MessageId"], body_obj["bucket"], body_obj["pathPrefix"], e))
                    else:
                        logging.info('No messages in queue. Re-trying to check...')
                except KeyboardInterrupt:
                    break

                if check_if_script_runs_too_long():
                    logging.info('Script is running too long. Stop processing new messages from queue.')
                    break


async def process_file(bucket, s3_path, client, semaphore, session):
    async with semaphore:
        total_events = 0
        logging.info("Start processing file {}".format(s3_path))
        sentinel = AzureSentinelConnectorAsync(
                                                session,
                                                LOG_ANALYTICS_URI,
                                                WORKSPACE_ID,
                                                SHARED_KEY,
                                                LOG_TYPE, 
                                                queue_size=MAX_BUCKET_SIZE
                                                )
        response = await client.get_object(Bucket=bucket, Key=s3_path)
        s = ''
        async for decompressed_chunk in AsyncGZIPDecompressedStream(response["Body"]):
            s += decompressed_chunk.decode(errors='ignore')
            lines = re.split(r'{0}'.format(LINE_SEPARATOR), s)
            for n, line in enumerate(lines):
                if n < len(lines) - 1:
                    if line:
                        try:
                            event = json.loads(line)
                        except ValueError as e:
                            logging.error('Error while loading json Event at s value {}. Error: {}'.format(line, str(e)))
                            raise e
                        await sentinel.send(event)
            s = line
        if s:
            try:
                event = json.loads(s)
            except ValueError as e:
                logging.error('Error while loading json Event at s value {}. Error: {}'.format(line, str(e)))
                raise e
            await sentinel.send(event)
        await sentinel.flush()
        total_events += sentinel.successfull_sent_events_number
        logging.info("Finish processing file {}. Sent events: {}".format(s3_path, sentinel.successfull_sent_events_number))


async def download_message_files(msg, session):
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROCESSING_FILES)
    async with _create_s3_client() as client:
        cors = []
        for s3_file in msg['files']:
            cors.append(process_file(msg['bucket'], s3_file['path'], client, semaphore, session))
        await asyncio.gather(*cors)