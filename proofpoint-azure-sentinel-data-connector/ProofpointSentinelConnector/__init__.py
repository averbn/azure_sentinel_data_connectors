import os
import datetime
import socket
import websocket
import json
import ssl
import time
import base64
import hashlib
import hmac
import requests
import azure.functions as func


customer_id = os.environ['WorkspaceID'] 
shared_key = os.environ['WorkspaceKey']
log_type = 'ProofpointPOD'

cluster_id = os.environ['ProofpointClusterID']
_token = os.environ['ProofpointToken']
time_period_minutes = 10
time_delay_minutes = 500


def main():
    api = Proofpoint_api()

    #api.get_data(event_type='message')
    api.get_data(event_type='maillog')


class Proofpoint_api:
    def __init__(self):

        self.cluster_id = cluster_id
        self._token = _token
        self.time_period_minutes = int(time_period_minutes)
        self.time_delay_minutes = int(time_delay_minutes)
        self.gen_timeframe(time_period_minutes=self.time_period_minutes, time_delay_minutes=self.time_delay_minutes)

    def gen_timeframe(self, time_period_minutes, time_delay_minutes):
        before_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=time_delay_minutes)
        after_time = before_time - datetime.timedelta(minutes=time_period_minutes)
        self.before_time = before_time.strftime("%Y-%m-%dT%H:%M:00.000000")
        self.after_time = after_time.strftime("%Y-%m-%dT%H:%M:00.000000")



    def set_websocket_conn(self, event_type):

        url = f"wss://logstream.proofpoint.com:443/v1/stream?cid={self.cluster_id}&type={event_type}&sinceTime={self.after_time}&toTime={self.before_time}"
        print(url)
        # defining headers for websocket connection (do not change this)
        header = {
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Authorization": f"Bearer {self._token}",
            "Sec-WebSocket-Key": "SGVsbG8sIHdvcmxkIQ==",
            "Sec-WebSocket-Version": "13"
        }

        try:
            ws = websocket.create_connection(url, header=header, sslopt={"cert_reqs": ssl.CERT_NONE})
            ws.settimeout(20)
            time.sleep(2)
            self.logging.info(
                'Websocket connection established to cluster_id={}, event_type={}'.format(self.cluster_id, event_type))
            print(
                'Websocket connection established to cluster_id={}, event_type={}'.format(self.cluster_id, event_type))
            return ws

        except Exception as err:
            self.logging.error('Error while connectiong to websocket {}'.format(err))
            print('Error while connectiong to websocket {}'.format(err))
            return None

    def gen_chunks_to_object(self,data,chunksize=100):
        chunk = []
        for index, line in enumerate(data):
            if (index % chunksize == 0 and index > 0):
                yield chunk
                del chunk[:]
            chunk.append(line)
        yield chunk

    def gen_chunks(self, data,event_type):
        for chunk in self.gen_chunks_to_object(data, chunksize=1000):
            print(len(chunk))
            obj_array = []
            for row in chunk:
                obj_array.append(row)
            body = json.dumps(obj_array)
            self.post_data(body,len(obj_array))


    def build_signature(self, date, content_length, method, content_type, resource):
        x_headers = 'x-ms-date:' + date
        string_to_hash = method + "\n" + str(content_length) + "\n" + content_type + "\n" + x_headers + "\n" + resource
        bytes_to_hash = bytes(string_to_hash, encoding="utf-8")
        decoded_key = base64.b64decode(shared_key)
        encoded_hash = base64.b64encode(
            hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()).decode()
        authorization = "SharedKey {}:{}".format(customer_id, encoded_hash)
        return authorization


    def post_data(self,body,chunk_count):
        method = 'POST'
        content_type = 'application/json'
        resource = '/api/logs'
        rfc1123date = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
        content_length = len(body)
        signature = self.build_signature(rfc1123date, content_length, method, content_type,
                                    resource)
        uri = 'https://' + customer_id + '.ods.opinsights.azure.com' + resource + '?api-version=2016-04-01'
        headers = {
            'content-type': content_type,
            'Authorization': signature,
            'Log-Type': log_type,
            'x-ms-date': rfc1123date
        }
        response = requests.post(uri, data=body, headers=headers)
        if (response.status_code >= 200 and response.status_code <= 299):
            print('Accepted')
            logging.info("Chunk was processed({} events)".format(chunk_count))
            print("Chunk was processed({} events)".format(chunk_count))
        else:
            print("Response code: {}".format(response.status_code))
            logging.warn("Response code: {}".format(response.status_code))


    def get_data(self, event_type=None):
        sent_events = 0

        ws = self.set_websocket_conn(event_type)

        time.sleep(2)
        print(ws)
        if ws is not None:
            events = []
            while True:
                try:
                    data = ws.recv()
                    events.append(data)
                    sent_events += 1
                except websocket._exceptions.WebSocketTimeoutException:
                    break
                except Exception as err:
                    self.logging.error('Error while receiving data: {}'.format(err))
                    print('Error while receiving data: {}'.format(err))
                    break
            if sent_events > 0:
                self.gen_chunks(events,event_type)
            self.logging.info('Total events sent: {}. Type: {}. Period(UTC): {} - {}'.format(sent_events, event_type,
                                                                                            self.after_time,
                                                                                            self.before_time))
            print('Total events sent: {}. Type: {}. Period(UTC): {} - {}'.format(sent_events, event_type,
                                                                                            self.after_time,
                                                                                           self.before_time))
        else:
            exit(1)

#if __name__ == "__main__":
#    main()