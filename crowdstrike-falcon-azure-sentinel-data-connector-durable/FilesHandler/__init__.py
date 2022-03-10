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
from os import walk
temp_dir_name = '/var/folders/24/w2xsm9ps45scpl5vt9bfp6y40000gn/T/tmpibqny59r'

def get_list_files_recurse(dir):
    result_files = [os.path.join(dp, f) for dp, dn, filenames in os.walk(temp_dir_name) for f in filenames if os.path.splitext(f)[1] == '.gz']
    os.environ['temp_directory'] = json.dumps(result_files)
    return result_files

async def main(mytimer: func.TimerRequest, starter: str) -> None:
    logging.info('Starting files handler')
    # client = df.DurableOrchestrationClient(starter)
    # instance_id = await client.start_new("Orchestrator", None, None)
    # logging.info(f"Started orchestration with ID = '{instance_id}'.")
    logging.info(f"{temp_dir_name}")
    result_files = get_list_files_recurse(temp_dir_name)
    logging.info(os.environ.get("temp_directory"))
    #logging.info(result_files)


