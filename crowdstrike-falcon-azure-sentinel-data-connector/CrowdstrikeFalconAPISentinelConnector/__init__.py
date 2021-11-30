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
                    response = {'Messages': [{'MessageId': 'd3e4e45a-3991-4a16-890b-29f5e002cc0d', 'ReceiptHandle': 'AQEBtevy42A+qkJy1MPMAZwI8oU6xjYhs5xUJdT6IWakRfIFGUdGSck3jIlWJtxsVq9Uew/NVB7/woCGJYak/AVAxM7KHXTJA1kXPgJY9frV88ByX5u55fA84wUvLLLxYdFW3Ow4mt1YOOu1zie8VPTV/5OX6txn/q+Tki+QsBSYax/6cPXl9khXC8eMB8Zg9oY+OoRJdoqenIInrcJ/n3b5gtPqFpTqzMFX/Yo3s/ahkdma8oeYdDCLGO2/t2FtpK0dNb0B/IzRjeYBq97HrK8d10n/Dve1k0/x37Rqi3S7WNx8/UZaeFBchnEa7YmipK8vRUunFZ50LOH2/QQLTLKVdWwfw2eLeh6oF/9pnlgIbdiQVUz9RTLFAnrmbjUc+EYNUZF/ZrKrTCn/ldof55vLPoEPM3YjXD2Q4Sz71zYI7XA=', 'MD5OfBody': '12510297427d6e463b2ffff6f45645fc', 'Body': '{"cid":"e941027a2d1141f189b6c6c049c83215","timestamp":1638174719495,"fileCount":40,"totalSize":761795770,"bucket":"cs-prod-cannon-55b1c7be53ada86a","pathPrefix":"data/31c788e5-6979-46e7-9a94-bb8df912899c","files":[{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00000.gz","size":18935583,"checksum":"5b352239e04c9e865d54b0d5d33d095c"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00001.gz","size":19137117,"checksum":"78d4412225908bb5e3704467ffcc1528"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00002.gz","size":19121610,"checksum":"422308173596dd6f2cf817fd75c6b18e"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00003.gz","size":18990430,"checksum":"7dd7a8c7fb364814e3bb8f160d774160"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00004.gz","size":19043702,"checksum":"a74ba39494f8843055dec20db8ba6373"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00005.gz","size":19077136,"checksum":"b9d850eef0dd96ffb3aac72cc4427f23"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00006.gz","size":19049288,"checksum":"295fe5736f86fd20a68387e5c71ff02e"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00007.gz","size":19054640,"checksum":"fe1f1fd5f722cf9143e5beed6d343dd4"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00008.gz","size":19067178,"checksum":"d1b8b768e0d3244459d498487407b407"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00009.gz","size":18946961,"checksum":"789f743dc67b2f04b1d3c673d26dfa27"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00010.gz","size":19102374,"checksum":"e5a3416b1c1e66381463b294a571ea6c"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00011.gz","size":19042016,"checksum":"32657a9618174bef681456a07da38495"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00012.gz","size":19017420,"checksum":"8e9504e7bb96197282eb1a4b916933c0"},{"path":"data/31c788e5-6979-46e7-9a94-bb8df912899c/part-00013.gz","size":19074313,"checksum":"8afd7b9655d4fd24acca35e2d04380c7"}]}'}], 'ResponseMetadata': {'RequestId': '70ad1d8b-beb6-5c37-9d48-47952c8908e7', 'HTTPStatusCode': 200, 'HTTPHeaders': {'x-amzn-requestid': '70ad1d8b-beb6-5c37-9d48-47952c8908e7', 'date': 'Mon, 29 Nov 2021 08:32:00 GMT', 'content-type': 'text/xml', 'content-length': '8346'}, 'RetryAttempts': 0}}
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
                        #logging.info(event)
            s = line
        if s:
            try:
                event = json.loads(s)
            except ValueError as e:
                logging.error('Error while loading json Event at s value {}. Error: {}'.format(line, str(e)))
                raise e
            await sentinel.send(event)
            #logging.info(event)
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