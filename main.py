# [START app]
# Standard library imports:
from base64 import b64decode
from os import environ, path, getcwd
from io import StringIO
from json import loads as jsonload
from ast import literal_eval
from logging import exception


from flask import current_app, Flask, render_template, request

import pytz
from google.cloud import pubsub_v1
from google.cloud import bigquery as bq
from google.oauth2 import service_account
from google.oauth2 import id_token
from google.auth.transport import requests
from googleapiclient import discovery
from datetime import datetime

# Probably not required:
from datetime import timezone
import httplib2

gateway_dict = literal_eval(environ["GATEWAYCONFIG"])
# print(f'DEBUG dump sheetId: {gateway_dict["tiltname"]["sheetId"]}')


def writeSheet(timestamp, message, deviceId):
    '''Appends timestamp, temp, SG to Google Sheets
    Create a new sheet with the colour of the tilt
    '''
    try:
        # Check for a sheet ID based on the deviceID
        # print(f"DEBUG tz: {gateway_dict[deviceId]['timezone']}")
        print(f'DEBUG sheetId: {gateway_dict[deviceId]["sheetId"]}')
        idOfSheet = gateway_dict[deviceId]['sheetId']

    except:
        print(f'No Sheet config for your tilt')
        return False
    try:
        sheet_client_secret = path.join(
            getcwd(),
            'keys/sa-sheets-append-key.json'
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
        service = discovery.build('sheets', 'v4', credentials=credentials, cache_discovery=False)
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


app = Flask(__name__)
app.config['PUBSUB_VERIFICATION_TOKEN'] = \
    environ['PUBSUB_TOKEN']
app.config['PUBSUB_TOPIC'] = environ['PUBSUB_TOPIC']
app.config['GCLOUD_PROJECT'] = environ['GOOGLE_CLOUD_PROJECT']
# For BQ client library:
app.config['BQ_DATASET'] = environ['BQ_DATASET']
app.config['BQ_TABLE'] = environ['BQ_TABLE']
app.config['BQ_KEYPATH'] = environ['BQ_KEYPATH']
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
    topic_path = publisher.topic_path(current_app.config['GCLOUD_PROJECT'],
                                      current_app.config['PUBSUB_TOPIC'])
    future = publisher.publish(topic_path, data)
    future.result()
    return 'OK', 200
# [END index]


# [START push]
@app.route('/_ah/push-handlers/receive_messages', methods=['POST'])
def receive_messages_handler():
    if (request.args.get('token', '') != current_app.config['PUBSUB_VERIFICATION_TOKEN']):
        print(f"DEBUG: {current_app.config['PUBSUB_VERIFICATION_TOKEN']}")
        print(f"DEBUG: {request.args.get('token', '')}")
        return 'Invalid request', 403

    # Verify that the push request originates from Cloud Pub/Sub.
    try:
        # Get the Cloud Pub/Sub-generated JWT in the "Authorization" header.
        bearer_token = request.headers.get('Authorization')
        # To-Do: Add exception handler for split error if no Auth token received
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
        print(f"debug: {e}")
        print(f"DEBUG bearer: {bearer_token}")
        return 'Invalid token: {}\n'.format(e), 403
    envelope = jsonload(request.data.decode("utf-8"))
    payload = b64decode(envelope['message']['data'])
    try:
        message = jsonload(
            b64decode(envelope["message"]["data"]).decode('utf-8'))
    except ValueError as e:
        print(f'Message is not JSON format: {e}')
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
        current_app.config['BQ_KEYPATH'],
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

    print(f'DEBUG data: {data}')
    data_as_file = StringIO(data)

    client = bq.Client(
        credentials=credentials,
        project=credentials.project_id,
    )
    dataset_ref = client.dataset(current_app.config['BQ_DATASET'])
    table_ref = dataset_ref.table(current_app.config['BQ_TABLE'])
    job_config = bq.LoadJobConfig()
    job_config.source_format = bq.SourceFormat.NEWLINE_DELIMITED_JSON
    job_config.schema = [
        bq.SchemaField("messageId", "INT64", mode="REQUIRED"),
        bq.SchemaField("deviceId", "STRING", mode="REQUIRED"),
        bq.SchemaField("deviceRegistryId", "STRING", mode="REQUIRED"),
        bq.SchemaField("deviceLogTime", "TIMESTAMP", mode="REQUIRED"),
        bq.SchemaField("cloudLogTime", "TIMESTAMP", mode="REQUIRED"),
        bq.SchemaField("specificGravity", "FLOAT64", mode="REQUIRED"),
        bq.SchemaField("colour", "STRING", mode="REQUIRED"),
        bq.SchemaField("temperature", "FLOAT64", mode="REQUIRED"),
        bq.SchemaField(
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
    print(f"Loaded {job.output_rows} rows into {current_app.config['BQ_DATASET']}:{current_app.config['BQ_TABLE']}.")
    return 'OK', 200
# [END push]


@app.errorhandler(500)
def server_error(e):
    exception('An error occurred during a request.')
    return """
    An internal error occurred: <pre>{}</pre>
    See logs for full stacktrace.
    """.format(e), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)
# [END app]