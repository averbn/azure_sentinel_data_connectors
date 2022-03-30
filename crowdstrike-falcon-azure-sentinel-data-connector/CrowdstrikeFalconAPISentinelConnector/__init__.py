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
from .state_manager import StateManager

WORKSPACE_ID = os.environ['WorkspaceID']
SHARED_KEY = os.environ['WorkspaceKey']
LOG_TYPE = "CrowdstrikeReplicatorLogs"
AWS_KEY = os.environ['AWS_KEY']
AWS_SECRET = os.environ['AWS_SECRET']
AWS_REGION_NAME = os.environ['AWS_REGION_NAME']
QUEUE_URL = os.environ['QUEUE_URL']
VISIBILITY_TIMEOUT = 1800
LINE_SEPARATOR = os.environ.get('lineSeparator',  '[\n\r\x0b\v\x0c\f\x1c\x1d\x85\x1e\u2028\u2029]+')
connection_string = os.environ['AzureWebJobsStorage']

# Defines how many files can be processed simultaneously
MAX_CONCURRENT_PROCESSING_FILES = int(os.environ.get('SimultaneouslyProcessingFiles', 20))

# Defines max number of events that can be sent in one request to Azure Sentinel
MAX_BUCKET_SIZE = int(os.environ.get('EventsBucketSize', 2000))

LOG_ANALYTICS_URI = os.environ.get('logAnalyticsUri')
if not LOG_ANALYTICS_URI or str(LOG_ANALYTICS_URI).isspace():
    LOG_ANALYTICS_URI = 'https://' + WORKSPACE_ID + '.ods.opinsights.azure.com'
pattern = r'https:\/\/([\w\-]+)\.ods\.opinsights\.azure.([a-zA-Z\.]+)$'
match = re.match(pattern, str(LOG_ANALYTICS_URI))
if not match:
    raise Exception("Invalid Log Analytics Uri.")

drop_files_array = []

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
    boto_config = BotoCoreConfig(region_name=AWS_REGION_NAME, retries = {'max_attempts': 10, 'mode': 'standard'})
    return s3_session.create_client(
                                    's3',
                                    region_name=AWS_REGION_NAME,
                                    aws_access_key_id=AWS_KEY,
                                    aws_secret_access_key=AWS_SECRET,
                                    config=boto_config
                                    )

def customize_event(line):
    element = json.loads(line)
    required_fileds = [
                        "timestamp", "aip", "aid", "EventType", "LogonType", "HostProcessType", "UserPrincipal", "DomainName",
                        "RemoteAddressIP", "ConnectionDirection", "TargetFileName", "LocalAddressIP4", "IsOnRemovableDisk",
                        "UserPrincipal", "UserIsAdmin", "LogonTime", "LogonDomain", "RemoteAccount", "UserId", "Prevalence",
                        "CurrentProcess", "ConnectionDirection", "event_simpleName", "TargetProcessId", "ProcessStartTime",
                        "UserName", "DeviceProductId", "TargetSHA256HashData", "SHA256HashData", "MD5HashData", "TargetDirectoryName",
                        "TargetFileName", "FirewallRule", "TaskName", "TaskExecCommand", "TargetAddress", "TargetProcessId",
                        "SourceFileName", "RegObjectName", "RegValueName", "ServiceObjectName", "RegistryPath", "RawProcessId",
                        "event_platform", "CommandLine", "ParentProcessId", "ParentCommandLine", "ParentBaseFileName",
                        "GrandParentBaseFileName", "RemotePort", "VolumeDeviceType", "VolumeName", "ClientComputerName", "ProductId"
                    ]
    required_fields_data = {}
    custom_fields_data = {}
    for key, value in element.items():
        if key in required_fileds:
            required_fields_data[key] = value
        else:
            custom_fields_data[key] = value
    event = required_fields_data
    custom_fields_data_text = str(json.dumps(custom_fields_data))
    if custom_fields_data_text != "{}":
        event["custom_fields_message"] = custom_fields_data_text
    return event

async def main(mytimer: func.TimerRequest):
    global drop_files_array
    drop_files_array.clear()
    script_start_time = int(time.time())
    filepath = f'{script_start_time}_file'
    state = StateManager(connection_string=connection_string, share_name='funcstatemarkershare', file_path=filepath)
    logging.info("Creating SQS connection")
    async with _create_sqs_client() as client:
        async with aiohttp.ClientSession() as session:
            logging.info('Trying to check messages off the queue...')
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
                pass
    if len(drop_files_array) > 0:
        logging.info("list of files that were not processed: {}".format(drop_files_array))
        state.post(str(drop_files_array))

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
        try_number = 1
        while True:
            try:
                response = await client.get_object(Bucket=bucket, Key=s3_path)
                #response = await client.get_object(Bucket=bucket, Key="egre.g")
                s = ''
                async for decompressed_chunk in AsyncGZIPDecompressedStream(response["Body"]):
                    s += decompressed_chunk.decode(errors='ignore')
                    lines = re.split(r'{0}'.format(LINE_SEPARATOR), s)
                    for n, line in enumerate(lines):
                        if n < len(lines) - 1:
                            if line:
                                try:
                                    event = customize_event(line)
                                except ValueError as e:
                                    logging.error('Error while loading json Event at s value {}. Error: {}'.format(line, str(e)))
                                    raise e
                                await sentinel.send(event)
                    s = line
                if s:
                    try:
                        event = customize_event(line)
                    except ValueError as e:
                        logging.error('Error while loading json Event at s value {}. Error: {}'.format(line, str(e)))
                        raise e
                    await sentinel.send(event)
                await sentinel.flush()   
            except Exception as e:
                if try_number < 3:
                    await asyncio.sleep(try_number)
                    try_number += 1
                    logging.warn('Error while sending data to Azure Sentinel. Try number: {}. Trying one more time. {}'.format(try_number, e))
                else:
                    logging.error(str(e))
                    drop_files_array.append({"bucket": bucket, "path": s3_path})
                    logging.info(drop_files_array)
                    raise e
            else:
                total_events += sentinel.successfull_sent_events_number
                logging.info("Finish processing file {}. Sent events: {}".format(s3_path, sentinel.successfull_sent_events_number))         
                break

async def download_message_files(msg, session):
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROCESSING_FILES)
    async with _create_s3_client() as client:
        cors = []
        logging.info(msg['files'])
        #[{'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00000.gz', 'size': 21703183, 'checksum': '73595d9dee6482056b03fc8dd49b4885'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00001.gz', 'size': 21710530, 'checksum': '74887e135ce7a0f3429da577eda493e3'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00002.gz', 'size': 21685446, 'checksum': '2df20bb170947c784a9659087034120b'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00003.gz', 'size': 21567758, 'checksum': '0b6d36c683ccaf9a98f45bafe3d40084'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00004.gz', 'size': 21676679, 'checksum': '8271eb351b3e69264dfc3c1f4cfb8bbd'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00005.gz', 'size': 21664534, 'checksum': 'af3d417176e72846d7fd5b5c427dcb57'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00006.gz', 'size': 21654387, 'checksum': '578c911ce00ba6f0b11f0c3fa12e0af3'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00007.gz', 'size': 21618733, 'checksum': '7e6d533e5858fb5992de1f05bc4be843'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00008.gz', 'size': 21712517, 'checksum': '6afc68897b05ef5a9e975de8e16783ee'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00009.gz', 'size': 21706984, 'checksum': '0e7c140bbebd6120e90b6f7674bd4071'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00010.gz', 'size': 21598584, 'checksum': '24590094084ac6ecada279fa2b5497ae'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00011.gz', 'size': 21545785, 'checksum': '99ebf24b9c33ca960ffb3831f6030e1b'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00012.gz', 'size': 21656341, 'checksum': 'e52d93f5573485becd5cf6eccaf82a4e'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00013.gz', 'size': 21626849, 'checksum': '16243b9110b1ae5fc463eae0148e7517'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00014.gz', 'size': 21688321, 'checksum': '16a520ac0a12a68b7c77f92173fa6419'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00015.gz', 'size': 21680524, 'checksum': '170b8c5fc9f9f794d9f05314ac43e0d7'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00016.gz', 'size': 21569716, 'checksum': '79f75b03f416948a473089949989953a'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00017.gz', 'size': 21540035, 'checksum': 'bf492fbdaad070c9437179434c92928f'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00018.gz', 'size': 21548651, 'checksum': '9ddf079b8a8ecfb0eb9d87a4622e0aed'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00019.gz', 'size': 21669466, 'checksum': '8f0d7192f3da7b41e4e681f1b058948f'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00020.gz', 'size': 21557571, 'checksum': '273a752dd0e35fcefeefad454662cf79'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00021.gz', 'size': 21568091, 'checksum': 'eb26acaec1ff4651b1389fc4baf129dd'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00022.gz', 'size': 21627070, 'checksum': 'b71afcd3c2e452a7d0f6a3faab295514'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00023.gz', 'size': 21772391, 'checksum': 'c75151d53af8e7ab0b27192b91feb12c'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00024.gz', 'size': 21795986, 'checksum': '28f7ad47584ddfc469d5906315630f86'}, {'path': 'data/8c96cc5a-da4d-4737-b012-46444c105156/part-00025.gz', 'size': 21795046, 'checksum': '152289a1d7c78ea431ecf4bac4274c98'}]
        for s3_file in msg['files']:
            cors.append(process_file(msg['bucket'], s3_file['path'], client, semaphore, session))
        await asyncio.gather(*cors)