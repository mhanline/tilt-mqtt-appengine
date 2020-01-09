# [START app]
import base64
from flask import current_app, Flask, render_template, request
import json
import logging
import os
import httplib2
from datetime import datetime, timezone
import pytz

from google.auth.transport import requests
from google.cloud import pubsub_v1
from google.oauth2 import id_token

# For BQ client:
from google.cloud import bigquery
from google.oauth2 import service_account
import io

# For Sheets functionality
import httplib2
import os

from googleapiclient import discovery

gatewayConfig = {
    'bhanl-tilt01': {
        'sheetId': '1nuCXTYfxZoZpCwc2NmxcsWXWNiA1ODwy6RvfSqvuLW4',
        'timezone': 'Australia/Brisbane'
    },
    'mhanl-tilt01': {
        'sheetId': '1qUbyqXzKp8R1UooWznKSy6EXY3hLFOHIB-uBIcqgs0s',
        'timezone': 'Australia/Sydney'
    }
}


def writeSheet(timestamp, message, deviceId):
    '''Appends timestamp, temp, SG to Google Sheets
    Create a new sheet with the colour of the tilt
    '''
    try:
        # Check for a sheet ID based on the deviceID
        # print(f"DEBUG tz: {gatewayConfig[deviceId]['timezone']}")
        idOfSheet = gatewayConfig[deviceId]['sheetId']

    except:
        print(f'No Sheet config for your tilt')
        return False
    try:
        sheet_client_secret = os.path.join(
            os.getcwd(),
            'keys/sheets-append-client_secret.json'
        )
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/spreadsheets"
        ]
        range_name = message["colour"] + '!A1:C2'
        values = {
            'values': [
                [timestamp.astimezone(pytz.timezone("Australia/Brisbane")).strftime("%d/%m/%Y %H:%M:%S"), message["SG"], message["temperature"]]
            ]
        }
        # Keep here in case:
        # timestamp.astimezone(pytz.timezone("Australia/Brisbane")).isoformat(" ", timespec="seconds")
        print(f'DEBUG Values: {values}')
        credentials = service_account.Credentials.from_service_account_file(
            sheet_client_secret,
            scopes=scopes)
        service = discovery.build('sheets', 'v4', credentials=credentials)
        request = service.spreadsheets().values().append(
            spreadsheetId=idOfSheet,
            range=range_name,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=values)
        response = request.execute()
        # print(f'DEBUG: {response}')
    except OSError as e:
        print(f'Error: {e}')
    return True


# For BQ client library:
dataset_id = "tilt_log_dataset"
table_id = "tilt_log_table"
bq_key_path = "bq-keys/tilt-logger-bq-sa.json"


app = Flask(__name__)
# Configure the following environment variables via app.yaml
# This is used in the push request handler to verify that the request came from
# pubsub and originated from a trusted source.
app.config['PUBSUB_VERIFICATION_TOKEN'] = \
    os.environ['PUBSUB_VERIFICATION_TOKEN']
app.config['PUBSUB_TOPIC'] = os.environ['PUBSUB_TOPIC']
app.config['GCLOUD_PROJECT'] = os.environ['GOOGLE_CLOUD_PROJECT']

# Global list to store messages, tokens, etc. received by this instance.
MESSAGES = []
TOKENS = []
CLAIMS = []

# [START index]
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        return render_template('index.html', messages=MESSAGES)

    data = request.form.get('payload', 'Example payload').encode('utf-8')

    # Consider initializing the publisher client outside this function
    # for better latency performance.
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(app.config['GCLOUD_PROJECT'],
                                      app.config['PUBSUB_TOPIC'])
    future = publisher.publish(topic_path, data)
    future.result()
    return 'OK', 200
# [END index]


# [START push]
@app.route('/_ah/push-handlers/receive_messages', methods=['POST'])
def receive_messages_handler():
    # Verify that the request originates from the application.
    if (request.args.get('token', '') !=
            current_app.config['PUBSUB_VERIFICATION_TOKEN']):
        return 'Invalid request', 403

    # Verify that the push request originates from Cloud Pub/Sub.
    try:
        # Get the Cloud Pub/Sub-generated JWT in the "Authorization" header.
        bearer_token = request.headers.get('Authorization')
        token = bearer_token.split(' ')[1]
        TOKENS.append(token)
        claim = id_token.verify_oauth2_token(token, requests.Request(),
                                             audience=None)
        # Must also verify the `iss` claim.
        if claim['iss'] not in [
            'accounts.google.com',
            'https://accounts.google.com'
        ]:
            raise ValueError('Wrong issuer.')
        CLAIMS.append(claim)
    except Exception as e:
        return 'Invalid token: {}\n'.format(e), 403
    envelope = json.loads(request.data.decode("utf-8"))
    payload = base64.b64decode(envelope['message']['data'])
    try:
        message = json.loads(
            base64.b64decode(envelope["message"]["data"]).decode('utf-8'))
    except ValueError as e:
        print(f'Message is not JSON format: {e}')
        # Remove payload eventually
        print(f'Message body: {payload}')
        return 'OK', 200
    print(f'Payload: {payload}')
    print(f'message: {message}')
    recordTimestamp = datetime.now(pytz.timezone('utc'))
    # print(f'DEBUG: {envelope["message"]["attributes"]["deviceId"]}')
    writeSheet(  # Call function to write to gsheet
        recordTimestamp,
        message,
        envelope["message"]["attributes"]["deviceId"]
    )
    MESSAGES.append(payload)
    credentials = service_account.Credentials.from_service_account_file(
        bq_key_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    # Assemble the JSON payload to load BQ
    data = f'{{"messageId": \
    "{envelope["message"]["messageId"]}", \
    "deviceId": \
    "{envelope["message"]["attributes"]["deviceId"]}", \
    "deviceRegistryId": \
    "{envelope["message"]["attributes"]["deviceRegistryId"]}", \
    "deviceLogTime": \
    "1970-12-04 12:00:00", \
    "cloudLogTime": \
    "{recordTimestamp.isoformat(" ", timespec="seconds")}", \
    "specificGravity": \
    "{message["SG"]}", \
    "colour": \
    "{message["colour"]}", \
    "temperature": \
    "{message["temperature"]}", \
    "deviceRegistryLocation": \
    "{envelope["message"]["attributes"]["deviceRegistryLocation"]}"}}'

    print(f'data: {data}')
    data_as_file = io.StringIO(data)

    client = bigquery.Client(
        credentials=credentials,
        project=credentials.project_id,
    )
    dataset_ref = client.dataset(dataset_id)
    table_ref = dataset_ref.table(table_id)
    job_config = bigquery.LoadJobConfig()
    job_config.source_format = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON
    job_config.schema = [
        bigquery.SchemaField("messageId", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("deviceId", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("deviceRegistryId", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("deviceLogTime", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("cloudLogTime", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("specificGravity", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("colour", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("temperature", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField(
            "deviceRegistryLocation",
            "STRING",
            mode="REQUIRED")
    ]
    job = client.load_table_from_file(
        data_as_file,
        table_ref,
        job_config=job_config)
    try:
        job.result()  # Waits for table load to complete.
    except exception as e:
        print(f'ERROR: {e}')
    print(f"Loaded {job.output_rows} rows into {dataset_id}:{table_id}.")
    return 'OK', 200
# [END push]


@app.errorhandler(500)
def server_error(e):
    logging.exception('An error occurred during a request.')
    return """
    An internal error occurred: <pre>{}</pre>
    See logs for full stacktrace.
    """.format(e), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
# [END app]