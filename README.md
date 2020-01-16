# Tilt Gateway AppEngine parser to BigQuery and Google Sheets

## Setup

Set up the Google Cloud project, service accounts, datasets, tables:
(Assume you are using the Google Cloud Console, otherwise replace $DEVSHELL_PROJECT_ID environment variable with your actual DEVSHELL_PROJECT_ID)

1. Create a new project and assign a billing account.
 Note: This should utilise the GCP free tier but you will still need a valid credit card in case your app goes over the free tier

```
# Clone this project 
git clone ...
```
2. Enable APIs:
```
gcloud services enable pubsub.googleapis.com
gcloud services enable cloudiot.googleapis.com
gcloud services enable sheets.googleapis.com
gcloud services enable bigquery.googleapis.com
gcloud services enable drive.googleapis.com
```

3. export environment variables:
```
export APPENGINE_REGION="us-central"
export PUBSUB_TOPIC="tilt-gateways"
export PUBSUB_SUBSCRIPTION="tilt-gateway-sub"
export IOTCORE_REGION="us-central1"
export REGISTRY_NAME="iot-core-tilt-registry"
# Name you configure in the Tilt gateway
export DEVICE_NAME=""
export SA_EMAIL=service-`gcloud projects list --filter="$DEVSHELL_PROJECT_ID" --format="value(PROJECT_NUMBER)"`@gcp-sa-pubsub.iam.gserviceaccount.com
export PUBSUB_TOKEN="Long_string_of_rnd_chars"
export BQ_REGION='us-west2'
```

4. Pub/Sub
```
# This allows pubsub to generate keys using its own internal service account
gcloud projects add-iam-policy-binding ${DEVSHELL_PROJECT_ID} --member="serviceAccount:${SA_EMAIL}" --role='roles/iam.serviceAccountTokenCreator'

# This creates a new service account for sending authenticated pushes to AppEngine 
gcloud iam service-accounts create sa-pubsub-test --display-name "Pub/Sub ${PUBSUB_TOPIC} Push Subscription Service Account"

# Creates the push subscription from IoT Core to App Engine
gcloud pubsub subscriptions create $PUBSUB_SUBSCRIPTION --topic $PUBSUB_TOPIC --push-endpoint https://${DEVSHELL_PROJECT_ID}.appspot.com/_ah/push-handlers/receive_messages?token=$PUBSUB_TOKEN --push-auth-service-account=sa-pubsub-test@${DEVSHELL_PROJECT_ID}.iam.gserviceaccount.com --ack-deadline 10
```

5. Cloud IoT core
```
# Allow the IoT Core service account to publish to Pub/Sub
gcloud projects add-iam-policy-binding $DEVSHELL_PROJECT_ID \
  --member=serviceAccount:cloud-iot@system.gserviceaccount.com \
  --role=roles/pubsub.publisher

#Create Cloud IoT registry specifying Cloud Pub/Sub topic name 
gcloud iot registries create $REGISTRY_NAME --region=$IOTCORE_REGION --enable-mqtt-config --enable-http-config --event-notification-config=topic=${PUBSUB_TOPIC}

# Generate an Eliptic Curve (EC) ES256 private / public key pair
#To-Do: Remove the private key from upload.
openssl ecparam -genkey -name prime256v1 -noout -out keys/${DEVICE_NAME}_ec_private.pem
openssl ec -in keys/${DEVICE_NAME}_ec_private.pem -pubout -out keys/${DEVICE_NAME}_ec_public.pem

# Create a new Cloud IoT device
gcloud iot devices create $DEVICE_NAME \
  --region=$IOTCORE_REGION \
  --registry=$REGISTRY_NAME \
  --public-key="path=./keys/${DEVICE_NAME}_ec_public.pem,type=es256"
```
6. Sheets Setup
```
# Setup Service Account
gcloud iam service-accounts create sa-sheets-append --display-name "sa-sheets-append"
gcloud iam service-accounts keys create keys/sa-sheets-append-key.json --iam-account sa-sheets-append@${DEVSHELL_PROJECT_ID}.iam.gserviceaccount.com
#Shouldn't be needed
gcloud projects add-iam-policy-binding ${DEVSHELL_PROJECT_ID} --member serviceAccount:sa-sheets-append@${DEVSHELL_PROJECT_ID}.iam.gserviceaccount.com --role roles/iam.serviceAccountUser
```
Manual process:
- Create a new blank Google Sheet
- Name the first tab with the colour of the Tilt you have
- Click "Share", then set the service account user with edit permissions
7. BigQuery setup
```
# Create the service account and key
gcloud iam service-accounts create sa-bq-loader --display-name "bq-loader"
gcloud iam service-accounts keys create keys/sa-bq-loader-key.json --iam-account sa-bq-loader@${DEVSHELL_PROJECT_ID}.iam.gserviceaccount.com

# Allow the service account permissions in BigQuery to load data
gcloud projects add-iam-policy-binding ${DEVSHELL_PROJECT_ID} --member serviceAccount:sa-bq-loader@${DEVSHELL_PROJECT_ID}.iam.gserviceaccount.com --role roles/bigquery.dataEditor
gcloud projects add-iam-policy-binding ${DEVSHELL_PROJECT_ID} --member serviceAccount:sa-bq-loader@${DEVSHELL_PROJECT_ID}.iam.gserviceaccount.com --role roles/bigquery.jobUser
gcloud projects add-iam-policy-binding ${DEVSHELL_PROJECT_ID} --member serviceAccount:sa-bq-loader@${DEVSHELL_PROJECT_ID}.iam.gserviceaccount.com --role roles/iam.serviceAccountUser

# Create the BigQuery dataset and table
bq --location=$BQ_REGION mk --dataset --description "tilt log dataset" tilt_log_dataset
bq mk --table tilt_log_dataset.tilt_log_table tilt-logger-schema.json
```
8. Deploy App Engine app
```
cp env_config.yaml.sample env_config.yaml
```
Edit the particulars of env_config.yaml 

```


gcloud app create --region=$APPENGINE_REGION
gcloud app deploy

```

