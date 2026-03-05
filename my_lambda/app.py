import os
import json
import traceback
import boto3
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from uuid import uuid4
import awsgi
from pymongo import MongoClient

# --- Load environment variables ---
load_dotenv(".env")

# --- Initialize Flask app ---
app = Flask(__name__)
CORS(app)

# --- AWS SQS Client ---
sqs = boto3.client("sqs")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")

# --- Service imports ---
from textract_service import run_textract, get_random_textract_client
from azure_llm import AzureLLMAgent
from mongo import (
    fetch_requested_fields,
    fetch_extracted_text,
    mark_file_as_failed,
    extract_fields_with_llm,
    update_extracted_values_to_mongo,
    update_debit_credit,
    update_job_status,
    fetch_job_status,
    delete_credit_record
)
from bson import ObjectId

from config import S3_BUCKET_NAME


# MongoDB connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client["DB_NAME"]
collection = db["FILE_DETAILS"]

def background_processing(job_id, body):
    try:
        user_id = body["userId"]
        cluster_id = body["clusterId"]
        file_id = body["fileId"]
        
        # ✅ New: Validate creditId
        if "creditId" not in body or not body["creditId"]:
            raise ValueError("❌ Missing required field: creditId")
        
        credit_id = body["creditId"]
        bucket = body.get("bucket", S3_BUCKET_NAME)
        print(f"🚀 Starting processing for fileId: {file_id}")
        print(f"🚀 S3 bucket: {bucket}")

        agent = AzureLLMAgent()

        print('pushin sixth time from git to lambda')
        # Fetch requested fields
        fields = fetch_requested_fields(user_id, cluster_id)
        
        print('****fields****',fields)

        # Check for cached text
        full_text, filename, page_count = fetch_extracted_text(user_id, cluster_id, file_id)

        if full_text:
            print("🟡 Using cached extracted text from DB")
        else:
            #filename = filename or f"{file_id}.pdf"  # fallback if filename missing
            # Fetch originalFile URL from DB
            query = {
                    "_id": ObjectId(file_id),
                    "userId": ObjectId(user_id),
                    "clusterId": ObjectId(cluster_id)
                }
            
            document = collection.find_one({"_id": ObjectId(file_id)})
            original_file_url = document.get("originalFile")
            
            if not original_file_url:
                raise Exception("File not found in database")
            
            #original_file_url = document.get("originalFile")    
            
            

            if not original_file_url:
                raise ValueError("❌ originalFile URL missing")

            print(f"🔎 Using originalFile URL: {original_file_url}")

            full_text, filename, page_count, _ = run_textract(
                original_file_url
            )
            
            print('**page_count**',page_count)
            
            print('***fulltext***',full_text)

            if not full_text:
                print(f"❌ Textract failed for fileId: {file_id}, userId: {user_id}, clusterId: {cluster_id}")
                mark_file_as_failed(file_id)
                update_job_status(job_id, status="error", message="❌ Textract failed")
                delete_credit_record(credit_id, file_id)
                return

        values = extract_fields_with_llm(full_text, fields, agent)
        
        print('***values***',values)

        if not values:
            print("❌ Field extraction failed")
            mark_file_as_failed(file_id)
            update_job_status(job_id, status="error", message="❌ Field extraction failed")
            delete_credit_record(credit_id, file_id)
            return

        update_extracted_values_to_mongo(user_id, cluster_id, file_id, fields, values, full_text)
        
        update_debit_credit(credit_id)
        print('credits updated')
        summary = {
            "file": filename,
            "pageCount": page_count,
            "fields": dict(zip([f["fieldName"] for f in fields], values))
        }

        print(f"✅ Job {job_id} finished successfully")
        update_job_status(job_id, status="success", summary=summary)

    except Exception as e:
        print(f"❌ Error in background_processing: {e}")
        print(traceback.format_exc())
        delete_credit_record(credit_id, file_id)
        update_job_status(job_id, status="error", message=str(e))


@app.route("/run-processing", methods=["POST"])
def run_processing():
    try:
        body = request.get_json()
        job_id = str(uuid4())
        body["jobId"] = job_id

        print(f"📥 Received new job request: {job_id}")

        update_job_status(job_id, status="queued")

        sqs.send_message(QueueUrl=SQS_QUEUE_URL, MessageBody=json.dumps(body))

        return jsonify({
            "status": "queued",
            "message": "⏳ Job enqueued for background processing",
            "jobId": job_id
        }), 202

    except Exception as e:
        print(f"❌ Error in run-processing: {e}")
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get-job-result/<job_id>", methods=["GET"])
def get_job_result(job_id):
    try:
        print(f"🔍 Fetching job result for ID: {job_id}")
        job = fetch_job_status(job_id)

        if not job:
            return jsonify({
                "job_id": job_id,
                "status": "processing",
                "message": "Job is still being registered. Please retry in a few seconds."
            }), 200

        return jsonify(job), 200

    except Exception as e:
        print(f"❌ Error in get-job-result: {e}")
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "API is running"}), 200


def lambda_handler(event, context):
    if "httpMethod" in event:
        return awsgi.response(app, event, context)

    if "Records" in event:
        for record in event["Records"]:
            try:
                body = json.loads(record["body"])
                job_id = body.get("jobId", str(uuid4()))
                print(f"⚙️ Processing SQS job: {job_id}")
                background_processing(job_id, body)
            except Exception as e:
                print(f"❌ SQS record processing error: {e}")
                print(traceback.format_exc())
        return {"statusCode": 200, "body": "SQS messages processed"}

    return {"statusCode": 400, "body": "Unsupported event source"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
